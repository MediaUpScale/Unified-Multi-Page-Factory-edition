# One-shot sanitizer: strip mug/cup/coffee/glass from LOFI writer-facing JSON.
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BANNED = re.compile(r"\b(mugs?|cups?|coffee|glasses?|glass|sippy)\b", re.I)
HANDHELD = re.compile(r"\b(phones?|keys?|in hand)\b", re.I)

REPLACEMENTS = [
    {"setting": "bedroom, unmade empty bed", "key_object": "unmade empty bed"},
    {"setting": "rain-streaked window at dusk", "key_object": "rain on the window"},
    {"setting": "empty train platform at night", "key_object": "empty platform"},
    {"setting": "hallway with a closed suitcase on the floor", "key_object": "closed suitcase on the floor"},
    {"setting": "empty park path at dusk", "key_object": "distant horizon"},
    {"setting": "coat hook by the door", "key_object": "two coats hanging"},
    {"setting": "window overlooking a quiet street", "key_object": "empty chair"},
    {"setting": "hallway near an open door", "key_object": "open doorway"},
    {"setting": "street corner under a streetlamp", "key_object": "streetlamp"},
    {"setting": "curb at evening", "key_object": "empty passenger seat"},
    {"setting": "kitchen at dawn", "key_object": "kettle just clicked off"},
    {"setting": "sofa in evening light", "key_object": "folded blanket"},
]
DETAILS = [
    "rain streaking an empty window",
    "an unmade bed left empty",
    "a closed suitcase resting on the floor",
    "an empty train platform at night",
    "two coats hanging on one hook",
    "a streetlamp on a quiet corner",
    "an empty passenger seat watched from the curb",
    "a kettle that just clicked off",
    "a folded blanket on the sofa",
    "an open doorway with no one in it",
]

n = {"pairs": 0, "details": 0, "scenes": 0}
idx = {"p": 0, "d": 0}


def _next_pair() -> dict[str, str]:
    p = REPLACEMENTS[idx["p"] % len(REPLACEMENTS)]
    idx["p"] += 1
    return dict(p)


def _next_detail() -> str:
    d = DETAILS[idx["d"] % len(DETAILS)]
    idx["d"] += 1
    return d


def _bad_obj(text: str) -> bool:
    return bool(BANNED.search(text or "") or HANDHELD.search(text or ""))


def _clean_pairs(pairs: list) -> list:
    out = []
    seen = set()
    for row in pairs or []:
        if not isinstance(row, dict):
            continue
        setting = str(row.get("setting") or "")
        obj = str(row.get("key_object") or "")
        if _bad_obj(obj) or _bad_obj(setting):
            row = _next_pair()
            n["pairs"] += 1
            setting, obj = row["setting"], row["key_object"]
        key = (setting, obj)
        if key in seen:
            continue
        seen.add(key)
        out.append({"setting": setting, "key_object": obj})
    return out


def _clean_details(details: list) -> list:
    out = []
    for d in details or []:
        if not isinstance(d, dict):
            continue
        text = str(d.get("detail") or "")
        if _bad_obj(text):
            text = _next_detail()
            n["details"] += 1
        out.append({
            "detail": text,
            "sensory_type": str(d.get("sensory_type") or "object"),
        })
    return out


def _walk(obj):
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict) and "subtheme" in obj[0] and "setting_object_pairs" in obj[0]:
            for row in obj:
                if not isinstance(row, dict):
                    continue
                if isinstance(row.get("setting_object_pairs"), list):
                    row["setting_object_pairs"] = _clean_pairs(row["setting_object_pairs"])
                if isinstance(row.get("concrete_details"), list):
                    row["concrete_details"] = _clean_details(row["concrete_details"])
                scene = str(row.get("sample_scene") or "")
                if scene and _bad_obj(scene):
                    p = _next_pair()
                    row["sample_scene"] = f"{p['setting']}, {p['key_object']}, mid shot"
                    n["scenes"] += 1
            return obj
        if obj and isinstance(obj[0], dict) and "concrete_details" in obj[0] and "theme" not in obj[0]:
            for row in obj:
                if isinstance(row, dict) and isinstance(row.get("concrete_details"), list):
                    row["concrete_details"] = _clean_details(row["concrete_details"])
            return obj
        return [_walk(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _walk(v) for k, v in obj.items()}
    return obj


FILES = [
    ROOT / "core_engine/economic_reel_lofi/data/seed_themes_relationship.json",
    ROOT / "core_engine/economic_reel_lofi/data/seed_themes_parenting.json",
    ROOT / "core_engine/economic_reel_lofi/data/seed_concrete_details_relationship.json",
    ROOT / "core_engine/economic_reel_lofi/data/seed_concrete_details_parenting.json",
    ROOT / "core_engine/economic_reel_lofi/store/lofi_theme_bank_relationship.json",
    ROOT / "core_engine/economic_reel_lofi/store/lofi_theme_bank_parenting.json",
]


def main() -> None:
    for path in FILES:
        if not path.is_file():
            print("skip missing", path.name)
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        cleaned = _walk(data)
        path.write_text(json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("wrote", path.name)
    print("replaced", n)


if __name__ == "__main__":
    main()
