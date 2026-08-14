# -*- coding: utf-8 -*-
"""CopywriterAgent — investigative caption from the curated theme."""
from __future__ import annotations

import logging
import threading

from core_engine.agentic_pipeline.agents.base import BaseAgent
from core_engine.agentic_pipeline.llm import complete_json
from core_engine.agentic_pipeline.state import PipelineState
from core_engine.agentic_pipeline.starters import (
    forbidden_opener_rules_block,
    has_forbidden_opener,
    iter_starter_styles,
    pick_reserved_starter,
    strip_leading_cliche,
    style_to_instruction,
)

_LOG = logging.getLogger(__name__)

_STARTER_LOCK = threading.Lock()

_DISCLAIMER = (
    "This channel presents theories and historical accounts as education and "
    "entertainment only. Never claim a conspiracy is factual. Use hedges: "
    "'some researchers believe', 'ancient records suggest', 'one theory proposes'."
)

_SYSTEM = (
    "You are the Copywriter for Ancient Knowledge. Voice: investigative, "
    "neutral, immersive, respectful of every culture. Hook → evidence → mystery. "
    "No AI-sales words (Unlock, Dive, Elevate, Game-changer). "
    f"{_DISCLAIMER}"
)

_LONG_SYSTEM = (
    _SYSTEM
    + " LONG_CAPTION_IMAGE: write a comprehensive 3-paragraph post "
    "(Hook, narrative Body, Call to Action) plus 5 hashtags. Minimum 180 words. "
    "Never a single-sentence title."
)

# Starter styles preferred by post type when the orchestrator has not already
# reserved one.  Keys are post-type-suffixed so LONG_CAPTION_IMAGE and
# SMART_BAIT open differently from each other too.
_POST_TYPE_STARTERS: dict[str, tuple[str, ...]] = {
    "LONG_CAPTION_IMAGE": (
        "direct historical statement",
        "specific year + place anchor",
        "provocative question",
        "counter-intuitive understatement",
    ),
    "SMART_BAIT": (
        "startling / shocking claim",
        "provocative question",
        "direct historical statement",
        "first-person on-the-scene narrative",
    ),
    "STANDARD_QUOTE": (
        "direct historical statement",
        "specific year + place anchor",
        "startling / shocking claim",
        "provocative question",
    ),
}


def _default_bank(post_type: str) -> tuple[str, ...]:
    return _POST_TYPE_STARTERS.get((post_type or "").upper().strip(), iter_starter_styles())


def _reserve_starter(state: PipelineState) -> str:
    """Determine and lock the opening style for this slot (thread-safe, deduped)."""
    with _STARTER_LOCK:
        if state.reserved_starter:
            # Already reserved by orchestrator/theme — register it so dupes are caught.
            if state.reserved_starter.lower() not in {s.lower() for s in state.used_starters}:
                state.used_starters.append(state.reserved_starter)
            return state.reserved_starter
        bank = _default_bank(state.post_type)
        state.reserved_starter = pick_reserved_starter(state.used_starters)
        state.used_starters.append(state.reserved_starter)
        return state.reserved_starter


def _starter_directive(state: PipelineState) -> str:
    style = _reserve_starter(state)
    return (
        f"RESERVED OPENING STYLE for this post: {style}\n"
        f"{style_to_instruction(style)}\n\n"
        f"{forbidden_opener_rules_block()}"
    )


