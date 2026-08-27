# -*- coding: utf-8 -*-
"""
LOFI Chroma-style collections — JSON source of truth, optional Chroma mirror.

Collections:
  - lofi_reference_structures
  - lofi_verified_quotes
  - lofi_generated_history_{module}
  - lofi_theme_bank_{module}
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import re
import struct
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from core.economic_reel_lofi import config as lofi_cfg

_LOG = logging.getLogger(__name__)
_EMBED_DIM = 128


class DeterministicHashEmbedding:
    """Same lightweight embedder pattern as VisualQA_Agent (no torch)."""

    def __init__(self, dim: int = _EMBED_DIM) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = (text or "").lower().replace(",", " ").split() or ["empty"]
        for tok in tokens:
            digest = hashlib.sha256(tok.encode("utf-8")).digest()
            for i in range(0, min(len(digest), self.dim * 4), 4):
                idx = struct.unpack_from(">I", digest, i)[0] % self.dim
                sign = 1.0 if digest[i] % 2 == 0 else -1.0
                vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


_EMBEDDER = DeterministicHashEmbedding()


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return float(sum(x * y for x, y in zip(a, b)))


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _store_path(name: str) -> Path:
    return lofi_cfg.STORE_DIR / f"{name}.json"


def ensure_seeded() -> None:
    """Copy seed JSON into store/ on first run (idempotent)."""
    lofi_cfg.STORE_DIR.mkdir(parents=True, exist_ok=True)
    seeds = {
        "lofi_reference_structures": lofi_cfg.DATA_DIR / "seed_reference_structures.json",
        "lofi_verified_quotes": lofi_cfg.DATA_DIR / "seed_verified_quotes.json",
        "lofi_theme_bank_relationship": lofi_cfg.DATA_DIR / "seed_themes_relationship.json",
        "lofi_theme_bank_parenting": lofi_cfg.DATA_DIR / "seed_themes_parenting.json",
    }
    for name, src in seeds.items():
        dest = _store_path(name)
        if dest.is_file():
            if name.startswith("lofi_theme_bank_"):
                _merge_new_seed_rows(dest, src)
            continue
        if src.is_file():
            _write_json(dest, _read_json(src, []))
            _LOG.info("LOFI RAG seeded %s from %s", name, src.name)
        else:
            _write_json(dest, [])

    _merge_concrete_details_sidecars()
    _merge_thematic_hooks_sidecars()
    _purge_banned_hooks_from_theme_banks()

    for module in ("relationship", "parenting"):
        hist = _store_path(f"lofi_generated_history_{module}")
        if not hist.is_file():
            _write_json(hist, [])


# ── Reference structures ────────────────────────────────────────────────────

def get_reference_structures(module: str | None = None, limit: int = 4) -> list[dict[str, Any]]:
    ensure_seeded()
    rows = _read_json(_store_path("lofi_reference_structures"), [])
    if not isinstance(rows, list):
        return []
    if module:
        mod = module.lower()
        filtered = [r for r in rows if str(r.get("module", "")).lower() == mod]
        if filtered:
            rows = filtered
    return rows[: max(1, limit)]


# ── Structure library (additive rhetorical shapes) ──────────────────────────

RHETORIC_HISTORY_N = 10
RHETORIC_HIGH = frozenset(
    {
        "domino_chain",
        "definition_then_evidence",
        "negation_list_to_permission",
        "parable_triad",
    }
)
RHETORIC_MEDIUM = frozenset(
    {
        "anaphora_to_universal_law",
        "contrast_metaphor_resolution",
        "thinker_juxtaposition",
        "before_after_transformation",
    }
)
RHETORIC_LOW = frozenset(
    {
        "retrospective_reversal",
        "personification_sensory",
        "open_question",
    }
)
RHETORIC_APHORISM_PATTERNS = frozenset(
    {"anaphora_to_universal_law", "thinker_juxtaposition"}
)
_RHETORIC_LIB_CACHE: dict[str, Any] | None = None


def reset_structure_library_cache() -> None:
    global _RHETORIC_LIB_CACHE
    _RHETORIC_LIB_CACHE = None


def load_structure_library() -> dict[str, Any]:
    """Prefer v2 continuous-prose library; fall back to v1 caption examples."""
    global _RHETORIC_LIB_CACHE
    if _RHETORIC_LIB_CACHE is not None:
        return _RHETORIC_LIB_CACHE
    v2 = lofi_cfg.DATA_DIR / "structure_library_v2.json"
    v1 = lofi_cfg.DATA_DIR / "structure_library.json"
    path = v2 if v2.is_file() else v1
    raw = _read_json(path, {})
    if not isinstance(raw, dict):
        raw = {}
    patterns = [p for p in (raw.get("patterns") or []) if isinstance(p, dict) and p.get("name")]
    _RHETORIC_LIB_CACHE = {
        "version": raw.get("version") or 1,
        "patterns": patterns,
        "source": str(path.name),
    }
    return _RHETORIC_LIB_CACHE


def get_rhetoric_pattern(name: str) -> dict[str, Any] | None:
    needle = str(name or "").strip()
    if not needle:
        return None
    for row in load_structure_library().get("patterns") or []:
        if str(row.get("name") or "") == needle:
            return dict(row)
    return None


def recent_rhetoric_patterns(module: str, n: int = RHETORIC_HISTORY_N) -> list[str]:
    """Last N scripts' assigned rhetoric_pattern names (skips unstamped rows)."""
    out: list[str] = []
    for row in _recent_history_rows(module, n):
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        name = str((meta or {}).get("rhetoric_pattern") or "").strip()
        if name:
            out.append(name)
    return out


def pick_rhetoric_pattern(
    module: str,
    *,
    force: str | None = None,
    force_hook_overlay: str | None = None,
) -> dict[str, Any]:
    """
    Assign ONE library pattern before writing.

    Target mix: ~70% tool_strength=high, ~30% medium/low.
    Low-strength patterns (7, 8, 11) at most once in any 6-episode window.
    Never repeat the same pattern back-to-back.
    anaphora_to_universal_law may also overlay as a hook-only device.
    """
    lib = load_structure_library()
    by_name = {str(p.get("name") or ""): dict(p) for p in (lib.get("patterns") or [])}
    high_names = [
        n for n, p in by_name.items()
        if str(p.get("tool_strength") or "").lower() == "high"
    ] or [n for n in RHETORIC_HIGH if n in by_name]
    med_names = [
        n for n, p in by_name.items()
        if str(p.get("tool_strength") or "").lower() == "medium"
    ] or [n for n in RHETORIC_MEDIUM if n in by_name]
    low_names = [
        n for n, p in by_name.items()
        if str(p.get("tool_strength") or "").lower() == "low"
    ] or [n for n in RHETORIC_LOW if n in by_name]
    forced = str(force or os.getenv("LOFI_FORCE_RHETORIC_PATTERN") or "").strip()
    overlay_req = str(
        force_hook_overlay or os.getenv("LOFI_FORCE_HOOK_OVERLAY") or ""
    ).strip()
    recent = recent_rhetoric_patterns(module, RHETORIC_HISTORY_N)
    last = recent[-1] if recent else ""
    low_in_window = sum(1 for p in recent[-6:] if p in set(low_names))

    chosen: dict[str, Any] | None = None
    if forced and forced in by_name:
        chosen = dict(by_name[forced])
    else:
        high = [n for n in high_names if n != last]
        med = [n for n in med_names if n != last]
        low = (
            [n for n in low_names if n != last]
            if low_in_window < 1
            else []
        )
        if not high and not med and not low:
            high = list(high_names)
            med = list(med_names)
        roll = random.random()
        if roll < 0.70 and high:
            pool = high
        else:
            pool = med + low
            if not pool:
                pool = high or med or list(by_name)
        name = random.choice(pool)
        chosen = dict(by_name.get(name) or next(iter(by_name.values())))

    hook_overlay = ""
    if (
        overlay_req == "anaphora_to_universal_law"
        and str(chosen.get("name") or "") != "anaphora_to_universal_law"
    ):
        hook_overlay = "anaphora_to_universal_law"
    elif (
        not overlay_req
        and not forced
        and str(chosen.get("name") or "") != "anaphora_to_universal_law"
        and random.random() < 0.12
    ):
        hook_overlay = "anaphora_to_universal_law"
    chosen["hook_overlay"] = hook_overlay
    return chosen


