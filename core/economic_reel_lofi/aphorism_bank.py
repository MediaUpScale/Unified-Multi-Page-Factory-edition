"""Curated aphorisms for the light-paraphrase LOFI writer mode."""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from core.economic_reel_lofi import config as lofi_cfg

_CACHE: list[dict[str, Any]] | None = None
_RECENT_N = 8


def bank_path() -> Path:
    return lofi_cfg.STORE_DIR / "aphorism_bank.json"


def entries(*, refresh: bool = False) -> list[dict[str, Any]]:
    global _CACHE
    if _CACHE is not None and not refresh:
        return list(_CACHE)
    raw = json.loads(bank_path().read_text(encoding="utf-8"))
    rows = raw.get("entries") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise ValueError("aphorism_bank.json must contain an entries list")
    _CACHE = [
        dict(row)
        for row in rows
        if isinstance(row, dict)
        and str(row.get("id") or "").strip()
        and str(row.get("text") or "").strip()
    ]
    return list(_CACHE)


def _recent_path() -> Path:
    return lofi_cfg.STORE_DIR / "aphorism_recent.json"


def recent_ids(n: int = _RECENT_N) -> list[str]:
    path = _recent_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    ids = raw.get("ids") if isinstance(raw, dict) else raw
    if not isinstance(ids, list):
        return []
    out: list[str] = []
    for item in ids:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out[: max(1, int(n))]


def note_used(entry_id: str) -> None:
    wanted = str(entry_id or "").strip()
    if not wanted:
        return
    ids = [wanted, *[item for item in recent_ids() if item != wanted]]
    _recent_path().write_text(
        json.dumps({"ids": ids[:_RECENT_N]}, indent=2),
        encoding="utf-8",
    )


def _compatible(
    row: dict[str, Any],
    *,
    module: str,
    theme: str = "",
) -> bool:
    modules = {str(x).lower() for x in (row.get("modules") or [])}
    themes = {str(x).lower() for x in (row.get("themes") or [])}
    if modules and module not in modules:
        return False
    if theme and themes and theme not in themes:
        return False
    return True


def get_entry(
    entry_id: str | None = None,
    *,
    module: str = "relationship",
    theme: str | None = None,
    randomize: bool = True,
    exclude_ids: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Resolve an explicit id, otherwise a random module-compatible bank row."""
    rows = entries()
    wanted = str(entry_id or "").strip()
    if wanted:
        for row in rows:
            if str(row.get("id") or "") == wanted:
                return row
        raise ValueError(f"unknown LOFI aphorism id: {wanted!r}")
    mod = str(module or "").strip().lower()
    topic = str(theme or "").strip().lower()
    pool = [row for row in rows if _compatible(row, module=mod, theme=topic)]
    if not pool:
        pool = [row for row in rows if _compatible(row, module=mod)]
    if not pool:
        raise ValueError(f"no aphorism found for module={mod!r} theme={topic!r}")
    if not randomize:
        return pool[0]
    blocked = {str(x).strip() for x in (exclude_ids if exclude_ids is not None else recent_ids()) if str(x).strip()}
    open_pool = [row for row in pool if str(row.get("id") or "") not in blocked]
    pick = random.choice(open_pool or pool)
    note_used(str(pick.get("id") or ""))
    return pick

