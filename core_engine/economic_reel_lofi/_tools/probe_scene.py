# -*- coding: utf-8 -*-
"""Replay pipeline.py's per-scene QA retry loop on one locked-script beat.

Usage (from factory root):
  python -m core_engine.economic_reel_lofi._tools.probe_scene \\
    --script outputs/wonder_feed/clips/lofi_stills_attachment_20260821_v02.json \\
    --scene 3 --trials 3
"""
from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True, encoding="utf-8-sig")


def _load_script(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("script"), dict):
        return dict(raw["script"])
    if isinstance(raw, dict):
        return dict(raw)
    raise ValueError(f"not a JSON object: {path}")


def _beat_row(script: dict[str, Any], scene: int) -> dict[str, Any]:
    for line in script.get("lines") or []:
        if isinstance(line, dict) and int(line.get("scene") or 0) == scene:
            return deepcopy(line)
    raise SystemExit(f"scene {scene} not found in locked script")


def _run_trial(
    trial: int,
    base_row: dict[str, Any],
    out_dir: Path,
    *,
    focus_step_override: int | None,
) -> dict[str, Any]:
    from core_engine.economic_reel_lofi import config as lofi_cfg
    from core_engine.economic_reel_lofi.image_gen import generate_scene_image
    from core_engine.economic_reel_lofi.pipeline import (
        _qa_scene_image,
        assess_default_object_intrusion,
        assess_photoreal_style,
    )
    from core_engine.economic_reel_lofi.visual_identity import assemble_v2_prompt

    row = deepcopy(base_row)
    visual = assemble_v2_prompt(row, focus_step=int(focus_step_override or 0))
    row["visual_prompt"] = visual
    subject_type = str(row.get("subject_type") or "").strip()
    key_object = str(row.get("key_object") or "").strip()
    st_l = subject_type.lower().replace(" ", "_")
    mood_meta = {
        "id": str(row.get("riso_id") or f"probe_t{trial}"),
        "lighting": "from_riso_prompt",
        "palette": row.get("riso_palette") or row.get("palette_key"),
        "shadow": lofi_cfg.DUOTONE_SHADOW,
        "highlight": lofi_cfg.DUOTONE_HIGHLIGHT,
    }
    last_fix = ""
    last_intruder = False
    focus_step = int(focus_step_override or 0)
    attempts: list[dict[str, Any]] = []
    shipped = False
    n_img = 0
    n_critic = 0
    for attempt in range(1, lofi_cfg.IMAGE_MAX_RETRIES_PER_SCENE + 2):
        prompt_i = visual
        if st_l in {"object_focus", "silhouette"}:
            if last_intruder and focus_step_override is None:
                focus_step = min(focus_step + 1, 2)
            pinned = int(focus_step_override if focus_step_override is not None else focus_step)
            prompt_i = assemble_v2_prompt(row, focus_step=pinned)
            row["visual_prompt"] = prompt_i
            if attempt > 1 and last_fix:
                guard = str(getattr(lofi_cfg, "LOFI_PROMPT_LINEWORK_GUARD", "") or "")
                extras = [p for p in (guard, last_fix) if p]
                if extras:
                    prompt_i = f"{prompt_i} {' '.join(extras)}"
        elif attempt > 1:
            guard = str(getattr(lofi_cfg, "LOFI_PROMPT_LINEWORK_GUARD", "") or "")
            extras = [p for p in (guard, last_fix) if p]
            if extras:
                prompt_i = f"{visual} {' '.join(extras)}"
        out_img = out_dir / f"trial{trial:02d}_a{attempt}.png"
        try:
            generate_scene_image(prompt_i, out_img, mood=mood_meta, verbatim=True)
            n_img += 1
        except Exception as exc:  # noqa: BLE001
            attempts.append(
                {"attempt": attempt, "passed": False, "flaws": [f"image gen failed: {exc}"]}
            )
            print(f"trial={trial} attempt={attempt} HOLD gen_fail={exc}")
            continue
        passed, flaws, last_fix = _qa_scene_image(
            out_img, visual, subject_type=subject_type, key_object=key_object
        )
        n_critic += 1
        _ok, gate_flaws, gate_meta = assess_default_object_intrusion(
            out_img, subject_type=subject_type, key_object=key_object
        )
        _sok, style_flaws, style_meta = assess_photoreal_style(
            out_img, subject_type=subject_type
        )
        last_intruder = bool(gate_flaws)
        if gate_flaws:
            flaws = list(flaws) + gate_flaws
            passed = False
            extra = str(gate_meta.get("fix_instructions") or "").strip()
            if extra:
                last_fix = f"{last_fix} {extra}".strip() if last_fix else extra
        if style_flaws:
            flaws = list(flaws) + style_flaws
            passed = False
            extra = str(style_meta.get("fix_instructions") or "").strip()
            if extra:
                last_fix = f"{last_fix} {extra}".strip() if last_fix else extra
        rec = {
            "attempt": attempt,
            "passed": bool(passed),
            "flaws": list(flaws),
            "focus_step": focus_step,
            "image": out_img.name,
        }
        attempts.append(rec)
        print(
            f"trial={trial} attempt={attempt} "
            f"{'SHIP' if passed else 'HOLD'} flaws={flaws or '(none)'}"
        )
        if passed:
            shipped = True
            break
    return {
        "trial": trial,
        "shipped": shipped,
        "attempts": attempts,
        "n_image_calls": n_img,
        "n_critic_calls": n_critic,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--script", required=True, type=Path)
    ap.add_argument("--scene", required=True, type=int)
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--focus-step", type=int, default=None)
    args = ap.parse_args()
    script_path = args.script if args.script.is_absolute() else ROOT / args.script
    script = _load_script(script_path)
    row = _beat_row(script, args.scene)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = (
        ROOT
        / "outputs/wonder_feed/clips/economic_reels_tests"
        / f"scene{args.scene:02d}_{stamp}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    print(
        f"beat scene={args.scene} type={row.get('subject_type')} "
        f"object={row.get('key_object')!r} setting={row.get('setting')!r}"
    )
    trials = []
    n_img = n_critic = 0
    for t in range(1, max(1, args.trials) + 1):
        rec = _run_trial(
            t, row, out_dir, focus_step_override=args.focus_step
        )
        trials.append(rec)
        n_img += rec["n_image_calls"]
        n_critic += rec["n_critic_calls"]
    shipped = sum(1 for r in trials if r["shipped"])
    report = {
        "script": str(script_path),
        "scene": args.scene,
        "beat": {
            "subject_type": row.get("subject_type"),
            "key_object": row.get("key_object"),
            "setting": row.get("setting"),
            "text": row.get("text"),
        },
        "n_trials": len(trials),
        "shipped": shipped,
        "ship_rate": shipped / max(len(trials), 1),
        "n_image_calls": n_img,
        "n_critic_calls": n_critic,
        "est_cost_usd": round(0.00197 * n_img + 0.00015 * n_critic, 5),
        "trials": trials,
    }
    (out_dir / "probe_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"SUMMARY ship={shipped}/{len(trials)} "
        f"images={n_img} critic={n_critic} est_cost=${report['est_cost_usd']:.4f} "
        f"dir={out_dir}"
    )


if __name__ == "__main__":
    main()
