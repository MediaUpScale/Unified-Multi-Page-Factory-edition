# -*- coding: utf-8 -*-
"""
Master Mei visual compiler — GOLDEN JULY-24 reference logic.

Recovered from outputs:
  reel_weakness_isn_t_choice__it_s_an_u_v12.mp4
  reel_your_solitude_is_not_loneliness__v14.mp4
  reel_the_matrix_fog_obscures_your_tru_v15.mp4
and pages_config/master_mei/golden_prompts.json (run_20260724).

Aesthetic: ROLE-SEPARATED contrast (Master / Disciples / Digital Slaves).
Act count target: 8–10 keyframes (not dense 18–20 micro-segmentation).

Prompt formula (production path uses avatar_engine.visual_roles):
  per-scene ROLE style + subject + environment — never a global Mei×cyberpunk blob
"""
from __future__ import annotations

import re
from typing import Mapping

# ---------------------------------------------------------------------------
# Neutral cinematic locks — subjects come from visual_roles (ROLE A/B/C)
# ---------------------------------------------------------------------------
MASTER_STYLE_ANCHOR_DEFAULT: str = (
    "Cinematic 8k photorealistic raw photography, detailed skin textures, "
    "octane render, volumetric lighting — role-separated temple / training / dystopia"
)

MASTER_STYLE_ANCHOR_COMPACT: str = MASTER_STYLE_ANCHOR_DEFAULT

ILLUSTRATION_STYLE_GOLDEN: str = (
    "Cinematic 8k photorealistic raw photography, detailed skin textures, "
    "octane render, volumetric lighting. Scenes alternate traditional temple wisdom, "
    "organic disciple training, OR neon dystopian tech addiction — never fused in one frame. "
    "NO teens in bedrooms. NO lifestyle soft-focus."
)

_CINEMATIC_FINISH: str = (
    "cinematic 8k photorealistic raw photography, volumetric lighting, "
    "detailed skin textures, octane render, full-bleed, no borders"
)

_NO_STATIC: str = (
    "NO static lifestyle soft-focus, NO teens in bedrooms, NO plain smartphone desks"
)

_NO_MEI_ON_CAMERA: str = (
    "NO Master Mei, no elder sage, no white-bearded mentor on camera"
)

_NO_CJK: str = (
    "neon / signs: English text or abstract tech glyphs only — "
    "NO Chinese, Japanese, Korean, or East Asian characters"
)

_ANTI_DISTORTION: str = (
    "anatomically correct hands and faces, clean anatomy"
)

NEGATIVE_INJECTION: str = (
    "(deformed samurai, warped body, extra limbs, mutated hands, "
    "glitched geometry, bad anatomy, blurry faces:1.5)"
)

MANDATORY_NEGATIVE_PROMPT: str = (
    "static portrait, blank stare into camera, teens in bedrooms, smartphone desk, "
    "lifestyle soft-focus, pastel wellness, gym neon, fitness model, "
    "Chinese characters, Japanese characters, Korean characters, CJK text, "
    "kanji, hangul, hiragana, katakana, Chinese neon signs, Japanese neon signs, "
    "deformed samurai, warped body, extra limbs, mutated hands, glitched geometry, "
    "bad anatomy, blurry faces, midjourney, --ar, --no"
)

_ENFORCEMENT: str = (
    "role-separated contrast — temple wisdom OR waterfall training OR neon dystopia, "
    "never teens in bedrooms, never cybernetics on Master Mei"
)
POLICY_ANCHOR: str = _ENFORCEMENT
SAFETY_STAMP: str = _ENFORCEMENT

_FORBIDDEN_TOKENS: tuple[str, ...] = (
    "ZERO HALLUCINATION",
    "Avatar path:",
    "SHOT TYPE:",
    "LITERAL NOUN ANCHORS:",
    "SPOKEN BEAT:",
    "ANTI-MONOTONY:",
    "SUBJECT FREQUENCY CAP:",
    "--no",
    "--ar",
    "Gossip Goblin",
)

_BANNED_PHRASE_RE = re.compile(
    r"\b(?:"
    r"relationship\s+therapy|couple|romantic|cozy|pastel|"
    r"morning\s+light\s+linen|journaling\s+wellness|blush\s+pink|terracotta|"
    r"teenager\s+bedroom|teen\s+bedroom|smartphone\s+desk|ordinary\s+bedroom|"
    r"influencer\s+selfie|smiling\s+lifestyle"
    r")\b",
    re.IGNORECASE,
)

