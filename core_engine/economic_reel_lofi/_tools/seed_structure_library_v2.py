# -*- coding: utf-8 -*-
"""One-time Claude seed: 15–25 continuous-prose rhetorical patterns."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_engine.economic_reel_lofi import config as lofi_cfg
from core_engine.economic_reel_lofi.claude_polish import (
    _usd,
    _usage_rec,
    append_cost_log,
    _resolve_polish_model,
)

DATA = lofi_cfg.DATA_DIR
V1_PATH = DATA / "structure_library_v1.json"
V2_PATH = DATA / "structure_library_v2.json"
BANK_PATH = lofi_cfg.STORE_DIR / "lofi_theme_bank_relationship.json"
CORPUS_PATH = DATA / "reference_corpus.json"

SEED_SYSTEM = """You distill reusable RHETORICAL SHAPES, not finished scripts.

Output STRICT JSON:
{"version":2,"notes":"...","patterns":[...]}

Each pattern:
- name: snake_case
- label: one sentence
- tool_strength: high | medium | low
- hook_device: boolean (true only if strongest as an opening rhythm)
- aphorism_ok: boolean
- shape: 2-4 sentences describing how it opens, develops/complicates, turns, resolves
- in_house_example: ONE CONTINUOUS PARAGRAPH, 100-180 words, original house language

Do not write isolated one-line captions or a numbered list of short beats.
Write each example as continuous prose a person would actually say out loud
in one breath after another. Real sentences. Real connectives that do logical work.