# ── Verified quotes ─────────────────────────────────────────────────────────

def list_verified_quotes(tag: str | None = None) -> list[dict[str, Any]]:
    ensure_seeded()
    rows = _read_json(_store_path("lofi_verified_quotes"), [])
    if not isinstance(rows, list):
        return []
    if tag:
        t = tag.lower()
        rows = [
            r for r in rows
            if t in {str(x).lower() for x in (r.get("tags") or [])}
            or t in str(r.get("theme", "")).lower()
        ]
    return rows


def get_quote_by_id(quote_id: str) -> dict[str, Any] | None:
    for q in list_verified_quotes():
        if str(q.get("id")) == str(quote_id):
            return q
    return None


def find_quote_exact(quote_text: str) -> dict[str, Any] | None:
    needle = " ".join((quote_text or "").strip().split()).lower()
    if not needle:
        return None
    for q in list_verified_quotes():
        body = " ".join(str(q.get("quote_text") or "").strip().split()).lower()
        if body and body in needle:
            return q
        if needle and needle in body:
            return q
    return None


def pick_quote_for_theme(theme: str, module: str) -> dict[str, Any] | None:
    quotes = list_verified_quotes(tag=module) or list_verified_quotes()
    if not quotes:
        return None
    theme_lo = (theme or "").lower()
    scored: list[tuple[int, dict[str, Any]]] = []
    for q in quotes:
        tags = " ".join(str(t) for t in (q.get("tags") or [])).lower()
        score = 1 if theme_lo and theme_lo in tags else 0
        if module.lower() in tags:
            score += 1
        scored.append((score, q))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored else None


# ── Theme bank ──────────────────────────────────────────────────────────────

def _theme_bank_name(module: str) -> str:
    return f"lofi_theme_bank_{module}"


_THEME_FIELD_COPY = (
    "setting_object_pairs",
    "preferred_mood_palette",
    "sample_scene",
    "concrete_details",
    "hooks",
)


def _normalize_hook(item: Any) -> dict[str, Any] | None:
    if isinstance(item, str):
        text = item.strip()
        if not text:
            return None
        return {"text": text, "last_used_date": None, "performance_score": None}
    if not isinstance(item, dict):
        return None
    text = str(item.get("text") or "").strip()
    if not text:
        return None
    return {
        "text": text,
        "last_used_date": item.get("last_used_date"),
        "performance_score": item.get("performance_score"),
    }


def _merge_new_seed_rows(dest: Path, src: Path) -> None:
    """Append missing seed rows; copy new fields onto live rows; never rewrite used status."""
    if not src.is_file() or not dest.is_file():
        return
    seed_rows = _read_json(src, [])
    live = _read_json(dest, [])
    if not isinstance(seed_rows, list):
        return
    if not isinstance(live, list):
        live = []
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for r in live:
        if isinstance(r, dict):
            by_key[(str(r.get("theme")), str(r.get("subtheme")))] = r
    added = 0
    filled = 0
    for row in seed_rows:
        if not isinstance(row, dict):
            continue
        key = (str(row.get("theme")), str(row.get("subtheme")))
        if key in by_key:
            live_row = by_key[key]
            for field in _THEME_FIELD_COPY:
                incoming_val = row.get(field)
                if field == "setting_object_pairs" and isinstance(incoming_val, list):
                    live_pairs = list(live_row.get(field) or [])
                    have_p = {
                        (str(p.get("setting")), str(p.get("key_object")))
                        for p in live_pairs
                        if isinstance(p, dict)
                    }
                    for p in incoming_val:
                        if not isinstance(p, dict):
                            continue
                        pk = (str(p.get("setting")), str(p.get("key_object")))
                        if pk in have_p:
                            continue
                        live_pairs.append(p)
                        have_p.add(pk)
                        filled += 1
                    live_row[field] = live_pairs
                    continue
                if field == "concrete_details" and isinstance(incoming_val, list):
                    live_details = list(live_row.get(field) or [])
                    have_d = {
                        str(d.get("detail") or "").strip().lower()
                        for d in live_details
                        if isinstance(d, dict)
                    }
                    for d in incoming_val:
                        if not isinstance(d, dict):
                            continue
                        dk = str(d.get("detail") or "").strip().lower()
                        if not dk or dk in have_d:
                            continue
                        live_details.append(
                            {
                                "detail": str(d.get("detail") or "").strip(),
                                "sensory_type": str(d.get("sensory_type") or "object"),
                                "last_used_date": d.get("last_used_date"),
                                "performance_score": d.get("performance_score"),
                            }
                        )
                        have_d.add(dk)
                        filled += 1
                    live_row[field] = live_details
                    continue
                if field == "hooks" and isinstance(incoming_val, list):
                    live_hooks = [_normalize_hook(h) for h in (live_row.get(field) or [])]
                    live_hooks = [h for h in live_hooks if h]
                    have_h = {h["text"].lower() for h in live_hooks}
                    for h in incoming_val:
                        nh = _normalize_hook(h)
                        if not nh or nh["text"].lower() in have_h:
                            continue
                        live_hooks.append(nh)
                        have_h.add(nh["text"].lower())
                        filled += 1
                    live_row[field] = live_hooks
                    continue
                if incoming_val and not live_row.get(field):
                    live_row[field] = incoming_val
                    filled += 1
            continue
        incoming = dict(row)
        incoming.setdefault("status", "unused")
        live.append(incoming)
        by_key[key] = incoming
        added += 1
    if added or filled:
        _write_json(dest, live)
        _LOG.info(
            "LOFI theme bank merged %s new rows, filled %s fields into %s",
            added,
            filled,
            dest.name,
        )
        print(
            f"[LOFI theme] merged {added} new seed themes, "
            f"filled {filled} fields into {dest.name}"
        )


def _theme_lru_key(row: dict[str, Any]) -> str:
    raw = str(row.get("last_used_date") or "").strip()
    return raw if raw else "0000-01-01"


