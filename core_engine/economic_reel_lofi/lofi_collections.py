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
            continue
        if src.is_file():
            _write_json(dest, _read_json(src, []))
            _LOG.info("LOFI RAG seeded %s from %s", name, src.name)
        else:
            _write_json(dest, [])

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


def select_theme(module: str) -> dict[str, Any]:
    """Pull next unused theme; recycle cooldown/used when bank exhausted."""
    ensure_seeded()
    path = _store_path(_theme_bank_name(module))
    rows = _read_json(path, [])
    if not isinstance(rows, list) or not rows:
        return {"theme": "connection", "subtheme": "general", "status": "unused"}

    unused = [r for r in rows if str(r.get("status", "unused")) == "unused"]
    pool = unused or rows
    chosen = dict(pool[0])
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
    return os.getenv("LOFI_USE_CHROMA", "0").strip() in {"1", "true", "True", "yes"}
