# -*- coding: utf-8 -*-
"""
ECONOMIC_REEL_LOFI orchestrator.

ThemeSelector → ScriptGenerator → Validator → ImageGen → VisualQA → Assembler
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_engine.economic_reel_lofi import config as lofi_cfg
from core_engine.economic_reel_lofi import lofi_collections as rag
from core_engine.economic_reel_lofi.assembler import assemble_lofi_reel
from core_engine.economic_reel_lofi.image_gen import generate_scene_image
from core_engine.economic_reel_lofi.script_agent import generate_script
from core_engine.economic_reel_lofi.validator_agent import validate_script

_LOG = logging.getLogger(__name__)


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


def _qa_scene_image(image_path: Path, visual_prompt: str) -> tuple[bool, list[str]]:
    """
    Run VisualQA lofi_economic profile when available.
    Soft-fail open if Gemini/critic unavailable (log + accept).
    """
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
        return bool(verdict.passed), list(verdict.flaws or [])
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("VisualQA skipped for %s (%s)", image_path.name, exc)
        # Lightweight local heuristics when critic unavailable
        flaws: list[str] = []
        try:
            from PIL import Image as PILImage

            im = PILImage.open(image_path)
            w, h = im.size
            if h < w:
                flaws.append("aspect ratio not vertical")
            if w < 512 or h < 512:
                flaws.append("resolution too low for reels")
        except Exception:  # noqa: BLE001
            pass
        return (len(flaws) == 0), flaws


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
    scene_paths: list[Path] = []
    captions: list[str] = []
    qa_flags: list[str] = []

    for row in lines:
        scene_i = int(row.get("scene") or len(scene_paths) + 1)
        out_img = run_dir / f"scene_{scene_i:02d}.png"
        visual = str(row.get("visual_prompt") or "")
        caption = str(row.get("text") or "")
        ok_img = False
        last_flaws: list[str] = []
        for attempt in range(1, lofi_cfg.IMAGE_MAX_RETRIES_PER_SCENE + 2):
            try:
                generate_scene_image(visual, out_img)
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
            # Nudge prompt on retry
            visual = visual + ", stronger ink illustration, avoid photorealism, clean anatomy"
        if not ok_img:
            qa_flags.append(f"scene_{scene_i}: {'; '.join(last_flaws) or 'qa failed'}")
            if not out_img.is_file():
                # last-resort solid placeholder so assembly still completes
                from PIL import Image as PILImage

                PILImage.new("RGB", (768, 1344), (30, 40, 55)).save(out_img)
        scene_paths.append(out_img)
        captions.append(caption)

    theme_slug = "".join(
        c if c.isalnum() else "_"
        for c in str(script.get("theme") or "lofi").lower()
    ).strip("_")[:32] or "lofi"
    out_mp4 = clips_dir / f"lofi_reel_{theme_slug}_{stamp}_v{index:02d}.mp4"
    try:
        assemble_lofi_reel(
            scene_paths,
            captions,
            out_mp4,
            engine_root=_engine_root(),
            page_id=page_id,
            scene_duration_s=lofi_cfg.SCENE_DURATION_S,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.error("assemble failed: %s", exc, exc_info=True)
        return LofiItemResult(
            ok=False,
            module=module,
            theme=str(script.get("theme") or ""),
            hook_type=str(script.get("hook_type") or ""),
            scene_count=scene_count,
            duration_s=scene_count * lofi_cfg.SCENE_DURATION_S,
            manual_review=True,
            errors=[f"assemble failed: {exc}"],
            script=script,
        )

    meta = {
        "post_type": "ECONOMIC_REEL_LOFI",
        "page": page_id,
        "module": module,
        "duration_requested_s": duration_s,
        "duration_actual_s": scene_count * lofi_cfg.SCENE_DURATION_S,
        "scene_count": scene_count,
        "scene_duration_s": lofi_cfg.SCENE_DURATION_S,
        "theme": script.get("theme"),
        "subtheme": script.get("subtheme"),
        "hook_type": script.get("hook_type"),
        "quote_id": script.get("quote_id"),
        "script": script,
        "video_path": str(out_mp4),
        "scene_images": [str(p) for p in scene_paths],
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
        duration_s=scene_count * lofi_cfg.SCENE_DURATION_S,
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