Do not produce ready-to-ship 9-beat scripts for any theme.
Do not copy third-party phrasing. Invent every example sentence.
Do not name real authors, philosophers, or accounts.
Do not reuse any 4-or-more-word sequence from the reference corpus.
Return the requested pattern count. Prefer keeping/refining any supplied
starting shapes, then add new shapes so the theme list is covered. No two
examples may share the same concrete nouns.
"""


def _themes() -> list[str]:
    rows = json.loads(BANK_PATH.read_text(encoding="utf-8"))
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        theme = str(row.get("theme") or "").strip()
        sub = str(row.get("subtheme") or "").strip()
        if theme:
            out.append(f"{theme} / {sub}" if sub else theme)
    return out


def _starting_shapes() -> list[dict[str, str]]:
    src = V1_PATH if V1_PATH.is_file() else DATA / "structure_library.json"
    raw = json.loads(src.read_text(encoding="utf-8"))
    out = []
    for p in raw.get("patterns") or []:
        if not isinstance(p, dict):
            continue
        out.append(
            {
                "name": str(p.get("name") or ""),
                "tool_strength": str(p.get("tool_strength") or "medium"),
                "shape": str(p.get("shape") or ""),
                "hook_device": bool(p.get("hook_device")),
            }
        )
    return out


def _corpus_block() -> str:
    if not CORPUS_PATH.is_file():
        return (
            "REFERENCE CORPUS: not on disk. Distill from the starting shapes "
            "and the theme list only. Do not invent fake third-party transcripts."
        )
    raw = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    pieces = raw.get("pieces") if isinstance(raw, dict) else raw
    if not isinstance(pieces, list) or not pieces:
        return (
            "REFERENCE CORPUS: file present but empty. Distill from starting "
            "shapes and the theme list only."
        )
    blocks = []
    for i, piece in enumerate(pieces, 1):
        if isinstance(piece, dict):
            title = piece.get("id") or piece.get("source") or f"piece_{i}"
            body = piece.get("text") or " ".join(piece.get("lines") or [])
        else:
            title = f"piece_{i}"
            body = str(piece)
        body = " ".join(str(body).split())
        if body:
            blocks.append(f"[{title}]\n{body}")
    if not blocks:
        return "REFERENCE CORPUS: empty after parse."
    note = (
        "REFERENCE CORPUS (shape only — never copy phrasing). "
        "corpus_15 and corpus_17 are a near-identical A/B pair from the same "
        "source video (wasn't / was). Treat them as ONE structural example, "
        "not two independent patterns.\n\n"
    )
    return note + "\n\n".join(blocks)


def _extract_json(text: str) -> dict:
    raw = (text or "").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start : end + 1]
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("seed output is not an object")
    return data


def _call_seed(client, model: str, user: str, *, tag: str) -> dict:
    print(f"[seed] {tag} user_chars={len(user)}")
    message = client.messages.create(
        model=model,
        max_tokens=8192,
        thinking={"type": "disabled"},
        system=SEED_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    chunks = [getattr(b, "text", None) for b in (getattr(message, "content", []) or [])]
    text = "\n".join(c for c in chunks if c).strip()
    usage = getattr(message, "usage", None)
    if usage is not None:
        rec = _usage_rec(usage, theme=f"structure_library_v2_{tag}", model=model)
        rec["kind"] = "seed"
        rec["note"] = "one-time seed; Messages.create (list price, no batch)"
        append_cost_log([rec])
        print(
            f"[seed] {tag} tokens in={rec['input_tokens']} out={rec['output_tokens']} "
            f"usd~{rec['cost_usd']:.4f}"
        )
    return _extract_json(text)


def _normalize_patterns(raw: dict) -> list[dict]:
    patterns = [p for p in (raw.get("patterns") or []) if isinstance(p, dict) and p.get("name")]
    for p in patterns:
        ex = p.get("in_house_example")
        if isinstance(ex, list):
            p["in_house_example"] = " ".join(str(x).strip() for x in ex if str(x).strip())
        words = len(str(p.get("in_house_example") or "").split())
        if words < 80:
            print(f"[seed] WARN {p.get('name')} example is {words} words")
    return patterns


def main() -> int:
    themes = _themes()
    shapes = _starting_shapes()
    theme_block = "THEME / SUBTHEME COVERAGE TARGETS:\n" + "\n".join(f"- {t}" for t in themes)
    corpus = _corpus_block()
    import config as app_config
    import anthropic

    api_key = getattr(app_config, "ANTHROPIC_API_KEY", None)
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY missing")
    client = anthropic.Anthropic(api_key=str(api_key))
    model = _resolve_polish_model(client)
    print(f"[seed] model={model} themes={len(themes)} starting_shapes={len(shapes)}")

    user_a = (
        f"{corpus}\n\n{theme_block}\n\n"
        "PASS A — Refine these starting shapes. Keep the names. "
        "Rewrite each in_house_example as ONE continuous 100-180 word paragraph. "
        "Return exactly these patterns, no extras.\n"
        + json.dumps(shapes, indent=2, ensure_ascii=False)
    )
    user_b = (
        f"{corpus}\n\n{theme_block}\n\n"
        "PASS B — Invent 8-12 NEW patterns that are NOT in this list: "
        + ", ".join(s["name"] for s in shapes)
        + ". Cover theme gaps. Return only the new patterns."
    )
    data_a = _call_seed(client, model, user_a, tag="pass_a")
    data_b = _call_seed(client, model, user_b, tag="pass_b")
    seen: set[str] = set()
    patterns: list[dict] = []
    for p in _normalize_patterns(data_a) + _normalize_patterns(data_b):
        name = str(p.get("name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        patterns.append(p)
    if len(patterns) < 15:
        raise SystemExit(f"seed returned {len(patterns)} patterns, need 15-25")
    from core_engine.economic_reel_lofi.corpus_echo import echo_hits

    clean: list[dict] = []
    for p in patterns:
        hits = echo_hits(str(p.get("in_house_example") or ""))
        if not hits:
            clean.append(p)
            continue
        print(f"[seed] ECHO {p.get('name')} {hits} — regenerating example")
        retry_user = (
            f"{corpus}\n\n"
            f"Rewrite ONLY this pattern's in_house_example as a new 100-180 "
            f"word continuous paragraph. Keep name/shape/strength. "
            f"Forbidden: any 4+ word sequence from the corpus, including: "
            + "; ".join(h["ngram"] for h in hits)
            + "\n\nPATTERN:\n"
            + json.dumps(p, indent=2, ensure_ascii=False)
        )
        try:
            redone = _call_seed(client, model, retry_user, tag=f"echo_{p.get('name')}")
            nxt = _normalize_patterns(redone)
            pick = next((x for x in nxt if x.get("name") == p.get("name")), nxt[0] if nxt else p)
            again = echo_hits(str(pick.get("in_house_example") or ""))
            if again:
                print(f"[seed] ECHO persist {p.get('name')} {again} — DROPPED")
                continue
            clean.append(pick)
        except Exception as exc:  # noqa: BLE001
            print(f"[seed] echo regen failed {p.get('name')} ({exc}) — DROPPED")
    patterns = clean
    if len(patterns) < 15:
        raise SystemExit(f"after echo filter {len(patterns)} patterns remain, need 15+")
    data = {
        "version": 2,
        "notes": (
            "Continuous-prose rhetorical shapes distilled from the 23-piece "
            "reference corpus (15/17 treated as one A/B pair). In-house examples "
            "are original and must never be copied at generation time. No "
            "third-party phrasing is stored. Echo-checked for 4+ word sequences."
        ),
        "patterns": patterns,
    }
    V2_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[seed] wrote {V2_PATH} patterns={len(patterns)}")
    from core_engine.economic_reel_lofi.lofi_collections import reset_structure_library_cache

    reset_structure_library_cache()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
