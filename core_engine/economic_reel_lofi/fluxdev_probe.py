# -*- coding: utf-8 -*-
"""
Schnell vs FLUX.1-dev comparison probe.

Same beats, full QA stack (critic + uniq16 + INTRUDER + style + hand-anatomy).
Does not write into the live Schnell episode path. No LoRA.
"""
from __future__ import annotations

import argparse
import copy
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_engine.economic_reel_lofi import config as lofi_cfg
from core_engine.economic_reel_lofi.image_gen import (
    generate_scene_image,
    generate_scene_image_dev,
)
from core_engine.economic_reel_lofi.pipeline import generate_and_qa_scene
from core_engine.economic_reel_lofi.visual_identity import (
    assemble_v2_prompt,
    assemble_v2_prompt_dev,
)

# Frozen subset of the distance / silence_that_speaks validation mix.
# Used when --episode is omitted or cannot be read.
_FALLBACK_BEATS: tuple[dict[str, Any], ...] = (
    {
        "scene": 1,
        "text": "Distance starts as a quiet room.",
        "beat_text": "Distance starts as a quiet room.",
        "subject_type": "woman",
        "subject_expression": "distant, still",
        "setting": "bedroom, unmade empty bed",
        "key_object": "unmade empty bed",
        "time_of_day": "dusk",
        "arc_position": "act1",
        "visual_anchor_hint": "woman alone beside an unmade empty bed at dusk",
    },
    {
        "scene": 2,
        "text": "The platform keeps the last goodbye.",
        "beat_text": "The platform keeps the last goodbye.",
        "subject_type": "couple",
        "subject_expression": "apart, waiting",
        "setting": "empty train platform at night",
        "key_object": "empty platform",
        "time_of_day": "night",
        "arc_position": "act1",
        "visual_anchor_hint": "two small figures far apart on an empty night platform",
    },
    {
        "scene": 3,
        "text": "A suitcase that never left the floor.",
        "beat_text": "A suitcase that never left the floor.",
        "subject_type": "object_focus",
        "subject_expression": "abandoned",
        "setting": "hallway with a closed suitcase on the floor",
        "key_object": "closed suitcase on the floor",
        "time_of_day": "evening",
        "arc_position": "act2",
        "visual_anchor_hint": "closed suitcase alone on a bare floor",
    },
    {
        "scene": 4,
        "text": "Rain says what neither of you will.",
        "beat_text": "Rain says what neither of you will.",
        "subject_type": "silhouette",
        "subject_expression": "watching",
        "setting": "rain-streaked window at dusk",
        "key_object": "rain on the window",
        "time_of_day": "dusk",
        "arc_position": "act2",
        "visual_anchor_hint": "lone silhouette facing rain on a dusk window",
    },
    {
        "scene": 5,
        "text": "The horizon is still wider than the fight.",
        "beat_text": "The horizon is still wider than the fight.",
        "subject_type": "man",
        "subject_expression": "resigned",
        "setting": "empty park path at dusk",
        "key_object": "distant horizon",
        "time_of_day": "dusk",
        "arc_position": "act3",
        "visual_anchor_hint": "man looking out at a distant dusk horizon",
    },
)

_FLAW_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hand", ("hand", "finger", "grip", "wrist", "anatomy")),
    ("intruder", ("intruder", "mug", "cup", "laptop", "extra object", "clutter")),
    ("text", ("text", "letter", "watermark", "logo", "typography", "garbled")),
    ("style", ("photoreal", "photograph", "uniq16", "linework", "flat graphic")),
    ("other", ()),
)


def _engine_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_beats(episode_path: str | None, n: int) -> list[dict[str, Any]]:
    if episode_path:
        raw = json.loads(Path(episode_path).read_text(encoding="utf-8"))
        script = raw.get("script") if isinstance(raw.get("script"), dict) else raw
        lines = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
        if not lines:
            raise ValueError(f"no lines in episode: {episode_path}")
        picked = _pick_mixed(lines, n)
        return [copy.deepcopy(r) for r in picked]
    return [copy.deepcopy(r) for r in _FALLBACK_BEATS[:n]]