def select_theme(
    module: str,
    theme: str | None = None,
    subtheme: str | None = None,
) -> dict[str, Any]:
    """Next unused theme, else least-recently-used — never collapse to rows[0]."""
    ensure_seeded()
    path = _store_path(_theme_bank_name(module))
    rows = _read_json(path, [])
    if not isinstance(rows, list) or not rows:
        return {"theme": "connection", "subtheme": "general", "status": "unused"}

    want = str(theme or "").strip().lower()
    want_sub = str(subtheme or "").strip().lower()
    if want:
        matches = [
            r
            for r in rows
            if isinstance(r, dict) and str(r.get("theme") or "").strip().lower() == want
        ]
        if want_sub:
            sub_hits = [
                r
                for r in matches
                if str(r.get("subtheme") or "").strip().lower() == want_sub
            ]
            if sub_hits:
                matches = sub_hits
        if matches:
            chosen = dict(matches[0])
            print(
                f"[LOFI theme] forced theme={chosen.get('theme')} "
                f"subtheme={chosen.get('subtheme')}"
            )
            return chosen
        print(f"[LOFI theme] WARN forced theme={theme!r} not found — falling through")

    unused = [r for r in rows if isinstance(r, dict) and str(r.get("status", "unused")) == "unused"]
    if unused:
        chosen = dict(unused[0])
        print(
            f"[LOFI theme] unused theme={chosen.get('theme')} "
            f"subtheme={chosen.get('subtheme')} remaining={len(unused)}"
        )
        return chosen

    used = [r for r in rows if isinstance(r, dict)]
    used.sort(key=_theme_lru_key)
    chosen = dict(used[0])
    print(
        f"[LOFI theme] recycle LRU theme={chosen.get('theme')} "
        f"subtheme={chosen.get('subtheme')} last_used={chosen.get('last_used_date')}"
    )
    return chosen


def mark_theme_used(module: str, theme: str, subtheme: str | None = None) -> None:
    ensure_seeded()
    path = _store_path(_theme_bank_name(module))
    rows = _read_json(path, [])
    if not isinstance(rows, list):
        return
    today = date.today().isoformat()
    for r in rows:
        if str(r.get("theme")) != theme:
            continue
        if subtheme and str(r.get("subtheme", "")) != subtheme:
            continue
        r["status"] = "used"
        r["last_used_date"] = today
        break
    _write_json(path, rows)


# ── Generated history / dedup ───────────────────────────────────────────────

def _history_name(module: str) -> str:
    return f"lofi_generated_history_{module}"


_PHRASE_HISTORY_N = 10
_TRIGRAM_DUP_THRESHOLD = 0.6
_MIN_PHRASE_WORDS = 3
_BATCH_CAPTIONS: list[tuple[str, str]] = []
_BATCH_PHRASES: set[str] = set()
_BATCH_THEMES: list[str] = []


def reset_batch_scripts() -> None:
    """Clear same-run caption/phrase memory (call at the start of a qty>1 batch)."""
    _BATCH_CAPTIONS.clear()
    _BATCH_PHRASES.clear()
    _BATCH_THEMES.clear()


def note_batch_script(script: dict[str, Any] | str, *, theme: str = "") -> None:
    """Record a finished script so later episodes in THIS run can be cross-checked."""
    if isinstance(script, dict):
        theme = str(theme or script.get("theme") or "").strip().lower()
        lines = script.get("lines") or []
        texts = [
            str(r.get("text") or r.get("beat_text") or "")
            for r in lines
            if isinstance(r, dict)
        ]
        blob = " ".join(texts) or str(script.get("monologue") or "")
    else:
        blob = str(script or "")
        texts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", blob) if p.strip()]
    if theme:
        _BATCH_THEMES.append(theme)
    for text in texts:
        norm = normalize_caption_text(text)
        if norm:
            _BATCH_CAPTIONS.append((theme, norm))
            _BATCH_PHRASES.update(phrases_from_text(text, min_words=3))
            _BATCH_PHRASES.update(phrases_from_text(text, min_words=4))
    if blob:
        _BATCH_PHRASES.update(phrases_from_text(blob, min_words=4))


def batch_script_count() -> int:
    return len(_BATCH_THEMES)


def recent_batch_captions() -> list[str]:
    return [cap for _theme, cap in _BATCH_CAPTIONS]


def normalize_caption_text(text: str) -> str:
    """Lowercase, strip punctuation — used for exact and near-dupe checks."""
    blob = re.sub(r"[^\w\s']", " ", (text or "").lower())
    return " ".join(blob.split())


def caption_trigrams(text: str) -> set[str]:
    words = normalize_caption_text(text).split()
    if not words:
        return set()
    if len(words) < 3:
        return {" ".join(words)}
    return {" ".join(words[i : i + 3]) for i in range(len(words) - 2)}


def trigram_overlap(a: str, b: str) -> float:
    """Jaccard overlap of word trigrams. Exact-normalized match is 1.0."""
    na = normalize_caption_text(a)
    nb = normalize_caption_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = caption_trigrams(a), caption_trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def intra_script_duplicate_pairs(
    lines: list[Any],
    *,
    threshold: float = _TRIGRAM_DUP_THRESHOLD,
) -> list[tuple[int, int, float]]:
    """1-based scene pairs that are exact or near-duplicate captions."""
    texts = [
        str((row or {}).get("text") or (row or {}).get("beat_text") or "")
        if isinstance(row, dict)
        else ""
        for row in (lines or [])
    ]
    hits: list[tuple[int, int, float]] = []
    for i, a in enumerate(texts):
        if not a.strip():
            continue
        for j in range(i + 1, len(texts)):
            b = texts[j]
            if not b.strip():
                continue
            score = trigram_overlap(a, b)
            if score >= threshold:
                hits.append((i + 1, j + 1, score))
    return hits


def phrases_from_text(text: str, *, min_words: int = _MIN_PHRASE_WORDS) -> set[str]:
    """Sliding 3–4 word phrases after normalize (catches 'isn't loud' leakage)."""
    words = normalize_caption_text(text).split()
    out: set[str] = set()
    for n in (min_words, min_words + 1):
        if len(words) < n:
            continue
        for i in range(0, len(words) - n + 1):
            out.add(" ".join(words[i : i + n]))
    return out


def _recent_history_rows(module: str, n: int = _PHRASE_HISTORY_N) -> list[dict[str, Any]]:
    ensure_seeded()
    rows = _read_json(_store_path(_history_name(module)), [])
    if not isinstance(rows, list) or not rows:
        return []
    take = max(1, int(n or _PHRASE_HISTORY_N))
    return [r for r in rows[-take:] if isinstance(r, dict)]


def preceding_setting_archetypes(module: str) -> list[str]:
    """Unique archetypes from the immediately preceding generated script."""
    rows = _recent_history_rows(module, 1)
    if not rows:
        return []
    meta = rows[-1].get("meta") if isinstance(rows[-1].get("meta"), dict) else {}
    raw = (meta or {}).get("setting_archetypes")
    if isinstance(raw, list) and raw:
        return [str(x).strip() for x in raw if str(x).strip()]
    beats = (meta or {}).get("setting_archetype_beats")
    if isinstance(beats, list) and beats:
        return sorted({str(x).strip() for x in beats if str(x).strip()})
    # First run of this gate: reconstruct perseverance from its locked script.
    theme = str((meta or {}).get("theme") or "").strip().lower()
    if theme == "perseverance":
        from core.economic_reel_lofi.setting_archetypes import (
            classify_setting_archetype,
        )

        locked = _store_path("locked_script_perseverance_v2")
        if not locked.is_file():
            locked = Path(__file__).resolve().parent / "store" / (
                "locked_script_perseverance_v2.json"
            )
        try:
            data = _read_json(locked, {})
        except Exception:
            data = {}
        script = data.get("script") if isinstance(data.get("script"), dict) else data
        used: list[str] = []
        for row in (script or {}).get("lines") or []:
            if not isinstance(row, dict):
                continue
            used.append(
                classify_setting_archetype(
                    str(row.get("setting") or ""),
                    str(row.get("key_object") or ""),
                )
            )
        return sorted({a for a in used if a and a != "unknown"})
    return []


