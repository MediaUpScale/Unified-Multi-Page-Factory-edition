# -*- coding: utf-8 -*-
"""
VisualQA_Agent entrypoint — Master Mei biomechanical Visual QA loop.

Usage (from factory root):
    python -m VisualQA_Agent.main --channel master_mei --beat "A gaunt worker trapped in a CRT dungeon..."
    python -m VisualQA_Agent.main --list-channels
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_FACTORY_ROOT = Path(__file__).resolve().parents[1]
if str(_FACTORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_FACTORY_ROOT))

from rich.console import Console
from rich.json import JSON

from VisualQA_Agent import config
from VisualQA_Agent.agent_loop import run_visual_qa_loop
from VisualQA_Agent.channel_rag import list_channels, seed_default_channels

_console = Console()

DEFAULT_BEAT = (
    "A gaunt worker trapped in a CRT dungeon, copper conduits forced into his throat, "
    "black oil leaking from his jaw, staring into dirty glowing monitors"
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="VisualQA_Agent — generate, critique, and auto-correct FLUX stills."
    )
    p.add_argument(
        "--channel",
        default="master_mei",
        help=(
            "Channel DNA key "
            "(outputs → outputs/{channel}/VisualQA_Agent_Judge/...)"
        ),
    )
    p.add_argument(
        "--beat",
        default=DEFAULT_BEAT,
        help="Base script beat / scene description to visualize",
    )
    p.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help=f"Override MAX_RETRIES (default {config.MAX_RETRIES})",
    )
    p.add_argument(
        "--threshold",
        type=float,
        default=None,
        help=f"Override QUALITY_THRESHOLD (default {config.QUALITY_THRESHOLD})",
    )
    p.add_argument(
        "--model",
        default=None,
        help="Together FLUX model id (default from config / env)",
    )
    p.add_argument(
        "--list-channels",
        action="store_true",
        help="List registered channel DNA keys and exit",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    config.ensure_runtime_dirs()
    seed_default_channels(force=False)

    if args.list_channels:
        channels = list_channels()
        _console.print("[bold]Registered channels:[/bold]")
        for name in channels:
            _console.print(f"  • {name}")
            paths = config.ensure_channel_dirs(name)
            _console.print(f"      attempts → {paths['attempts']}")
            _console.print(f"      approved → {paths['approved']}")
            _console.print(f"      logs     → {paths['logs']}")
        return 0

    if not config.GEMINI_API_KEY:
        _console.print("[red]ERROR[/red] GEMINI_API_KEY missing")
        return 2
    flux_model = args.model or config.FLUX_MODEL
    if "schnell" in (flux_model or "").lower():
        if not config.DEEPINFRA_API_KEY:
            _console.print("[red]ERROR[/red] DEEPINFRA_API_KEY missing")
            return 2
    elif not config.TOGETHER_API_KEY:
        _console.print("[red]ERROR[/red] TOGETHER_API_KEY missing")
        return 2

    config.ensure_channel_dirs(args.channel)

    result = run_visual_qa_loop(
        base_script_beat=args.beat,
        channel_name=args.channel,
        max_retries=args.max_retries,
        quality_threshold=args.threshold,
        flux_model=args.model,
    )

    summary = {
        "approved": result.approved,
        "final_score": result.final_score,
        "final_image_path": result.final_image_path,
        "translated_concept": result.translated_concept,
        "attempts": len(result.attempts),
        "total_cost_usd": round(result.total_cost_usd, 4),
        "channel": result.channel_name,
        "attempts_dir": str(config.attempts_dir(args.channel)),
        "approved_dir": str(config.approved_dir(args.channel)),
        "logs_dir": str(config.logs_dir(args.channel)),
    }
    _console.print("\n[bold]RESULT[/bold]")
    _console.print(JSON.from_data(summary))

    if result.approved:
        _console.print(f"[green]Saved approved still → {result.final_image_path}[/green]")
        return 0

    fail_log = config.failed_shots_path(args.channel)
    _console.print(
        f"[yellow]Not approved after {len(result.attempts)} attempts. "
        f"See {fail_log}[/yellow]"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
