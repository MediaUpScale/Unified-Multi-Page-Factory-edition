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


def _active_channel(channel=None):
    """Resolve the injected channel, or the hybrid factory for ACTIVE_PAGE."""
    if channel is not None:
        return channel
    from core_engine.interfaces.factory import ChannelFactory

    return ChannelFactory.from_env()


def _is_isolated_channel(channel=None) -> bool:
    from core_engine.interfaces.factory import ChannelFactory

    ch = _active_channel(channel)
    return ChannelFactory.is_isolated(ch.channel_id)


def _persona_block(channel=None) -> str:
    ch = _active_channel(channel)
    if _is_isolated_channel(ch):
        rag = ch.get_rag_context("")
        prompt = ch.get_system_prompt()
        return f"{prompt}\n\n{rag}" if rag else prompt
    return persona_context_block()


def _cta_for(topic: str, channel=None) -> str:
    ch = _active_channel(channel)
    if _is_isolated_channel(ch):
        options = ch.get_ctas()
        return random.choice(options) if options else ""
    return contextual_cta_keyword(topic)

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

def build_gemini_researcher_instruction(topic: str, *, variation_notes: str = "", channel=None) -> str:
    persona = _persona_block(channel)
    desk = _active_channel(channel).display_name
    if _is_isolated_channel(channel):
        return dedent(
            f"""
            You are the Research Desk for `{topic}` at {desk}.

            {persona}
            {variation_notes}

            Constraints:
            - Use ONLY the CHANNEL RAG block supplied with this prompt. Never the PDF wellness corpus.
            - Hedge every theory. Never present a conspiracy as established fact.
            - Do not use biochemical protocol language, Payhip offers, or another channel's persona.

            Produce a RAW FACT SHEET with these sections exactly:
            1. Recognised world anchor (the real monument, artefact, or site).
            2. The impossible / unexplained element researchers debate.
            3. Competing theories, each labelled as theory — not fact.
            4. Visual cues the image engine must show.
            5. Confidence Statement (High/Medium/Low) plus gaps.

            Keep tone investigative and documentary.
            """
        ).strip()
    return dedent(
        f"""
        You are the Research Desk for `{topic}` at {desk}.

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


def build_batch_researcher_instruction(topic: str, num_variants: int, *, channel=None) -> str:
    """
    Build a single-call prompt that asks Gemini to produce `num_variants`
    distinct research narratives in one response (the 'One-Call Rule').
    """
    ch = _active_channel(channel)
    persona = _persona_block(ch)
    isolated_angles = ch.get_narrative_angles("default") if _is_isolated_channel(ch) else None
    if isolated_angles:
        angle_lines = "\n".join(f"  - {a}" for a in isolated_angles)
    else:
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
    if isolated_angles:
        rotation_note = (
            "Rotate through this channel's investigative narrative angles. "
            "No two consecutive narratives may share the same opening."
        )

    example_delimiter = f"{BATCH_DELIMITER_OPEN}1==="
    desk = ch.display_name

    return dedent(
        f"""
        You are the Research Desk for {desk}. Your task is to generate
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
        - Pull facts ONLY from the {"CHANNEL RAG block" if isolated_angles else "PDF corpus"} below. Do not hallucinate.
        - {"Hedge every theory; never present conspiracy as fact." if isolated_angles else "Include precise biochemical language when quoted in sources."}
        - Each narrative must have a unique opening sentence -- no repeated hooks.
        - {"Do not embed Payhip offers or wellness protocols." if isolated_angles else "Identify any Payhip link / offer present in excerpts; embed in narrative 1 only."}
        - Never reference author names that are on the banned list from the persona block above.
        - {"Open from a real world anchor, then the impossible element." if isolated_angles else "Contrast natural mechanisms against expensive synthetic alternatives where relevant."}

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

def build_claude_humanizer_system_prompt(topic_brand: str = "Anna", *, channel=None) -> str:
    return f"You are {topic_brand}. {_persona_block(channel)}"


def build_claude_humanizer_user_prompt(
    topic: str,
    raw_fact_sheet: str,
    *,
    variation_index: int = 0,
    total_variants: int = 1,
    cta_keyword: str | None = None,
    cta_enabled: bool = True,
    channel=None,
) -> str:
    format_rules = _pick_format_rules(cta_enabled)
    if _is_isolated_channel(channel):
        format_rules = _active_channel(channel).get_system_prompt()
    suffix = _variant_suffix(variation_index, total_variants)
    tail = f"\n\nTopic focus: `{topic}`\n\nFACT SHEET:\n```\n{raw_fact_sheet}\n```"
    if cta_enabled:
        kw = cta_keyword or _cta_for(topic, channel)
        cta_instruction = (
            _active_channel(channel).get_system_prompt()
            if _is_isolated_channel(channel)
            else (CTA_VOICE_INSTRUCTION or (
                f"Weave 'Comment {kw}' naturally into the caption as a personal DM invitation."
            ))
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
    channel=None,
) -> str:
    persona = _persona_block(channel)
    format_rules = (
        _active_channel(channel).get_system_prompt()
        if _is_isolated_channel(channel)
        else _pick_format_rules(cta_enabled)
    )
    suffix = _variant_suffix(variation_index, total_variants)
    if cta_enabled:
        kw = cta_keyword or _cta_for(topic, channel)
        cta_instruction = (
            "End with one of this channel's CTA lines; do not use another page's keyword."
            if _is_isolated_channel(channel)
            else (CTA_VOICE_INSTRUCTION or (
                f"Weave 'Comment {kw}' naturally into the caption as a personal DM invitation."
            ))
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
    channel=None,
) -> str:
    """
    Build a single LLM prompt that returns both:
      OVERLAY — ultra-short image overlay question/statement (max ~12 words)
      CAPTION — brief, persona-voiced paragraph with dark/sarcastic/witty tone

    The overlay is designed for immediate comment engagement (not educational).
    The caption complements it with personality, not exposition.
    """
    if _is_isolated_channel(channel):
        ch = _active_channel(channel)
        cta_note = (
            "CAPTION CLOSE: End with one of this channel's CTA lines."
            if cta_enabled
            else "CAPTION CLOSE: End cleanly. Do NOT include any comment links or call-to-actions."
        )
        return dedent(
            f"""
            You are creating a curiosity-bait social post for: {page_display_name}
            Content niche: {page_niche or topic}

            {persona_block}

            TASK — produce exactly two parts as JSON.

            ─── PART 1: OVERLAY TEXT (displayed ON the image) ───
            - SINGLE sentence or question. ABSOLUTE MAXIMUM: 12 words.
            - Investigative curiosity, not relationship psychology.
            - Topic context: {topic}

            ─── PART 2: CAPTION ───
            - EXACTLY 1 SENTENCE.
            - Dark, investigative, unexplained-history tone. Never Anna/Mei voice.
            {cta_note}

            ─── OUTPUT FORMAT — respond with ONLY valid JSON ───
            {{
              "quote_text": "A unique investigative overlay hook about the topic. Never repeat previous formats.",
              "visual_subject": "A photoreal documentary scene of the named monument, artifact, or landscape. No couples, no face-masks, no relationship psychology. Wide aerial or environmental camera. Central subject. Foreground dust or stone arch; background temple, starfield, or horizon."
            }}

            Return ONLY the JSON object. No extra text, no markdown fences, no explanation.
            """
        ).strip()

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

# Warrior / Liberation Protocol angles — ONE angle = ONE episode theme
_REEL_WARRIOR_ANGLES: list[str] = [
    "seduction & the siren trap — deceptive allure and neon impulse slavery",
    "the panopticon & digital slavery — devices and cables as modern chains",
    "sun tzu & strategic patience in wealth — focus as the front line of capital",
    "physical hardship & discipline protocol — pai mei cold conditioning",
    "marcus aurelius inner citadel — protect attention before the market opens",
    "musashi timing — strike once with discipline, never a hundred times with impulse",
    "buddhist detachment — watch craving rise and refuse to obey it",
    "financial sovereignty — forge unshakeable assets or remain a cog",
]


def build_reel_narration_prompt(
    topic: str,
    page_display_name: str,
    page_niche: str,
    persona_block: str,
    engagement_bait_examples: str = "",
    niche_disclaimer: str = "",
    narrative_mode: str = "",
    batch_angle_block: str = "",
    uniqueness_rejection: str = "",
    channel=None,
) -> str:
    """
    Build the user-side LLM prompt for ECONOMIC_REEL narration generation.

    Asks for a 4-sentence, 65-80 word voiceover script plus a short on-screen
    hook headline and a scene description — all as a single JSON object.

    ``narrative_mode`` selects structure:
      - ``investigative`` / non-empty niche_disclaimer → documentary mystery
      - ``warrior_discipline`` → Master Mei 4-Act Philosophical Storyline
      - default → relationship psychology
    """
    import random as _rnd_angle

    _mode = (narrative_mode or "").strip().lower()
    if not _mode:
        _mode = "investigative" if niche_disclaimer else "psychology"

    if _is_isolated_channel(channel):
        _mode = "investigative"
        _ch_angles = _active_channel(channel).get_narrative_angles("investigative")
        _angle = (
            _rnd_angle.choice(_ch_angles)
            if _ch_angles
            else _rnd_angle.choice(_REEL_INVESTIGATION_ANGLES)
        )
    elif _mode == "investigative":
        _angle = _rnd_angle.choice(_REEL_INVESTIGATION_ANGLES)
    elif _mode == "warrior_discipline":
        _angle = _rnd_angle.choice(_REEL_WARRIOR_ANGLES)
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
    _batch_block = f"\n{batch_angle_block}\n" if batch_angle_block else ""
    _reject_block = (
        f"\n        ── UNIQUENESS REJECTION (REGENERATE) ──\n        {uniqueness_rejection}\n"
        if uniqueness_rejection else ""
    )

    if _mode == "warrior_discipline":
        from avatar_engine.mei_narrative import (
            build_seo_title,
            episode_theme_meta,
        )

        _ep = episode_theme_meta(topic)
        _seo_ex = build_seo_title(topic, theme_key=_ep["key"])
        return dedent(
            f"""
            You are writing voice narration for a short-form V4.0 Cinematic Story Arc
            (3-Act Master Philosophical Lesson) for the page: {page_display_name}
            Content niche: {page_niche or topic}

            {persona_block}
            {_disclaimer_block}
            {_batch_block}
            {_reject_block}
            TOPIC CONTEXT — SINGLE EPISODE THEME: {topic}
            LOCKED THEME: {_ep['label']} — {_ep['core']}
            PHILOSOPHER: {_ep.get('philosopher', 'Master Mei')} | CONCEPT: {_ep.get('concept', _ep['label'])}

            ── CRITICAL SINGLE-CONCEPT RULE ──
            Transform THIS core concept into ONE cohesive didactic story (Acts I–III).
            This video is ONLY about "{_ep['label']}". Never mix philosophies.
            MANDATORY: explicitly introduce and credit {_ep.get('philosopher', 'the thinker')}
            by name in Act I (e.g. Plato's Cave, Nietzsche, Schopenhauer, Marcus Aurelius,
            Epictetus, Sun Tzu, Buddha, Bentham's Panopticon). Connect that philosophy to
            modern digital manipulation, reclaiming focus, mental sovereignty, and
            financial/personal independence.
            Avoid disconnected quotes or generic soundbites.
            Core purpose (immutable): building real wealth and sovereignty through ruthless
            physical, mental, and spiritual discipline.
            NEVER import ancient-mystery conspiracy content, relationship therapy, or soft wellness fluff.

            ── MANDATORY ANGLE (narrative lens for THIS episode only) ──
            {_angle}

            ─── YOUR TASK ───
            Produce a JSON object with exactly four fields:

            1. "image_text_overlay"
               A single sharp command or uncomfortable truth — maximum 12 words.
               Burned visually onto the video frame as the headline.

            2. "seo_title"
               YouTube/SEO title naming the concept or authority figure.
               Format example: "{_seo_ex}"
               Max 90 characters. Include "Master Mei" and a relevant hashtag token.

            3. "caption_body"
               A FULL voiceover narration, 200–240 words MAX (~80–100 s at deliberate pace),
               as ONE continuous 4-Act storyline (NEVER skip an act):

               ACT I — HOOK & CONCEPT INTRODUCTION (~0:00–0:20 | ~4 shots):
                 Introduce philosophy/author. Modern world as a trap for the unprepared.
               ACT II — SLAVERY OF MODERN COMFORT (~0:20–0:45 | ~5 shots):
                 Easy living, cheap dopamine, fast food, digital lures → techno-slaves.
               ACT III — FORGE OF DISCIPLINE (~0:45–1:15 | ~6 shots):
                 Strategic preparation, voluntary suffering, severe training.
                 CRITICAL — never drop a philosopher name out of nowhere.
                 Correct: "As Sun Tzu taught on the battlefield, your focus is your front line…"
               ACT IV — SOVEREIGN CONCLUSION (~1:15–1:40 | ~5 shots):
                 Synthesize. Command: claim self-mastery or remain enslaved.
                 DO NOT say "Follow Master Mei" or "Subscribe" — CTA is added separately.

               Tone: authoritative, solemn, deep, gravitas. Pai Mei sternness.
               PACING: Insert ellipses (`...`) between heavy philosophical statements.
               Short sentences. Strategic commas. Pure spoken prose ONLY.
               FORBIDDEN: ALL emotion/expression tags — never `[cold chuckle]`,
               `[arrogant scoff]`, `[deep subtle laugh]`, `[cackles]`, or `<break/>`.

            4. "visual_subject"
               NO repetitive monk heads. Prefer human agony, techno-slavery, forge hardship,
               abstract metaphors (Panopticon circular prison, Art of War chessboard city,
               Inner Fortress citadel in chest).
               Master Mei ONLY Opening + Key Transition + Final CTA (avatar.png lock).
               Crowds/youth: Black, Caucasian, Hispanic, Asian, Middle Eastern blend.
               Each shot 4–5 seconds; 18–22 distinct visuals. 9:16 cinematic 8k.

            ─── OUTPUT FORMAT ───
            Return ONLY valid JSON. No markdown fences, no explanation.
            {{
              "image_text_overlay": "...",
              "seo_title": "...",
              "caption_body": "...",
              "visual_subject": "..."
            }}
            {_bait_block}
            """
        ).strip()

    if _mode == "investigative":
        # ── INVESTIGATIVE / DOCUMENTARY niche structure (ancient_knowledge etc.) ──
        return dedent(
            f"""
            You are writing voice narration for a short-form investigative documentary reel
            for the page: {page_display_name}
            Content niche: {page_niche or topic}

            {persona_block}
            {_disclaimer_block}
            {_batch_block}
            {_reject_block}
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
    signature: str = "",
    channel=None,
) -> str:
    """
    Build a long-form LLM prompt for LONG_CAPTION_IMAGE.

    Isolated channels (ancient_knowledge) get investigative 3-paragraph copy
    with Pinterest metadata — never relationship-psychology archetypes.
    """
    _sig = signature or f"© {page_display_name} | by MediaUpScale"
    if _is_isolated_channel(channel):
        cta_close = (
            f"\nParagraph 3 (CTA) must end with a curiosity invitation, then the exact line: {_sig}"
            if cta_enabled
            else f"\nDo not add a follow CTA. End with: {_sig}"
        )
        return dedent(
            f"""
            You are writing a LONG_CAPTION_IMAGE post for: {page_display_name}
            Niche: {page_niche or topic}

            {persona_block}

            TOPIC: {topic}

            Write a comprehensive 3-paragraph post:
            1. HOOK — one punchy opening that names a real world monument or artefact.
            2. BODY — evidence, competing theories, hedges ('some researchers believe').
            3. CALL TO ACTION — invite a theory in the comments, then 5 hashtags.

            Rules:
            - Minimum 180 words. Never a single-sentence title.
            - No relationship psychology, no wellness, no microbiome language.
            - No markdown bold, no bullet lists in the caption body.
            {cta_close}

            Respond with ONLY valid JSON:
            {{
              "image_text_overlay": "",
              "caption_body": "paragraph 1\\n\\nparagraph 2\\n\\nparagraph 3 plus 5 hashtags",
              "pinterest_title": "keyword-rich pin title, max 100 characters",
              "pinterest_description": "two-sentence pin description, no social CTAs"
            }}
            """
        ).strip()

    cta_close = (
        f"\n\nEnd the caption with the exact line: {_sig}"
        if cta_enabled
        else ""
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
        channel=None,
    ) -> None:
        from core_engine.interfaces.factory import ChannelFactory

        self._channel = channel or ChannelFactory.from_env()
        self.last_seo_title = ""
        self.last_pinterest_title = ""
        self.last_pinterest_description = ""
        g_key = gemini_key or app_config.GEMINI_API_KEY
        a_key = anthropic_key or app_config.ANTHROPIC_API_KEY
        if not g_key:
            raise ValueError("Gemini API key missing. Set GEMINI_API_KEY.")
        # v1beta -> v1 fallback; SDK sends x-goog-api-key header automatically.
        self._gemini = make_gemini_client_with_fallback(g_key)

        # Handshake: cost-first research model (flash); premium only if enabled.
        from avatar_engine.providers.model_router import text_model as _route_text

        _page_tier = None
        try:
            _page_tier = getattr(app_config, "ACTIVE_PAGE_COST_TIER", None)
        except Exception:
            pass
        research_route = _route_text(
            task="research",
            page_cost_tier=_page_tier,
            model_override=research_model,
            preferred=research_model or app_config.GEMINI_RESEARCH_MODEL,
            log=True,
        )
        research_hint = research_route.model_id
        # Ordered preference stays inside the resolved tier
        _prefs = (
            ["2.5-pro", "2.5-flash"] if research_route.tier == "premium"
            else ["2.5-flash", "2.0-flash", "flash"]
        )
        active_research = get_active_model_id(
            self._gemini,
            preference=_prefs,
            capability_type="text",
        )
        self._text_model_chain = build_model_chain(
            self._gemini,
            capability_type="text",
            preferred=research_hint,
        )
        # Keep handshake result first; strip pro SKUs on cheap tier
        if research_route.tier != "premium":
            self._text_model_chain = [
                m for m in self._text_model_chain
                if "pro" not in m.lower() or "flash" in m.lower()
            ] or list(research_route.chain)
        if not self._text_model_chain or self._text_model_chain[0] != active_research:
            self._text_model_chain = [active_research] + [
                m for m in self._text_model_chain if m != active_research
            ]

        econ_route = _route_text(
            task="caption",
            page_cost_tier=_page_tier,
            preferred=app_config.GEMINI_ECONOMIC_BRAIN_MODEL,
            log=True,
        )
        self._econ_gemini_chain = build_model_chain(
            self._gemini,
            capability_type="text",
            preferred=econ_route.model_id,
        )
        if econ_route.tier != "premium":
            self._econ_gemini_chain = [
                m for m in self._econ_gemini_chain
                if "pro" not in m.lower() or "flash" in m.lower()
            ] or list(econ_route.chain)
        self._router_tier = research_route.tier
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
                    "CaptionEngine | DeepSeek client initialised (flash=%s pro=%s)",
                    app_config.DEEPSEEK_FLASH_MODEL,
                    app_config.DEEPSEEK_PRO_MODEL,
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
        model: str | None = None,
    ) -> str:
        """
        Send a single-turn completion to DeepSeek via the OpenAI-compatible API.

        Raises RuntimeError if the DeepSeek client is not initialised.
        Callers should guard with ``if self._deepseek``.

        ``model`` defaults to ``DEEPSEEK_FLASH_MODEL`` (economic/fast). Pass
        ``DEEPSEEK_PRO_MODEL`` for longer quality scripts (sequence voiceover).
        """
        if self._deepseek is None:
            raise RuntimeError("DeepSeek client not initialised (no DEEPSEEK_API_KEY).")
        _model = (model or app_config.DEEPSEEK_FLASH_MODEL or app_config.DEEPSEEK_MODEL).strip()
        # Hard-block retired IDs even if a caller passes them explicitly
        if _model.lower() in {"deepseek-chat", "deepseek-coder", "deepseek-chat-v3"}:
            logger.warning(
                "DeepSeek model '%s' is retired — remapping to %s",
                _model, app_config.DEEPSEEK_FLASH_MODEL,
            )
            _model = app_config.DEEPSEEK_FLASH_MODEL
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_prompt})
        resp = self._deepseek.chat.completions.create(
            model=_model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return (resp.choices[0].message.content or "").strip()

    def _gemini_text_complete(self, contents: str, *, model_id: str | None = None) -> str:
        """Gemini text completion — cost-first router; stays in active tier on retry."""
        from avatar_engine.providers.gemini_utils import chain_with_preferred_first
        from avatar_engine.providers.gemini_utils import generate_content_with_model_fallback
        from avatar_engine.providers.model_router import text_model as _route_text

        route = _route_text(
            task="caption",
            model_override=model_id,
            preferred=model_id or app_config.GEMINI_ECONOMIC_BRAIN_MODEL,
            tier=getattr(self, "_router_tier", None),
            log=True,
        )
        econ = route.model_id
        base = list(route.chain) + list(self._econ_gemini_chain or self._text_model_chain or [])
        if route.tier != "premium":
            base = [m for m in base if "pro" not in m.lower() or "flash" in m.lower()]
        chain = chain_with_preferred_first(base or [econ], econ)
        response = generate_content_with_model_fallback(
            self._gemini, chain, contents=[contents],
        )
        text_attr = getattr(response, "text", None)
        raw = text_attr() if callable(text_attr) else (text_attr or "")
        if raw:
            return str(raw).strip()
        parts_out: list[str] = []
        for cand in getattr(response, "candidates", []) or []:
            content = getattr(cand, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", []) or []:
                t = getattr(part, "text", None)
                if t:
                    parts_out.append(t)
        return "\n".join(parts_out).strip()

    def _claude_text_complete(
        self,
        user_prompt: str,
        *,
        system: str = "",
        max_tokens: int = 900,
        temperature: float = 0.70,
    ) -> str:
        """Claude text fallback used when DeepSeek fails (economic or premium)."""
        if self._anthropic is None:
            return ""
        message = self._anthropic.messages.create(
            model=self._writer_model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system or "You are a precise creative writer. Follow the user instructions exactly.",
            messages=[{"role": "user", "content": [{"type": "text", "text": user_prompt}]}],
        )
        chunks = [
            getattr(b, "text", None)
            for b in (getattr(message, "content", []) or [])
        ]
        return "\n".join(c for c in chunks if c).strip()

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
        instruction = build_gemini_researcher_instruction(
            topic, variation_notes=vnote, channel=self._channel
        )

        if _is_isolated_channel(self._channel):
            context = self._channel.get_rag_context(topic)
            corpus_open, corpus_close = "CHANNEL RAG BEGIN", "CHANNEL RAG END"
        else:
            context = corpus_to_prompt_context(pdf_bundle)
            corpus_open, corpus_close = "PDF CORPUS BEGIN", "PDF CORPUS END"
        if len(context) > _MAX_PROMPT_CHARS:
            context = context[:_MAX_PROMPT_CHARS]

        # --- Gemini PRIMARY only (no DeepSeek / Claude text fallbacks) ---
        full_prompt = (
            instruction
            + f"\n\n{corpus_open}\n"
            + context
            + f"\n{corpus_close}"
        )
        model = research_model_override or self._research_model
        try:
            chain = chain_with_preferred_first(self._text_model_chain, model)
            response = generate_content_with_model_fallback(
                self._gemini,
                chain,
                contents=[instruction, f"\n\n{corpus_open}\n", context, f"\n{corpus_close}"],
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
            if raw_text and str(raw_text).strip():
                result = str(raw_text).strip()
                if len(result) < 150:
                    logger.warning(
                        "synthesize_facts | Gemini draft < 150 chars — re-prompting to expand."
                    )
                    expand = generate_content_with_model_fallback(
                        self._gemini,
                        chain,
                        contents=[
                            instruction
                            + "\n\nCRITICAL: Expand the fact sheet substantially (≥150 characters).\n\n"
                            + f"{corpus_open}\n"
                            + context
                            + f"\n{corpus_close}",
                        ],
                    )
                    exp_attr = getattr(expand, "text", None)
                    exp_text = exp_attr() if callable(exp_attr) else exp_attr
                    if exp_text and len(str(exp_text).strip()) > len(result):
                        result = str(exp_text).strip()
                logger.info("synthesize_facts | Gemini OK (topic='%s')", topic)
                return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("synthesize_facts | Gemini failed (%s).", exc)

        return ""

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
        instruction = build_batch_researcher_instruction(topic, count, channel=self._channel)

        if _is_isolated_channel(self._channel):
            context = self._channel.get_rag_context(topic)
            corpus_open, corpus_close = "CHANNEL RAG BEGIN", "CHANNEL RAG END"
        else:
            context = corpus_to_prompt_context(pdf_bundle)
            corpus_open, corpus_close = "PDF CORPUS BEGIN", "PDF CORPUS END"
        if len(context) > _MAX_PROMPT_CHARS:
            context = context[:_MAX_PROMPT_CHARS]

        chain = list(self._text_model_chain)
        try:
            response = generate_content_with_model_fallback(
                self._gemini,
                chain,
                contents=[instruction, f"\n\n{corpus_open}\n", context, f"\n{corpus_close}"],
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
        brand = (
            self._channel.display_name
            if _is_isolated_channel(self._channel)
            else "Anna"
        )
        system_prompt = build_claude_humanizer_system_prompt(brand, channel=self._channel)
        user_prompt = build_claude_humanizer_user_prompt(
            topic,
            raw_fact_sheet,
            variation_index=variation_index,
            total_variants=total_variants,
            cta_keyword=cta_keyword,
            cta_enabled=cta_enabled,
            channel=self._channel,
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
            channel=self._channel,
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

    def generate_sequence_voiceover(
        self,
        topic: str,
        *,
        page_niche: str = "",
        persona_voice: str = "investigative, neutral, immersive",
        n_acts: int = 4,
        duration_s: float = 80.0,
        total_words_target: int | None = None,
        economic: bool = False,
        niche_disclaimer: str = "",
        cta_line: str = "",
        narrative_mode: str = "",
        batch_angle_block: str = "",
        uniqueness_rejection: str = "",
        previously_generated_hooks: "list[str] | None" = None,
    ) -> str:
        """
        Generate a full-length documentary narration script for SEQUENCE_REEL TTS.

        Unlike ``humanize_smart_bait`` (which produces a 65-80 word social-media
        caption), this method targets ``total_words_target`` words spread across
        ``n_acts`` acts. Word counts come from ``words_for_duration()``
        (single measured WPS), not a hardcoded WPM table.

        When ``cta_line`` is provided the LLM is instructed to append it naturally
        as the final sentence so the output becomes a single, self-contained script
        that requires only ONE ElevenLabs API call — no secondary CTA audio request.

        The [ACT N] markers are stripped before returning so the raw prose can be
        passed directly to ElevenLabs without any spoken artefacts.

        Returns the cleaned narration string, or "" on failure.
        """
        from core_engine.reel_sequence_engine import build_sequence_script_prompt  # local import avoids circular
        from config import words_for_duration

        import re as _re

        if total_words_target is None:
            total_words_target = words_for_duration(duration_s)

        # Resolve channel_name so build_sequence_script_prompt can pull
        # per-channel script guidance from the RAG bridge. Empty string is
        # a safe no-op (bridge returns "" and prompt falls back to the
        # existing niche_disclaimer / persona_voice path).
        _rag_channel_name = ""
        try:
            _rag_channel_name = str(getattr(self._channel, "channel_id", "") or "")
        except Exception:  # noqa: BLE001
            _rag_channel_name = ""
        prompt = build_sequence_script_prompt(
            topic=topic,
            niche=page_niche or topic,
            persona_voice=persona_voice,
            n_acts=n_acts,
            duration_s=duration_s,
            total_words_target=total_words_target,
            narrative_mode=narrative_mode,
            niche_disclaimer=niche_disclaimer,
            previously_generated_hooks=previously_generated_hooks,
            batch_angle_block=batch_angle_block,
            uniqueness_rejection=uniqueness_rejection,
            channel_name=_rag_channel_name,
        )

        # Append CTA instruction so the single voice script ends with the follow call.
        cta_instruction = (
            f"\n\nCrucial: Append the text \"{cta_line}\" naturally as the very last "
            "sentence of the voiceover script. Do NOT add any label or marker before it."
            if cta_line else ""
        )

        _mei_voice = ""
        _warrior = (
            (narrative_mode or "").strip().lower() == "warrior_discipline"
            and not _is_isolated_channel(self._channel)
        )
        _mei_prof: dict = {}
        if _warrior:
            try:
                from avatar_engine.mei_narrative import (
                    apply_mei_word_budget_from_duration,
                    mei_voice_prompt_block,
                )
                _mei_prof = apply_mei_word_budget_from_duration(duration_s)
                _mei_voice = mei_voice_prompt_block(duration_s=duration_s)
            except Exception:  # noqa: BLE001
                _mei_voice = (
                    "Insert ellipses (`...`) between heavy truths. Short sentences. "
                    "NEVER write emotion tags, sound directions, or SSML — pure spoken prose only."
                )
        _sys_base = (
            f"{niche_disclaimer}\n\n" if niche_disclaimer else ""
        ) + (
            "Write ONLY the Master Philosophical Lesson narration with [ACT N] markers. "
            "SINGLE-TOPIC: one continuous story arc — beginning, middle, end. "
            f"{_mei_voice} "
            "Do NOT write Follow/Subscribe CTA — that is stitched separately after narration. "
            "No preamble, no meta commentary, no bracketed emotion tags."
            if _warrior
            else
            "Write ONLY the documentary narration script with [ACT N] markers. "
            "No preamble, no meta commentary."
        )

        raw = ""
        # Hard word-count floor from the single measured rate. No char-count escape.
        _min_words = int(
            getattr(app_config, "SEQUENCE_VOICEOVER_MIN_WORDS", 0)
            or words_for_duration(80.0)
        )
        if _warrior and _mei_prof:
            _min_words = int(_mei_prof.get("words_min", words_for_duration(80.0)))
            _max_words = int(_mei_prof.get("words_max", words_for_duration(80.0) + 20))
            _accept_chars_lo = max(700, _min_words * 5)
            _accept_chars_hi = max(_accept_chars_lo + 200, _max_words * 8)
        elif _warrior:
            _min_words = max(_min_words, words_for_duration(80.0))
            _max_words = words_for_duration(80.0) + 20
            _accept_chars_lo = 1000
            _accept_chars_hi = 1350
        else:
            _min_words = words_for_duration(80.0)
            _max_words = words_for_duration(80.0) + 20
            _accept_chars_lo = 0
            _accept_chars_hi = 0
        _full_prompt = _sys_base + "\n\n" + prompt + cta_instruction

        def _metrics_script(text: str) -> str:
            """Strip markers/breaks for word/char metrics only."""
            c = _re.sub(r"\[ACT\s+\d+\]\s*", " ", text or "").strip()
            c = _re.sub(r"<\s*break\s+[^>]*/?\s*>", " ", c, flags=_re.IGNORECASE)
            c = _re.sub(r"<[^>]+>", " ", c)
            c = _re.sub(
                r"\[(?:cackles?|chuckles?|cold\s*chuckle|arrogant\s*scoff|"
                r"deep\s*subtle\s*laugh|dry\s*laugh|giggles?|laughs?|sighs?|"
                r"whispers?|pause|beat|silence)[^\]]*\]",
                " ",
                c,
                flags=_re.IGNORECASE,
            )
            c = _re.sub(r"\[[^\]]*\]", " ", c)
            c = _re.sub(r"[ \t]{2,}", " ", c)
            c = _re.sub(r"\n{3,}", "\n\n", c)
            return c.strip()

        def _finalize_script(text: str) -> str:
            """Keep strategic break pauses; strip emotion/act tags for TTS."""
            c = text or ""
            _tok = "<<<MEI_BREAK>>>"
            c = _re.sub(r"<\s*break\s+[^>]*/?\s*>", f" {_tok} ", c, flags=_re.IGNORECASE)
            c = _re.sub(r"\[ACT\s+\d+\]\s*", " ", c)
            c = _re.sub(
                r"\[(?:cackles?|chuckles?|cold\s*chuckle|arrogant\s*scoff|"
                r"deep\s*subtle\s*laugh|dry\s*laugh|giggles?|laughs?|sighs?|"
                r"whispers?|pause|beat|silence)[^\]]*\]",
                " ",
                c,
                flags=_re.IGNORECASE,
            )
            c = _re.sub(r"\[[^\]]*\]", " ", c)
            # Drop non-break XML
            c = _re.sub(r"<(?!/?break\b)[^>]+>", " ", c, flags=_re.IGNORECASE)
            c = c.replace(_tok, '<break time="1.5s"/>')
            c = _re.sub(r"[ \t]{2,}", " ", c)
            c = _re.sub(r"\n{3,}", "\n\n", c)
            return c.strip()

        # Backward-compatible alias used by older log paths
        def _clean_script(text: str) -> str:
            return _metrics_script(text)

        def _word_count(text: str) -> int:
            return len(_metrics_script(text).split()) if text else 0

        def _passes_length(text: str) -> bool:
            cleaned = _metrics_script(text)
            if not cleaned:
                return False
            wc = len(cleaned.split())
            if _warrior:
                if _min_words <= wc <= _max_words:
                    return True
                return False
            return wc >= _min_words  # hard word-count floor only, no char-count escape hatch

        def _try_gemini(*, attempt: int = 1, expand: bool = False, prev_text: str = "") -> str:
            try:
                from avatar_engine.providers.gemini_utils import chain_with_preferred_first
                from avatar_engine.providers.gemini_utils import generate_content_with_model_fallback
                from avatar_engine.providers.model_router import text_model as _route_text

                # Cost-first: flash primary; pro only when premium tier enabled
                route = _route_text(
                    task="voiceover_script",
                    preferred=app_config.GEMINI_ECONOMIC_BRAIN_MODEL,
                    tier=getattr(self, "_router_tier", None),
                    log=True,
                )
                preferred = route.model_id
                base = list(route.chain) + list(self._text_model_chain or [])
                if route.tier != "premium":
                    base = [m for m in base if "pro" not in m.lower() or "flash" in m.lower()]
                chain = chain_with_preferred_first(base or [preferred], preferred)
                boost = ""
                if expand or attempt > 1:
                    if _warrior:
                        boost = (
                            f"\n\nCRITICAL RETRY (MASTER MEI): Previous draft failed length gates "
                            f"(need {_min_words}–{_max_words} spoken words OR {_accept_chars_lo}–"
                            f"{_accept_chars_hi} chars; target {total_words_target} words). "
                            f"Rewrite a FULLER first-person script with AT LEAST {_min_words} words "
                            f"and at most {_max_words} words. Expand Act II forge + Act III liberation. "
                            f"Insert <break time=\"1.5s\"/> after 2–4 key philosophical impacts. "
                            f"3-step deep philosophy + financial sovereignty (money=energy/freedom). "
                            f"FIRST PERSON only. No 'Master Mei' third person. No Follow/Subscribe CTA."
                        )
                    else:
                        prev_wc = len(_metrics_script(prev_text).split()) if prev_text else 0
                        deficit = max(_min_words - prev_wc, 0)
                        boost = (
                            f"\n\nCRITICAL EXPAND/RETRY: Your previous draft had {prev_wc} words. "
                            f"That is {deficit} words SHORT of the required minimum ({_min_words} words). "
                            f"Do not just add filler — add {deficit}+ words of NEW descriptive content "
                            f"(one additional visual detail or piece of evidence per act). "
                            f"Final draft must be {_min_words}-{_min_words + 20} words."
                        )
                response = generate_content_with_model_fallback(
                    self._gemini, chain,
                    contents=[_full_prompt + boost],
                )
                text_attr = getattr(response, "text", None)
                out = text_attr() if callable(text_attr) else (text_attr or "")
                return (out or "").strip()
            except Exception as _exc:
                logger.warning(
                    "generate_sequence_voiceover | Gemini attempt %d failed (%s)",
                    attempt, _exc,
                )
                return ""

        # ── Gemini ONLY (no Claude / DeepSeek fallbacks) ─────────────────────
        raw = _try_gemini(attempt=1)
        _chars = len(_metrics_script(raw))
        _first_wc = _word_count(raw)
        _first_pass = _passes_length(raw)
        logger.info(
            "generate_sequence_voiceover | first_draft words=%d passed_gate=%s min_words=%d",
            _first_wc, _first_pass, _min_words,
        )
        if _first_pass:
            logger.info(
                "generate_sequence_voiceover | Gemini PRIMARY OK (%d chars, %d words)",
                _chars, _first_wc,
            )
        else:
            logger.warning(
                "generate_sequence_voiceover | Gemini draft below word floor "
                "(%d words; need ≥%d) — one retry with deficit.",
                _first_wc, _min_words,
            )
            expanded = _try_gemini(attempt=2, expand=True, prev_text=raw)
            if expanded and (
                _passes_length(expanded)
                or abs(_word_count(expanded) - int(total_words_target or _min_words))
                < abs(_word_count(raw) - int(total_words_target or _min_words))
            ):
                raw = expanded
            elif not raw:
                raw = expanded or ""

        if not raw or _word_count(raw) < 40:
            logger.error(
                "generate_sequence_voiceover | Gemini-only stack failed or returned stubs "
                "(%d words / %d chars) — returning empty.",
                _word_count(raw),
                len(_metrics_script(raw)),
            )
            return ""

        if _warrior:
            from avatar_engine.mei_narrative import (
                inject_philosophical_breaks,
                sanitize_mei_narration_body,
                strip_inline_follow_cta,
            )
            clean = _finalize_script(raw)
            clean = strip_inline_follow_cta(clean)
            # Shield breaks during POV/blacklist scrub
            _tok = "<<<MEI_BREAK>>>"
            clean = _re.sub(
                r"<\s*break\s+[^>]*/?\s*>", f" {_tok} ", clean, flags=_re.IGNORECASE,
            )
            clean = sanitize_mei_narration_body(clean)
            clean = clean.replace(_tok, '<break time="1.5s"/>')
            clean = inject_philosophical_breaks(clean)
        else:
            clean = _metrics_script(raw)
        logger.info(
            "generate_sequence_voiceover | final script %d words / %d chars "
            "(target_words=%d min_words=%d) [Gemini-only]",
            _word_count(clean), len(_metrics_script(clean)),
            total_words_target, _min_words,
        )
        return clean

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
        narrative_mode: str = "",
        batch_angle_block: str = "",
        uniqueness_rejection: str = "",
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
        persona = _persona_block(self._channel)
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
                narrative_mode=narrative_mode,
                batch_angle_block=batch_angle_block,
                uniqueness_rejection=uniqueness_rejection,
                channel=self._channel,
            )
        else:
            prompt = build_smart_bait_prompt(
                topic,
                page_display_name or topic,
                page_niche,
                persona,
                cta_enabled=cta_enabled,
                channel=self._channel,
            )

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
            if _is_isolated_channel(self._channel):
                _starter_ext = ""
                if batch_angle_block:
                    _starter_ext = "\n\n" + batch_angle_block.strip()
                _smart_bait_system = (
                    f"You are an investigative documentary writer for: {page_display_name or 'this page'}.\n\n"
                    "OUTPUT FORMAT — respond ONLY with valid JSON, no markdown fences:\n"
                    "{\n"
                    '  "image_text_overlay": "A single short investigative hook question or statement '
                    '(max 12 words). Burned onto the video frame as the visual headline.",\n'
                    '  "caption_body": "A full 4-sentence documentary narration for ElevenLabs. '
                    "Open from a real world anchor, then the impossible element. Hedge every theory. "
                    'Never relationship psychology, attachment theory, or another channel\'s voice.",\n'
                    '  "visual_subject": "A photoreal documentary scene of the named monument, artefact, '
                    'or landscape. No couples, no face-masks, no graphite sketch, no relationship psychology. '
                    'Wide aerial or immersive first-person. Foreground dust or stone arch; background temple or starfield."\n'
                    "}"
                    + _hooks_block
                    + _starter_ext
                )
            else:
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

        raw_response = ""
        mode_tag = "humanized"

        # --- Gemini ONLY (models/gemini-2.5-flash) — no Claude / DeepSeek ---
        try:
            econ = model_id or app_config.GEMINI_ECONOMIC_BRAIN_MODEL
            gemini_contents = f"{_smart_bait_system}\n\n{prompt}"
            raw_response = self._gemini_text_complete(gemini_contents, model_id=econ)
            if raw_response and len(raw_response.strip()) < 150:
                logger.warning(
                    "humanize_smart_bait | Gemini draft < 150 chars — re-prompting to expand."
                )
                expand_prompt = (
                    gemini_contents
                    + "\n\nCRITICAL: Expand your previous answer. "
                    "Return a richer JSON payload with more detail (≥150 characters total)."
                )
                expanded = self._gemini_text_complete(expand_prompt, model_id=econ)
                if expanded and len(expanded.strip()) > len(raw_response.strip()):
                    raw_response = expanded
            if raw_response:
                mode_tag = "humanized"
                logger.info("humanize_smart_bait | Gemini PRIMARY OK")
        except Exception as exc:  # noqa: BLE001
            logger.warning("humanize_smart_bait | Gemini failed (%s).", exc)
            raw_response = ""

        if not raw_response:
            return "", "", "researcher_fallback", ""

        import json as _json  # noqa: PLC0415
        from avatar_engine.post_planner import extract_clean_caption  # noqa: PLC0415

        overlay_text = ""
        visual_subject = ""
        caption = ""
        self.last_seo_title = ""

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
                self.last_seo_title = str(data.get("seo_title", "") or "").strip()
                if not caption:
                    caption = overlay_text  # final fallback
            else:
                # SMART_BAIT schema: quote_text / visual_subject
                overlay_text   = str(data.get("quote_text",      "")).strip()
                visual_subject = str(data.get("visual_subject",  "")).strip()
                caption        = overlay_text  # quote_text doubles as the post caption

        except (_json.JSONDecodeError, Exception):  # noqa: BLE001
            # Fallback: salvage caption_body from invalid/fenced JSON, then labels
            logger.debug("humanize_smart_bait | JSON parse failed — using salvage/label fallback (%s)", post_type)
            caption = extract_clean_caption(raw_response)
            # If salvage returned the whole blob (no caption_body found), try labels
            _looks_like_json = (
                caption.lstrip().startswith("{") or '"caption_body"' in caption[:80]
            )
            if _looks_like_json or not caption:
                caption = ""
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
                    elif ls.upper().startswith("SEO_TITLE:"):
                        self.last_seo_title = ls[len("SEO_TITLE:"):].strip()
                if not overlay_text and not caption:
                    caption = extract_clean_caption(raw_response)

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
        signature: str = "",
        opening_style_block: str = "",
    ) -> tuple[str, str]:
        """
        Generate a LONG_CAPTION_IMAGE caption: profound, long-form storytelling
        about relationship dynamics, emotional character, and modern marriage.

        ``signature`` is the brand copyright line appended to the caption footer
        (e.g. ``© Ancient Knowledge | by MediaUpScale``).  When omitted it is
        derived from ``page_display_name`` so it is always page-specific.

        Returns ``(caption, mode_tag)`` where mode_tag is one of:
          "humanized"           -- primary LLM succeeded
          "gemini_fallback"     -- Claude failed, Gemini succeeded
          "researcher_fallback" -- all LLMs failed
        """
        _sig = signature or (
            f"© {page_display_name} | by MediaUpScale" if page_display_name else "© MediaUpScale"
        )
        persona = _persona_block(self._channel)
        prompt = build_long_caption_prompt(
            topic,
            page_display_name or topic,
            page_niche,
            persona,
            cta_enabled=cta_enabled,
            signature=_sig,
            channel=self._channel,
        )

        self.last_pinterest_title = ""
        self.last_pinterest_description = ""

        if _is_isolated_channel(self._channel):
            _long_caption_system = (
                "You are an investigative documentary writer for LONG_CAPTION_IMAGE.\n\n"
                "Write a comprehensive 3-paragraph post including a Hook, a narrative Body, "
                "a Call to Action, and 5 hashtags. Minimum 180 words. Never a one-sentence title.\n\n"
                "OUTPUT FORMAT (respond with ONLY valid JSON):\n"
                "{\n"
                '  "image_text_overlay": "",\n'
                '  "caption_body": "Hook paragraph, then body, then CTA + 5 hashtags...",\n'
                '  "pinterest_title": "keyword-rich pin title, max 100 characters",\n'
                '  "pinterest_description": "two-sentence pin description without social CTAs"\n'
                "}\n\n"
                "image_text_overlay MUST be empty. Never use relationship or wellness language. "
                "End caption_body with: " + _sig
                + ("\n\n" + opening_style_block.strip() if opening_style_block else "")
            )
        else:
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
                "- End with the exact line: " + _sig
            )

        raw_response = ""
        mode_tag = "humanized"

        # --- Gemini ONLY — no Claude / DeepSeek ---
        try:
            econ = model_id or app_config.GEMINI_ECONOMIC_BRAIN_MODEL
            raw_response = self._gemini_text_complete(
                f"{_long_caption_system}\n\n{prompt}", model_id=econ,
            )
            if raw_response and len(raw_response.strip()) < 150:
                logger.warning(
                    "humanize_long_caption | Gemini draft < 150 chars — re-prompting to expand."
                )
                expanded = self._gemini_text_complete(
                    f"{_long_caption_system}\n\n{prompt}\n\n"
                    "CRITICAL: Expand the essay substantially (≥150 characters).",
                    model_id=econ,
                )
                if expanded and len(expanded.strip()) > len(raw_response.strip()):
                    raw_response = expanded
            if raw_response:
                mode_tag = "humanized"
                logger.info("humanize_long_caption | Gemini PRIMARY OK")
        except Exception as exc:  # noqa: BLE001
            logger.warning("humanize_long_caption | Gemini failed (%s).", exc)
            raw_response = ""

        if not raw_response:
            return "", "researcher_fallback"

        # Parse JSON first; salvage caption_body from invalid/fenced JSON; else plain text
        import json as _json  # noqa: PLC0415
        from avatar_engine.post_planner import extract_clean_caption  # noqa: PLC0415

        _copyright = _sig
        caption = ""
        try:
            _clean = (
                raw_response.strip()
                .removeprefix("```json").removeprefix("```")
                .removesuffix("```").strip()
            )
            _data = _json.loads(_clean)
            caption = str(_data.get("caption_body", "")).strip()
            self.last_pinterest_title = str(
                _data.get("pinterest_title") or ""
            ).strip()[:100]
            self.last_pinterest_description = str(
                _data.get("pinterest_description") or ""
            ).strip()
            # image_text_overlay is always empty for this format — ignored
        except (_json.JSONDecodeError, Exception):  # noqa: BLE001
            logger.debug(
                "humanize_long_caption | JSON parse failed — salvaging caption_body"
            )
            caption = extract_clean_caption(raw_response)

        if not caption:
            return "", "researcher_fallback"

        if _copyright not in caption:
            caption = f"{caption}\n\n{_copyright}"

        if _is_isolated_channel(self._channel):
            if not self.last_pinterest_title:
                self.last_pinterest_title = (topic or page_display_name or "Ancient Mystery")[:100]
            if not self.last_pinterest_description:
                self.last_pinterest_description = (caption.split("\n\n")[0] if caption else topic)[:500]

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
          1. Gemini flash (PRIMARY default for all pages).
          2. DeepSeek (optional secondary fallback).
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

        # --- Gemini ONLY (keyword heuristic if Gemini unavailable) ---
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
        kw = (cta_keyword or _cta_for(topic, self._channel)) if cta_enabled else None

        if economic:
            # Gemini PRIMARY ONLY for economic captions (all pages)
            try:
                result = self.humanize_voice_gemini(
                    raw_fact_sheet, topic,
                    variation_index=variation_index,
                    total_variants=total_variants,
                    model_id=model_id or app_config.GEMINI_ECONOMIC_BRAIN_MODEL,
                    cta_keyword=kw,
                    cta_enabled=cta_enabled,
                )
                if result and len(result.strip()) < 150:
                    logger.warning(
                        "humanize_voice_with_fallback | Gemini draft < 150 chars — re-prompting."
                    )
                    result = self.humanize_voice_gemini(
                        raw_fact_sheet
                        + "\n\nCRITICAL: Expand the caption substantially (≥150 characters).",
                        topic,
                        variation_index=variation_index,
                        total_variants=total_variants,
                        model_id=model_id or app_config.GEMINI_ECONOMIC_BRAIN_MODEL,
                        cta_keyword=kw,
                        cta_enabled=cta_enabled,
                    ) or result
                if result:
                    logger.info("humanize_voice_with_fallback | Gemini PRIMARY OK")
                    return result, "humanized"
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Gemini humanizer failed (economic mode): %s — no Claude/DeepSeek fallback.",
                    exc,
                )
            return "", "researcher_fallback"

        # Premium path: Gemini-only (Claude / DeepSeek removed from text stack)
        try:
            result = self.humanize_voice_gemini(
                raw_fact_sheet, topic,
                variation_index=variation_index,
                total_variants=total_variants,
                cta_keyword=kw,
                cta_enabled=cta_enabled,
            )
            if result and len(result.strip()) < 150:
                result = self.humanize_voice_gemini(
                    raw_fact_sheet
                    + "\n\nCRITICAL: Expand the caption substantially (≥150 characters).",
                    topic,
                    variation_index=variation_index,
                    total_variants=total_variants,
                    cta_keyword=kw,
                    cta_enabled=cta_enabled,
                ) or result
            if result:
                return result, "humanized"
        except Exception as gem_exc:  # noqa: BLE001
            logger.warning("Gemini humanizer failed: %s", gem_exc)

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