def recent_setting_archetypes(
    module: str,
    *,
    theme: str = "",
    n: int = _PHRASE_HISTORY_N,
) -> list[list[str]]:
    """Per-script archetype lists from the last N history rows (optionally one theme)."""
    out: list[list[str]] = []
    needle = str(theme or "").strip().lower()
    for row in _recent_history_rows(module, n):
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        if needle and str((meta or {}).get("theme") or "").strip().lower() != needle:
            continue
        raw = (meta or {}).get("setting_archetypes")
        if isinstance(raw, list) and raw:
            out.append([str(x).strip() for x in raw if str(x).strip()])
            continue
        beats = (meta or {}).get("setting_archetype_beats")
        if isinstance(beats, list) and beats:
            out.append(sorted({str(x).strip() for x in beats if str(x).strip()}))
    return out


def recent_dominant_lighting(module: str, n: int = 5) -> list[str]:
    """Dominant lighting condition IDs from the last N history + performance rows."""
    out: list[str] = []
    for row in _recent_history_rows(module, max(n, 8)):
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        lid = str((meta or {}).get("dominant_lighting") or "").strip()
        if lid:
            out.append(lid)
    events = _read_json(_store_path("lofi_performance_events"), [])
    if isinstance(events, list):
        for ev in events:
            if not isinstance(ev, dict):
                continue
            if str(ev.get("module") or "") != module:
                continue
            lid = str(ev.get("dominant_lighting") or "").strip()
            if lid:
                out.append(lid)
    return out[-max(1, int(n)) :]


def recent_used_anchors(module: str, n: int = _PHRASE_HISTORY_N) -> set[str]:
    """Anchor nouns from the last N generated scripts (all themes)."""
    out: set[str] = set()
    skip = {"the", "and", "empty", "just", "off", "on", "name", "initial", "final", "state"}
    for row in _recent_history_rows(module, n):
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        raw = (meta or {}).get("anchor_object")
        if isinstance(raw, dict):
            name = str(raw.get("name") or "").strip().lower()
        else:
            name = str(raw or "").strip().lower()
        if not name or name in {"none", "null"}:
            continue
        out.add(name)
        for tok in re.findall(r"[a-z]+", name):
            if len(tok) >= 3 and tok not in skip:
                out.add(tok)
    return out


def _history_hook_text(row: dict[str, Any]) -> str:
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    hook = str((meta or {}).get("hook") or "").strip()
    if hook:
        return hook
    text = str(row.get("text") or "").strip()
    if not text:
        return ""
    return text.split(".")[0].strip() or text.split("\n")[0].strip()


def recent_used_phrases(module: str, n: int = _PHRASE_HISTORY_N) -> set[str]:
    """Hook 3-grams plus distinctive 4-grams from last-N full scripts + this batch."""
    out: set[str] = set()
    for row in _recent_history_rows(module, n):
        hook = _history_hook_text(row)
        if hook:
            out.update(phrases_from_text(hook, min_words=3))
        body = str(row.get("text") or "")
        if body:
            out.update(phrases_from_text(body, min_words=4))
    out |= set(_BATCH_PHRASES)
    return out


def cross_run_phrase_hits(
    module: str,
    script: dict[str, Any] | str,
    *,
    n: int = _PHRASE_HISTORY_N,
) -> list[str]:
    """
    Phrase-level history reuse (not whole-script cosine).

    History side: last-N hooks + their anchor nouns (all themes).
    New script: any beat that repeats a history-hook phrase, or the same
    anchor noun (catches 'stone' / \"isn't loud\" leaking across themes).
    """
    if isinstance(script, dict):
        lines = script.get("lines") or []
        texts = [
            str(r.get("text") or r.get("beat_text") or "")
            for r in lines
            if isinstance(r, dict)
        ]
        ao = script.get("anchor_object")
        anchor = ""
        if isinstance(ao, dict):
            anchor = str(ao.get("name") or "").strip()
        elif isinstance(ao, str):
            anchor = ao.strip()
        blob = " ".join(texts)
    else:
        blob = str(script or "")
        anchor = ""
    used_phrases = recent_used_phrases(module, n)
    used_anchors = recent_used_anchors(module, n)
    hits: list[str] = []
    if isinstance(script, dict):
        self_theme = str(script.get("theme") or "").strip().lower()
        peer_caps = {cap for th, cap in _BATCH_CAPTIONS if th != self_theme}
        for raw in texts:
            norm = normalize_caption_text(raw)
            if norm and len(norm.split()) >= 4 and norm in peer_caps:
                hits.append(f"batch_caption_reuse:{norm}")
    if anchor:
        tokens = {anchor.lower()}
        tokens.update(w for w in re.findall(r"[a-z]+", anchor.lower()) if len(w) >= 3)
        # Ignore generic fillers that appear inside longer object names
        tokens -= {"the", "and", "empty", "just", "off", "on"}
        overlap = tokens & used_anchors
        if overlap:
            hits.append("anchor_reuse:" + ",".join(sorted(overlap)))
    new_norm = normalize_caption_text(blob)
    shared = sorted(p for p in used_phrases if p and p in new_norm)
    if shared:
        hits.append("phrase_reuse:" + ";".join(shared[:8]))
    return hits


def max_similarity_to_history(module: str, script_text: str) -> float:
    """Whole-script embedding cosine vs generated history (all themes)."""
    ensure_seeded()
    rows = _read_json(_store_path(_history_name(module)), [])
    if not isinstance(rows, list) or not rows:
        return 0.0
    query = _EMBEDDER.embed(script_text)
    best = 0.0
    for row in rows:
        emb = row.get("embedding")
        if isinstance(emb, list) and emb:
            best = max(best, _cosine(query, [float(x) for x in emb]))
        else:
            best = max(best, _cosine(query, _EMBEDDER.embed(str(row.get("text") or ""))))
    return best


_CONNECTIVE_DEVICES: tuple[tuple[str, str], ...] = (
    ("even though", "exception"),
    ("even if", "exception"),
    ("even then", "persist"),
    ("even when", "persist"),
    ("not later", "reversal"),
    ("not because", "reversal"),
    ("the way", "manner"),
    ("because", "cause"),
    ("since", "cause"),
    ("therefore", "consequence"),
    ("anyway", "anyway"),
    ("without", "exception"),
    ("although", "contrast"),
    ("though", "exception"),
    ("still", "persist"),
    ("yet", "contrast"),
    ("but", "contrast"),
    ("so", "consequence"),
)
BANNED_CONNECTIVE_SHAPES: frozenset[tuple[str, ...]] = frozenset(
    {
        ("contrast", "exception", "anyway", "persist", "cause", "consequence"),
    }
)


def extract_connective_device(text: str) -> dict[str, str] | None:
    blob = str(text or "").strip().lower()
    if not blob:
        return None
    for word, family in _CONNECTIVE_DEVICES:
        if re.search(rf"\b{re.escape(word)}\b", blob):
            return {"word": word, "family": family}
    return None


