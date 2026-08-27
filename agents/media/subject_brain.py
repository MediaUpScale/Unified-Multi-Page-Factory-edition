# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import config as app_config
from agents.rag.pdf_loader import corpus_to_prompt_context
from agents.media.providers.gemini_utils import generate_text_with_client_chain
from agents.media.persona_dna import persona_context_block

_MAX_CHARS = 120_000


def build_imagine_subject_instruction() -> str:
    persona = persona_context_block()
    return dedent(
        f"""
        You are the editorial strategist for The Holistic Legacy.

        {persona}

        Task: From the excerpted Digital Product corpus (holistic / naturopathic guides), propose ONE concise
        content subject (2-7 words) that:
        - is strongly supported by the excerpts (themes, compounds, modalities actually mentioned),
        - fits Anna's biochemical, non-hype positioning,
        - would resonate on Instagram/Pinterest premium wellness audiences today.

        Output rules:
        - Return ONLY the subject line. No quotation marks. No preamble. One line only.
        """
    ).strip()


def imagine_subject(pdf_bundle: dict[str, str], *, model_id: str | None = None) -> str:
    """Ask Gemini to invent a topic from PDF excerpts + persona (live API call)."""
    key = app_config.GEMINI_API_KEY
    if not key:
        raise ValueError("GEMINI_API_KEY missing; cannot invent subject.")

    ctx = corpus_to_prompt_context(pdf_bundle)
    if len(ctx) > _MAX_CHARS:
        ctx = ctx[:_MAX_CHARS]

    instruction = build_imagine_subject_instruction()
    from agents.media.providers.model_router import text_model as _route_text
    _route = _route_text(
        task="research",
        model_override=model_id,
        preferred=model_id or app_config.GEMINI_RESEARCH_MODEL,
        log=True,
    )
    pref = _route.model_id

    response = generate_text_with_client_chain(
        api_key=key,
        preferred_model=pref,
        contents=[instruction, "\n\nCORPUS BEGIN\n", ctx, "\nCORPUS END"],
    )

    text_attr = getattr(response, "text", None)
    line = text_attr() if callable(text_attr) else text_attr
    if not line:
        parts_out: list[str] = []
        for cand in getattr(response, "candidates", []) or []:
            content = getattr(cand, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", []) or []:
                t = getattr(part, "text", None)
                if t:
                    parts_out.append(t)
        line = "\n".join(parts_out)

    subject = (line or "").strip().splitlines()[0].strip().strip("\"'") if line else ""
    return subject or "Holistic vitality protocol"


def imagine_subject_instruction_preview() -> str:
    """Dry-run scaffold for Gemini subject imagination."""
    return build_imagine_subject_instruction()


# ---------------------------------------------------------------------------
# Bulk topic generator — one Gemini call, n unique topics
# ---------------------------------------------------------------------------

def generate_bulk_topics(
    count: int,
    *,
    page_id: str = "",
    page_niche: str = "",
    model_id: str | None = None,
) -> list[str]:
    """
    Generate ``count`` unique, high-impact topics in a single Gemini call.

    Used for bulk runs (``--quantity > 1``) when no topic is supplied on the
    command line so each variant receives its own fresh, non-repeating subject.

    For ``ancient_knowledge`` the prompt is specialised around alien intervention,
    megalithic mysteries, OOPArts, and lost-civilisation conspiracy angles that
    maximise shares and comments.  For all other pages a generic niche-aware
    prompt is used.

    Returns a deduplicated list of up to ``count`` topics.  Returns ``[]`` on
    any API failure so callers can fall back to the static pool or placeholder.
    """
    import logging as _lg
    import re as _re

    key = app_config.GEMINI_API_KEY
    if not key:
        return []

    _page_lower = page_id.lower()

    if _page_lower == "ancient_knowledge":
        instruction = dedent(
            f"""
            You are the editorial director of the Ancient Knowledge channel — an investigative,
            documentary-style page covering ancient history, hidden civilisations, and unsolved
            mysteries.

            Generate exactly {count} unique, engagement-driving post topics.

            Draw from these content pillars (mix for maximum variety):
            • Ancient civilisations and alleged extraterrestrial or advanced-intelligence involvement
            • Impossible megalithic structures, lost cities, unexplained ancient architecture
            • Out-of-place artefacts (OOPArts): anomalous objects that defy conventional timelines
            • Suppressed discoveries, hidden history, and knowledge buried by mainstream academia
            • Lost civilisations: Atlantis, Tartaria, Lemuria, Mu, pre-diluvian cultures
            • Ancient astronomical alignments, sacred geometry, and encoded mathematical knowledge
            • Ancient genetic manipulation, hybrid beings, and DNA anomalies in the archaeological record

            Rules:
            - Each topic is concise (5-10 words), specific, and immediately intriguing
            - Topics must be DIVERSE — no two topics may cover the same angle or civilisation
            - Favour CONTROVERSIAL, SHOCKING, or MIND-EXPANDING angles that drive shares
            - Return ONLY a numbered list — no preamble, no explanations, no markdown

            Format (exactly):
            1. Topic one here
            2. Topic two here
            """
        ).strip()
    elif _page_lower == "master_mei":
        instruction = dedent(
            f"""
            You are the editorial director of Master Mei — Philosophical Engine for Liberation.
            Generate exactly {count} unique, engagement-driving post topics.

            Each topic MUST lock to ONE primary philosophical pillar (Single-Topic Rule):
            • Pillar A — Trap Beauty & Cheap Dopamine: scrolling, algorithms, hollow allure that drains drive
            • Pillar B — Art of War & Financial Sovereignty: Sun Tzu strategy, focus as capital, fortress wealth
            • Pillar C — Panopticon & Digital Slavery: Bentham/Foucault surveillance, Matrix cables, soft comfort
            • Pillar D — Stoic Hardship & Physical Forge: brutal body/mind/spirit discipline under ash and rain
            Core purpose (immutable): teach resilience of body, mind, spirit, and financial sovereignty —
            escaping digital slavery, cheap dopamine, and trap beauty through uncompromising self-mastery.

            Rules:
            - Each topic is a sharp uncomfortable truth (8-14 words) for ONE pillar only
            - Topics must be DIVERSE — rotate across all four pillars
            - Tone: deeply authoritative, solemn, uncompromising — no wellness fluff
            - Return ONLY a numbered list — no preamble, no explanations, no markdown

            Format (exactly):
            1. Topic one here
            2. Topic two here
            """
        ).strip()
    else:
        instruction = dedent(
            f"""
            Generate exactly {count} unique, high-engagement content topics
            for a social-media page in the "{page_niche or page_id}" niche.

            Rules:
            - Each topic is 5-10 words, specific and interesting
            - Topics must be DIVERSE — cover different angles and sub-topics
            - Do NOT repeat topics across the list
            - Return ONLY a numbered list, one per line — no preamble

            Format (exactly):
            1. Topic one here
            2. Topic two here
            """
        ).strip()

    try:
        from agents.media.providers.model_router import text_model as _route_text
        _route = _route_text(
            task="research",
            model_override=model_id,
            preferred=model_id or app_config.GEMINI_RESEARCH_MODEL,
            log=True,
        )
        pref = _route.model_id
        response = generate_text_with_client_chain(
            api_key=key,
            preferred_model=pref,
            contents=[instruction],
        )
        text_attr = getattr(response, "text", None)
        raw = text_attr() if callable(text_attr) else (text_attr or "")

        topics: list[str] = []
        for line in (raw or "").strip().splitlines():
            line = line.strip()
            clean = _re.sub(r"^\d+[\.\)]\s*", "", line).strip()
            clean = _re.sub(r"^[-*•]\s*", "", clean).strip()
            if clean and len(clean) > 3:
                topics.append(clean)

        # Deduplicate preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for t in topics:
            if t.lower() not in seen:
                seen.add(t.lower())
                unique.append(t)

        _lg.getLogger(__name__).info(
            "generate_bulk_topics | page=%s count=%d generated=%d",
            page_id, count, len(unique),
        )
        return unique[:count]

    except Exception as exc:  # noqa: BLE001
        _lg.getLogger(__name__).warning("generate_bulk_topics failed: %s", exc)
        return []