CAMERA_MOTIONS: tuple[str, ...] = (
    "slow cinematic pan",
    "dynamic low angle tracking shot",
    "dramatic kinetic action shot",
    "rapid push-in through rain and steam",
    "sweeping lateral parallax through neon rain",
    "overhead crane sweep across the training courtyard",
)

# ---------------------------------------------------------------------------
# Canonical act templates (golden July-24)
# ---------------------------------------------------------------------------
CANONICAL_PROMPTS: dict[str, str] = {
    "hook": (
        f"{ILLUSTRATION_STYLE_GOLDEN}. "
        "HOOK FRAME 1 — STRICT CHARACTER LOCK + REFERENCE LIKENESS. "
        "Master Mei SEATED in meditation in ancestral samurai temple / misty mountain "
        "sacred ground — spine erect, absolute stillness, long white topknot and beard, "
        "dark/gold robes. {_CINEMATIC_FINISH}."
    ),
    "dopamine": (
        f"{ILLUSTRATION_STYLE_GOLDEN}. "
        "B-ROLL — MATRIX / DISTRACTION / CHEAP DOPAMINE (CYBERPUNK). "
        "Futuristic cyberpunk slaves chained by glowing neon neural wires, "
        "bio-mechanical screens fused to faces, neon-lit rain-drenched dystopian megacity. "
        f"{_NO_MEI_ON_CAMERA}. {_NO_CJK}. {_NO_STATIC}. {_CINEMATIC_FINISH}."
    ),
    "propaganda": (
        f"{ILLUSTRATION_STYLE_GOLDEN}. "
        "B-ROLL — MATRIX / DIGITAL BLINDNESS. VR-visored masses under holographic "
        "addiction feeds in neon rain megacity. "
        f"{_NO_MEI_ON_CAMERA}. {_NO_CJK}. {_NO_STATIC}. {_CINEMATIC_FINISH}."
    ),
    "system": (
        f"{ILLUSTRATION_STYLE_GOLDEN}. "
        "B-ROLL — SYSTEM / MASS MANIPULATION. Bleak dystopian megacity: endless neon "
        "towers, mass-control holograms, herds of wired citizens under rain. "
        f"{_NO_MEI_ON_CAMERA}. {_NO_CJK}. {_NO_STATIC}. {_CINEMATIC_FINISH}."
    ),
    "trap": (
        f"{ILLUSTRATION_STYLE_GOLDEN}. "
        "B-ROLL — MATRIX / CHEAP DOPAMINE. Humans chained by glowing neon neural wires "
        "in neon-lit rain-drenched dystopian megacity. "
        f"{_NO_MEI_ON_CAMERA}. {_NO_CJK}. {_NO_STATIC}. {_CINEMATIC_FINISH}."
    ),
    "training": (
        f"{ILLUSTRATION_STYLE_GOLDEN}. "
        "B-ROLL — DISCIPLINE / CONDITIONING (ACTIVE). Master Mei ACTIVE in misty mountain "
        "peaks, roaring waterfall training, rain-soaked ancient temple — hard "
        "physical/spiritual conditioning in motion with young disciples. "
        f"{_NO_CJK}. {_CINEMATIC_FINISH}."
    ),
    "forge": (
        f"{ILLUSTRATION_STYLE_GOLDEN}. "
        "B-ROLL — DISCIPLINE / CONDITIONING (ACTIVE). Master Mei ACTIVE demonstrating "
        "martial form under waterfall / storm rain with sweating disciples. "
        f"{_NO_CJK}. {_CINEMATIC_FINISH}."
    ),
    "focus": (
        f"{ILLUSTRATION_STYLE_GOLDEN}. "
        "B-ROLL — INNER MASTERY / CYBERPUNK CONTRAST. Master Mei in active "
        "ancestral-temple discipline under storm or waterfall — OR neon rain megacity "
        "vs distant ancestral temple silhouette. "
        f"{_NO_CJK}. {_CINEMATIC_FINISH}."
    ),
    "business": (
        f"{ILLUSTRATION_STYLE_GOLDEN}. "
        "B-ROLL — WEALTH / EMPIRE. Ancient stone altar projecting holographic financial "
        "charts; molten gold reflecting on cyber-armor; focus treated as capital. "
        f"{_NO_CJK}. {_CINEMATIC_FINISH}."
    ),
    "rebellion": (
        f"{ILLUSTRATION_STYLE_GOLDEN}. "
        "B-ROLL — REBELLION / AWAKENING. Young cybernetic rebels tearing off glowing "
        "digital chains against dystopian neon towers. "
        f"{_NO_MEI_ON_CAMERA}. {_NO_CJK}. {_NO_STATIC}. {_CINEMATIC_FINISH}."
    ),
    "liberation": (
        f"{ILLUSTRATION_STYLE_GOLDEN}. "
        "B-ROLL — REBELLION / AWAKENING. Young cybernetic rebels tearing off glowing "
        "digital chains against dystopian neon towers. "
        f"{_NO_MEI_ON_CAMERA}. {_NO_CJK}. {_NO_STATIC}. {_CINEMATIC_FINISH}."
    ),
    "finale": (
        f"{ILLUSTRATION_STYLE_GOLDEN}. "
        "FINAL FRAME — Master Mei among liberated disciples after neural cords are "
        "severed — sovereign stillness after battle, rainy neon temple courtyard, "
        "REFERENCE LIKENESS REQUIRED. {_CINEMATIC_FINISH}."
    ),
    "siren": (
        f"{ILLUSTRATION_STYLE_GOLDEN}. "
        "B-ROLL — TRAP BEAUTY / SIREN. Cybernetic temptation amid neon rain megacity; "
        "masses chained by glowing neural wires. "
        f"{_NO_MEI_ON_CAMERA}. {_NO_CJK}. {_NO_STATIC}. {_CINEMATIC_FINISH}."
    ),
}

