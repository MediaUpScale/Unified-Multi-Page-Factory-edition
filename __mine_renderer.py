# -*- coding: utf-8 -*-
"""Mine an agent transcript JSONL for the LAST renderer.py state per feature."""
import json
import sys
from pathlib import Path

TRANSCRIPT = Path(
    r"C:\Users\Freedom or Death\.cursor\projects\g-My-Drive-Z-sosFiles-Z-act-NETWORK-MEDIAUPSCALE-FACTORY-DYNAMIC-CONTENT-Unified-Multi-Page-Factory\agent-transcripts\beddcea8-8b0e-44a3-b91f-36553c86699b\beddcea8-8b0e-44a3-b91f-36553c86699b.jsonl"
)


def parse_transcript():
    calls = []
    with open(TRANSCRIPT, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            content = obj.get("message", {}).get("content")
            if isinstance(content, str):
                items = [{"type": "text", "text": content}]
            elif isinstance(content, list):
                items = content
            else:
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                typ = item.get("type")
                if typ == "tool_use":
                    name = item.get("name")
                    inp = item.get("input") or {}
                    calls.append((name, inp, obj.get("timestamp", "")))
                elif typ == "tool_result":
                    continue
    return calls


def normpath(p):
    if not p:
        return None
    return str(Path(p)).replace("\\", "/").lower()


def is_renderer(p):
    n = normpath(p)
    if not n:
        return False
    return n.endswith("channels_config/aiwake/media/renderer.py") or n.endswith("renderer.py")


def collect_by_own_target(calls):
    """Collect StrReplace/Write where the TARGET is renderer.py."""
    results = {"StrReplace": [], "Write": []}
    for name, inp, ts in calls:
        if name not in ("StrReplace", "Write"):
            continue
        path = inp.get("path") or inp.get("file_path")
        if not is_renderer(path):
            continue
        rec = {"input": inp, "timestamp": ts}
        results[name].append(rec)
    return results


def main():
    calls = parse_transcript()
    print(f"TOTAL tool_use-ish calls parsed: {len(calls)}")

    grouped = collect_by_own_target(calls)
    print(f"StrReplace targeting renderer.py: {len(grouped['StrReplace'])}")
    print(f"Write targeting renderer.py: {len(grouped['Write'])}")

    if "--summary" in sys.argv:
        for name in ("StrReplace", "Write"):
            print(f"\n===== {name} edits (chronological) =====")
            for i, rec in enumerate(grouped[name]):
                inp = rec["input"]
                old = inp.get("old_string", "")
                new = inp.get("new_string", "")
                old_first = old.strip().splitlines()[0] if old.strip() else "EMPTY"
                new_first = new.strip().splitlines()[0] if new.strip() else (inp.get("file_path") or "")
                flags = list(inp.keys())
                print(
                    f"[{i}] ts={rec['timestamp']} old_first={old_first!r} "
                    f"new_first={new_first!r} keys={flags}"
                )
        return

    # --- Anchor-based: print LAST new_string per feature anchor ---
    anchors = {
        "def dock_progress": "def dock_progress",
        "_MODEL_ACCENTS": "_MODEL_ACCENTS",
        "def model_accent": "def model_accent",
        "def pick_cta": "def pick_cta",
        "def cta_revealed_chars": "def cta_revealed_chars",
        "def send_click_alpha": "def send_click_alpha",
        "_CTA_LINES": "_CTA_LINES",
        "_TITLE_COPY": "_TITLE_COPY",
        "def _draw_cta_frame": "def _draw_cta_frame",
        "def _prepare_cta_voice": "def _prepare_cta_voice",
        "_CTA_FADE_S": "_CTA_FADE_S",
        "def _fade_frame_to_black": "def _fade_frame_to_black",
        "def _top_mask_px": "def _top_mask_px",
        "__all__": "__all__",
        "Aiwake": "Aiwake",
    }
    searchable = []
    for name in ("StrReplace", "Write"):
        for rec in grouped[name]:
            new = rec["input"].get("new_string", "") or ""
            searchable.append((rec["input"], new, rec["timestamp"]))

    for label, needle in anchors.items():
        hits = [rec for rec in searchable if needle in rec[1]]
        if not hits:
            print(f"\n#### {label}: NO HITS")
            continue
        last_new = hits[-1][1]
        last_ts = hits[-1][2]
        print(f"\n########## {label} (last hit ts={last_ts}) ##########")
        print(last_new)
        print("########## END ##########")


if __name__ == "__main__":
    main()
