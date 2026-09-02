# -*- coding: utf-8 -*-
"""Offline provocation-weight curator.

Not a training loop. Reads past transcripts, scores which tagged categories
preceded a judged win (CONCEDE / EMBARRASSED / FUNNY) versus a hard-cap fizzle,
and prints recommended mixed-mode weights. A render never calls this.

    python -m channels_config.aiwake.tune_provocations
    python -m channels_config.aiwake.tune_provocations --write store/provocation_weight_recommendations.json

Low-volume categories are flagged for human review instead of being zeroed.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

try:
    from .provocations import SELECTABLE_FOCUSES, resolve_weights
    from .settings import load_settings, resolve_store_dir
except ImportError:  # pragma: no cover — standalone extraction
    from provocations import SELECTABLE_FOCUSES, resolve_weights  # type: ignore[no-redef]
    from settings import load_settings, resolve_store_dir  # type: ignore[no-redef]

_LOG = logging.getLogger("aiwake.tune")

_WIN_REASONS = frozenset({"CONCEDE", "EMBARRASSED", "FUNNY"})
_FIZZLE_REASONS = frozenset(
    {
        "max_turns_reached",
        "max_turns_reached_with_verdict",
        "max_turns_reached_verdict_failed",
        "max_duration_reached",
        "max_duration_reached_with_verdict",
        "max_duration_reached_verdict_failed",
    }
)
_MIN_SAMPLES_TO_SHIFT = 8
_MIN_WEIGHT = 1
_MAX_WEIGHT = 12


def _transcript_dir(explicit: Path | None = None) -> Path:
    return explicit or (resolve_store_dir() / "transcripts")


def load_runs(transcript_dir: Path) -> list[dict[str, object]]:
    """Load finished debate JSON transcripts (skip .jsonl sidecars)."""
    runs: list[dict[str, object]] = []
    if not transcript_dir.is_dir():
        return runs
    for path in sorted(transcript_dir.glob("*.json")):
        if path.name.endswith(".jsonl"):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _LOG.warning("skipping unreadable transcript %s: %s", path.name, exc)
            continue
        if not isinstance(payload, dict):
            continue
        payload["_path"] = str(path)
        runs.append(payload)
    return runs


def _categories_for_run(run: dict[str, object]) -> list[str]:
    metadata = run.get("metadata") or {}
    tags = metadata.get("provocation_tags") or []
    categories: list[str] = []
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, dict):
                category = str(tag.get("category") or "").strip()
                if category:
                    categories.append(category)
    if not categories:
        focus = str(metadata.get("provocation_focus") or "").strip()
        if focus:
            categories.append(focus)
    return categories


def aggregate(runs: list[dict[str, object]]) -> dict[str, dict[str, int]]:
    """Return per-category win / fizzle / other counts."""
    stats = {
        category: {"wins": 0, "fizzles": 0, "other": 0, "runs": 0}
        for category in SELECTABLE_FOCUSES
    }
    stats["biological"] = {"wins": 0, "fizzles": 0, "other": 0, "runs": 0}
    for run in runs:
        metadata = run.get("metadata") or {}
        reason = str(metadata.get("dialogue_end_reason") or "")
        categories = _categories_for_run(run)
        if not categories:
            continue
        bucket = "wins" if reason in _WIN_REASONS else "fizzles" if reason in _FIZZLE_REASONS else "other"
        # Credit each distinct tagged category that appeared in the run.
        for category in set(categories):
            row = stats.setdefault(category, {"wins": 0, "fizzles": 0, "other": 0, "runs": 0})
            row[bucket] += 1
            row["runs"] += 1
    return stats


def recommend_weights(
    stats: dict[str, dict[str, int]],
    current: dict[str, int],
) -> tuple[dict[str, int], list[str]]:
    """Nudge weights toward categories that land judged wins.

    Never deletes a category. Flags low-sample or all-fizzle rows for review.
    """
    recommended = dict(current)
    flags: list[str] = []
    for category in SELECTABLE_FOCUSES:
        row = stats.get(category) or {"wins": 0, "fizzles": 0, "runs": 0}
        runs = int(row.get("runs") or 0)
        wins = int(row.get("wins") or 0)
        fizzles = int(row.get("fizzles") or 0)
        if runs < _MIN_SAMPLES_TO_SHIFT:
            flags.append(f"{category}: only {runs} tagged run(s) — keep current weight, need more volume")
            continue
        rate = wins / runs if runs else 0.0
        if wins == 0 and fizzles >= _MIN_SAMPLES_TO_SHIFT:
            flags.append(f"{category}: {fizzles} fizzles, 0 wins — review by hand, weight not removed")
            recommended[category] = max(_MIN_WEIGHT, current.get(category, 3) - 1)
            continue
        if rate >= 0.45:
            recommended[category] = min(_MAX_WEIGHT, current.get(category, 3) + 2)
        elif rate >= 0.25:
            recommended[category] = min(_MAX_WEIGHT, current.get(category, 3) + 1)
        elif rate < 0.1:
            recommended[category] = max(_MIN_WEIGHT, current.get(category, 3) - 1)
            flags.append(f"{category}: win rate {rate:.0%} — deprioritized, not removed")
    return recommended, flags


def render_report(
    stats: dict[str, dict[str, int]],
    current: dict[str, int],
    recommended: dict[str, int],
    flags: list[str],
    *,
    run_count: int,
) -> str:
    lines = [
        f"Provocation curation over {run_count} transcript(s)",
        "",
        f"{'category':<14} {'runs':>5} {'wins':>5} {'fizzles':>8} {'current':>8} {'recommend':>10}",
    ]
    for category in [*SELECTABLE_FOCUSES, "biological"]:
        row = stats.get(category) or {"runs": 0, "wins": 0, "fizzles": 0}
        lines.append(
            f"{category:<14} {row.get('runs', 0):>5} {row.get('wins', 0):>5} "
            f"{row.get('fizzles', 0):>8} {current.get(category, 0):>8} "
            f"{recommended.get(category, 0):>10}"
        )
    if flags:
        lines.append("")
        lines.append("Human review:")
        lines.extend(f"  - {flag}" for flag in flags)
    lines.append("")
    lines.append(
        "This script never mutates aiwake_config.yaml. Copy recommended weights "
        "by hand, or pass --write to dump a sidecar JSON."
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aiwake.tune_provocations",
        description="Offline batch: score provocation categories against cornered outcomes.",
    )
    parser.add_argument(
        "--transcript-dir",
        type=Path,
        help="Directory of debate JSON transcripts (defaults to the module store)",
    )
    parser.add_argument(
        "--write",
        type=Path,
        help="Write recommended weights JSON to this path (does not edit YAML)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = load_settings()
    current = resolve_weights(settings.debate.provocation_weights.as_mapping())
    runs = load_runs(_transcript_dir(args.transcript_dir))
    stats = aggregate(runs)
    recommended, flags = recommend_weights(stats, current)
    print(render_report(stats, current, recommended, flags, run_count=len(runs)))
    if args.write:
        payload = {
            "source_runs": len(runs),
            "current": current,
            "recommended": recommended,
            "flags": flags,
            "stats": stats,
        }
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {args.write}")
    if len(runs) < _MIN_SAMPLES_TO_SHIFT:
        print(
            f"\nNot enough tagged renders to justify a weight shift "
            f"(have {len(runs)}, want {_MIN_SAMPLES_TO_SHIFT}+). "
            "Keep shipping B7 tags; run this again later."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