CANONICAL_PROMPTS["tech_slavery"] = CANONICAL_PROMPTS["trap"]
CANONICAL_PROMPTS["trap_beauty"] = CANONICAL_PROMPTS["siren"]
CANONICAL_PROMPTS["panopticon"] = CANONICAL_PROMPTS["system"]
CANONICAL_PROMPTS["art_of_war"] = CANONICAL_PROMPTS["forge"]
CANONICAL_PROMPTS["warrior_forge"] = CANONICAL_PROMPTS["training"]
CANONICAL_PROMPTS["rat_race"] = CANONICAL_PROMPTS["system"]
CANONICAL_PROMPTS["discipline"] = CANONICAL_PROMPTS["training"]
CANONICAL_PROMPTS["wealth"] = CANONICAL_PROMPTS["business"]
CANONICAL_PROMPTS["digital_blindness"] = CANONICAL_PROMPTS["propaganda"]
CANONICAL_PROMPTS["seduction"] = CANONICAL_PROMPTS["siren"]

MASTER_MEI_SANCTUARY_PROMPT: str = CANONICAL_PROMPTS["forge"]

CANONICAL_SCENES: dict[str, dict[str, str]] = {
    k: {"subject": k, "environment": "hybrid ancestral temple × cyberpunk"}
    for k in CANONICAL_PROMPTS
}

_CONCEPT_ALIASES: Mapping[str, str] = {
    "digital slavery": "trap",
    "matrix": "trap",
    "cheap dopamine": "dopamine",
    "mass cyber-slavery": "trap",
    "panopticon": "system",
    "surveillance": "system",
    "propaganda": "propaganda",
    "digital blindness": "propaganda",
    "discipline": "training",
    "forge": "forge",
    "liberation": "liberation",
    "art of war": "forge",
    "master mei": "forge",
    "wealth": "business",
    "siren": "siren",
    "seduction": "siren",
    "trap beauty": "siren",
    "femme fatale": "siren",
    "hook": "hook",
    "focus": "focus",
    "training": "training",
}

MODULE_DEFAULT_SHOT: dict[str, str] = {k: "" for k in CANONICAL_SCENES}
SHOT_TYPE_CYCLE: tuple[str, ...] = CAMERA_MOTIONS

THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "dopamine": (
        "matrix", "phone", "dopamine", "scroll", "addiction", "screen", "impulse",
        "feed", "pod", "comfort", "harvest", "velvet", "distract", "weakness",
    ),
    "propaganda": (
        "propaganda", "blind", "vr", "goggles", "headset", "holographic", "illusion",
        "false", "glazed", "fog",
    ),
    "system": (
        "system", "rat race", "crowd", "mass", "tower", "panopticon", "surveillance",
        "watchtower", "herd", "slavery", "enslaved",
    ),
    "siren": (
        "siren", "seduction", "allure", "lust", "beauty", "femme", "tempt",
        "fleeting", "narciss", "trap beauty",
    ),
    "rebellion": (
        "rebel", "sever", "tear", "rip", "break free", "shatter", "unplug", "liberation",
        "sovereign", "reclaim",
    ),
    "training": (
        "train", "discipline", "martial", "kata", "conditioning", "dojo", "forge",
        "sword", "stance", "sweat", "rain", "resistance", "disciple", "hardship", "pain",
    ),
    "business": (
        "wealth", "empire", "capital", "financial", "gold", "altar", "sovereignty",
        "temple brick", "money",
    ),
    "focus": (
        "focus", "attention", "clarity", "silence", "meditat", "inner", "citadel",
        "defiance", "solitude", "stillness", "calm",
    ),
}

VISUAL_MATRIX: dict[str, str] = {
    k: MASTER_STYLE_ANCHOR_DEFAULT for k in CANONICAL_PROMPTS
}
# Ensure theme-keyword keys resolve even when not in CANONICAL_PROMPTS aliases
for _tk in THEME_KEYWORDS:
    VISUAL_MATRIX.setdefault(_tk, MASTER_STYLE_ANCHOR_DEFAULT)


def resolve_concept_key(concept_key: str | None) -> str:
    raw = (concept_key or "").strip()
    if not raw:
        return "trap"
    if raw in CANONICAL_PROMPTS:
        return raw
    if raw in _CONCEPT_ALIASES:
        return _CONCEPT_ALIASES[raw]
    lo = raw.lower()
    for alias, key in _CONCEPT_ALIASES.items():
        if alias.lower() in lo or lo in alias.lower():
            return key
    return "trap"


def shot_type_for_act(act_index: int, module_key: str, *, prefer_module_default: bool = False) -> str:
    del module_key, prefer_module_default
    return CAMERA_MOTIONS[int(act_index) % len(CAMERA_MOTIONS)]


