# -*- coding: utf-8 -*-
from __future__ import annotations

import random
import sys
import logging
from pathlib import Path
from textwrap import dedent

from anthropic import Anthropic
from google import genai

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import config as app_config
from avatar_engine.knowledge.pdf_loader import corpus_to_prompt_context
from avatar_engine.providers.gemini_utils import (
    build_model_chain,
    chain_with_preferred_first,
    generate_content_with_model_fallback,
    get_active_model_id,
    make_gemini_client_with_fallback,
)
from .persona_dna import (
    BATCH_DEFAULT_SIZE,
    BATCH_DELIMITER_OPEN,
    BATCH_ROTATION_PATTERN,
    BATCH_WORDS_PER_NARRATIVE,
    CTA_VOICE_INSTRUCTION,
    NARRATIVE_ANGLES,
    contextual_cta_keyword,
    persona_context_block,
)

logger = logging.getLogger(__name__)

_MAX_PROMPT_CHARS = 120_000

# ---------------------------------------------------------------------------
# Caption format: 60 % short (300-500 ch), 40 % long (500-800 ch)
# ---------------------------------------------------------------------------

_SHORT_FORM_CHANCE = 0.60


def _build_voice_rules(cta_enabled: bool = True) -> str:
    """Return the VOICE RULES block, conditionally including or suppressing the CTA line."""
    ending = (
        "- End with a decisive CTA embedding the provided keyword.\n"
        if cta_enabled
        else (
            "- End with a strong, conclusive statement. "
            "Do NOT include any call-to-action, comment links, or DM invitations.\n"
        )
    )
    return (
        "VOICE RULES (non-negotiable):\n"
        '- Style: "Dica de Dona de Casa" (Housewife Tip). Warm, personal, clinically grounded.\n'
        "- ABSOLUTELY NO headers, bold lists, bullet points, or fact-sheet labels.\n"
        "- No AI-sales words: Unlock / Dive / Elevate / Game-changer / Discover / Harness / Revolutionary.\n"
        "- Biochemically accurate, spoken like a trusted neighbour not a textbook.\n"
        "- Begin with a compelling conversational hook (never start with 'Did you know...').\n"
        + ending
        + "- Output ONLY the caption text -- no preamble, no labels, no markdown."
    )

_SHORT_FORMAT = (
    "FORMAT: SHORT FORM -- one punchy paragraph, 350-450 characters.\n"
    "Focus on a single golden tip. Dense, specific, zero filler."
)

_LONG_FORMAT = (
    "FORMAT: LONG FORM -- 3-4 short paragraphs, 550-750 characters.\n"
    "Tell a micro-story or frame 'The Legacy' context before delivering the core tip.\n"
    "Each paragraph must carry its own weight -- no padding or repetition."
)


def _pick_format_rules(cta_enabled: bool = True) -> str:
    """Return voice + format block, randomly short (60 %) or long (40 %)."""
    voice = _build_voice_rules(cta_enabled)
    fmt = _SHORT_FORMAT if random.random() < _SHORT_FORM_CHANCE else _LONG_FORMAT
    return f"{voice}\n\n{fmt}"


# ---------------------------------------------------------------------------
# Variant helpers
# ---------------------------------------------------------------------------

def _variant_suffix(variation_index: int, total_variants: int) -> str:
    if total_variants <= 1:
        return ""
    return dedent(
        f"""
        Variation mandate: Creative variant #{variation_index + 1} of {total_variants}.
        Deliver a visibly different opening emphasis from other variants yet keep facts faithful to the FACT SHEET.
        """
    ).strip()


# ---------------------------------------------------------------------------
# Smart Bait image-theme heuristic (keyword fallback)
# ---------------------------------------------------------------------------

def _keyword_image_theme(text: str) -> str:
    """
    Derive a vivid, bright visual atmosphere string from SMART_BAIT overlay text
    using keyword matching.  Used as a zero-cost fallback when no LLM is available.

    All descriptions are intentionally BRIGHT and VIVID to pass platform quality
    filters (no uniformly dark / murky backgrounds).
    """
    t = text.lower()

    _dark_fire = ("fire", "burn", "flame", "ember", "smoke", "hell", "ash", "blaze")
    _anger_toxic = ("anger", "rage", "toxic", "destroy", "explode", "hate", "poison")
    _heartbreak = ("ex ", " ex,", "heartbreak", "heartbroken", "divorce", "breakup",
                   "break up", "goodbye", "left me", "cheated", "betrayal")
    _pets = ("cat", "dog", "kitten", "puppy", "pet", "fur baby", "feline", "meow")
    _money = ("money", "rich", "wealth", "finance", "broke", "salary", "cash",
              "invest", "stock", "afford")
    _health = ("habit", "health", "diet", "lose weight", "gym", "workout",
               "exercise", "sleep", "calorie", "nutrition")
    _dark_humor = ("put them out", "would you do", "honest", "dark", "embarrassing",
                   "secret", "admit", "confess", "worst")

    if any(w in t for w in _dark_fire):
        return (
            "Dynamic outdoor bonfire scene with vivid orange and amber flames reaching upward. "
            "Rich warm glow illuminating the surroundings, vibrant colours, "
            "high contrast with a deep-blue twilight sky. Photographic, cinematic, no people."
        )
    if any(w in t for w in _heartbreak):
        return (
            "Vibrant sunset sky with rich gold, orange, and magenta tones. "
            "Solitary figure silhouetted on a beach or hilltop, dramatic colour palette, "
            "emotionally resonant. Bright and vivid, photographic quality."
        )
    if any(w in t for w in _anger_toxic):
        return (
            "Dramatic split-light scene: one side vibrant warm gold, the other cool electric blue. "
            "Cracked or fractured surface with intense colour contrast. "
            "Cinematic, vivid, high energy. No people."
        )
    if any(w in t for w in _pets):
        return (
            "Bright warm domestic interior flooded with soft golden afternoon sunlight. "
            "Plush textures, warm tones, shallow depth of field, cozy and inviting. "
            "Photographic quality, lifestyle imagery."
        )
    if any(w in t for w in _money):
        return (
            "Sleek modern interior with abundant natural light. Clean marble or polished "
            "surfaces, soft gold accents, high-end minimal aesthetic. "
            "Bright, aspirational, photographic quality."
        )
    if any(w in t for w in _health):
        return (
            "Bright airy morning scene. Sunlit kitchen counter with fresh fruit, "
            "clean wooden surfaces, warm natural light, calm and energised atmosphere. "
            "Photographic quality, lifestyle photography."
        )
    if any(w in t for w in _dark_humor):
        return (
            "Dramatic cinematic scene with vivid contrast: bold primary colours, "
            "expressive lighting, dynamic composition. "
            "Bright and striking, editorial photography quality."
        )
    # Generic fallback: vivid + bright
    return (
        "Vibrant cinematic scene with rich colours and dynamic lighting. "
        "High-quality photographic composition, bright and visually striking. "
        "No people, abstract or environmental."
    )


# ---------------------------------------------------------------------------
# Researcher prompt (kept intact -- produces structured fact sheet)
# ---------------------------------------------------------------------------

def build_gemini_researcher_instruction(topic: str, *, variation_notes: str = "") -> str:
    persona = persona_context_block()
    return dedent(
        f"""
        You are the Research Desk for `{topic}` at The Holistic Legacy.

        {persona}
        {variation_notes}

        Constraints:
        - Read ONLY what is verbatim in SOURCE FILE excerpts.
        - If the excerpts lack data, declare missing evidence explicitly rather than hallucinating.
        - Pull precise biochemical language (substrates, pathways, enzyme names) when quoted in sources.

        Produce a RAW FACT SHEET with these sections exactly:
        1. Verified Mechanisms (bullets quoting page hints / filenames).
        2. Safety Flags & Contraindications (explicit if absent).
        3. Protocol Notes From Guides (timing, dosing, cautions-if-stated-only).
        4. Monetization Spine: Identify the SINGLE best Payhip link / offer string present in excerpts.
           If ambiguous, enumerate candidate strings with rationale.
        5. Confidence Statement (High/Medium/Low) plus gaps.

        Keep tone clinical; this is downstream for an editor persona.
        """
    ).strip()