class CopywriterAgent(BaseAgent):
    """Writes ``caption`` (+ optional overlay hook) from ``state.theme``."""

    name = "copywriter"

    def __init__(self, *, caption_engine: object | None = None) -> None:
        self._engine = caption_engine

    def run(self, state: PipelineState) -> PipelineState:
        state.last_node = self.name
        self._bind_cost_tracker(state)
        topic = state.theme or state.seed_topic
        if not topic:
            state.record_error(self.name, "No theme on state — cannot write caption.")
            state.caption = ""
            return state

        # Reserve the opening style BEFORE any caption work so both the
        # CaptionEngine path and the LLM fallback share the same slot.
        starter_blk = _starter_directive(state)

        if self._try_caption_engine(state, topic):
            return state

        long_form = (state.post_type or "").upper() == "LONG_CAPTION_IMAGE"
        try:
            if long_form:
                data = complete_json(
                    (
                        f"THEME: {topic}\n"
                        f"GEO ANCHOR: {state.geo_anchor}\n"
                        f"VISUAL HOOK: {state.visual_hook}\n\n"
                        f"{starter_blk}\n\n"
                        "Write a comprehensive 3-paragraph post including a Hook, "
                        "a narrative Body, a Call to Action, and 5 hashtags. "
                        "Minimum 180 words. Return JSON only:\n"
                        "{\n"
                        '  "overlay_text": "",\n'
                        '  "caption": "paragraph 1\\n\\nparagraph 2\\n\\nparagraph 3 + 5 hashtags",\n'
                        '  "pinterest_title": "keyword-rich pin title, max 100 characters",\n'
                        '  "pinterest_description": "two-sentence pin description, no social CTAs"\n'
                        "}\n"
                    ),
                    system=_LONG_SYSTEM,
                )
            else:
                data = complete_json(
                    (
                        f"THEME: {topic}\n"
                        f"GEO ANCHOR: {state.geo_anchor}\n"
                        f"VISUAL HOOK: {state.visual_hook}\n\n"
                        f"{starter_blk}\n\n"
                        "Return JSON only:\n"
                        "{\n"
                        '  "overlay_text": "punchy 8–14 word hook (no hashtags)",\n'
                        '  "caption": "120–180 word investigative caption, 2–3 short paragraphs, '
                        "end with a curiosity CTA (comment/follow). Hedge all theories.\",\n"
                        '  "pinterest_title": "keyword-rich pin title, max 100 characters",\n'
                        '  "pinterest_description": "two-sentence pin description, no social CTAs"\n'
                        "}\n"
                    ),
                    system=_SYSTEM,
                )
            caption = str(data.get("caption") or "").strip()
            overlay = str(data.get("overlay_text") or "").strip()
            state.pinterest_title = str(data.get("pinterest_title") or "").strip()[:100]
            state.pinterest_description = str(data.get("pinterest_description") or "").strip()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("Copywriter LLM failed (%s).", exc)
            state.record_error(self.name, str(exc))
            caption, overlay = "", ""

        if not caption:
            caption = self._fallback_caption(state)
            state.caption_mode = "fallback"
        else:
            state.caption_mode = "agentic"
        caption = self._enforce_starter(state, caption)
        state.caption = caption
        state.overlay_text = overlay
        if not state.pinterest_title:
            state.pinterest_title = (topic or "Ancient Mystery")[:100]
        if not state.pinterest_description:
            state.pinterest_description = caption.split("\n\n")[0][:500]
        _LOG.info("Copywriter | #%s | %d chars", state.post_index + 1, len(caption))
        return state

    @staticmethod
    def _fallback_caption(state: PipelineState) -> str:
        topic = state.theme or state.seed_topic or "Ancient mystery"
        return (
            f"{topic.rstrip('.')}. Some researchers believe the evidence at "
            f"{state.geo_anchor or 'the site'} still has no mainstream explanation. "
            "What do you think really happened? Tell us in the comments.\n\n"
            "#AncientKnowledge #LostCivilizations #Megaliths #OOPArts #HiddenHistory"
        )

    @staticmethod
    def _enforce_starter(state: PipelineState, caption: str) -> str:
        """Strip any forbidden opener and log when the model still used one."""
        hits = has_forbidden_opener(caption)
        cleaned = strip_leading_cliche(caption) if hits else caption
        if hits or cleaned != caption:
            _LOG.warning(
                "Copywriter | #%s | starter cleaned | reserved=%r | removed=%s",
                state.post_index + 1, state.reserved_starter, hits,
            )
        return cleaned

    def _try_caption_engine(self, state: PipelineState, topic: str) -> bool:
        engine = self._engine
        if engine is None:
            return False
        post_type = (state.post_type or "SMART_BAIT").upper()
        starter_blk = self._starter_block_for_engine(state)
        try:
            if post_type == "LONG_CAPTION_IMAGE":
                humanize = getattr(engine, "humanize_long_caption", None)
                if humanize is None:
                    return False
                caption, mode = humanize(
                    topic,
                    page_display_name="Ancient Knowledge",
                    page_niche=(
                        "ancient history, lost civilisations, unexplained archaeology, "
                        "ancient mysteries"
                    ),
                    cta_enabled=True,
                    economic=True,
                    signature="© Ancient Knowledge | by MediaUpScale",
                    opening_style_block=starter_blk,
                )
                if not caption or len(caption.split()) < 40:
                    return False
                caption = self._enforce_starter(state, str(caption).strip())
                state.overlay_text = ""
                state.caption = caption
                state.caption_mode = str(mode or "humanized")
                state.pinterest_title = str(
                    getattr(engine, "last_pinterest_title", "") or topic
                )[:100]
                state.pinterest_description = str(
                    getattr(engine, "last_pinterest_description", "") or caption[:500]
                )
                _LOG.info(
                    "Copywriter | #%s | LONG_CAPTION_IMAGE | engine=%s | %d chars",
                    state.post_index + 1, state.caption_mode, len(state.caption),
                )
                return True

            humanize = getattr(engine, "humanize_smart_bait", None)
            if humanize is None:
                return False
            overlay, caption, mode, visual = humanize(
                topic,
                page_display_name="Ancient Knowledge",
                page_niche=(
                    "ancient history, lost civilisations, unexplained archaeology, "
                    "ancient mysteries"
                ),
                cta_enabled=True,
                economic=True,
                post_type=post_type,
                niche_disclaimer=_DISCLAIMER,
                narrative_mode="investigative",
                batch_angle_block=starter_blk,
            )
            if not (caption or overlay):
                return False
            overlay = str(overlay or "").strip()
            caption = str(caption or overlay or "").strip()
            caption = self._enforce_starter(state, caption)
            state.overlay_text = overlay
            state.caption = caption
            state.caption_mode = str(mode or "humanized")
            if visual and not state.visual_hook:
                state.visual_hook = str(visual).strip()
            state.pinterest_title = str(
                getattr(engine, "last_pinterest_title", "") or topic
            )[:100]
            state.pinterest_description = str(
                getattr(engine, "last_pinterest_description", "") or caption[:500]
            )
            _LOG.info(
                "Copywriter | #%s | engine=%s | %d chars",
                state.post_index + 1, state.caption_mode, len(state.caption),
            )
            return True
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("Copywriter CaptionEngine failed (%s) — LLM fallback.", exc)
            state.record_error(self.name, f"caption_engine: {exc}")
            return False

    def _starter_block_for_engine(self, state: PipelineState) -> str:
        """Return the reserved-starter directive for the CaptionEngine prompt."""
        style = _reserve_starter(state)
        return (
            f"OPENING STYLE REQUIRED (from batch scheduling): {style}\n"
            f"{style_to_instruction(style)}\n\n"
            f"{forbidden_opener_rules_block()}"
        )