def strip_banned_terms(text: str) -> str:
    clean = _BANNED_PHRASE_RE.sub("", text or "")
    clean = re.sub(r"--no\b[^\n,]*", " ", clean, flags=re.IGNORECASE)
    clean = re.sub(r"--ar\s*\d+\s*:\s*\d+", " ", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s{2,}", " ", clean)
    clean = re.sub(r"(?:\s*[,.]){2,}", ", ", clean)
    return clean.strip(" ,.\u2014-\"'")


def strip_apparel_overrides(text: str) -> str:
    return strip_banned_terms(text)


def _scrub_metadata(text: str) -> str:
    clean = text or ""
    for tok in _FORBIDDEN_TOKENS:
        clean = clean.replace(tok, " ")
    return strip_banned_terms(clean)


def get_canonical_prompt(concept_key: str | None = None) -> str:
    key = resolve_concept_key(concept_key)
    prompt = CANONICAL_PROMPTS.get(key) or CANONICAL_PROMPTS["trap"]
    return strip_banned_terms(prompt)


def get_master_mei_sanctuary_prompt() -> str:
    return strip_banned_terms(MASTER_MEI_SANCTUARY_PROMPT)


def _metaphor_style_base(base_style: str) -> str:
    style = (base_style or "").strip() or MASTER_STYLE_ANCHOR_DEFAULT
    style = re.sub(r"Master Mei[^.]*(?:\.|$)", "", style, flags=re.IGNORECASE)
    style = re.sub(r"\s{2,}", " ", style).strip(" .")
    return style or MASTER_STYLE_ANCHOR_DEFAULT


def compile_theme_prompt(
    *,
    theme: str,
    base_style: str,
    visual_dna: str,
    spoken_beat: str,
    subject: str,
    hook_env: str = "",
    story_phase: str = "",
    act_index: int = 0,
) -> str:
    """
    Compile one act prompt using July-24 golden templates.

    Themes: hook | dopamine | propaganda | system | training | forge | focus |
            business | rebellion | liberation | finale | siren | trap
    """
    del story_phase  # golden classifier is theme-driven, not 3-act forced
    style = (base_style or "").strip() or ILLUSTRATION_STYLE_GOLDEN
    dna = (visual_dna or "").strip()
    chunk = (spoken_beat or subject or "").strip()
    cam = shot_type_for_act(act_index, theme or "trap")
    t = (theme or "").strip().lower()
    env_hint = (hook_env or "").strip()

    # Act 0 always locks to HOOK when theme is hook / empty first frame
    if act_index == 0 or t in ("hook",):
        mei = dna or (
            "Master Mei, older East Asian sage, long white hair bun, long flowing white "
            "beard, traditional dark linen robes — NO cybernetics, NO VR, NO neon wires"
        )
        env = env_hint or "ancestral samurai temple / misty mountain sacred ground"
        return strip_banned_terms(
            f"{style}. "
            f"MEDIUM: Ultra-realistic cinematic 4K photography — NOT illustration, NOT CGI polish. "
            f"HOOK FRAME 1 — FIRST APPEARANCE — STRICT CHARACTER LOCK + REFERENCE LIKENESS. "
            f"{mei}. POSE MANDATE: Master Mei meditating, standing in fog, OR observing "
            f"disciples from a temple balcony — spine erect, absolute stillness. "
            f"ENVIRONMENT: {env}. Traditional organic world ONLY. "
            f"THEME CONTEXT: {subject or chunk}. Spoken beat: {chunk}. "
            f"[Camera Motion]: {cam}. {_ANTI_DISTORTION}. {_NO_CJK}. {_CINEMATIC_FINISH}."
        )

    if t in ("finale", "final", "cta"):
        mei = dna or (
            "Master Mei, older East Asian sage, long white topknot, long white beard, "
            "dark linen robes — NO cybernetics"
        )
        return strip_banned_terms(
            f"{style}. "
            f"[SUBJECT & ACTION]: {mei}, standing in misty temple courtyard with "
            f"young disciples after hard training — sovereign stillness, REFERENCE LIKENESS. "
            f"Traditional organic world ONLY. Spoken beat: {chunk}. [Camera Motion]: {cam}. "
            f"{_ANTI_DISTORTION}. {_NO_CJK}. {_CINEMATIC_FINISH}."
        )

    if t in ("training", "forge"):
        # ROLE B default when DNA empty — disciples only (no Master contamination)
        if dna:
            return strip_banned_terms(
                f"{style}. "
                f"B-ROLL — MASTER + DISCIPLINE. {dna}. "
                f"Master Mei guiding disciples at misty mountain peaks, roaring waterfall "
                f"training, rain-soaked ancient temple — NO cybernetics on Master. "
                f"Spoken beat: {chunk}. "
                f"[Camera Motion]: {cam}. {_ANTI_DISTORTION}. {_NO_CJK}. {_CINEMATIC_FINISH}."
            )
        return strip_banned_terms(
            f"{style}. "
            f"B-ROLL — DISCIPLES / CONDITIONING (ACTIVE). "
            f"Young martial arts disciples carrying heavy stones, waterfall training, "
            f"stances in torrential rain — organic physical discipline. {_NO_MEI_ON_CAMERA}. "
            f"Spoken beat: {chunk}. "
            f"[Camera Motion]: {cam}. {_ANTI_DISTORTION}. {_NO_CJK}. {_CINEMATIC_FINISH}."
        )

    if t == "focus":
        if dna:
            return strip_banned_terms(
                f"{style}. "
                f"B-ROLL — INNER MASTERY. {dna}. "
                f"Master Mei in ancestral-temple discipline under storm or mist — "
                f"traditional organic world ONLY, no neon city fused onto his body. "
                f"Spoken beat: {chunk}. [Camera Motion]: {cam}. "
                f"{_ANTI_DISTORTION}. {_NO_CJK}. {_CINEMATIC_FINISH}."
            )
        return strip_banned_terms(
            f"{style}. "
            f"B-ROLL — DISCIPLE STILLNESS. Young monks in silent stance on wet temple stone. "
            f"{_NO_MEI_ON_CAMERA}. Spoken beat: {chunk}. [Camera Motion]: {cam}. "
            f"{_ANTI_DISTORTION}. {_NO_CJK}. {_CINEMATIC_FINISH}."
        )

    if t == "business":
        style_b = _metaphor_style_base(style) if not dna else style
        return strip_banned_terms(
            f"{style_b}. "
            f"B-ROLL — WEALTH / EMPIRE. Spoken beat: {chunk}. "
            f"Ancient stone altar projecting holographic financial charts; molten gold "
            f"reflecting on cyber-armor; focus treated as capital. "
            f"{('REFERENCE LIKENESS. ' + dna + '. ') if dna else ''}"
            f"[Camera Motion]: {cam}. {_ANTI_DISTORTION}. {_NO_CJK}. {_CINEMATIC_FINISH}."
        )

    if t in ("rebellion", "liberation"):
        style_b = _metaphor_style_base(style)
        return strip_banned_terms(
            f"{style_b}. "
            f"B-ROLL — REBELLION / AWAKENING. Spoken beat: {chunk}. "
            f"Young cybernetic rebels tearing off glowing digital chains against "
            f"dystopian neon towers. {_NO_MEI_ON_CAMERA}. "
            f"[Camera Motion]: {cam}. {_ANTI_DISTORTION}. {_NO_CJK}. {_NO_STATIC}. "
            f"{_CINEMATIC_FINISH}."
        )

    if t == "siren":
        style_b = _metaphor_style_base(style)
        return strip_banned_terms(
            f"{style_b}. "
            f"B-ROLL — TRAP BEAUTY / SIREN. Spoken beat: {chunk}. "
            f"Cybernetic temptation amid neon rain; masses chained by glowing neural wires. "
            f"{_NO_MEI_ON_CAMERA}. [Camera Motion]: {cam}. "
            f"{_ANTI_DISTORTION}. {_NO_CJK}. {_NO_STATIC}. {_CINEMATIC_FINISH}."
        )

    # Default: MATRIX / DOPAMINE / SYSTEM enslavement B-roll (no Mei)
    style_b = _metaphor_style_base(style)
    label = {
        "propaganda": "MATRIX / DIGITAL BLINDNESS",
        "system": "SYSTEM / MASS MANIPULATION",
        "dopamine": "MATRIX / DISTRACTION / CHEAP DOPAMINE (CYBERPUNK)",
        "trap": "MATRIX / CHEAP DOPAMINE (CYBERPUNK)",
    }.get(t, "MATRIX / DISTRACTION / CHEAP DOPAMINE (CYBERPUNK)")
    return strip_banned_terms(
        f"{style_b}. "
        f"B-ROLL — {label}. Spoken beat: {chunk}. "
        f"Futuristic cyberpunk slaves: humans chained by glowing neon neural wires, "
        f"bio-mechanical screens fused to faces, neon-lit rain-drenched dystopian megacity. "
        f"Brainwashed masses under holographic addiction feeds. "
        f"{_NO_MEI_ON_CAMERA}. [Camera Motion]: {cam}. "
        f"{_ANTI_DISTORTION}. {_NO_CJK}. {_NO_STATIC}. {_CINEMATIC_FINISH}."
    )


def compile_flux_prompt(
    spoken_beat_script: str,
    concept_key: str,
    style_anchor: str | None = None,
    shot_type: str = "",
) -> str:
    del shot_type
    key = resolve_concept_key(concept_key)
    base = (style_anchor or "").strip() or MASTER_STYLE_ANCHOR_DEFAULT
    return strip_banned_terms(
        compile_theme_prompt(
            theme=key,
            base_style=base,
            visual_dna="",
            spoken_beat=spoken_beat_script or "",
            subject=spoken_beat_script or key,
            story_phase="",
            act_index=0 if key == "hook" else 1,
        )
    )


def assemble_scene_prompt(
    subject: str = "",
    environment: str = "",
    atmosphere: str = "",
    camera: str = "",
    style_anchor: str | None = None,
) -> str:
    """Legacy helper used by older callers."""
    anchor = (style_anchor or "").strip() or MASTER_STYLE_ANCHOR_DEFAULT
    parts = [p for p in (subject, environment, atmosphere, camera, anchor, _CINEMATIC_FINISH) if p]
    return strip_banned_terms(". ".join(parts))
