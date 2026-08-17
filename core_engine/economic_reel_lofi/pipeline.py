# -*- coding: utf-8 -*-
"""
ECONOMIC_REEL_LOFI orchestrator.

ThemeSelector → ScriptGenerator → Validator → ImageGen → VisualQA → Assembler
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from core_engine.economic_reel_lofi import config as lofi_cfg
from core_engine.economic_reel_lofi import lofi_collections as rag
from core_engine.economic_reel_lofi.assembler import (
    assemble_lofi_reel,
    compute_caption_scene_duration_s,
)
from core_engine.economic_reel_lofi.image_gen import generate_scene_image
from core_engine.economic_reel_lofi.riso_prompt_bank import (
    assign_riso_prompts_for_scenes,
    export_active_library_diff,
)
from core_engine.economic_reel_lofi.script_agent import (
    _sanitize_caption_typos,
    generate_script,
)
from core_engine.economic_reel_lofi.validator_agent import validate_script

_LOG = logging.getLogger(__name__)


def _tts_text_with_breaks(caption: str) -> str:
    """Insert ElevenLabs SSML pauses at commas / dashes. Caption on-screen stays clean."""
    text = (caption or "").strip()
    if not text:
        return text
    text = re.sub(r"\s*[—–]\s*", ' <break time="0.40s" /> ', text)
    text = re.sub(r",\s+", ', <break time="0.30s" /> ', text)
    text = re.sub(r";\s+", '; <break time="0.32s" /> ', text)
    return " ".join(text.split())


@dataclass
class LofiItemResult:
    ok: bool
    video_path: str | None = None
    meta_path: str | None = None
    module: str = ""
    theme: str = ""
    hook_type: str = ""
    scene_count: int = 0
    duration_s: float = 0.0
    manual_review: bool = False
    errors: list[str] = field(default_factory=list)
    script: dict[str, Any] | None = None


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _engine_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _assess_linework_complexity(image_path: Path) -> tuple[bool, list[str]]:
    """Reject near-flat graphics with no inked structure (grief scene-3 failure)."""
    try:
        from PIL import Image as PILImage

        im = PILImage.open(image_path).convert("RGB")
        im.thumbnail((512, 512))
        arr = np.array(im, dtype=np.float32)
        gray = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
        lap = (
            gray[1:-1, 1:-1] * 4
            - gray[:-2, 1:-1]
            - gray[2:, 1:-1]
            - gray[1:-1, :-2]
            - gray[1:-1, 2:]
        )
        lap_var = float(lap.var())
        q = (arr.astype(np.uint8) // 16).reshape(-1, 3)
        uniq = int(np.unique(q, axis=0).shape[0])
        edge = float(
            np.abs(gray[:, 1:] - gray[:, :-1]).mean()
            + np.abs(gray[1:, :] - gray[:-1, :]).mean()
        )
        std = float(gray.std())
        flaws: list[str] = []
        if uniq < 90:
            flaws.append(f"low color structure uniq16={uniq}")
        if lap_var < 80 and edge < 3.0:
            flaws.append(f"flat/no linework lap_var={lap_var:.1f} edge={edge:.2f}")
        if std < 22 and uniq < 140:
            flaws.append(f"near-flat luminance std={std:.1f}")
        if flaws:
            print(
                f"[LOFI image QA] REJECT {image_path.name} "
                f"uniq16={uniq} lap_var={lap_var:.1f} edge={edge:.2f} std={std:.1f} "
                f"| {'; '.join(flaws)}"
            )
        return (len(flaws) == 0), flaws
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("linework QA failed for %s (%s)", image_path.name, exc)
        return True, []


def _qa_scene_image(image_path: Path, visual_prompt: str) -> tuple[bool, list[str]]:
    """
    Run VisualQA lofi_economic profile when available.
    Soft-fail open if Gemini/critic unavailable (log + accept).
    Always run local linework/complexity check (rejects flat graphics).
    """
    del visual_prompt
    struct_ok, struct_flaws = _assess_linework_complexity(image_path)
    try:
        from VisualQA_Agent.channel_rag import get_channel_rules, set_channel_context
        from VisualQA_Agent.visual_critic import evaluate_image

        set_channel_context("lofi_economic")
        rules = get_channel_rules("lofi_economic")
        verdict = evaluate_image(
            image_path,
            channel_name="lofi_economic",
            rules=rules,
            quality_threshold=6.0,
        )
        critic_ok = bool(verdict.passed)
        critic_flaws = list(verdict.flaws or [])
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("VisualQA skipped for %s (%s)", image_path.name, exc)
        critic_ok = True
        critic_flaws = []
        try:
            from PIL import Image as PILImage

            im = PILImage.open(image_path)
            w, h = im.size
            if h < w:
                critic_flaws.append("aspect ratio not vertical")
                critic_ok = False
            if w < 512 or h < 512:
                critic_flaws.append("resolution too low for reels")
                critic_ok = False
        except Exception:  # noqa: BLE001
            pass
    flaws = list(struct_flaws)
    if not critic_ok:
        flaws.extend(critic_flaws)
    return (struct_ok and critic_ok and not flaws), flaws


def _generate_validated_script(
    *,
    module: str,
    theme_row: dict[str, Any],
    scene_count: int,
) -> tuple[dict[str, Any] | None, list[str], bool]:
    """Returns (script|None, errors, needs_manual_review)."""
    feedback: str | None = None
    last_errors: list[str] = []
    for attempt in range(1, lofi_cfg.SCRIPT_MAX_RETRIES + 1):
        script = generate_script(
            module=module,
            theme=str(theme_row.get("theme") or "connection"),
            subtheme=str(theme_row.get("subtheme") or ""),
            scene_count=scene_count,
            feedback=feedback,
            theme_row=theme_row,
        )
        script["subtheme"] = theme_row.get("subtheme")
        result = validate_script(
            script,
            module=module,
            scene_count=scene_count,
            persist_on_pass=True,
        )
        if result.ok and result.script:
            return result.script, [], False
        last_errors = list(result.reasons)
        feedback = result.feedback()
        _LOG.info(
            "Script attempt %d/%d rejected: %s",
            attempt,
            lofi_cfg.SCRIPT_MAX_RETRIES,
            feedback,
        )
    # Last resort: deterministic short-line fallback so a test still renders
    from core_engine.economic_reel_lofi.script_agent import _fallback_script

    fallback = _fallback_script(
        module=module,
        theme=str(theme_row.get("theme") or "connection"),
        scene_count=scene_count,
        hook_type="definition",
        quote=None,
        setting_object_pairs=theme_row.get("setting_object_pairs")
        if isinstance(theme_row.get("setting_object_pairs"), list)
        else None,
    )
    fallback["subtheme"] = theme_row.get("subtheme")
    result = validate_script(
        fallback,
        module=module,
        scene_count=scene_count,
        persist_on_pass=True,
    )
    if result.ok and result.script:
        print("[LOFI script] using fallback after validator retries")
        return result.script, [], False
    return None, last_errors or ["script validation failed"], True


def _resolve_page_dirs(page_id: str, outputs_dir: Path | str | None) -> tuple[Path, Path, Path]:
    """
    Return ``(page_outputs, clips_dir, assets_dir)`` using the existing
    per-page layout: ``outputs/<page>/{clips,assets}/``.
    """
    engine_root = _engine_root()
    if outputs_dir is None:
        page_outputs = engine_root / "outputs" / page_id
    else:
        page_outputs = Path(outputs_dir)
        # Callers may pass .../clips by mistake — normalize to page root.
        if page_outputs.name == "clips":
            page_outputs = page_outputs.parent
        elif page_outputs.name == "economic_reel_lofi":
            page_outputs = page_outputs.parent
    clips_dir = page_outputs / "clips"
    assets_dir = page_outputs / "assets"
    clips_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)
    return page_outputs, clips_dir, assets_dir


def _produce_one(
    *,
    page_id: str,
    module: str,
    duration_s: int,
    clips_dir: Path,
    assets_dir: Path,
    index: int,
) -> LofiItemResult:
    scene_count = lofi_cfg.scene_count_for_duration(duration_s)
    theme_row = rag.select_theme(module)
    stamp = _utc_stamp()
    # Working stills live under assets/; final deliverables go to clips/.
    run_dir = assets_dir / f"lofi_run_{stamp}_{index:02d}"
    run_dir.mkdir(parents=True, exist_ok=True)

    script, errs, manual = _generate_validated_script(
        module=module, theme_row=theme_row, scene_count=scene_count,
    )
    if script is None:
        review_path = clips_dir / f"lofi_manual_review_{stamp}_{index:02d}.json"
        review_path.write_text(
            json.dumps(
                {"errors": errs, "theme": theme_row, "module": module},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return LofiItemResult(
            ok=False,
            module=module,
            theme=str(theme_row.get("theme") or ""),
            scene_count=scene_count,
            duration_s=float(duration_s),
            manual_review=True,
            errors=errs,
            meta_path=str(review_path),
        )

    lines = list(script.get("lines") or [])
    # V2 identity bank assembles prompts from beat fields (live riso JSON untouched).
    if bool(getattr(lofi_cfg, "USE_VISUAL_IDENTITY_V2", False)):
        from core_engine.economic_reel_lofi.visual_identity import apply_v2_prompts_to_lines

        apply_v2_prompts_to_lines(lines, theme_row=theme_row)
        for row in lines:
            if not isinstance(row, dict):
                continue
            print(
                f"[LOFI identity v2] scene={row.get('scene')} "
                f"type={row.get('subject_type')} expr={row.get('subject_expression')!r} "
                f"setting={row.get('setting')!r} object={row.get('key_object')!r} "
                f"tod={row.get('time_of_day')} pal={row.get('palette_key')} "
                f"act={row.get('arc_position')}"
            )
            print(f"[LOFI identity v2] prompt={row.get('visual_prompt')!r}")
    elif bool(getattr(lofi_cfg, "USE_RISO_PROMPT_LIBRARY", True)):
        lib_diff = export_active_library_diff()
        try:
            from core_engine.economic_reel_lofi.riso_prompt_bank import load_riso_library

            live_dump = clips_dir / f"riso_library_live_{stamp}.json"
            live_dump.write_text(
                json.dumps(load_riso_library(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"[LOFI riso] full live library exported -> {live_dump}")
            print(f"[LOFI riso] diff added={lib_diff.get('added')} removed={lib_diff.get('removed')}")
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("library export failed: %s", exc)
        emotions = [str(r.get("emotion") or r.get("mood") or "") for r in lines]
        arcs = [str(r.get("arc_position") or "") for r in lines]
        riso_rows = assign_riso_prompts_for_scenes(
            len(lines) if lines else scene_count,
            theme=str(script.get("theme") or ""),
            seed=None,
            scene_emotions=emotions,
            scene_arcs=arcs,
        )
        for i, row in enumerate(lines):
            if i >= len(riso_rows):
                break
            r = riso_rows[i]
            row["visual_prompt"] = str(r.get("prompt") or "")
            row["riso_id"] = r.get("id")
            row["riso_palette"] = r.get("palette")
            row["riso_mood"] = r.get("mood")
            row["riso_scene_type"] = r.get("scene_type")
            row["riso_emotion_tags"] = r.get("emotion_tags")
            row["riso_arc_position"] = r.get("arc_position")
            row.pop("lighting_mood", None)
        ids = [str(r.get("id")) for r in riso_rows[: len(lines)]]
        print("[LOFI pipeline] riso prompts assigned: " + ", ".join(ids))
        if len(ids) != len(set(ids)):
            raise RuntimeError(f"duplicate riso ids in one video: {ids}")
        print(
            "[LOFI pipeline] riso_013/014 distinct scenes: "
            f"013={'riso_013' in ids} 014={'riso_014' in ids}"
        )

    scene_paths: list[Path] = []
    captions: list[str] = []
    scene_moods: list[dict] = []
    qa_flags: list[str] = []
    voice_paths: list[Path | None] = []
    word_timings_per_scene: list[list[tuple[str, float, float]] | None] = []
    voice_settings_result: dict[str, Any] | None = None

    if bool(getattr(lofi_cfg, "ENABLE_VOICEOVER", True)):
        try:
            from avatar_engine.audio_engine import apply_elevenlabs_voice_settings

            voice_id_pre = str(getattr(lofi_cfg, "LOFI_VOICE_ID", "") or "")
            speed_pre = float(getattr(lofi_cfg, "LOFI_VOICE_SPEED", 0.8))
            voice_settings_result = apply_elevenlabs_voice_settings(
                voice_id_pre,
                speed=speed_pre,
                stability=1.0,
                similarity_boost=1.0,
                style=0.0,
                use_speaker_boost=True,
            )
        except Exception as exc:  # noqa: BLE001
            msg = f"ElevenLabs voice settings/edit failed: {exc}"
            _LOG.warning(msg)
            print(f"[LOFI VO] WARN {msg}")
            if bool(getattr(lofi_cfg, "REQUIRE_VOICEOVER", True)):
                qa_flags.append(msg)

    for row in lines:
        scene_i = int(row.get("scene") or len(scene_paths) + 1)
        out_img = run_dir / f"scene_{scene_i:02d}.png"
        visual = str(row.get("visual_prompt") or "")
        caption = _sanitize_caption_typos(str(row.get("text") or ""))
        mood_meta = {
            "id": str(row.get("riso_id") or f"scene_{scene_i}"),
            "lighting": "from_riso_prompt",
            "palette": row.get("riso_palette"),
            "shadow": lofi_cfg.DUOTONE_SHADOW,
            "highlight": lofi_cfg.DUOTONE_HIGHLIGHT,
        }
        ok_img = False
        last_flaws: list[str] = []
        for attempt in range(1, lofi_cfg.IMAGE_MAX_RETRIES_PER_SCENE + 2):
            prompt_i = visual
            if attempt > 1:
                guard = str(getattr(lofi_cfg, "LOFI_PROMPT_LINEWORK_GUARD", "") or "")
                if guard:
                    prompt_i = f"{visual} {guard}"
            try:
                _, mood_meta = generate_scene_image(
                    prompt_i,
                    out_img,
                    mood=mood_meta,
                    verbatim=True,
                )
            except Exception as exc:  # noqa: BLE001
                last_flaws = [f"image gen failed: {exc}"]
                _LOG.warning("scene %s gen attempt %s failed: %s", scene_i, attempt, exc)
                continue
            passed, flaws = _qa_scene_image(out_img, visual)
            if passed:
                ok_img = True
                break
            last_flaws = flaws
            _LOG.info(
                "VisualQA reject scene %s attempt %s: %s",
                scene_i,
                attempt,
                "; ".join(flaws),
            )
            # Retry same verbatim prompt (do not expand/hallucinate)
        if not ok_img:
            qa_flags.append(f"scene_{scene_i}: {'; '.join(last_flaws) or 'qa failed'}")
            if not out_img.is_file():
                from PIL import Image as PILImage

                PILImage.new("RGB", (768, 1344), (30, 40, 55)).save(out_img)
        row["riso_id"] = mood_meta.get("id")
        scene_paths.append(out_img)
        captions.append(caption)
        scene_moods.append(mood_meta)

        # Per-scene VO + word timestamps (test voice until approved)
        vo_path: Path | None = None
        timings: list[tuple[str, float, float]] | None = None
        if bool(getattr(lofi_cfg, "ENABLE_VOICEOVER", True)) and caption.strip():
            try:
                from avatar_engine.audio_engine import generate_voiceover_with_timestamps

                vo_path = run_dir / f"vo_scene_{scene_i:02d}.mp3"
                voice_id = str(getattr(lofi_cfg, "LOFI_VOICE_ID", "") or "")
                tts_text = _tts_text_with_breaks(caption)
                use_ssml = "<break" in tts_text
                speed = float(getattr(lofi_cfg, "LOFI_VOICE_SPEED", 0.8))
                print(
                    f"[LOFI VO] scene={scene_i} voice={voice_id} "
                    f"speed={speed} ssml={use_ssml} text={caption!r} tts={tts_text!r}"
                )
                vo_path, raw_timings = generate_voiceover_with_timestamps(
                    tts_text,
                    vo_path,
                    voice_id=voice_id or None,
                    force_elevenlabs=True,
                    expressive_mode=False,
                    enable_ssml=use_ssml,
                    speed=speed,
                    voice_settings={
                        "stability": 1.0,
                        "similarity_boost": 1.0,
                        "style": 0.0,
                        "use_speaker_boost": True,
                        "speed": speed,
                    },
                )
                timings = [
                    (str(w), float(s), float(e))
                    for w, s, e in (raw_timings or [])
                    if str(w).strip()
                    and not str(w).startswith("<")
                    and str(w).lower() not in {"break", "time"}
                ]
            except Exception as exc:  # noqa: BLE001
                msg = f"scene_{scene_i} VO failed: {exc}"
                _LOG.warning(msg)
                if bool(getattr(lofi_cfg, "REQUIRE_VOICEOVER", True)):
                    qa_flags.append(msg)
                vo_path = None
                timings = None
        voice_paths.append(vo_path)
        word_timings_per_scene.append(timings)

    theme_slug = "".join(
        c if c.isalnum() else "_"
        for c in str(script.get("theme") or "lofi").lower()
    ).strip("_")[:32] or "lofi"
    out_mp4 = clips_dir / f"lofi_reel_{theme_slug}_{stamp}_v{index:02d}.mp4"
    scene_durations: list[float] = []
    scene_timing_flags: list[dict[str, Any]] = []
    for i, cap in enumerate(captions):
        timings_i = word_timings_per_scene[i] if i < len(word_timings_per_scene) else None
        vp_i = voice_paths[i] if i < len(voice_paths) else None
        dur_i, extended_i, dur_meta = compute_caption_scene_duration_s(
            timings_i,
            Path(vp_i) if vp_i else None,
        )
        scene_durations.append(dur_i)
        scene_timing_flags.append(
            {
                "scene": i + 1,
                "text": cap,
                "extended": extended_i,
                **dur_meta,
            }
        )
        if extended_i:
            print(
                f"[LOFI caption-timing] FLAG scene {i + 1} extended to {dur_i:.2f}s "
                f"(base={lofi_cfg.SCENE_DURATION_S:.1f}s) so the line can fully "
                f"display + hold | text={cap!r}"
            )
    actual_dur = float(sum(scene_durations))
    try:
        assemble_lofi_reel(
            scene_paths,
            captions,
            out_mp4,
            engine_root=_engine_root(),
            page_id=page_id,
            scene_duration_s=lofi_cfg.SCENE_DURATION_S,
            scene_durations=scene_durations,
            moods=scene_moods,
            caption_style=lofi_cfg.DEFAULT_CAPTION_STYLE,
            voice_paths=voice_paths,
            word_timings_per_scene=word_timings_per_scene,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.error("assemble failed: %s", exc, exc_info=True)
        return LofiItemResult(
            ok=False,
            module=module,
            theme=str(script.get("theme") or ""),
            hook_type=str(script.get("hook_type") or ""),
            scene_count=scene_count,
            duration_s=actual_dur,
            manual_review=True,
            errors=[f"assemble failed: {exc}"],
            script=script,
        )

    meta = {
        "post_type": "ECONOMIC_REEL_LOFI",
        "page": page_id,
        "module": module,
        "duration_requested_s": duration_s,
        "duration_actual_s": actual_dur,
        "scene_count": scene_count,
        "scene_duration_s": lofi_cfg.SCENE_DURATION_S,
        "scene_durations": scene_durations,
        "caption_timing": scene_timing_flags,
        "voice_settings_api": voice_settings_result,
        "voice_speed": getattr(lofi_cfg, "LOFI_VOICE_SPEED", None),
        "logo_path": str(
            lofi_cfg.resolve_logo_path(page_id, _engine_root()) or ""
        ),
        "caption_line_height_frac": lofi_cfg.CAPTION_LINE_HEIGHT_FRAC,
        "subtheme": script.get("subtheme"),
        "hook_type": script.get("hook_type"),
        "quote_id": script.get("quote_id"),
        "script": script,
        "riso_ids": [str(r.get("riso_id") or "") for r in lines],
        "visual_identity": "v2"
        if bool(getattr(lofi_cfg, "USE_VISUAL_IDENTITY_V2", False))
        else "riso_library",
        "voice_id": getattr(lofi_cfg, "LOFI_VOICE_ID", None),
        "caption_style": lofi_cfg.DEFAULT_CAPTION_STYLE,
        "grading_applied": bool(getattr(lofi_cfg, "LOFI_APPLY_GRADING", False)),
        "video_path": str(out_mp4),
        "scene_images": [str(p) for p in scene_paths],
        "voice_paths": [str(p) if p else None for p in voice_paths],
        "work_dir": str(run_dir),
        "visual_qa_flags": qa_flags,
        "manual_review": bool(qa_flags),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    meta_path = clips_dir / f"{out_mp4.stem}.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return LofiItemResult(
        ok=True,
        video_path=str(out_mp4),
        meta_path=str(meta_path),
        module=module,
        theme=str(script.get("theme") or ""),
        hook_type=str(script.get("hook_type") or ""),
        scene_count=scene_count,
        duration_s=actual_dur,
        manual_review=bool(qa_flags),
        errors=qa_flags,
        script=script,
    )


def run_economic_reel_lofi(
    *,
    page_id: str,
    quantity: int = 1,
    duration: int | None = None,
    module: str | None = None,
    outputs_dir: Path | str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """
    Registry entrypoint for ECONOMIC_REEL_LOFI.

    Final MP4 + metadata → ``outputs/<page>/clips/``
    Working scene stills → ``outputs/<page>/assets/lofi_run_*/``
    """
    page = (page_id or "").strip().lower()
    mod = lofi_cfg.validate_module_for_page(module or "relationship", page)
    dur = lofi_cfg.validate_duration(duration if duration is not None else lofi_cfg.DEFAULT_DURATION_S)
    qty = max(1, int(quantity))

    rag.ensure_seeded()

    page_outputs, clips_dir, assets_dir = _resolve_page_dirs(page, outputs_dir)

    items: list[dict[str, Any]] = []
    ok_n = 0
    for i in range(1, qty + 1):
        print(
            f"[ECONOMIC_REEL_LOFI] ({i}/{qty}) page={page} module={mod} "
            f"duration={dur}s scenes={lofi_cfg.scene_count_for_duration(dur)} "
            f"→ {clips_dir}"
        )
        result = _produce_one(
            page_id=page,
            module=mod,
            duration_s=dur,
            clips_dir=clips_dir,
            assets_dir=assets_dir,
            index=i,
        )
        item = asdict(result)
        items.append(item)
        if result.ok:
            ok_n += 1
            print(f"  Video : {result.video_path}")
            print(f"  Meta  : {result.meta_path}")
            if result.manual_review:
                print(f"  WARN  : flagged for manual review ({'; '.join(result.errors)})")
        else:
            print(f"  FAIL  : {'; '.join(result.errors)}")
            if result.meta_path:
                print(f"  Review: {result.meta_path}")

    envelope = {
        "post_type": "ECONOMIC_REEL_LOFI",
        "page": page,
        "module": mod,
        "duration_s": dur,
        "quantity": qty,
        "successful": ok_n,
        "items": items,
        "outputs_dir": str(page_outputs),
        "clips_dir": str(clips_dir),
    }
    summary_path = clips_dir / f"lofi_batch_{_utc_stamp()}.json"
    summary_path.write_text(json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")
    envelope["batch_meta"] = str(summary_path)
    return envelope