def build_batch_researcher_instruction(topic: str, num_variants: int) -> str:
    """
    Build a single-call prompt that asks Gemini to produce `num_variants`
    distinct research narratives in one response (the 'One-Call Rule').

    Each narrative is separated by the delimiter ===NARRATIVE_N=== so the
    response can be parsed into individual fact-sheets without extra API calls.
    """
    persona = persona_context_block()

    angle_lines = "\n".join(
        f"  - {a.get('code', '')}: {a.get('description', '')}"
        for a in NARRATIVE_ANGLES
    ) if NARRATIVE_ANGLES else (
        "  - SCIENTIFIC: Biochemical mechanism\n"
        "  - LEGACY: Ancestral wisdom story\n"
        "  - EXPOSE: Why this is suppressed"
    )

    rotation_note = BATCH_ROTATION_PATTERN or (
        "Rotate through SCIENTIFIC, LEGACY, EXPOSE angles in order. "
        "No two consecutive narratives may share the same angle."
    )

    example_delimiter = f"{BATCH_DELIMITER_OPEN}1==="

    return dedent(
        f"""
        You are the Research Desk for The Holistic Legacy. Your task is to generate
        {num_variants} DISTINCT research narratives about the topic: `{topic}`.

        {persona}

        BATCH RULES (non-negotiable):
        - Generate EXACTLY {num_variants} narratives, numbered 1 through {num_variants}.
        - Each narrative must be {BATCH_WORDS_PER_NARRATIVE} words.
        - Separate each narrative with EXACTLY this delimiter: {example_delimiter}
          (use the correct number for each, e.g. ===NARRATIVE_1===, ===NARRATIVE_2===, etc.)
        - Do NOT include any text before ===NARRATIVE_1=== or after the last narrative.

        NARRATIVE ANGLES -- rotate through these in order across the batch:
        {angle_lines}

        ROTATION RULE: {rotation_note}

        CONTENT RULES:
        - Pull facts ONLY from the PDF corpus below. Do not hallucinate.
        - Include precise biochemical language when quoted in sources.
        - Each narrative must have a unique opening sentence -- no repeated hooks.
        - Identify any Payhip link / offer present in excerpts; embed in narrative 1 only.
        - Never reference author names that are on the banned list from the persona block above.
        - Contrast natural mechanisms against expensive synthetic alternatives where relevant.

        OUTPUT FORMAT (start immediately with ===NARRATIVE_1===, nothing before it):
        ===NARRATIVE_1===
        [narrative content here]
        ===NARRATIVE_2===
        [narrative content here]
        ... continue through ===NARRATIVE_{num_variants}===
        """
    ).strip()


def _parse_batch_narratives(raw_response: str, num_variants: int) -> list[str]:
    """
    Split a batch research response into individual narrative strings.

    Expects delimiters in the form ===NARRATIVE_N=== produced by Gemini.
    Returns a list of `num_variants` strings; pads with empty strings if
    fewer narratives were returned than expected.
    """
    import re as _re
    parts = _re.split(r"===NARRATIVE_\d+===", raw_response)
    # parts[0] is text before the first delimiter (should be empty / preamble)
    narratives = [p.strip() for p in parts[1:] if p.strip()]
    # Pad to the requested count so callers can safely index
    while len(narratives) < num_variants:
        narratives.append("")
    return narratives[:num_variants]


# ---------------------------------------------------------------------------
# Humanizer prompts (Dica de Dona de Casa voice + variable length)
# ---------------------------------------------------------------------------

def build_claude_humanizer_system_prompt(topic_brand: str = "Anna") -> str:
    return f"You are {topic_brand}. {persona_context_block()}"


def build_claude_humanizer_user_prompt(
    topic: str,
    raw_fact_sheet: str,
    *,
    variation_index: int = 0,
    total_variants: int = 1,
    cta_keyword: str | None = None,
    cta_enabled: bool = True,
) -> str:
    format_rules = _pick_format_rules(cta_enabled)
    suffix = _variant_suffix(variation_index, total_variants)
    tail = f"\n\nTopic focus: `{topic}`\n\nFACT SHEET:\n```\n{raw_fact_sheet}\n```"
    if cta_enabled:
        kw = cta_keyword or contextual_cta_keyword(topic)
        cta_instruction = CTA_VOICE_INSTRUCTION or (
            f"Weave 'Comment {kw}' naturally into the caption as a personal DM invitation."
        )
        body = (
            f"{format_rules}\n"
            f"CTA keyword for this caption: {kw}\n"
            f"CTA instruction: {cta_instruction}\n"
            "Include the Payhip URL verbatim if present in FACT SHEET; omit if absent."
        )
    else:
        body = (
            f"{format_rules}\n"
            "Include the Payhip URL verbatim if present in FACT SHEET; omit if absent."
        )
    return (body + f"\n\n{suffix}" + tail) if suffix else (body + tail)


def build_gemini_humanizer_instruction(
    topic: str,
    raw_fact_sheet: str,
    *,
    variation_index: int = 0,
    total_variants: int = 1,
    cta_keyword: str | None = None,
    cta_enabled: bool = True,
) -> str:
    persona = persona_context_block()
    format_rules = _pick_format_rules(cta_enabled)
    suffix = _variant_suffix(variation_index, total_variants)
    if cta_enabled:
        kw = cta_keyword or contextual_cta_keyword(topic)
        cta_instruction = CTA_VOICE_INSTRUCTION or (
            f"Weave 'Comment {kw}' naturally into the caption as a personal DM invitation."
        )
        cta_block = f"CTA keyword for this caption: {kw}\nCTA instruction: {cta_instruction}"
    else:
        cta_block = (
            "CTA SUPPRESSED: Omit all comment links and DM invitations. "
            "End strongly without requesting any action."
        )
    return dedent(
        f"""
        {persona}

        {format_rules}
        {cta_block}

        {suffix}

        Topic focus: `{topic}`

        FACT SHEET:
        ```
        {raw_fact_sheet}
        ```

        Include the Payhip URL verbatim if present in FACT SHEET. Omit if absent.
        Output ONLY the caption -- no labels, no markdown.
        """
    ).strip()


def humanizer_preview_with_placeholder(topic: str) -> tuple[str, str]:
    placeholder = "[DYNAMIC: RAW FACT SHEET FROM GEMINI RESEARCHER WOULD FOLLOW]"
    system = build_claude_humanizer_system_prompt("Anna")
    user = build_claude_humanizer_user_prompt(topic, placeholder)
    return system, user


def economic_humanizer_instruction_preview(topic: str) -> str:
    return build_gemini_humanizer_instruction(topic, "[FACT SHEET PLACEHOLDER]")


# ---------------------------------------------------------------------------
# SMART_BAIT: engagement-bait overlay + brief caption prompt builder
# ---------------------------------------------------------------------------

def build_smart_bait_prompt(
    topic: str,
    page_display_name: str,
    page_niche: str,
    persona_block: str,
    *,
    cta_enabled: bool = True,
) -> str:
    """
    Build a single LLM prompt that returns both:
      OVERLAY — ultra-short image overlay question/statement (max ~12 words)
      CAPTION — brief, persona-voiced paragraph with dark/sarcastic/witty tone

    The overlay is designed for immediate comment engagement (not educational).
    The caption complements it with personality, not exposition.
    """
    cta_note = (
        "CAPTION CLOSE: End with a subtle, natural engagement hook or DM invite (one short line)."
        if cta_enabled
        else "CAPTION CLOSE: End cleanly. Do NOT include any comment links, DM invitations, or call-to-actions."
    )
    return dedent(
        f"""
        You are creating a hyper-viral social media engagement post for: {page_display_name}
        Content niche: {page_niche or topic}

        {persona_block}

        TASK — produce exactly two parts, formatted as shown below.

        ─── PART 1: OVERLAY TEXT (displayed ON the image) ───
        Rules:
        - SINGLE sentence or question. ABSOLUTE MAXIMUM: 12 words.
        - Must trigger an INSTANT emotional, nostalgic, curiosity, or self-reflection reaction.
        - Conversational, direct, punchy. No fluff. No hedging. No corporate language.
        - Designed to explode a comment section — not to sell, not to educate.
        - Topic context (interpret loosely, do NOT copy): {topic}

        ENGAGEMENT ARCHITECTURE — model your output's ENERGY and STRUCTURE after these
        high-performing reference hooks (do NOT reproduce them verbatim):
          1. "Name one thing destroying the world right now?"
          2. "Be honest, your cat probably has 15 different names, what are they?"
          3. "Old school name for a cigarette?"
          4. "What healthy habit helped you lose weight faster than expected?"
          5. "Are you still friends with your high school bestie??"

        Study what makes these work: they are SHORT, DIRECT, personal, slightly provocative,
        and demand a specific, personal answer. Replicate that energy — not those words.

        ─── PART 2: CAPTION (post body text) ───
        Rules:
        - EXACTLY 1 SENTENCE. Hard limit. No exceptions.
        - No educational content, no fact sheets, no lists, no headers.
        - Tone: safe-but-dark, dry wit, wry sarcasm, or self-aware humour — tightly tuned to persona.
        - Do NOT restate, explain, or reference the overlay question.
        - Must sound like a real human — slightly unfiltered, never corporate, never polished.
        {cta_note}

        ─── OUTPUT FORMAT — respond with ONLY valid JSON, nothing else ───
        {{
          "quote_text": "A completely new, unique, provocative relationship psychological bait hook text. Never repeat previous formats.",
          "visual_subject": "An intense interaction between exactly one man and one woman. VISUAL CONCEPT COMPOSITION RULES: Only ONE character may exhibit a surreal transformation or psychological deception element — never both simultaneously. One partner must always remain a completely normal, emotionally vulnerable human to ground the scene. THE TARGET: If the hook addresses toxic female behavior, keep the man fully human and depict the woman peeling away a smiling face-mask or casting a shadow with a subtle surreal alteration. If the hook addresses toxic male behavior, keep the woman fully human and apply the surreal mask or shadow alteration exclusively to the man. Background must be minimal and dark — no floating specters, no extra monsters, no screaming figures. The two characters must fill the entire canvas naturally with no heavy framing elements."
        }}

        Return ONLY the JSON object. No extra text, no markdown fences, no explanation.
        """
    ).strip()


