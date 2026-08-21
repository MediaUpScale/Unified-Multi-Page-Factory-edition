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
import struct
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from core_engine.economic_reel_lofi import config as lofi_cfg

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


def max_similarity_to_history(module: str, script_text: str) -> float:
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
    from core_engine.economic_reel_lofi.reference_guard import banned_hook_text

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
    from core_engine.economic_reel_lofi.reference_guard import banned_hook_text

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
