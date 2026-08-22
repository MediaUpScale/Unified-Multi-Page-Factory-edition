# -*- coding: utf-8 -*-
"""Replace one scene still in a finished ECONOMIC_REEL_LOFI episode and reassemble."""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_engine.economic_reel_lofi import config as lofi_cfg
from core_engine.economic_reel_lofi.assembler import assemble_lofi_reel
from core_engine.economic_reel_lofi.pipeline import (
    _engine_root,
    _sanitize_caption_typos,
    apply_ad_hoc_guidance,
    generate_and_qa_scene,
)

_LOG = logging.getLogger(__name__)


def load_episode_json(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"episode JSON is not an object: {p}")
    return raw


def save_episode_json(episode: dict[str, Any], path: Path | str) -> Path:
    p = Path(path)
    p.write_text(json.dumps(episode, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def assemble_video_from_episode(
    episode: dict[str, Any],
    *,
    output_mp4: Path | None = None,
) -> Path:
    """
    Re-render the MP4 from existing stills + VO. Does not regenerate
    script, voice, captions, or unused scenes.
    """
    images = [Path(p) for p in (episode.get("scene_images") or [])]
    if not images or any(not p.is_file() for p in images):
        missing = [str(p) for p in images if not p.is_file()]
        raise FileNotFoundError(f"missing scene stills: {missing}")
    script = episode.get("script") if isinstance(episode.get("script"), dict) else episode
    lines = list((script or {}).get("lines") or [])
    captions = [
        _sanitize_caption_typos(str(ln.get("text") or ""))
        for ln in lines
    ]
    if len(captions) != len(images):
        raise ValueError(
            f"caption/image count mismatch: {len(captions)} captions, {len(images)} stills"
        )
    voice_raw = list(episode.get("voice_paths") or [])
    voice_paths: list[Path | None] = []
    for i in range(len(images)):
        raw = voice_raw[i] if i < len(voice_raw) else None
        voice_paths.append(Path(raw) if raw else None)

    moods: list[dict[str, Any]] = []
    for i, ln in enumerate(lines):
        moods.append(
            {
                "id": str(ln.get("riso_id") or f"scene_{i + 1}"),
                "lighting": "from_riso_prompt",
                "palette": ln.get("riso_palette") or ln.get("palette_key"),
                "shadow": lofi_cfg.DUOTONE_SHADOW,
                "highlight": lofi_cfg.DUOTONE_HIGHLIGHT,
            }
        )

    scene_durations = episode.get("scene_durations")
    durs = [float(x) for x in scene_durations] if isinstance(scene_durations, list) else None

    page_id = str(episode.get("page") or "wonder_feed")
    src_video = Path(str(episode.get("video_path") or ""))
    if output_mp4 is None:
        if src_video.suffix.lower() == ".mp4" and src_video.parent.is_dir():
            output_mp4 = src_video.with_name(f"{src_video.stem}_regen{src_video.suffix}")
        else:
            clips = Path(episode.get("work_dir") or ".") 
            output_mp4 = clips / "lofi_reel_regen.mp4"

    return assemble_lofi_reel(
        images,
        captions,
        Path(output_mp4),
        engine_root=_engine_root(),
        page_id=page_id,
        scene_duration_s=float(episode.get("scene_duration_s") or lofi_cfg.SCENE_DURATION_S),
        scene_durations=durs,
        moods=moods,
        caption_style=str(episode.get("caption_style") or lofi_cfg.DEFAULT_CAPTION_STYLE),
        voice_paths=voice_paths,
        word_timings_per_scene=None,
    )


def regenerate_scene(
    episode_json_path: Path | str,
    scene_number: int,
    reason: str | None = None,
    max_attempts: int = 2,
) -> dict[str, Any]:
    """
    Regen one scene still, run the same QA stack, replace the image in place,
    and reassemble the MP4. Script, VO, captions, and other scenes stay as-is.
    ``reason`` is one-off prompt guidance only — not written to the RAG bank.
    """
    episode_path = Path(episode_json_path)
    episode = load_episode_json(episode_path)
    script = episode.get("script")
    if not isinstance(script, dict):
        raise ValueError(f"episode has no script object: {episode_path}")
    lines = list(script.get("lines") or [])
    idx = int(scene_number) - 1
    if idx < 0 or idx >= len(lines):
        raise IndexError(f"scene {scene_number} out of range (n={len(lines)})")

    images = list(episode.get("scene_images") or [])
    if idx >= len(images):
        raise IndexError(f"scene_images missing index {idx}")
    target_path = Path(str(images[idx]))
    if not target_path.is_file():
        raise FileNotFoundError(f"current still missing: {target_path}")

    gates = list(episode.get("object_gate_by_scene") or [])
    gate_entry = dict(gates[idx]) if idx < len(gates) and isinstance(gates[idx], dict) else {}
    row = dict(lines[idx])
    row["scene"] = int(row.get("scene") or scene_number)

    work_dir = Path(str(episode.get("work_dir") or target_path.parent))
    work_dir.mkdir(parents=True, exist_ok=True)
    tmp_img = work_dir / f"scene_{int(scene_number):02d}_regen_tmp.png"

    extra = apply_ad_hoc_guidance("", str(reason or ""))
    print(
        f"[LOFI regen] scene={scene_number} attempts={max_attempts} "
        f"reason={reason!r} tmp={tmp_img.name}"
    )
    gen_kw: dict[str, Any] = {
        "attempt_budget": max_attempts,
        "extra_prompt": extra,
    }
    if lofi_cfg.uses_flux_dev():
        from core_engine.economic_reel_lofi.image_gen import generate_scene_image_dev
        from core_engine.economic_reel_lofi.visual_identity import assemble_v2_prompt_dev

        gen_kw["generate_fn"] = generate_scene_image_dev
        gen_kw["assemble_fn"] = assemble_v2_prompt_dev
    ok_img, new_gate, n_img, n_crit = generate_and_qa_scene(
        row,
        tmp_img,
        **gen_kw,
    )
    new_gate = dict(new_gate)
    new_gate.pop("mood", None)
    new_gate["attempts_used"] = int(new_gate.get("attempts_used") or n_img)
    new_gate["manual_accept"] = False

    override = {
        "scene": int(scene_number),
        "reason": reason,
        "previous_flaws": list(gate_entry.get("qa_flaws") or []),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "image_calls": n_img,
        "critic_calls": n_crit,
    }
    episode.setdefault("manual_overrides", [])
    episode["image_calls"] = int(episode.get("image_calls") or 0) + n_img
    episode["critic_calls"] = int(episode.get("critic_calls") or 0) + n_crit

    if not ok_img or not new_gate.get("qa_passed"):
        episode["manual_review"] = True
        new_gate["qa_passed"] = False
        new_gate["manual_accept"] = False
        override["status"] = "manual_review_needed"
        override["qa_flaws"] = list(new_gate.get("qa_flaws") or [])
        episode["manual_overrides"].append(override)
        save_episode_json(episode, episode_path)
        if tmp_img.is_file():
            tmp_img.unlink()
        print(
            f"[LOFI regen] HOLD scene={scene_number} "
            f"flaws={new_gate.get('qa_flaws')}"
        )
        return {
            "status": "manual_review_needed",
            "scene": int(scene_number),
            "qa_flaws": list(new_gate.get("qa_flaws") or []),
        }

    shutil.copy2(tmp_img, target_path)
    work_copy = work_dir / f"scene_{int(scene_number):02d}.png"
    if work_copy.resolve() != target_path.resolve():
        shutil.copy2(tmp_img, work_copy)
    tmp_img.unlink(missing_ok=True)

    while len(gates) <= idx:
        gates.append({})
    gates[idx] = new_gate
    episode["object_gate_by_scene"] = gates
    lines[idx]["default_object_gate"] = new_gate
    script["lines"] = lines
    episode["script"] = script
    override["status"] = "ok"
    episode["manual_overrides"].append(override)
    save_episode_json(episode, episode_path)

    new_video = assemble_video_from_episode(episode)
    episode["video_path"] = str(new_video)
    save_episode_json(episode, episode_path)
    print(f"[LOFI regen] ok scene={scene_number} video={new_video}")
    return {
        "status": "ok",
        "scene": int(scene_number),
        "video_path": str(new_video),
        "image_path": str(target_path),
    }


def main() -> None:
    import argparse
    from dotenv import load_dotenv

    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env", override=True, encoding="utf-8-sig")

    parser = argparse.ArgumentParser(description="Replace one LOFI scene still and reassemble.")
    parser.add_argument("--episode", required=True, help="Episode sidecar JSON")
    parser.add_argument("--scene", type=int, required=True, help="1-indexed scene number")
    parser.add_argument("--reason", default="", help="One-off prompt guidance")
    parser.add_argument("--max-attempts", type=int, default=2)
    args = parser.parse_args()
    result = regenerate_scene(
        args.episode,
        args.scene,
        reason=args.reason or None,
        max_attempts=args.max_attempts,
    )
    print(json.dumps(result, indent=2))
    if result.get("status") != "ok":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