# ---------------------------------------------------------------------------
# ECONOMIC_REEL: dedicated narration prompt builder
# ---------------------------------------------------------------------------

_REEL_PSYCH_ANGLES: list[str] = [
    "attachment shadow — the unconscious emotional wound that drives partner selection",
    "boundary collapse — why saying yes when you mean no slowly destroys self-respect",
    "codependency metric — measuring how much of your identity lives inside another person",
    "intermittent reinforcement — why unpredictable love feels more addictive than consistent love",
    "anxious protest behaviour — the pursuing patterns that push avoidant partners further away",
    "emotional outsourcing — the habit of using a relationship to regulate your own nervous system",
    "love-bombing aftermath — what happens to your nervous system after the intensity disappears",
    "self-abandonment cycle — the small daily betrayals of your own needs to keep someone comfortable",
    "fantasy bonding — falling in love with who someone could be, not who they actually are",
    "hypervigilance in intimacy — scanning for threat in relationships that feel too safe to trust",
    "emotional debt — the invisible ledger we keep of unreciprocated vulnerability",
    "avoidant protective shell — why the most emotionally unavailable people crave depth the most",
    "inner child interference — when your 8-year-old self is choosing your adult partners",
    "identity enmeshment — losing the line between where you end and your partner begins",
    "trauma loyalty — staying with someone harmful because chaos feels like home",
]

# Investigation angles for ancient history / documentary channels
_REEL_INVESTIGATION_ANGLES: list[str] = [
    "suppressed evidence — findings that mainstream institutions refuse to acknowledge",
    "temporal impossibility — objects or structures that should not exist in that era",
    "convergent mythology — the same story told by civilisations that never met",
    "advanced precision — engineering tolerances modern tools can barely replicate today",
    "deliberate erasure — records that were systematically destroyed after discovery",
    "hidden in plain sight — monuments whose real meaning was encoded in plain view",
    "forbidden chronology — dating that pushes human sophistication back thousands of years",
    "cosmic alignment — structures precisely calibrated to astronomical events",
    "lost transmission — knowledge that vanished with a civilisation and has never been recovered",
    "unanswered silence — why official bodies refuse to comment on this specific discovery",
]


def build_reel_narration_prompt(
    topic: str,
    page_display_name: str,
    page_niche: str,
    persona_block: str,
    engagement_bait_examples: str = "",
    niche_disclaimer: str = "",
) -> str:
    """
    Build the user-side LLM prompt for ECONOMIC_REEL narration generation.

    Asks for a 4-sentence, 65-80 word voiceover script plus a short on-screen
    hook headline and a scene description — all as a single JSON object.

    When ``niche_disclaimer`` is supplied (e.g. for ancient_knowledge) the
    function switches to an investigative/documentary sentence structure instead
    of the default relationship-psychology structure, and injects the disclaimer
    so the LLM never presents theories as facts.
    """
    import random as _rnd_angle

    _is_investigative = bool(niche_disclaimer)

    if _is_investigative:
        _angle = _rnd_angle.choice(_REEL_INVESTIGATION_ANGLES)
    else:
        _angle = _rnd_angle.choice(_REEL_PSYCH_ANGLES)

    _bait_block = ""
    if engagement_bait_examples:
        _bait_block = dedent(f"""
        ── VIRAL ENGAGEMENT BAIT REFERENCE (from '@REDES REF SOURCE FONTE.xlsx') ──
        Study the psychological structure of these high-engagement examples.
        DO NOT copy or paraphrase any sentence, phrase, or hook from this list.
        Instead, identify the raw emotional friction, the provocative tension, and
        the 'bait' mechanism that drives massive audience engagement — then apply
        THAT same structural energy to a completely original script.
        DO NOT use generic openers like "Have you ever stayed in" or
        "Have you ever found yourself". Start with a direct psychological gut-punch
        inspired by the engagement patterns below, tailored to the new theme.

        REFERENCE EXAMPLES:
{engagement_bait_examples}
        ────────────────────────────────────────────────────────────────────────
        """).strip()

    _disclaimer_block = (
        f"\n        ── MANDATORY CHANNEL DISCLAIMER ──\n        {niche_disclaimer}\n"
        if niche_disclaimer else ""
    )

    if _is_investigative:
        # ── INVESTIGATIVE / DOCUMENTARY niche structure (ancient_knowledge etc.) ──
        return dedent(
            f"""
            You are writing voice narration for a short-form investigative documentary reel
            for the page: {page_display_name}
            Content niche: {page_niche or topic}

            {persona_block}
            {_disclaimer_block}
            TOPIC CONTEXT — STRICT THEME MANDATE: {topic}

            ── CRITICAL THEME COMPLIANCE RULE ──
            The topic of this script MUST be strictly about "{topic}".
            NEVER import relationship advice, psychology therapy, or romantic content.
            Every sentence must be exclusively about ancient history, mysteries, or the conspiracy described.

            ── MANDATORY INVESTIGATION ANGLE (use this as your narrative lens) ──
            Explore this specific concept: {_angle}
            Weave it naturally into the narration without naming it directly.

            ─── YOUR TASK ───
            Produce a JSON object with exactly three fields:

            1. "image_text_overlay"
               A single provocative hook question or statement — maximum 12 words.
               This text is burned visually onto the video frame as the headline.
               Make it short, mysterious, awe-inspiring — designed to stop a scroll instantly.
               Example patterns: "What if history has been hiding this for centuries?"
               or "This discovery changes everything we thought we knew."

            2. "caption_body"
               A FULL 4-SENTENCE voiceover narration script for text-to-speech, 65–80 words total.
               Follow this EXACT sentence structure — order is strict:

               Sentence 1 (HOOK): An immediate attention-grabbing statement about the mystery.
                        Never start with "Have you ever stayed in a relationship".
                        Open with a striking historical fact, impossible detail, or forbidden question.

               Sentence 2 (REVELATION): Present the key evidence, theory, or discovery.
                        Use language like "some researchers believe", "ancient records suggest",
                        "according to legend" — never present as established fact.

               Sentence 3 (IMPLICATION): Explain what this means — why it matters, what it challenges.

               Sentence 4 (CTA): Invite viewers to share their theory or follow for more discoveries.

               Write at a measured, documentary spoken pace. No bullet points. No headers. Plain prose only.

            3. "visual_subject"
               A precise cinematic description: ancient ruins, artefacts, stone carvings, or
               historical environment relevant to the topic. Dramatic lighting, epic scale,
               no human relationship dynamics, no domestic settings.

            ─── OUTPUT FORMAT ───
            Return ONLY valid JSON. No markdown fences, no explanation.
            {{
              "image_text_overlay": "...",
              "caption_body": "...",
              "visual_subject": "..."
            }}
            {_bait_block}
            """
        ).strip()
    else:
        # ── RELATIONSHIP PSYCHOLOGY niche structure (wonder_feed etc.) ──
        return dedent(
            f"""
            You are writing voice narration for a 30-second vertical relationship psychology reel
            for the page: {page_display_name}
            Content niche: {page_niche or topic}

            {persona_block}

            TOPIC CONTEXT — STRICT THEME MANDATE: {topic}

            ── CRITICAL THEME COMPLIANCE RULE ──
            The topic of this script MUST be strictly about "{topic}".
            The spreadsheet reference data (ENGAGEMENT_BAIT_EXAMPLES below) is provided
            ONLY as an engagement blueprint — use it to understand how to structure a
            high-retention psychological cliffhanger and what makes a hook stop a scroll.
            Do NOT adopt the exact relationship trauma topics or phrasing from the spreadsheet
            unless they directly and naturally fit into "{topic}".
            The narrative must stay on-theme, original, and emotionally resonant with "{topic}".

            ── MANDATORY PSYCHOLOGICAL ANGLE (use this as your unique lens — do NOT ignore it) ──
            Explore this specific concept in your narration: {_angle}
            Your script must approach the topic THROUGH this lens. Do not name the concept
            directly — weave its emotional truth into the narrative naturally.

            ─── YOUR TASK ───
            Produce a JSON object with exactly three fields:

            1. "image_text_overlay"
               A single psychological hook question or statement — maximum 12 words.
               This text is burned visually onto the video frame as the headline.
               Make it short, emotionally charged, direct — designed to stop a scroll instantly.

            2. "caption_body"
               A FULL 4-SENTENCE voiceover narration script for text-to-speech, 65–80 words total.
               Follow this EXACT sentence structure — order is strict:

                    Sentence 1 (AUDIENCE WARM-UP — comes FIRST): Ask the viewer directly about their
                              own past relationship experience. Example pattern: "Have you ever stayed
                              in a relationship long after you knew it was over — and couldn't explain
                              why?" This must feel personal and conversational, NOT analytical.

                    Sentence 2 (PSYCHOLOGICAL TRUTH): Reveal the deep psychological reason — why
                              people repeat this pattern or tolerate it far longer than they should.

                    Sentence 3 (ACTIONABLE INSIGHT): Offer one specific emotional-intelligence
                              realization the viewer can apply to their life immediately.

                    Sentence 4 (WARM CTA): Invite viewers to share their own experience in the comments
                              in a warm, human, non-pushy way.

               Write at a natural, warm spoken pace. No bullet points. No headers. Plain prose only.

            3. "visual_subject"
               A precise graphite scene: exactly one man and one woman in a psychologically tense
               interaction. Only ONE character may show a surreal transformation. Minimal dark
               background, no heavy furniture or interior framing elements.

            ─── OUTPUT FORMAT ───
            Return ONLY valid JSON. No markdown fences, no explanation.
            {{
              "image_text_overlay": "...",
              "caption_body": "...",
              "visual_subject": "..."
            }}
            {_bait_block}
            """
        ).strip()


