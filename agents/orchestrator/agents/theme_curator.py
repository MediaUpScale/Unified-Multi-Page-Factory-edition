# -*- coding: utf-8 -*-
"""ThemeCuratorAgent — pick a unique investigative angle for one post."""
from __future__ import annotations

import logging
import random
import threading

from agents.orchestrator.agents.base import BaseAgent
from agents.orchestrator.criteria import GEO_ANCHORS
from agents.orchestrator.llm import complete_json
from agents.orchestrator.state import PipelineState, QAStatus

_LOG = logging.getLogger(__name__)

_SYSTEM = (
    "You are the Theme Curator for the Ancient Knowledge channel — an investigative "
    "documentary page covering lost civilisations, megalithic anomalies, and "
    "suppressed history. You never present a theory as established fact. "
    "Always open from a REAL recognisable world anchor, then introduce the "
    "impossible / anomalous element."
)


def _topic_pool() -> list[str]:
    try:
        from channels_config.ancient_knowledge import page_config as _pc

        pool = list(getattr(_pc, "TOPIC_POOL", []) or [])
        if pool:
            return pool
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("TOPIC_POOL import failed (%s) — using geo-anchor fallback.", exc)
    return [f"{g} — the measurement that mainstream archaeology cannot explain" for g in GEO_ANCHORS]


class ThemeCuratorAgent(BaseAgent):
    """Selects ``theme`` + ``geo_anchor`` + ``visual_hook`` onto state."""

    name = "theme_curator"

    def __init__(self, *, used_themes: list[str] | None = None) -> None:
        self._used = used_themes if used_themes is not None else []
        self._lock = threading.Lock()

    def run(self, state: PipelineState) -> PipelineState:
        state.last_node = self.name
        self._bind_cost_tracker(state)
        pool = _topic_pool()
        try:
            from channels_config.ancient_knowledge.channel_adapter import (
                AncientKnowledgeAdapter,
            )

            adapter = AncientKnowledgeAdapter()
            pool = adapter.get_niche_topics() or pool
        except Exception:
            adapter = None
        with self._lock:
            used_snapshot = list(self._used)
        used_l = {t.strip().lower() for t in used_snapshot if t}
        unused = [t for t in pool if t.strip().lower() not in used_l]
        seed = (state.seed_topic or "").strip()
        if adapter is not None and (not seed or adapter.topic_is_off_niche(seed)):
            seed = (
                random.choice(unused) if unused else adapter.pick_niche_topic()
            )
        elif not seed:
            seed = random.choice(unused) if unused else random.choice(pool)

        try:
            data = complete_json(
                (
                    f"SEED / ASSIGNED SLOT:\n{seed}\n\n"
                    f"ALREADY USED THIS BATCH (do not repeat):\n"
                    f"{chr(10).join(f'- {t}' for t in used_snapshot[-24:]) or '(none)'}\n\n"
                    "Return JSON only:\n"
                    "{\n"
                    '  "theme": "one-sentence unique investigative theme",\n'
                    '  "geo_anchor": "real-world site/artefact name",\n'
                    '  "visual_hook": "concrete visual that must appear in the image"\n'
                    "}\n"
                    "Rules: unique vs used list; real geo-anchor; no lifestyle/relationship/"
                    "microbiome/wellness topics. Never expand a health or newborn seed."
                ),
                system=_SYSTEM,
            )
            theme = str(data.get("theme") or "").strip() or seed
            geo = str(data.get("geo_anchor") or "").strip()
            hook = str(data.get("visual_hook") or "").strip()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("ThemeCurator LLM failed (%s) — using seed fallback.", exc)
            state.record_error(self.name, str(exc))
            theme, geo, hook = seed, "", ""

        if not geo:
            lo = theme.lower()
            geo = next((g for g in GEO_ANCHORS if g.lower() in lo), GEO_ANCHORS[0])
        if not hook:
            hook = f"{geo} under dramatic torchlight with an impossible precision detail"

        state.theme = theme
        state.geo_anchor = geo
        state.visual_hook = hook
        state.qa_status = QAStatus.PENDING
        with self._lock:
            self._used.append(theme)
        _LOG.info("ThemeCurator | #%s | %s | geo=%s", state.post_index + 1, theme[:120], geo)
        return state
