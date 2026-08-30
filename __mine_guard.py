# -*- coding: utf-8 -*-
"""Replay the authoritative edit sequence from the transcript onto baseline files.

For each target file I read the module's CURRENT baseline content and then replay
the assistant's ``StrReplace``/``Write`` tool_use calls in order (all targeting
that file), exactly like the previous chat did. This reproduces the rich
pre-undo FINAL state.

Usage:
    python __mine_guard.py --apply
    python __mine_guard.py --dump   # print replay + save under _reconstructed/
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TRANSCRIPT = Path(
    r"C:\Users\Freedom or Death\.cursor\projects\g-My-Drive-Z-sosFiles-Z-act-NETWORK-MEDIAUPSCALE"
    r"-FACTORY-DYNAMIC-CONTENT-Unified-Multi-Page-Factory\agent-transcripts"
    r"\beddcea8-8b0e-44a3-b91f-36553c86699b\beddcea8-8b0e-44a3-b91f-36553c86699b.jsonl"
)

REPO = Path(
    r"g:\My Drive\Z sosFiles\Z_act\@ NETWORK\@MEDIAUPSCALE_FACTORY_DYNAMIC_CONTENT"
    r"\Unified Multi-Page Factory"
)
AIMAKE = REPO / "channels_config" / "aiwake"

TARGET_FILES = [
    "contracts.py",
    "room.py",
    "orchestrator.py",
    "settings.py",
    "aiwake_config.yaml",
    "personas.py",
]


def _is_tool_use(content) -> bool:
    if not isinstance(content, list):
        return False
    return any(
        isinstance(b, dict) and b.get("type") == "tool_use" for b in content
    )


def collect_edits() -> dict[str, list[dict]]:
    edits: dict[str, list[dict]] = {name: [] for name in TARGET_FILES}
    with TRANSCRIPT.open(encoding="utf-8-sig") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = rec.get("message", {})
            content = msg.get("content")
            if not _is_tool_use(content):
                continue
            for block in content:
                if block.get("type") != "tool_use":
                    continue
                if block.get("name") not in ("StrReplace", "Write"):
                    continue
                inp = block.get("input", {})
                path = str(inp.get("path", ""))
                for target in TARGET_FILES:
                    if path.endswith(target):
                        edits[target].append(
                            {
                                "tool": block["name"],
                                "new_string": inp.get("new_string", ""),
                                "old_string": inp.get("old_string", ""),
                                "replace_all": inp.get("replace_all", False),
                            }
                        )
                        break
    return edits


def apply_edits(path: Path, edit_list: list[dict]) -> tuple[str, list[int], list[int]]:
    return _replay(path, edit_list)


def _dump_edits(edits: dict[str, list[dict]]) -> Path:
    """Write every edit's new_string (index, tool, content) to per-file dumps."""
    dumpdir = REPO / "_reconstructed" / "_dumps"
    dumpdir.mkdir(parents=True, exist_ok=True)
    for fn in TARGET_FILES:
        lst = edits[fn]
        out = dumpdir / (fn + ".edits.txt")
        parts = []
        for i, e in enumerate(lst):
            parts.append(f"\n########## EDIT {i}  tool={e['tool']} replace_all={e['replace_all']} "
                         f"old_len={len(e['old_string'])} new_len={len(e['new_string'])} ##########")
            parts.append("----- OLD (first 300 chars) -----")
            parts.append(e["old_string"][:300])
            parts.append("----- NEW --------------------------")
            parts.append(e["new_string"])
            parts.append("----- END -----")
        out.write_text("\n".join(parts), encoding="utf-8")
    return dumpdir


def main() -> None:
    edits = collect_edits()
    outdir = REPO / "_reconstructed"
    outdir.mkdir(exist_ok=True)
    dumpdir = _dump_edits(edits)
    for fn in TARGET_FILES:
        path = AIMAKE / fn
        try:
            baseline_lines = len(path.read_text(encoding="utf-8").splitlines())
        except FileNotFoundError:
            baseline_lines = -1
        final, applied, skipped = _replay(path, edits[fn])
        out = outdir / fn
        out.write_text(final, encoding="utf-8")
        print(f"FILE {fn}: baseline_lines={baseline_lines}  final_lines={len(final.splitlines())}")
        print(f"  edits={len(edits[fn])}  applied={len(applied)}  already_applied/skipped={len(skipped)}")
        print(f"  applied_indices={applied}")
        print(f"  skipped_indices={skipped}")
    print("\nCompleted reconstruct attempts in", outdir)
    print("Full edit dumps written to", dumpdir)


def _replay(path: Path, edit_list: list[dict]) -> tuple[str, list[str], list[str]]:
    """Apply edits; edits whose old_string is absent are treated as already-applied.

    Returns (final_text, applied_indices, skipped_indices).
    """
    text = path.read_text(encoding="utf-8")
    applied: list[int] = []
    skipped: list[int] = []
    for i, e in enumerate(edit_list):
        if e["tool"] == "Write":
            if not e["old_string"] and not e["new_string"]:
                # Degenerate open-empty write from the editor; no-op.
                continue
            text = e["new_string"]
            applied.append(i)
            continue
        old = e["old_string"]
        if old not in text:
            skipped.append(i)
            continue
        if text.count(old) > 1 and not e["replace_all"]:
            # Ambiguous old_string: the transcript resolved it against an
            # earlier base; fall back to first occurrence.
            pass
        new = e["new_string"]
        text = text.replace(old, new) if e["replace_all"] else text.replace(old, new, 1)
        applied.append(i)
    return text, applied, skipped


if __name__ == "__main__":
    main()