# ---------------------------------------------------------------------------
# SMART_BAIT: image generation prompt builder (toggle-based)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# BIOMECHANICAL HORROR MUTATION ENGINE
# Subtly injects one unsettling directive into the visual_subject description
# on a randomised 35% trigger — never the focal point, always woven naturally
# into the charcoal composition so it reads as psychological unease, not gore.
# ---------------------------------------------------------------------------
_HORROR_DIRECTIVES: list[str] = [
    # --- Porcelain mask directives (priority visual — user-specified) ---
    "a smooth, expressionless theatrical porcelain mask seamlessly covering the lower half of the face, "
    "its surface cracking into fine hairline fractures that scatter like ash at the edges",
    "a pristine white porcelain mask held delicately over one character's face with both hands, "
    "the mask's hollow eye sockets revealing only deep charcoal darkness beneath",
    "a porcelain doll mask crumbling at the jaw line — fragments dissolving into fine grey ash "
    "that drifts downward through the composition like quiet dissipation",
    "an expressionless theatre mask partially fused to the skin, the seam between porcelain and "
    "flesh rendered in exquisite graphite cross-hatching — neither fully human nor fully object",
    # --- Snake tongue directives (priority visual — user-specified) ---
    "a faint, split serpent tongue emerging subtly at the corner of the character's barely-parted lips — "
    "elongated and slithering, rendered in fine charcoal lines as a secondary compositional element",
    "twin forked snake tongues barely visible between the couple's lips, "
    "drawn in delicate graphite detail as though caught mid-flicker in a single held breath",
    # --- Classic biomechanical / psychological horror directives ---
    "a hidden serpent tongue subtly visible at the corner of the character's mouth",
    "subtle biomechanical skin fusions where organic tissue melds with dark metallic lattice along the jaw",
    "a hollow, hyper-detailed expressive mask layer half-peeled from the face revealing a dark void beneath",
    "meat-based intricate physics blending with charcoal lines — fine anatomical displacement visible under skin",
    "a faint secondary skeletal silhouette visible through translucent skin like an X-ray ghost overlay",
    "delicate dark tendrils of shadow emerging almost imperceptibly from the character's fingertips",
    "an almost invisible insect limb partially folded beneath the character's collar, caught mid-motion",
    "the character's eye socket geometry subtly elongated into an inhuman dark void — barely perceptible",
    "thin wire suture lines crossing the cheekbones as if skin has been carefully reattached",
    "a secondary reflected face in the character's iris that does not match the angle of the scene",
]


def maybe_inject_horror_mutation(
    visual_subject: str,
    probability: float = 0.35,
) -> str:
    """
    On a random roll <= `probability`, seamlessly appends one subtle biomechanical
    horror directive into the scene description for the character sketch.

    The directive is framed as a secondary composition note so the LLM integrates
    it naturally into the charcoal rendering without making it the focal point.
    Safe no-op when the roll misses or visual_subject is empty.
    """
    if not visual_subject or random.random() > probability:
        return visual_subject
    directive = random.choice(_HORROR_DIRECTIVES)
    return (
        f"{visual_subject} SUBTLE BIOMECHANICAL HORROR ELEMENT — integrate naturally "
        f"into the charcoal composition as a secondary detail, NOT as the focal point: "
        f"{directive}."
    )


def build_smart_bait_image_prompt(
    visual_subject: str,
    base_graphite_prompt: str,
    *,
    # All toggle params kept for call-site compatibility but are intentionally ignored.
    # A single permanent base style is always used regardless of format.
    enable_sketch_style: bool = True,
    enable_horror_transformations: bool = False,
    raw_graphite_horror_prompt: str = "",
    use_style_reference: bool = True,
    style_characters: str = "",
    sketch_style_prompt: str = "",
) -> str:
    """
    Returns the locked base graphite style prompt + scene concept.
    Horror mutation (if any) must be injected into visual_subject BEFORE calling
    this function via maybe_inject_horror_mutation().
    """
    base = base_graphite_prompt or sketch_style_prompt
    if visual_subject:
        return f"{base} Original scene concept: {visual_subject}."
    return base


# ---------------------------------------------------------------------------
# LONG_CAPTION_IMAGE: deep long-form storytelling prompt builder
# ---------------------------------------------------------------------------

def build_long_caption_prompt(
    topic: str,
    page_display_name: str,
    page_niche: str,
    persona_block: str,
    *,
    cta_enabled: bool = True,
) -> str:
    """
    Build a long-form LLM prompt that produces a profound, storytelling-style
    caption about relationship dynamics, modern marriage, and emotional character.

    The output must NOT contain headers, bullet points, or structured lists.
    It must read as a seamless, gripping narrative in short impactful paragraphs,
    modelled on the two structural archetypes below.
    """
    cta_close = (
        "\n\nEnd the caption with the exact line: © Wonder Feed | by MediaUpScale"
        if cta_enabled
        else "\n\nEnd the caption with the exact line: © Wonder Feed | by MediaUpScale"
    )
    return dedent(
        f"""
        You are crafting a long-form, deeply engaging Facebook caption for the page: {page_display_name}
        Page niche: {page_niche or topic}

        {persona_block}

        TOPIC SEED (interpret freely — do not copy verbatim): {topic}

        ─── STRUCTURAL REFERENCE ARCHETYPES ───
        Study the TONE, PACING, and PARAGRAPH STRUCTURE of these two exemplar formats.
        Do NOT reproduce them. Capture their energy:

        ARCHETYPE 1 — Slow-burn cautionary narrative:
        "A disrespectful woman does not destroy a home in one day. She destroys it slowly.
        First, it starts with the small things — the eye rolls, the dismissive tone, the way
        she speaks to him in front of others.
        Then the children start to notice. Then the respect disappears. Then so does he.
        Not every man leaves loudly. Some men just... stop trying.
        They stop bringing home flowers. Stop sharing their day. Stop fighting for the marriage.
        Because there comes a point when a man realizes the woman beside him doesn't see his worth.
        And a man who doesn't feel valued at home will eventually find peace somewhere else.
        Not in another woman. In silence. In distance. In work. In anything that doesn't
        remind him that he is not enough in his own house.
        Character outlasts beauty. Respect outlasts passion. A home built on dignity
        survives what a home built on appearance never can."

        ARCHETYPE 2 — Analytical cautionary tone:
        "The Most Dangerous Woman in a Man's Life Is Not Always the One He Fears.
        She doesn't arrive with red flags. She arrives with warmth, with beauty, with a laugh
        that makes you forget your last wound.
        But over time — quietly — she teaches you that your feelings are inconvenient.
        That your boundaries are negotiable. That needing respect means you're insecure.
        And the most dangerous part? You don't see it happening.
        By the time you do, you've already changed. You argue less. Agree more. Shrink.
        Not because she demanded it. But because you got tired of the cost of standing tall.
        The most dangerous woman is the one who makes you disappear while making you feel loved."

        ─── YOUR TASK ───
        Write a NEW original long-form caption in the SAME profound, analytical, cautionary style.
        - 6 to 10 short, impactful paragraphs.
        - No headers, no lists, no bullet points.
        - Topic must relate to: {topic}
        - Tone: deep, honest, slightly sobering — like wisdom from someone who's lived it.
        - Each paragraph must be 1–4 sentences. Use white space between paragraphs.
        - DO NOT mention the page name inside the text body.
        - DO NOT use generic motivational phrases like "you deserve better" or "love yourself first".
        - Sound like a real person sharing hard-earned perspective, not a coach selling a course.{cta_close}

        Output ONLY the caption text. No intro, no outro, no labels, no markdown.
        """
    ).strip()