def extract_connective_map(lines: list[dict[str, Any]] | list[str]) -> list[dict[str, Any]]:
    """Per-beat connective word + family. Empty if the beat has no device."""
    out: list[dict[str, Any]] = []
    for i, row in enumerate(lines or []):
        if isinstance(row, dict):
            text = str(row.get("text") or row.get("beat_text") or "")
        else:
            text = str(row or "")
        hit = extract_connective_device(text)
        if not hit:
            continue
        rec = {"scene": i + 1, "word": hit["word"], "family": hit["family"], "text": text}
        out.append(rec)
    return out


def connective_sequence(cmap: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(str(r.get("family") or "") for r in cmap if r.get("family"))


def recent_connective_records(
    module: str,
    n: int = 10,
    exclude_themes: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Last N scripts' connective maps (from meta, or derived from monologue)."""
    skip = {str(t).strip().lower() for t in (exclude_themes or set()) if str(t).strip()}
    records: list[dict[str, Any]] = []
    for row in _recent_history_rows(module, n):
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        theme = str((meta or {}).get("theme") or "").strip().lower()
        if theme and theme in skip:
            continue
        raw = (meta or {}).get("connective_map")
        cmap: list[dict[str, Any]] = []
        if isinstance(raw, list) and raw:
            cmap = [r for r in raw if isinstance(r, dict) and r.get("word")]
        if not cmap:
            text = str(row.get("text") or "")
            parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
            cmap = extract_connective_map(parts)
        if not cmap:
            continue
        records.append(
            {
                "theme": theme,
                "map": cmap,
                "sequence": connective_sequence(cmap),
                "pairs": {(int(r.get("scene") or 0), str(r.get("word") or "")) for r in cmap},
            }
        )
    return records


def append_history(module: str, script_text: str, meta: dict[str, Any] | None = None) -> None:
    ensure_seeded()
    path = _store_path(_history_name(module))
    rows = _read_json(path, [])
    if not isinstance(rows, list):
        rows = []
    rows.append(
        {
            "id": hashlib.sha1(script_text.encode("utf-8")).hexdigest()[:12],
            "text": script_text,
            "embedding": _EMBEDDER.embed(script_text),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "meta": meta or {},
        }
    )
    # Cap history growth
    if len(rows) > 500:
        rows = rows[-500:]
    _write_json(path, rows)


def chroma_enabled() -> bool:
    """Optional Chroma mirror — never used for Core Mode (a) retrieval."""
    return os.getenv("LOFI_USE_CHROMA", "0").strip() in {"1", "true", "True", "yes"}


# ── Core Mode (a) concrete-detail retrieval (not quote mode b) ───────────────

ARC_TEMPLATES: tuple[dict[str, str], ...] = (
    {
        "id": "thematic_arc",
        "label": "hook → build → tension → insight → close",
        "act1": "hook + opening build of one theme",
        "act2": "complication / cost of the same theme",
        "act3": "insight and closing payoff",
    },
    {
        "id": "setup_ache_acceptance",
        "label": "setup/normalcy → absence/ache → quiet acceptance",
        "act1": "ordinary setup / what used to be true",
        "act2": "absence and ache in the room",
        "act3": "quiet acceptance, still concrete",
    },
    {
        "id": "loss_anger_acceptance",
        "label": "loss → anger → acceptance",
        "act1": "the leaving is already real",
        "act2": "heat, clenched objects, unfinished motion",
        "act3": "hands loosen; the object is set down",
    },
    {
        "id": "loss_memory_gratitude",
        "label": "loss → fond memory → gratitude",
        "act1": "what is missing now",
        "act2": "a small remembered ordinary minute",
        "act3": "thanks without asking it back",
    },
)


def _merge_concrete_details_sidecars() -> None:
    """Union curated sidecar details onto live theme banks (relationship + parenting)."""
    for module in ("relationship", "parenting"):
        _merge_concrete_details_for_module(module)


def _merge_concrete_details_for_module(module: str) -> None:
    src = lofi_cfg.DATA_DIR / f"seed_concrete_details_{module}.json"
    dest = _store_path(_theme_bank_name(module))
    if not src.is_file() or not dest.is_file():
        return
    sidecar = _read_json(src, [])
    live = _read_json(dest, [])
    if not isinstance(sidecar, list) or not isinstance(live, list):
        return
    by_sub = {
        str(r.get("subtheme") or ""): r.get("concrete_details")
        for r in sidecar
        if isinstance(r, dict) and r.get("subtheme")
    }
    filled = 0
    for row in live:
        if not isinstance(row, dict):
            continue
        incoming = by_sub.get(str(row.get("subtheme") or ""))
        if not isinstance(incoming, list) or not incoming:
            continue
        live_details = list(row.get("concrete_details") or [])
        have = {
            str(d.get("detail") or "").strip().lower()
            for d in live_details
            if isinstance(d, dict)
        }
        for d in incoming:
            if not isinstance(d, dict):
                continue
            dk = str(d.get("detail") or "").strip().lower()
            if not dk or dk in have:
                continue
            live_details.append(
                {
                    "detail": str(d.get("detail") or "").strip(),
                    "sensory_type": str(d.get("sensory_type") or "object"),
                    "last_used_date": None,
                    "performance_score": None,
                }
            )
            have.add(dk)
            filled += 1
        row["concrete_details"] = live_details
    if filled:
        _write_json(dest, live)
        print(
            f"[LOFI rag] merged {filled} concrete_details into "
            f"lofi_theme_bank_{module}"
        )


def _merge_thematic_hooks_sidecars() -> None:
    """Union curated hook lists onto live theme banks."""
    for module in ("relationship", "parenting"):
        src = lofi_cfg.DATA_DIR / f"seed_thematic_hooks_{module}.json"
        dest = _store_path(_theme_bank_name(module))
        if not src.is_file() or not dest.is_file():
            continue
        sidecar = _read_json(src, [])
        live = _read_json(dest, [])
        if not isinstance(sidecar, list) or not isinstance(live, list):
            continue
        by_sub = {
            str(r.get("subtheme") or ""): r.get("hooks")
            for r in sidecar
            if isinstance(r, dict) and r.get("subtheme")
        }
        filled = 0
        for row in live:
            if not isinstance(row, dict):
                continue
            incoming = by_sub.get(str(row.get("subtheme") or ""))
            if not isinstance(incoming, list) or not incoming:
                continue
            live_hooks = [_normalize_hook(h) for h in (row.get("hooks") or [])]
            live_hooks = [h for h in live_hooks if h]
            have = {h["text"].lower() for h in live_hooks}
            for h in incoming:
                nh = _normalize_hook(h)
                if not nh or nh["text"].lower() in have:
                    continue
                live_hooks.append(nh)
                have.add(nh["text"].lower())
                filled += 1
            row["hooks"] = live_hooks
        if filled:
            _write_json(dest, live)
            print(
                f"[LOFI rag] merged {filled} hooks into lofi_theme_bank_{module}"
            )


def _purge_banned_hooks_from_theme_banks() -> None:
    """Strip verbatim/near-verbatim reference lines from live hook lists."""
    from core.economic_reel_lofi.reference_guard import banned_hook_text

    for module in ("relationship", "parenting"):
        path = _store_path(_theme_bank_name(module))
        live = _read_json(path, [])
        if not isinstance(live, list):
            continue
        removed = 0
        for row in live:
            if not isinstance(row, dict):
                continue
            kept: list[dict[str, Any]] = []
            for h in row.get("hooks") or []:
                nh = _normalize_hook(h)
                if not nh:
                    continue
                if banned_hook_text(nh["text"]):
                    removed += 1
                    continue
                kept.append(nh)
            row["hooks"] = kept
        if removed:
            _write_json(path, live)
            print(
                f"[LOFI rag] purged {removed} reference-copied hooks from "
                f"lofi_theme_bank_{module}"
            )


def _detail_lru_key(detail: dict[str, Any]) -> tuple[str, float]:
    used = str(detail.get("last_used_date") or "").strip() or "0000-01-01"
    score = detail.get("performance_score")
    try:
        sc = float(score) if score is not None else 0.0
    except (TypeError, ValueError):
        sc = 0.0
    return (used, -sc)


def _resolve_theme_module(theme_row: dict[str, Any] | None, module: str | None) -> str:
    mod = str(module or (theme_row or {}).get("module") or "").strip().lower()
    if mod in getattr(lofi_cfg, "VALID_MODULES", {"relationship", "parenting"}):
        return mod
    return "relationship"


def _stamp_details_retrieved(
    module: str,
    theme: str,
    subtheme: str,
    picked: list[dict[str, Any]],
) -> None:
    """Mark retrieved details as used so LRU rotation actually moves."""
    if not theme or not picked:
        return
    path = _store_path(_theme_bank_name(module))
    rows = _read_json(path, [])
    if not isinstance(rows, list):
        return
    today = date.today().isoformat()
    needles = {
        str(d.get("detail") or "").strip().lower()
        for d in picked
        if str(d.get("detail") or "").strip()
    }
    if not needles:
        return
    stamped = 0
    for r in rows:
        if str(r.get("theme")) != theme:
            continue
        if subtheme and str(r.get("subtheme") or "") != subtheme:
            continue
        details = list(r.get("concrete_details") or [])
        for d in details:
            if not isinstance(d, dict):
                continue
            if str(d.get("detail") or "").strip().lower() in needles:
                d["last_used_date"] = today
                stamped += 1
        r["concrete_details"] = details
        break
    if stamped:
        _write_json(path, rows)
        print(
            f"[LOFI rag] stamped last_used_date={today} n={stamped} "
            f"theme={theme} subtheme={subtheme}"
        )


def select_concrete_details(
    theme_row: dict[str, Any] | None,
    *,
    n: int | None = None,
    module: str | None = None,
) -> list[dict[str, str]]:
    """Pick 2–3 ownerless sensory details as writer inspiration (not verbatim lines)."""
    ensure_seeded()
    count = int(n if n is not None else getattr(lofi_cfg, "CORE_DETAIL_COUNT", 3))
    count = max(2, min(3, count))
    mod = _resolve_theme_module(theme_row, module)
    theme = str((theme_row or {}).get("theme") or "")
    sub = str((theme_row or {}).get("subtheme") or "")
    details: list[dict[str, Any]] = []
    live = _read_json(_store_path(_theme_bank_name(mod)), [])
    for row in live if isinstance(live, list) else []:
        if theme and str(row.get("theme")) != theme:
            continue
        if sub and str(row.get("subtheme") or "") != sub:
            continue
        details = [
            d
            for d in (row.get("concrete_details") or [])
            if isinstance(d, dict) and str(d.get("detail") or "").strip()
        ]
        break
    if not details:
        raw = list((theme_row or {}).get("concrete_details") or [])
        details = [
            d for d in raw if isinstance(d, dict) and str(d.get("detail") or "").strip()
        ]
    if not details:
        return []
    ranked = sorted(details, key=_detail_lru_key)
    picked = ranked[:count]
    out = [
        {
            "detail": str(d.get("detail") or "").strip(),
            "sensory_type": str(d.get("sensory_type") or "object"),
        }
        for d in picked
    ]
    _stamp_details_retrieved(
        mod,
        str((theme_row or {}).get("theme") or ""),
        str((theme_row or {}).get("subtheme") or ""),
        out,
    )
    print(
        f"[LOFI rag] details n={len(out)} module={mod} "
        f"subtheme={(theme_row or {}).get('subtheme')} "
        + " | ".join(d["detail"] for d in out)
    )
    return out


def _stamp_hooks_retrieved(
    module: str,
    theme: str,
    subtheme: str,
    picked: list[dict[str, Any]],
) -> None:
    if not theme or not picked:
        return
    path = _store_path(_theme_bank_name(module))
    rows = _read_json(path, [])
    if not isinstance(rows, list):
        return
    today = date.today().isoformat()
    needles = {
        str(h.get("text") or "").strip().lower()
        for h in picked
        if str(h.get("text") or "").strip()
    }
    if not needles:
        return
    stamped = 0
    for r in rows:
        if str(r.get("theme")) != theme:
            continue
        if subtheme and str(r.get("subtheme") or "") != subtheme:
            continue
        hooks = [_normalize_hook(h) for h in (r.get("hooks") or [])]
        hooks = [h for h in hooks if h]
        for h in hooks:
            if h["text"].lower() in needles:
                h["last_used_date"] = today
                stamped += 1
        r["hooks"] = hooks
        break
    if stamped:
        _write_json(path, rows)
        print(
            f"[LOFI rag] stamped hook last_used_date={today} n={stamped} "
            f"theme={theme} subtheme={subtheme}"
        )


def select_thematic_seed(
    theme_row: dict[str, Any] | None,
    *,
    module: str | None = None,
    n_hooks: int = 2,
    n_details: int | None = None,
) -> dict[str, Any]:
    """Theme-keyed bundle: hooks[] + concrete details + optional quote."""
    ensure_seeded()
    mod = _resolve_theme_module(theme_row, module)
    theme = str((theme_row or {}).get("theme") or "")
    sub = str((theme_row or {}).get("subtheme") or "")
    details = select_concrete_details(theme_row, n=n_details, module=mod)
    hooks_raw: list[dict[str, Any]] = []
    live = _read_json(_store_path(_theme_bank_name(mod)), [])
    for row in live if isinstance(live, list) else []:
        if theme and str(row.get("theme")) != theme:
            continue
        if sub and str(row.get("subtheme") or "") != sub:
            continue
        hooks_raw = [_normalize_hook(h) for h in (row.get("hooks") or [])]
        hooks_raw = [h for h in hooks_raw if h]
        break
    if not hooks_raw:
        hooks_raw = [
            h
            for h in (_normalize_hook(x) for x in ((theme_row or {}).get("hooks") or []))
            if h
        ]
    ranked = sorted(hooks_raw, key=_detail_lru_key)
    from core.economic_reel_lofi.reference_guard import banned_hook_text

    ranked = [h for h in ranked if not banned_hook_text(str(h.get("text") or ""))]
    take = max(0, min(int(n_hooks), 3, len(ranked)))
    picked_hooks = ranked[:take] if ranked else []
    out_hooks = [{"text": h["text"]} for h in picked_hooks]
    _stamp_hooks_retrieved(mod, theme, sub, picked_hooks)
    quote = pick_quote_for_theme(theme, mod) if theme else None
    # Only attach a quote when it is actually tagged to this theme.
    if quote:
        tags = " ".join(str(t) for t in (quote.get("tags") or [])).lower()
        if theme.lower() not in tags:
            quote = None
    print(
        f"[LOFI rag] thematic_seed hooks={len(out_hooks)} details={len(details)} "
        f"quote={quote.get('id') if quote else None} theme={theme} subtheme={sub}"
    )
    return {
        "hooks": out_hooks,
        "details": details,
        "quote": quote,
    }


def select_arc_template(
    module: str,
    theme: str,
    subtheme: str = "",
) -> dict[str, str]:
    """Pick arc shape. Wonder Feed / Momma Circle default to thematic_arc."""
    ensure_seeded()
    default_id = str(
        getattr(lofi_cfg, "DEFAULT_ARC_BY_MODULE", {}).get(module) or ""
    ).strip()
    if default_id:
        for t in ARC_TEMPLATES:
            if t["id"] == default_id:
                print(
                    f"[LOFI rag] arc={t['id']} (module default) "
                    f"theme={theme} subtheme={subtheme}"
                )
                return dict(t)
    path = _store_path(_theme_bank_name(module))
    rows = _read_json(path, [])
    dates: dict[str, str] = {}
    for r in rows if isinstance(rows, list) else []:
        if str(r.get("theme")) != theme:
            continue
        if subtheme and str(r.get("subtheme") or "") != subtheme:
            continue
        raw = r.get("arc_template_dates") or {}
        if isinstance(raw, dict):
            dates = {str(k): str(v or "") for k, v in raw.items()}
        break
    ranked = sorted(
        ARC_TEMPLATES,
        key=lambda t: str(dates.get(t["id"]) or "0000-01-01"),
    )
    chosen = dict(ranked[0])
    print(f"[LOFI rag] arc={chosen['id']} theme={theme} subtheme={subtheme}")
    return chosen


def mark_core_rag_used(module: str, script: dict[str, Any]) -> None:
    """Stamp retrieved details + arc template on the live theme row after a pass."""
    ensure_seeded()
    theme = str(script.get("theme") or "")
    subtheme = str(script.get("subtheme") or "")
    if not theme:
        return
    path = _store_path(_theme_bank_name(module))
    rows = _read_json(path, [])
    if not isinstance(rows, list):
        return
    today = date.today().isoformat()
    used_details = {
        str(d.get("detail") or "").strip().lower()
        for d in (script.get("retrieved_details") or [])
        if isinstance(d, dict)
    }
    arc_id = str(script.get("arc_template") or "")
    ao = script.get("anchor_object")
    anchor_name = ""
    if isinstance(ao, dict):
        anchor_name = str(ao.get("name") or "").strip()
    elif isinstance(ao, str):
        anchor_name = ao.strip()
    for r in rows:
        if str(r.get("theme")) != theme:
            continue
        if subtheme and str(r.get("subtheme") or "") != subtheme:
            continue
        if used_details:
            details = list(r.get("concrete_details") or [])
            for d in details:
                if not isinstance(d, dict):
                    continue
                if str(d.get("detail") or "").strip().lower() in used_details:
                    d["last_used_date"] = today
            r["concrete_details"] = details
        if arc_id:
            dates = dict(r.get("arc_template_dates") or {})
            dates[arc_id] = today
            r["arc_template_dates"] = dates
        if anchor_name:
            r["last_anchor_object"] = anchor_name
            pairs = list(r.get("setting_object_pairs") or [])
            needle = anchor_name.lower()
            for p in pairs:
                if not isinstance(p, dict):
                    continue
                ko = str(p.get("key_object") or "").strip().lower()
                if ko and (needle == ko or needle in ko or ko in needle):
                    p["last_used_date"] = today
            r["setting_object_pairs"] = pairs
        break
    _write_json(path, rows)


def record_video_performance(
    module: str,
    *,
    theme: str,
    subtheme: str = "",
    views: float | int | None = None,
    completion_rate: float | None = None,
    anchor_object: str | None = None,
    video_path: str | None = None,
    retrieved_details: list[dict[str, Any]] | None = None,
    close_variant: str | None = None,
    close_target: str | None = None,
    eye_close_context: str | None = None,
    close_character: str | None = None,
    dominant_lighting: str | None = None,
    lighting_beats: list[str] | None = None,
) -> None:
    """
    Map a published video back onto theme+subtheme+anchor_object.

    `performance_score` was stored as null and never read. This writes an event
    and, when views/completion_rate are present, updates the theme row score:

        score = 0.4 * min(views/10000, 1) + 0.6 * completion_rate

    Pipeline records a stub event (views=None) at assemble time so later
    analytics can fill the same row. Does not block Core Mode generation.
    """
    ensure_seeded()
    events_path = _store_path("lofi_performance_events")
    events = _read_json(events_path, [])
    if not isinstance(events, list):
        events = []
    score = None
    if views is not None and completion_rate is not None:
        try:
            score = round(
                0.4 * min(float(views) / 10000.0, 1.0)
                + 0.6 * max(0.0, min(1.0, float(completion_rate))),
                4,
            )
        except (TypeError, ValueError):
            score = None
    events.append(
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "module": module,
            "theme": theme,
            "subtheme": subtheme,
            "anchor_object": anchor_object,
            "views": views,
            "completion_rate": completion_rate,
            "performance_score": score,
            "video_path": video_path,
            "close_variant": close_variant,
            "close_target": close_target,
            "eye_close_context": eye_close_context,
            "close_character": close_character,
            "dominant_lighting": dominant_lighting,
            "lighting_beats": list(lighting_beats or []),
            "retrieved_details": [
                str(d.get("detail") or d) if isinstance(d, dict) else str(d)
                for d in (retrieved_details or [])
            ],
        }
    )
    if len(events) > 2000:
        events = events[-2000:]
    _write_json(events_path, events)

    if score is None:
        return
    path = _store_path(_theme_bank_name(module))
    rows = _read_json(path, [])
    if not isinstance(rows, list):
        return
    used = {
        str(d.get("detail") or d).strip().lower()
        if not isinstance(d, dict)
        else str(d.get("detail") or "").strip().lower()
        for d in (retrieved_details or [])
    }
    for r in rows:
        if str(r.get("theme")) != theme:
            continue
        if subtheme and str(r.get("subtheme") or "") != subtheme:
            continue
        prev = r.get("performance_score")
        try:
            prev_f = float(prev) if prev is not None else score
        except (TypeError, ValueError):
            prev_f = score
        r["performance_score"] = round((prev_f + score) / 2.0, 4)
        if used:
            for d in r.get("concrete_details") or []:
                if not isinstance(d, dict):
                    continue
                if str(d.get("detail") or "").strip().lower() not in used:
                    continue
                dprev = d.get("performance_score")
                try:
                    dprev_f = float(dprev) if dprev is not None else score
                except (TypeError, ValueError):
                    dprev_f = score
                d["performance_score"] = round((dprev_f + score) / 2.0, 4)
        break
    _write_json(path, rows)


# ── Close-beat rotation (silhouette vs eye_close) ───────────────────────────

_SILHOUETTE_CLOSE_TARGETS: tuple[str, ...] = ("hands", "shoulders", "object")
_EYE_CLOSE_THEMES: frozenset[str] = frozenset(
    {
        "grief",
        "longing",
        "release",
        "loss",
        "missing",
        "regret",
        "goodbye",
        "absence",
    }
)
_EYE_CLOSE_CONTEXTS: tuple[str, ...] = (
    "window light on wet glass",
    "rain streaking a dark pane",
    "low amber lamp at the edge of frame",
    "dawn light through a thin curtain",
    "streetlight bounce on a night window",
    "overcast morning through a kitchen pane",
)


def _close_history_rows(module: str, n: int = 12) -> list[dict[str, Any]]:
    """Last N close-variant records from history meta + performance events."""
    rows: list[dict[str, Any]] = []
    for rec in _recent_history_rows(module, n):
        meta = rec.get("meta") if isinstance(rec.get("meta"), dict) else {}
        variant = str(meta.get("close_variant") or "").strip()
        if not variant:
            continue
        rows.append(
            {
                "close_variant": variant,
                "close_target": str(meta.get("close_target") or ""),
                "eye_close_context": str(meta.get("eye_close_context") or ""),
                "close_character": str(meta.get("close_character") or ""),
                "theme": str(meta.get("theme") or rec.get("theme") or ""),
            }
        )
    events = _read_json(_store_path("lofi_performance_events"), [])
    if isinstance(events, list):
        for ev in events:
            if not isinstance(ev, dict):
                continue
            if str(ev.get("module") or "") != module:
                continue
            variant = str(ev.get("close_variant") or "").strip()
            if not variant:
                continue
            rows.append(
                {
                    "close_variant": variant,
                    "close_target": str(ev.get("close_target") or ""),
                    "eye_close_context": str(ev.get("eye_close_context") or ""),
                    "close_character": str(ev.get("close_character") or ""),
                    "theme": str(ev.get("theme") or ""),
                }
            )
    return rows[-max(1, int(n)) :]


def recent_close_variants(module: str, n: int = 12) -> list[str]:
    return [
        str(r.get("close_variant") or "")
        for r in _close_history_rows(module, n)
        if r.get("close_variant")
    ]


def recent_silhouette_close_targets(module: str, n: int = 12) -> list[str]:
    return [
        str(r.get("close_target") or "")
        for r in _close_history_rows(module, n)
        if str(r.get("close_variant") or "") == "silhouette"
        and str(r.get("close_target") or "") in _SILHOUETTE_CLOSE_TARGETS
    ]


def recent_eye_close_contexts(module: str, n: int = 12) -> list[str]:
    return [
        str(r.get("eye_close_context") or "")
        for r in _close_history_rows(module, n)
        if str(r.get("close_variant") or "") == "eye_close"
        and r.get("eye_close_context")
    ]


def theme_fits_eye_close(theme: str) -> bool:
    blob = (theme or "").strip().lower().replace("-", " ").replace("_", " ")
    if not blob:
        return False
    tokens = set(re.findall(r"[a-z]+", blob))
    return bool(tokens & _EYE_CLOSE_THEMES)


def next_silhouette_close_target(module: str, seed: str = "") -> str:
    """Rotate hands → shoulders → object; eye_close does not consume a slot."""
    recent = recent_silhouette_close_targets(module)
    last = recent[-1] if recent else ""
    order = _SILHOUETTE_CLOSE_TARGETS
    if last in order:
        pick = order[(order.index(last) + 1) % len(order)]
    else:
        pick = order[sum(ord(c) for c in (seed or "hands")) % len(order)]
    print(
        f"[LOFI close] silhouette-rotate last={last or 'none'} "
        f"next={pick} recent={','.join(recent[-4:]) or 'none'}"
    )
    return pick


def pick_eye_close_context(module: str) -> str:
    used = {c.lower() for c in recent_eye_close_contexts(module)}
    for ctx in _EYE_CLOSE_CONTEXTS:
        if ctx.lower() not in used:
            return ctx
    return _EYE_CLOSE_CONTEXTS[len(used) % len(_EYE_CLOSE_CONTEXTS)]


def recent_close_characters(module: str, n: int = 12) -> list[str]:
    return [
        str(r.get("close_character") or "")
        for r in _close_history_rows(module, n)
        if r.get("close_character")
    ]


def pick_optional_close(theme: str, module: str) -> dict[str, str] | None:
    """
    Rotate silhouette vs portrait_close. eye_close is retired.

    Quota: at least 3 of the last 5 episodes must use portrait_close on
    the turn/insight beat. Skip is allowed only after that quota is met.
    """
    forced = str(os.getenv("LOFI_FORCE_CLOSE_VARIANT") or "").strip().lower()
    recent_raw = recent_close_variants(module, 8)
    recent = [
        "silhouette" if v == "eye_close" else v
        for v in recent_raw
        if v
    ]
    last5 = recent[-5:]
    n_portrait = last5.count("portrait_close")
    last = recent[-1] if recent else ""
    chars = recent_close_characters(module, 8)
    last_char = chars[-1] if chars else ""
    next_char = "man" if last_char == "woman" else "woman"
    if forced in {"portrait_close", "silhouette", "none", "skip"}:
        if forced in {"none", "skip"}:
            print(f"[LOFI close] force-skip last={last or 'none'}")
            return None
        print(
            f"[LOFI close] force variant={forced} character={next_char} "
            f"last={last or 'none'} quota={n_portrait}/5"
        )
        return {"variant": forced, "character": next_char}
    if n_portrait < 3:
        variant = "portrait_close"
        print(
            f"[LOFI close] quota portrait_close ({n_portrait}/5 last) "
            f"character={next_char} last={last or 'none'}"
        )
        return {"variant": variant, "character": next_char}
    seed = f"{theme}|{module}|{','.join(recent[-3:])}"
    roll = sum(ord(c) for c in seed) % 3
    if last == "portrait_close":
        if roll == 0:
            print(f"[LOFI close] skip-optional last={last} recent={recent[-4:]}")
            return None
        variant = "silhouette"
    elif last == "silhouette":
        if roll == 0:
            print(f"[LOFI close] skip-optional last={last} recent={recent[-4:]}")
            return None
        variant = "portrait_close"
        next_char = "woman" if next_char != "woman" else next_char
    else:
        variant = "portrait_close"
    print(
        f"[LOFI close] pick variant={variant} character={next_char} "
        f"last={last or 'none'} quota={n_portrait}/5"
    )
    return {"variant": variant, "character": next_char}


def should_use_eye_close(theme: str, module: str) -> bool:
    """Retired. Always False — eye_close is no longer assigned."""
    del theme, module
    return False


def stamp_close_variant_on_script(script: dict[str, Any], lines: list[dict[str, Any]]) -> None:
    """Copy the episode close choice onto the script root for history/logs."""
    close_row = next(
        (
            r
            for r in lines
            if isinstance(r, dict)
            and str(r.get("shot_scale") or "").strip().lower() == "close"
        ),
        {},
    )
    script["close_variant"] = str(close_row.get("close_variant") or "none")
    script["close_target"] = str(close_row.get("close_target") or "")
    script["eye_close_context"] = str(close_row.get("eye_close_context") or "")
    script["close_character"] = str(close_row.get("close_character") or "")


def stamp_lighting_on_script(script: dict[str, Any], lines: list[dict[str, Any]]) -> None:
    beats = [
        str(r.get("lighting_condition") or "")
        for r in lines
        if isinstance(r, dict)
    ]
    counts: dict[str, int] = {}
    for b in beats:
        if b:
            counts[b] = counts.get(b, 0) + 1
    dominant = ""
    if counts:
        dominant = max(counts, key=counts.get)
    script["dominant_lighting"] = dominant
    script["lighting_beats"] = beats
    script["lighting_counts"] = counts
