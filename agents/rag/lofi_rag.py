# -*- coding: utf-8 -*-
"""Explicit LOFI script RAG — query, hits, why, cost.

Store/theme-bank logic stays in ``core.economic_reel_lofi.lofi_collections``.
This module is the inspectable choke-point the writer calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from core.economic_reel_lofi import config as lofi_cfg
from core.economic_reel_lofi import lofi_collections as store

_RAG_LOG: list[dict[str, Any]] = []


@dataclass
class RagResult:
    query: dict[str, Any]
    details: list[dict[str, Any]]
    hooks: list[dict[str, Any]]
    quote: dict[str, Any] | None
    arc: dict[str, str]
    seed: dict[str, Any]
    hook_type: str
    thematic: bool
    reasons: list[str] = field(default_factory=list)
    cost_usd_est: float = 0.0


def reset_call_log() -> None:
    _RAG_LOG.clear()


def get_call_log() -> list[dict[str, Any]]:
    return list(_RAG_LOG)


def retrieve_script_seed(
    *,
    module: str,
    theme: str,
    subtheme: str = "",
    theme_row: dict[str, Any] | None = None,
    force_hook_type: str | None = None,
    thematic_hook_fn: Callable[[str], str] | None = None,
    default_hook_fn: Callable[[], str] | None = None,
) -> RagResult:
    """Look up arc + details/hooks/quote for one script. Local JSON — $0 API cost."""
    query = {
        "module": module,
        "theme": theme,
        "subtheme": subtheme,
        "force_hook_type": force_hook_type,
    }
    reasons: list[str] = []

    arc = store.select_arc_template(module, theme, subtheme)
    arc_id = str(arc.get("id") or "")
    default_id = str(getattr(lofi_cfg, "DEFAULT_ARC_BY_MODULE", {}).get(module) or "").strip()
    if default_id and arc_id == default_id:
        reasons.append(f"arc={arc_id!r} from DEFAULT_ARC_BY_MODULE[{module!r}]")
    else:
        reasons.append(f"arc={arc_id!r} from theme-bank / template list for module={module!r}")

    thematic = lofi_cfg.is_thematic_arc(arc_id)
    reasons.append(f"thematic={thematic} via is_thematic_arc({arc_id!r})")

    seed: dict[str, Any] = {"hooks": [], "details": [], "quote": None}
    if thematic:
        seed = store.select_thematic_seed(theme_row, module=module)
        retrieved = list(seed.get("details") or [])
        hook_type = force_hook_type or (thematic_hook_fn(module) if thematic_hook_fn else "question")
        reasons.append(
            "details+hooks: exact theme/subtheme row in "
            f"lofi_theme_bank_{module}; ranked LRU by last_used_date (oldest first)"
        )
        if seed.get("hooks"):
            reasons.append(f"hooks n={len(seed['hooks'])} from matched theme row")
    else:
        retrieved = store.select_concrete_details(theme_row, module=module)
        hook_type = force_hook_type or (default_hook_fn() if default_hook_fn else "definition")
        reasons.append(
            "details: exact theme/subtheme row in "
            f"lofi_theme_bank_{module}; ranked LRU by last_used_date (oldest first)"
        )

    quote: dict[str, Any] | None = seed.get("quote") if thematic else None
    if hook_type == "authority_quote":
        quote = store.pick_quote_for_theme(theme, module)
        if quote:
            tags = " ".join(str(t) for t in (quote.get("tags") or [])).lower()
            if theme.lower() in tags:
                reasons.append(
                    f"quote id={quote.get('id')!r} kept because theme={theme!r} is in quote.tags"
                )
            else:
                reasons.append(
                    f"quote id={quote.get('id')!r} dropped — theme={theme!r} not in tags"
                )
                quote = None
        if not quote:
            hook_type = "definition"
            reasons.append("authority_quote requested but no tagged quote — hook_type=definition")

    result = RagResult(
        query=query,
        details=retrieved,
        hooks=list(seed.get("hooks") or []),
        quote=quote if isinstance(quote, dict) else None,
        arc=dict(arc),
        seed=seed,
        hook_type=hook_type,
        thematic=thematic,
        reasons=reasons,
        cost_usd_est=0.0,
    )
    entry = {
        "kind": "rag",
        "provider": "local_json",
        "model": "lofi_collections",
        "input_tokens_est": 0,
        "output_tokens_est": 0,
        "cost_usd_est": 0.0,
        "query": query,
        "hits": {
            "details_n": len(result.details),
            "hooks_n": len(result.hooks),
            "quote_id": (result.quote or {}).get("id") if result.quote else None,
            "arc": arc_id,
        },
        "reasons": reasons,
    }
    _RAG_LOG.append(entry)
    print(
        "[LOFI rag] lookup cost_usd_est=0.0 query="
        f"module={module!r} theme={theme!r} subtheme={subtheme!r} "
        f"hook_type={hook_type!r}"
    )
    print("[LOFI rag] hits details="
          f"{len(result.details)} hooks={len(result.hooks)} "
          f"quote={(result.quote or {}).get('id') if result.quote else None} arc={arc_id}")
    for reason in reasons:
        print(f"[LOFI rag] why: {reason}")
    return result