# ---------------------------------------------------------------------------
# CaptionEngine
# ---------------------------------------------------------------------------

class CaptionEngine:
    """Dual-LLM relay (Gemini research -> Claude polish) or economic single-Gemini path."""

    def __init__(
        self,
        *,
        gemini_key: str | None = None,
        anthropic_key: str | None = None,
        research_model: str | None = None,
        writer_model: str | None = None,
    ) -> None:
        g_key = gemini_key or app_config.GEMINI_API_KEY
        a_key = anthropic_key or app_config.ANTHROPIC_API_KEY
        if not g_key:
            raise ValueError("Gemini API key missing. Set GEMINI_API_KEY.")
        # v1beta -> v1 fallback; SDK sends x-goog-api-key header automatically.
        self._gemini = make_gemini_client_with_fallback(g_key)

        # Handshake: validate research model against live models.list().
        research_hint = research_model or app_config.GEMINI_RESEARCH_MODEL
        # Ordered preference: 2.5-flash (fastest), then 2.5-pro as first fallback.
        active_research = get_active_model_id(
            self._gemini,
            preference=["2.5-flash", "2.5-pro"],
            capability_type="text",
        )
        self._text_model_chain = build_model_chain(
            self._gemini,
            capability_type="text",
            preferred=research_hint,
        )
        # If the env-hinted model is not live, use the handshake result.
        if not self._text_model_chain or self._text_model_chain[0] != active_research:
            self._text_model_chain = [active_research] + [
                m for m in self._text_model_chain if m != active_research
            ]

        self._econ_gemini_chain = build_model_chain(
            self._gemini,
            capability_type="text",
            preferred=app_config.GEMINI_ECONOMIC_BRAIN_MODEL,
        )
        self._research_model = self._text_model_chain[0] if self._text_model_chain else active_research

        # Headers sent on every request: x-api-key (auto from api_key)
        # + anthropic-version (required; defaults to 2023-06-01 from config).
        self._anthropic = (
            Anthropic(
                api_key=a_key,
                default_headers={
                    "anthropic-version": app_config.ANTHROPIC_API_VERSION,
                    "x-api-key": a_key,
                },
            )
            if a_key
            else None
        )

        # Dynamic Claude model selection via Models API.
        if writer_model:
            self._writer_model = writer_model
        else:
            self._writer_model = app_config.get_best_claude_model(self._anthropic)

        # DeepSeek — OpenAI-compatible economic brain (optional, activated by --economic)
        self._deepseek = None
        if app_config.DEEPSEEK_API_KEY:
            try:
                from openai import OpenAI as _OpenAI  # type: ignore
                self._deepseek = _OpenAI(
                    api_key=app_config.DEEPSEEK_API_KEY,
                    base_url=app_config.DEEPSEEK_BASE_URL,
                )
                logger.debug(
                    "CaptionEngine | DeepSeek client initialised (model: %s)",
                    app_config.DEEPSEEK_MODEL,
                )
            except ImportError:
                logger.warning(
                    "openai package not installed — DeepSeek unavailable. "
                    "Run: pip install openai"
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("DeepSeek client init failed (%s); falling back to Gemini.", exc)

        logger.debug(
            "CaptionEngine | Gemini primary: %s (chain len %d) | Claude: %s | DeepSeek: %s",
            self._research_model,
            len(self._text_model_chain),
            self._writer_model,
            "ready" if self._deepseek else "unavailable",
        )

    @property
    def research_primary_id(self) -> str:
        return self._research_model

    # ------------------------------------------------------------------
    # DeepSeek completion helper
    # ------------------------------------------------------------------

    def _deepseek_complete(
        self,
        user_prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1500,
        temperature: float = 0.70,
    ) -> str:
        """
        Send a single-turn completion to DeepSeek via the OpenAI-compatible API.

        Raises RuntimeError if the DeepSeek client is not initialised.
        Callers should guard with ``if self._deepseek``.
        """
        if self._deepseek is None:
            raise RuntimeError("DeepSeek client not initialised (no DEEPSEEK_API_KEY).")
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_prompt})
        resp = self._deepseek.chat.completions.create(
            model=app_config.DEEPSEEK_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return (resp.choices[0].message.content or "").strip()

    def synthesize_facts(
        self,
        topic: str,
        pdf_bundle: dict[str, str],
        *,
        research_model_override: str | None = None,
        variation_index: int = 0,
        total_variants: int = 1,
        economic: bool = False,
    ) -> str:
        vnote = ""
        if total_variants > 1:
            vnote = (
                f"Additional angle: emphasize nuance #{variation_index + 1} of {total_variants} "
                "while staying excerpt-faithful."
            )
        instruction = build_gemini_researcher_instruction(topic, variation_notes=vnote)

        context = corpus_to_prompt_context(pdf_bundle)
        if len(context) > _MAX_PROMPT_CHARS:
            context = context[:_MAX_PROMPT_CHARS]

        # --- DeepSeek economic path ---
        if economic and self._deepseek is not None:
            try:
                full_prompt = (
                    instruction
                    + "\n\nPDF CORPUS BEGIN\n"
                    + context
                    + "\nPDF CORPUS END"
                )
                result = self._deepseek_complete(
                    full_prompt,
                    system="You are a research assistant. Return only the requested fact sheet.",
                    max_tokens=2000,
                )
                if result:
                    logger.info("synthesize_facts | DeepSeek research complete (topic='%s')", topic)
                    return result
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "DeepSeek research failed (%s); falling back to Gemini.", exc
                )

        model = research_model_override or self._research_model
        chain = chain_with_preferred_first(self._text_model_chain, model)
        response = generate_content_with_model_fallback(
            self._gemini,
            chain,
            contents=[instruction, "\n\nPDF CORPUS BEGIN\n", context, "\nPDF CORPUS END"],
        )

        text_attr = getattr(response, "text", None)
        raw_text = text_attr() if callable(text_attr) else text_attr
        if not raw_text:
            parts_out: list[str] = []
            for candidate in getattr(response, "candidates", []) or []:
                content = getattr(candidate, "content", None)
                if not content:
                    continue
                for part in getattr(content, "parts", []) or []:
                    text_val = getattr(part, "text", None)
                    if text_val:
                        parts_out.append(text_val)
            raw_text = "\n".join(parts_out)

        return raw_text.strip() if raw_text else ""

    def synthesize_facts_batch(
        self,
        topic: str,
        pdf_bundle: dict[str, str],
        *,
        num_variants: int | None = None,
    ) -> list[str]:
        """
        Generate all variant narratives in a SINGLE Gemini API call (the One-Call Rule).

        Returns a list of ``num_variants`` raw fact-sheet strings. If Gemini returns
        fewer narratives than requested, the list is padded with empty strings so
        callers can safely iterate with a variant index.

        Falls back to an empty list on API failure, letting the caller revert to
        per-variant ``synthesize_facts()`` calls.
        """
        count = num_variants or BATCH_DEFAULT_SIZE
        instruction = build_batch_researcher_instruction(topic, count)

        context = corpus_to_prompt_context(pdf_bundle)
        if len(context) > _MAX_PROMPT_CHARS:
            context = context[:_MAX_PROMPT_CHARS]

        chain = list(self._text_model_chain)
        try:
            response = generate_content_with_model_fallback(
                self._gemini,
                chain,
                contents=[instruction, "\n\nPDF CORPUS BEGIN\n", context, "\nPDF CORPUS END"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Batch research API call failed: %s", exc)
            return []

        text_attr = getattr(response, "text", None)
        raw_text = text_attr() if callable(text_attr) else text_attr
        if not raw_text:
            parts_out: list[str] = []
            for candidate in getattr(response, "candidates", []) or []:
                content = getattr(candidate, "content", None)
                if not content:
                    continue
                for part in getattr(content, "parts", []) or []:
                    text_val = getattr(part, "text", None)
                    if text_val:
                        parts_out.append(text_val)
            raw_text = "\n".join(parts_out)

        if not raw_text:
            logger.warning("Batch research returned empty response for topic '%s'.", topic)
            return []

        narratives = _parse_batch_narratives(raw_text.strip(), count)
        logger.info(
            "Batch research complete: %d/%d narratives parsed for topic '%s'.",
            sum(1 for n in narratives if n),
            count,
            topic,
        )
        return narratives

    def humanize_voice(
        self,
        raw_fact_sheet: str,
        topic: str,
        *,
        variation_index: int = 0,
        total_variants: int = 1,
        cta_keyword: str | None = None,
        cta_enabled: bool = True,
    ) -> str:
        if not self._anthropic:
            raise ValueError(
                "Anthropic client unavailable. Add ANTHROPIC_API_KEY or enable economic_brain_mode."
            )
        system_prompt = build_claude_humanizer_system_prompt("Anna")
        user_prompt = build_claude_humanizer_user_prompt(
            topic,
            raw_fact_sheet,
            variation_index=variation_index,
            total_variants=total_variants,
            cta_keyword=cta_keyword,
            cta_enabled=cta_enabled,
        )

        message = self._anthropic.messages.create(
            model=self._writer_model,
            max_tokens=900,
            temperature=0.55,
            system=system_prompt,
            messages=[{"role": "user", "content": [{"type": "text", "text": user_prompt}]}],
        )

        paragraphs: list[str] = []
        for block in getattr(message, "content", []) or []:
            text_chunk = getattr(block, "text", None)
            if text_chunk:
                paragraphs.append(text_chunk)
        return "\n\n".join(paragraphs).strip()

    def humanize_voice_gemini(
        self,
        raw_fact_sheet: str,
        topic: str,
        *,
        variation_index: int = 0,
        total_variants: int = 1,
        model_id: str | None = None,
        cta_keyword: str | None = None,
        cta_enabled: bool = True,
    ) -> str:
        prompt = build_gemini_humanizer_instruction(
            topic,
            raw_fact_sheet,
            variation_index=variation_index,
            total_variants=total_variants,
            cta_keyword=cta_keyword,
            cta_enabled=cta_enabled,
        )
        econ = model_id or app_config.GEMINI_ECONOMIC_BRAIN_MODEL
        chain = chain_with_preferred_first(self._econ_gemini_chain, econ)
        response = generate_content_with_model_fallback(self._gemini, chain, contents=[prompt])

        text_attr = getattr(response, "text", None)
        caption = text_attr() if callable(text_attr) else text_attr
        if not caption:
            parts_out: list[str] = []
            for cand in getattr(response, "candidates", []) or []:
                content = getattr(cand, "content", None)
                if not content:
                    continue
                for part in getattr(content, "parts", []) or []:
                    t = getattr(part, "text", None)
                    if t:
                        parts_out.append(t)
            caption = "\n".join(parts_out)

        return (caption or "").strip()

    def humanize_smart_bait(
        self,
        topic: str,
        *,
        page_display_name: str = "",
        page_niche: str = "",
        cta_enabled: bool = True,
        economic: bool = False,
        model_id: str | None = None,
        post_type: str = "SMART_BAIT",
        engagement_bait_examples: str = "",
        previously_generated_hooks: "list[str] | None" = None,
        niche_disclaimer: str = "",
    ) -> tuple[str, str, str, str]:
        """
        Generate SMART_BAIT or ECONOMIC_REEL hook content.

        SMART_BAIT   → JSON {"quote_text": …, "visual_subject": …}
        ECONOMIC_REEL→ JSON {"image_text_overlay": …, "caption_body": …, "visual_subject": …}

        Returns ``(overlay_text, caption, mode_tag, visual_subject)`` where:
          overlay_text  — hook burned into image / spoken as voiceover narration
          caption       — post body / description caption
          mode_tag      — "humanized" | "gemini_fallback" | "researcher_fallback"
          visual_subject— scene description for graphite image generation
        """
        persona = persona_context_block()
        _is_reel = (post_type.upper() == "ECONOMIC_REEL")

        if _is_reel:
            # Use the dedicated reel prompt so the user-side instruction and the
            # system instruction both ask for the 4-sentence narration format.
            # This prevents DeepSeek/Gemini from falling back to the 1-sentence
            # SMART_BAIT schema when the user prompt contradicts the system message.
            prompt = build_reel_narration_prompt(
                topic,
                page_display_name or topic,
                page_niche,
                persona,
                engagement_bait_examples=engagement_bait_examples,
                niche_disclaimer=niche_disclaimer,
            )
        else:
            prompt = build_smart_bait_prompt(
                topic,
                page_display_name or topic,
                page_niche,
                persona,
                cta_enabled=cta_enabled,
            )

        raw_response = ""
        mode_tag = "humanized"

        if _is_reel:
            # Build the previously-generated hooks block for the system message.
            _hooks_block = ""
            if previously_generated_hooks:
                _listed = "\n".join(f"  - {h}" for h in previously_generated_hooks[-20:])
                _hooks_block = (
                    f"\n\nPREVIOUSLY_GENERATED_HOOKS (in this session):\n{_listed}\n\n"
                    "CRITICAL ANTI-REPETITION RULE: You MUST completely change the narrative "
                    "angle, vocabulary, and syntactic structure compared to every hook listed "
                    "above. Do NOT reuse the same emotional entry point, opening phrase, or "
                    "sentence format. Every script must feel like a fundamentally different "
                    "psychological conversation — fresh, deeply emotional, and engaging. "
                    "Use the spreadsheet examples purely for engagement-bait logic structure, "
                    "never for content copying."
                )
            _smart_bait_system = (
                f"You are an elite psychological author and voice scriptwriter for the social media "
                f"video reel pipeline of the page: {page_display_name or 'this page'}.\n\n"
                "OUTPUT FORMAT — respond ONLY with valid JSON, no markdown fences:\n"
                "{\n"
                '  "image_text_overlay": "A single short psychological hook question or statement '
                '(max 12 words). This text is burned onto the video frame as the visual headline.",\n'
                '  "caption_body": "A full 4-sentence narration script for ElevenLabs voiceover. '
                "Structure: (1) Open with the psychological hook question from image_text_overlay. "
                "(2) Deliver a deep truth about why people tolerate or repeat this pattern. "
                "(3) Offer one piece of actionable emotional-intelligence wisdom. "
                "(4) Close with a warm CTA inviting viewers to share their experience in comments. "
                "Total length: 60-80 words — enough to fill a 30-second spoken narration at "
                'natural pace. This same text also serves as the social media post caption.",\n'
                '  "visual_subject": "A precise graphite scene: exactly one man and one woman in a '
                'psychologically tense interaction. Only ONE may exhibit a surreal transformation. '
                'Minimal dark background."\n'
                "}"
                + _hooks_block
            )
        else:
            _smart_bait_system = (
                f"You are an elite viral content engineer for the social media page: {page_display_name or 'this page'}. "
                "Your only job is to write ultra-short, punchy, human hooks that explode comment sections. "
                "You have studied the top 1% of viral Facebook and Instagram posts. "
                "You write the way people actually talk — direct, slightly provocative, personal, never corporate. "
                "Short sentences. No hedging. No emojis unless they add genuine punch. "
                "Your overlay hooks must make someone stop scrolling and immediately type a response."
            )

        # --- DeepSeek economic path ---
        if economic and self._deepseek is not None:
            try:
                raw_response = self._deepseek_complete(
                    prompt,
                    system=_smart_bait_system,
                    max_tokens=280,
                    temperature=0.85,
                )
                if raw_response:
                    logger.info("humanize_smart_bait | DeepSeek OK")
            except Exception as exc:  # noqa: BLE001
                logger.warning("DeepSeek smart bait failed (%s); trying Claude/Gemini.", exc)
                raw_response = ""

        if not raw_response and not economic and self._anthropic:
            try:
                message = self._anthropic.messages.create(
                    model=self._writer_model,
                    max_tokens=280,
                    temperature=0.85,
                    system=_smart_bait_system,
                    messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
                )
                chunks = [
                    getattr(b, "text", None)
                    for b in (getattr(message, "content", []) or [])
                ]
                raw_response = "\n".join(c for c in chunks if c).strip()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Claude smart bait failed (%s). Falling back to Gemini.", exc)
                mode_tag = "gemini_fallback"

        if not raw_response:
            try:
                econ = model_id or app_config.GEMINI_ECONOMIC_BRAIN_MODEL
                chain = chain_with_preferred_first(self._econ_gemini_chain, econ)
                response = generate_content_with_model_fallback(
                    self._gemini, chain, contents=[prompt]
                )
                text_attr = getattr(response, "text", None)
                raw_response = text_attr() if callable(text_attr) else text_attr
                if not raw_response:
                    parts_out: list[str] = []
                    for cand in getattr(response, "candidates", []) or []:
                        content = getattr(cand, "content", None)
                        if not content:
                            continue
                        for part in getattr(content, "parts", []) or []:
                            t = getattr(part, "text", None)
                            if t:
                                parts_out.append(t)
                    raw_response = "\n".join(parts_out).strip()
                if economic:
                    mode_tag = "humanized"
            except Exception as exc:  # noqa: BLE001
                logger.warning("Gemini smart bait also failed: %s", exc)
                return "", "", "researcher_fallback", ""

        if not raw_response:
            return "", "", "researcher_fallback", ""

        import json as _json  # noqa: PLC0415

        overlay_text = ""
        visual_subject = ""
        caption = ""

        # Primary: JSON parse (handles both SMART_BAIT and ECONOMIC_REEL schemas)
        try:
            # Strip optional markdown fences the LLM may still add
            _clean = raw_response.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            data = _json.loads(_clean)

            if _is_reel:
                # ECONOMIC_REEL primary schema: image_text_overlay / caption_body / visual_subject
                # Fallback: some LLMs (especially DeepSeek in economic mode) ignore the system
                # instruction and return the SMART_BAIT schema (quote_text / visual_subject)
                # because the prompt template still uses SMART_BAIT wording.  Accept both.
                overlay_text = str(
                    data.get("image_text_overlay", "") or data.get("quote_text", "")
                ).strip()
                caption = str(
                    data.get("caption_body", "") or data.get("caption", "")
                ).strip()
                visual_subject = str(data.get("visual_subject", "")).strip()
                if not caption:
                    caption = overlay_text  # final fallback
            else:
                # SMART_BAIT schema: quote_text / visual_subject
                overlay_text   = str(data.get("quote_text",      "")).strip()
                visual_subject = str(data.get("visual_subject",  "")).strip()
                caption        = overlay_text  # quote_text doubles as the post caption

        except (_json.JSONDecodeError, Exception):  # noqa: BLE001
            # Fallback: legacy label-based parsing for backwards compatibility
            logger.debug("humanize_smart_bait | JSON parse failed — using label fallback (%s)", post_type)
            for line in raw_response.splitlines():
                ls = line.strip()
                if (ls.upper().startswith("OVERLAY:")
                        or ls.upper().startswith("IMAGE_TEXT_OVERLAY:")
                        or ls.upper().startswith("QUOTE_TEXT:")):
                    for prefix in ("IMAGE_TEXT_OVERLAY:", "QUOTE_TEXT:", "OVERLAY:"):
                        if ls.upper().startswith(prefix):
                            overlay_text = ls[len(prefix):].strip()
                            break
                elif ls.upper().startswith("VISUAL_SUBJECT:"):
                    visual_subject = ls[len("VISUAL_SUBJECT:"):].strip()
                elif ls.upper().startswith("CAPTION:") or ls.upper().startswith("CAPTION_BODY:"):
                    key_len = len("CAPTION_BODY:") if ls.upper().startswith("CAPTION_BODY:") else len("CAPTION:")
                    caption = ls[key_len:].strip()
            if not overlay_text and not caption:
                caption = raw_response.strip()

        return overlay_text, caption, mode_tag, visual_subject

    def humanize_long_caption(
        self,
        topic: str,
        *,
        page_display_name: str = "",
        page_niche: str = "",
        cta_enabled: bool = True,
        economic: bool = False,
        model_id: str | None = None,
    ) -> tuple[str, str]:
        """
        Generate a LONG_CAPTION_IMAGE caption: profound, long-form storytelling
        about relationship dynamics, emotional character, and modern marriage.

        Returns ``(caption, mode_tag)`` where mode_tag is one of:
          "humanized"           -- primary LLM succeeded
          "gemini_fallback"     -- Claude failed, Gemini succeeded
          "researcher_fallback" -- all LLMs failed
        """
        persona = persona_context_block()
        prompt = build_long_caption_prompt(
            topic,
            page_display_name or topic,
            page_niche,
            persona,
            cta_enabled=cta_enabled,
        )

        _long_caption_system = (
            "You are an elite psychological author writing for the 'LONG_CAPTION_IMAGE' format.\n\n"
            "OUTPUT FORMAT (respond with ONLY valid JSON, nothing else):\n"
            "{\n"
            '  "image_text_overlay": "",\n'
            '  "caption_body": "Full long-format essay text goes here..."\n'
            "}\n\n"
            "RULES FOR image_text_overlay: LEAVE THIS COMPLETELY EMPTY. "
            "No text goes on the image — the illustration must stay 100% clean.\n\n"
            "ESSAY REQUIREMENTS for caption_body:\n"
            "- Write a deep, highly impactful psychological essay.\n"
            "- Use a hyper-spaced, single-sentence rhythm: every standalone statement "
            "must be separated by a double line break (blank line between each sentence).\n"
            "- Strictly avoid markdown bolding (**), bullet points, or hashtags.\n"
            "- Analyze destructive female and male relationship behaviors.\n"
            "- Cover exactly 2 specific traitor/betrayal scenarios with concrete detail.\n"
            "- Conclude with guidance on managing the domestic home space for inner peace.\n"
            "- End with the exact line: © Wonder Feed | by MediaUpScale"
        )

        raw_response = ""
        mode_tag = "humanized"

        # --- DeepSeek economic path ---
        if economic and self._deepseek is not None:
            try:
                raw_response = self._deepseek_complete(
                    prompt,
                    system=_long_caption_system,
                    max_tokens=900,
                    temperature=0.78,
                )
                if raw_response:
                    logger.info("humanize_long_caption | DeepSeek OK")
            except Exception as exc:  # noqa: BLE001
                logger.warning("DeepSeek long caption failed (%s); trying Claude/Gemini.", exc)
                raw_response = ""

        # --- Claude premium path ---
        if not raw_response and not economic and self._anthropic:
            try:
                message = self._anthropic.messages.create(
                    model=self._writer_model,
                    max_tokens=900,
                    temperature=0.78,
                    system=_long_caption_system,
                    messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
                )
                chunks = [
                    getattr(b, "text", None)
                    for b in (getattr(message, "content", []) or [])
                ]
                raw_response = "\n".join(c for c in chunks if c).strip()
                if raw_response:
                    logger.info("humanize_long_caption | Claude OK")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Claude long caption failed (%s). Falling back to Gemini.", exc)
                mode_tag = "gemini_fallback"

        # --- Gemini fallback ---
        if not raw_response:
            try:
                econ = model_id or app_config.GEMINI_ECONOMIC_BRAIN_MODEL
                chain = chain_with_preferred_first(self._econ_gemini_chain, econ)
                response = generate_content_with_model_fallback(
                    self._gemini, chain, contents=[prompt]
                )
                text_attr = getattr(response, "text", None)
                raw_response = text_attr() if callable(text_attr) else text_attr
                if not raw_response:
                    parts_out: list[str] = []
                    for cand in getattr(response, "candidates", []) or []:
                        content = getattr(cand, "content", None)
                        if not content:
                            continue
                        for part in getattr(content, "parts", []) or []:
                            t = getattr(part, "text", None)
                            if t:
                                parts_out.append(t)
                    raw_response = "\n".join(parts_out).strip()
                if raw_response:
                    mode_tag = "humanized"
                    logger.info("humanize_long_caption | Gemini fallback OK")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Gemini long caption also failed: %s", exc)
                return "", "researcher_fallback"

        if not raw_response:
            return "", "researcher_fallback"

        # Parse JSON first; fall back to plain text if the LLM skips the format
        import json as _json  # noqa: PLC0415

        _copyright = "© Wonder Feed | by MediaUpScale"
        caption = ""
        try:
            _clean = (
                raw_response.strip()
                .removeprefix("```json").removeprefix("```")
                .removesuffix("```").strip()
            )
            _data = _json.loads(_clean)
            caption = str(_data.get("caption_body", "")).strip()
            # image_text_overlay is always empty for this format — ignored
        except (_json.JSONDecodeError, Exception):  # noqa: BLE001
            logger.debug("humanize_long_caption | JSON parse failed — using raw text fallback")
            caption = raw_response.strip()

        if not caption:
            return "", "researcher_fallback"

        if _copyright not in caption:
            caption = f"{caption}\n\n{_copyright}"

        return caption, mode_tag

    def extract_smart_bait_image_theme(
        self,
        overlay_text: str,
        *,
        economic: bool = False,
    ) -> str:
        """
        Derive a one-sentence visual atmosphere description from a SMART_BAIT
        overlay question so the Gemini image prompt emotionally matches the hook.

        Priority:
          1. DeepSeek (if ``economic=True`` and key present) — cheapest.
          2. Gemini flash economic chain.
          3. Keyword heuristic fallback — zero API cost, always succeeds.

        Returns
        -------
        Short atmosphere string ready to pass as ``atmosphere_style`` to
        ``VisualArchitect.build_prompt()``.  Returns empty string on empty input.
        """
        if not overlay_text.strip():
            return ""

        meta_prompt = (
            f'SMART BAIT QUESTION: "{overlay_text.strip()}"\n\n'
            "In exactly ONE sentence (≤ 28 words), describe the ideal ABSTRACT BACKGROUND IMAGE "
            "that emotionally matches this question's tone. "
            "CRITICAL: The image must be BRIGHT, VIVID, and richly coloured — "
            "high quality photographic, NOT dark/murky/underexposed. "
            "Examples:\n"
            "• Fire question → 'Dynamic bonfire with vivid orange and amber flames, "
            "rich warm glow against a deep-blue twilight sky.'\n"
            "• Heartbreak question → 'Vibrant sunset sky with gold and magenta, "
            "single silhouette on a beach, emotional and bright.'\n"
            "• Cats → 'Warm sunlit domestic interior, plush textures, "
            "golden afternoon light, cozy lifestyle shot.'\n"
            "• Money → 'Bright sleek modern interior, marble surfaces, "
            "gold accents, aspirational and vivid.'\n"
            "Output ONLY the one-sentence background description. No preamble, no labels."
        )

        # --- DeepSeek (economic, cheap) ---
        if economic and self._deepseek is not None:
            try:
                result = self._deepseek_complete(
                    meta_prompt,
                    system="You are a visual art director. Reply with only the requested description.",
                    max_tokens=60,
                    temperature=0.45,
                )
                if result:
                    r = result.strip().rstrip(".")
                    logger.debug("extract_smart_bait_image_theme | DeepSeek OK: %s", r[:80])
                    return r + "."
            except Exception as exc:  # noqa: BLE001
                logger.debug("DeepSeek theme extraction failed (%s); trying Gemini.", exc)

        # --- Gemini flash economic chain ---
        try:
            chain = chain_with_preferred_first(
                self._econ_gemini_chain, app_config.GEMINI_ECONOMIC_BRAIN_MODEL
            )
            response = generate_content_with_model_fallback(
                self._gemini, chain, contents=[meta_prompt]
            )
            text_attr = getattr(response, "text", None)
            raw = text_attr() if callable(text_attr) else text_attr
            if raw and raw.strip():
                result = raw.strip()
                logger.debug("extract_smart_bait_image_theme | Gemini OK: %s", result[:80])
                return result
        except Exception as exc:  # noqa: BLE001
            logger.debug("Gemini theme extraction failed (%s); using keyword fallback.", exc)

        # --- Keyword heuristic (always succeeds) ---
        fallback = _keyword_image_theme(overlay_text)
        logger.debug("extract_smart_bait_image_theme | keyword fallback: %s", fallback[:80])
        return fallback

    def humanize_voice_with_fallback(
        self,
        raw_fact_sheet: str,
        topic: str,
        *,
        variation_index: int = 0,
        total_variants: int = 1,
        cta_keyword: str | None = None,
        economic: bool = False,
        model_id: str | None = None,
        cta_enabled: bool = True,
    ) -> tuple[str, str]:
        """
        Try the primary humanizer (Claude if premium, Gemini if economic).
        On failure, automatically retries with Gemini fallback chain.

        Returns (caption, mode_tag) where mode_tag is one of:
          "humanized"           -- primary succeeded
          "gemini_fallback"     -- Claude failed, Gemini succeeded
          "researcher_fallback" -- all LLMs failed (returns empty string)
        """
        kw = (cta_keyword or contextual_cta_keyword(topic)) if cta_enabled else None

        if economic:
            # DeepSeek path: cheaper than Gemini, same or better quality for text
            if self._deepseek is not None:
                try:
                    prompt = build_gemini_humanizer_instruction(
                        topic,
                        raw_fact_sheet,
                        variation_index=variation_index,
                        total_variants=total_variants,
                        cta_keyword=kw,
                        cta_enabled=cta_enabled,
                    )
                    result = self._deepseek_complete(
                        prompt,
                        system="You are a human social media writer. Output ONLY the caption text.",
                        max_tokens=900,
                        temperature=0.72,
                    )
                    if result:
                        logger.info("humanize_voice_with_fallback | DeepSeek humanizer OK")
                        return result, "humanized"
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "DeepSeek humanizer failed (%s); falling back to Gemini economic.", exc
                    )
            # Gemini economic fallback
            try:
                result = self.humanize_voice_gemini(
                    raw_fact_sheet, topic,
                    variation_index=variation_index,
                    total_variants=total_variants,
                    model_id=model_id,
                    cta_keyword=kw,
                    cta_enabled=cta_enabled,
                )
                if result:
                    return result, "humanized"
            except Exception as exc:  # noqa: BLE001
                logger.warning("Gemini humanizer failed (economic mode): %s", exc)
            return "", "researcher_fallback"

        # Premium path: Claude first, then Gemini fallback.
        if self._anthropic:
            try:
                result = self.humanize_voice(
                    raw_fact_sheet, topic,
                    variation_index=variation_index,
                    total_variants=total_variants,
                    cta_keyword=kw,
                    cta_enabled=cta_enabled,
                )
                if result:
                    return result, "humanized"
            except Exception as claude_exc:  # noqa: BLE001
                logger.warning(
                    "Claude humanizer failed (%s). Retrying with Gemini fallback.", claude_exc
                )

        try:
            result = self.humanize_voice_gemini(
                raw_fact_sheet, topic,
                variation_index=variation_index,
                total_variants=total_variants,
                cta_keyword=kw,
                cta_enabled=cta_enabled,
            )
            if result:
                return result, "gemini_fallback"
        except Exception as gem_exc:  # noqa: BLE001
            logger.warning("Gemini fallback humanizer also failed: %s", gem_exc)

        return "", "researcher_fallback"

    def relay(
        self,
        topic: str,
        pdf_bundle: dict[str, str],
        *,
        economic_brain_mode: bool = False,
        variation_index: int = 0,
        total_variants: int = 1,
    ) -> tuple[str, str]:
        if not economic_brain_mode and not self._anthropic:
            raise ValueError("ANTHROPIC_API_KEY required for premium relay or set economic_brain_mode=True.")
        if economic_brain_mode:
            econ_model = app_config.GEMINI_ECONOMIC_BRAIN_MODEL
            raw = self.synthesize_facts(
                topic,
                pdf_bundle,
                research_model_override=econ_model,
                variation_index=variation_index,
                total_variants=total_variants,
            )
            caption = (
                self.humanize_voice_gemini(
                    raw,
                    topic,
                    variation_index=variation_index,
                    total_variants=total_variants,
                    model_id=econ_model,
                )
                if raw
                else ""
            )
            return raw, caption

        raw = self.synthesize_facts(
            topic,
            pdf_bundle,
            variation_index=variation_index,
            total_variants=total_variants,
        )
        caption = (
            self.humanize_voice(
                raw,
                topic,
                variation_index=variation_index,
                total_variants=total_variants,
            )
            if raw
            else ""
        )
        return raw, caption