def _pick_mixed(lines: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    want = ["woman", "couple", "object_focus", "silhouette", "man"]
    picked: list[dict[str, Any]] = []
    used: set[int] = set()
    for st in want:
        if len(picked) >= n:
            break
        for i, row in enumerate(lines):
            if i in used:
                continue
            if str(row.get("subject_type") or "").lower() == st:
                picked.append(row)
                used.add(i)
                break
    for i, row in enumerate(lines):
        if len(picked) >= n:
            break
        if i not in used:
            picked.append(row)
            used.add(i)
    return picked[:n]


def _bucket_flaws(flaws: list[str]) -> dict[str, int]:
    counts = {name: 0 for name, _ in _FLAW_BUCKETS}
    for raw in flaws:
        blob = str(raw).lower()
        hit = "other"
        for name, keys in _FLAW_BUCKETS:
            if name == "other":
                continue
            if any(k in blob for k in keys):
                hit = name
                break
        counts[hit] += 1
    return counts


def _run_path(
    *,
    label: str,
    beats: list[dict[str, Any]],
    out_dir: Path,
    generate_fn: Any,
    assemble_fn: Any,
) -> list[dict[str, Any]]:
    rows_out: list[dict[str, Any]] = []
    for src in beats:
        row = copy.deepcopy(src)
        scene_i = int(row.get("scene") or 0)
        if assemble_fn is assemble_v2_prompt_dev:
            row["visual_prompt"] = assemble_v2_prompt_dev(row)
        else:
            row["visual_prompt"] = assemble_v2_prompt(row)
        out_img = out_dir / f"{label}_scene_{scene_i:02d}.png"
        t0 = time.perf_counter()
        ok, gate, n_img, n_crit = generate_and_qa_scene(
            row,
            out_img,
            generate_fn=generate_fn,
            assemble_fn=assemble_fn,
        )
        elapsed = time.perf_counter() - t0
        flaws = [str(x) for x in (gate.get("qa_flaws") or [])]
        rec = {
            "path": label,
            "scene": scene_i,
            "subject_type": row.get("subject_type"),
            "key_object": row.get("key_object"),
            "text": row.get("text"),
            "visual_anchor_hint": row.get("visual_anchor_hint"),
            "visual_identity_profile": row.get("visual_identity_profile"),
            "dev_scene_builder": row.get("dev_scene_builder"),
            "assembled_prompt": str(row.get("visual_prompt") or ""),
            "prompt_len": len(str(row.get("visual_prompt") or "")),
            "qa_passed": bool(ok),
            "attempts_used": n_img,
            "critic_calls": n_crit,
            "elapsed_s": round(elapsed, 2),
            "qa_flaws": flaws,
            "flaw_buckets": _bucket_flaws(flaws),
            "n_intruders": gate.get("n_intruders"),
            "style_gate": gate.get("style_gate"),
            "image": str(out_img) if out_img.is_file() else None,
        }
        print(
            f"[FLUXDEV probe] {label} scene={scene_i} "
            f"ok={int(ok)} attempts={n_img} "
            f"prompt_len={rec['prompt_len']} t={elapsed:.1f}s "
            f"flaws={flaws or '-'}"
        )
        rows_out.append(rec)
    return rows_out


def _summarize(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    n = len(rows) or 1
    shipped = sum(1 for r in rows if r.get("qa_passed"))
    buckets: dict[str, int] = {name: 0 for name, _ in _FLAW_BUCKETS}
    for r in rows:
        for k, v in (r.get("flaw_buckets") or {}).items():
            buckets[k] = buckets.get(k, 0) + int(v)
    return {
        "path": label,
        "scenes": len(rows),
        "shipped": shipped,
        "ship_rate": round(shipped / n, 3),
        "mean_prompt_len": round(sum(int(r.get("prompt_len") or 0) for r in rows) / n),
        "mean_elapsed_s": round(sum(float(r.get("elapsed_s") or 0) for r in rows) / n, 2),
        "total_image_calls": sum(int(r.get("attempts_used") or 0) for r in rows),
        "flaw_buckets": buckets,
    }


def run_fluxdev_probe(
    *,
    page_id: str = "wonder_feed",
    episode: str | None = None,
    n_beats: int = 5,
    skip_schnell: bool = False,
) -> Path:
    from avatar_engine.providers.together_image import (
        estimate_deepinfra_schnell_cost_usd,
        estimate_together_image_cost,
    )

    n_beats = max(3, min(int(n_beats), 5))
    beats = _load_beats(episode, n_beats)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = (
        _engine_root()
        / "outputs"
        / page_id
        / "clips"
        / "economic_reels_tests"
        / f"fluxdev_probe_{stamp}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[FLUXDEV probe] dir={out_dir} beats={len(beats)}")

    schnell_rows: list[dict[str, Any]] = []
    if not skip_schnell:
        schnell_rows = _run_path(
            label="schnell",
            beats=beats,
            out_dir=out_dir,
            generate_fn=generate_scene_image,
            assemble_fn=assemble_v2_prompt,
        )
    dev_rows = _run_path(
        label="dev",
        beats=beats,
        out_dir=out_dir,
        generate_fn=generate_scene_image_dev,
        assemble_fn=assemble_v2_prompt_dev,
    )
    schnell_sum = _summarize(schnell_rows, "schnell")
    dev_sum = _summarize(dev_rows, "dev")
    from core_engine.economic_reel_lofi.image_gen import LOFI_IMAGE_STEPS

    schnell_cost = estimate_deepinfra_schnell_cost_usd(
        lofi_cfg.LOFI_IMAGE_WIDTH, lofi_cfg.LOFI_IMAGE_HEIGHT, LOFI_IMAGE_STEPS
    )
    dev_cost = estimate_together_image_cost(lofi_cfg.LOFI_DEV_IMAGE_MODEL)
    report = {
        "stamp": stamp,
        "n_beats": len(beats),
        "episode": episode,
        "guidance_scale": lofi_cfg.LOFI_DEV_GUIDANCE_SCALE,
        "dev_steps": lofi_cfg.LOFI_DEV_IMAGE_STEPS,
        "allow_lora": False,
        "profile": lofi_cfg.DEFAULT_VISUAL_IDENTITY_PROFILE,
        "schnell": schnell_sum,
        "dev": dev_sum,
        "est_usd_per_image": {"schnell": schnell_cost, "dev": dev_cost},
        "est_usd_total": {
            "schnell": round(schnell_cost * schnell_sum["total_image_calls"], 5),
            "dev": round(dev_cost * dev_sum["total_image_calls"], 5),
        },
        "rows": schnell_rows + dev_rows,
    }
    out_json = out_dir / "fluxdev_probe_report.json"
    out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("[FLUXDEV probe] SUMMARY")
    print(json.dumps({k: report[k] for k in ("schnell", "dev", "est_usd_total")}, indent=2))
    print(f"[FLUXDEV probe] report -> {out_json}")
    return out_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Schnell vs FLUX.1-dev stills probe")
    parser.add_argument("--page", default="wonder_feed")
    parser.add_argument("--episode", default="", help="Optional locked episode JSON")
    parser.add_argument("--beats", type=int, default=5)
    parser.add_argument(
        "--dev-only",
        action="store_true",
        help="Skip Schnell (reuse a prior probe stills/report if comparing later)",
    )
    args = parser.parse_args()
    run_fluxdev_probe(
        page_id=str(args.page),
        episode=str(args.episode).strip() or None,
        n_beats=int(args.beats),
        skip_schnell=bool(args.dev_only),
    )


if __name__ == "__main__":
    main()
