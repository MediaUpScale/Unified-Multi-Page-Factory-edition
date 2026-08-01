# -*- coding: utf-8 -*-
"""
Master Mei persona — loaded from pages_config/master_mei/master_dna.json.

Provides the shared persona_dna interface plus avatar-capable visual helpers.
Do NOT import this file directly — use the root persona_dna module (which
dynamically delegates here when ACTIVE_PAGE == 'master_mei').
"""
from __future__ import annotations

import json
import random
from pathlib import Path

_MASTER_DNA_PATH = Path(__file__).resolve().parent / "master_dna.json"


def _load() -> dict:
    return json.loads(_MASTER_DNA_PATH.read_text(encoding="utf-8"))


_P = _load()
_persona = _P.get("persona", {})

ANNA_DISPLAY_NAME: str = _persona.get("name") or _P.get("Subject", "Master Mei")
ANNA_AGE_YEARS: int = _P.get("Physical_Appearance", {}).get("Age", 72)

_phys = _P.get("Physical_Appearance", {})
ANNA_PHYSICAL: str = (
    f"{ANNA_AGE_YEARS}-year-old East Asian sage Master Mei: "
    f"{_phys.get('Hair', 'long white hair in a traditional bun')}. "
    f"Beard: {_phys.get('Beard', 'long flowing white beard')}. "
    f"Wardrobe: {_phys.get('Wardrobe', 'traditional dark robes with gold accents')}. "
    f"Physique: {_phys.get('Physique', '')}. "
    f"Skin: {_phys.get('Skin_Texture', '')} "
    f"Visual standard: {_phys.get('Visual_Standard', '')}"
)

_voice = _P.get("Brand_Voice_&_Mission", {})
ANNA_VOICE_PRINCIPLES: str = (
    f"Tone: {_voice.get('Tone', _persona.get('voice_tone', ''))}. "
    f"Mission: {_voice.get('Mission_Statement', _persona.get('core_mission', ''))}. "
    f"Themes: {_voice.get('Themes', '')}. "
    "Command over comfort. No hype. No therapy-speak. Captions are precise and uncompromising."
)

ANNA_EXPERTISE_LENS: str = (
    f"Target audience: {_voice.get('Target_Audience', 'Men seeking liberation')}. "
    "Protocol for Liberation: Pai Mei cold discipline, financial warfare, "
    "traps of illusion (seduction), and classical strategy "
    "(Sun Tzu, Marcus Aurelius, Musashi, Buddhist detachment)."
)

CORE_DIRECTIVE: str = _P.get("Core_Directive", "")
CORE_MECHANICS: list[str] = _P.get("Core_Natural_Mechanics", [])

_PHIL = _P.get("Philosophical_Core", {})
PHILOSOPHY_CORE_BELIEF: str = _PHIL.get("Core_Belief", "")
PHILOSOPHY_THE_CONFLICT: str = _PHIL.get("The_Conflict", "")
PHILOSOPHY_SCIENCE_METAPHORS: list[str] = _PHIL.get("Science_Metaphors", [])
PHILOSOPHY_VOICE_ADJECTIVES: list[str] = _PHIL.get("Voice_Adjectives", [])
PHILOSOPHY_BANNED_NAMES: list[str] = _PHIL.get("Banned_Name_References", [])

NARRATIVE_ANGLES: list[dict] = _P.get("Narrative_Angles", [])

_BATCH = _P.get("Batch_Research", {})
BATCH_DEFAULT_SIZE: int = _BATCH.get("Default_Batch_Size", 20)
BATCH_DELIMITER_OPEN: str = _BATCH.get("Delimiter_Open", "===NARRATIVE_")
BATCH_DELIMITER_CLOSE: str = _BATCH.get("Delimiter_Close", "===")
BATCH_WORDS_PER_NARRATIVE: str = _BATCH.get("Words_Per_Narrative", "120-200")
BATCH_ROTATION_PATTERN: str = _BATCH.get("Rotation_Pattern", "")

_CTA_INV = _P.get("Call_To_Action_Inventory", {})
CTA_KEYWORDS: list[str] = _CTA_INV.get("Authorized_Keywords", [])
_THEME_MAP: dict[str, str] = _CTA_INV.get("Theme_Keyword_Map", {})
CTA_VOICE_INSTRUCTION: str = _CTA_INV.get(
    "CTA_Voice_Instruction",
    "End with a firm command to master the mind or step into real discipline today.",
)

ENVIRONMENTS: list[dict] = _P.get("Environments", [])
CAMERA_ANGLES: list[str] = _P.get("Camera_Angles", [])
TIMES_OF_DAY: list[str] = _P.get("Times_Of_Day", [])
ANNA_ACTIONS: list[str] = _P.get("Anna_Actions", [])
LEGACY_RULE: dict = _P.get("Legacy_Rule", {"Probability": 0.0})


def contextual_cta_keyword(topic: str = "") -> str:
    topic_lower = (topic or "").lower()
    for fragment in sorted(_THEME_MAP.keys(), key=len, reverse=True):
        if fragment in topic_lower:
            return _THEME_MAP[fragment]
    return random_cta_keyword()


def random_cta_keyword() -> str:
    return random.choice(CTA_KEYWORDS) if CTA_KEYWORDS else "DISCIPLINE"


def philosophy_block() -> str:
    metaphors = "\n".join(f"  - {m}" for m in PHILOSOPHY_SCIENCE_METAPHORS)
    angles = "\n".join(
        f"  - {a.get('code', '')}: {a.get('description', '')}"
        for a in NARRATIVE_ANGLES
    )
    banned = ", ".join(f'"{n}"' for n in PHILOSOPHY_BANNED_NAMES) or "none"
    pillars = _persona.get("content_pillars", [])
    pillar_str = "\n".join(f"  - {p}" for p in pillars)
    return (
        f"PHILOSOPHY CORE:\n"
        f"  Belief: {PHILOSOPHY_CORE_BELIEF}\n"
        f"  The Conflict: {PHILOSOPHY_THE_CONFLICT}\n\n"
        f"CONTENT PILLARS:\n{pillar_str}\n\n"
        f"SCIENCE METAPHORS (use when relevant):\n{metaphors}\n\n"
        f"NARRATIVE ANGLES AVAILABLE:\n{angles}\n\n"
        f"BANNED NAME REFERENCES: {banned}"
    )


def persona_context_block() -> str:
    """Compact persona context for LLM system prompts."""
    mechanics = "\n".join(f"  - {m}" for m in CORE_MECHANICS)
    return (
        f"CHANNEL: {ANNA_DISPLAY_NAME}\n"
        f"Identity: {ANNA_DISPLAY_NAME}, age {ANNA_AGE_YEARS} — "
        f"{_persona.get('archetype', 'Stoic Executive Mentor')}.\n"
        f"Core directive: {CORE_DIRECTIVE}\n"
        f"Physical: {ANNA_PHYSICAL}\n"
        f"Audience lens: {ANNA_EXPERTISE_LENS}\n"
        f"Voice: {ANNA_VOICE_PRINCIPLES}\n"
        f"Core mechanics:\n{mechanics}\n\n"
        f"{philosophy_block()}"
    )


def visual_style_block() -> str:
    env = _P.get("visual_identity", {})
    phys = _P.get("Physical_Appearance", {})
    env_names = [e["name"] for e in ENVIRONMENTS if isinstance(e, dict) and "name" in e]
    lines = [
        f"Subject: {ANNA_DISPLAY_NAME}, age {ANNA_AGE_YEARS}. {phys.get('Hair', '')}.",
        f"Physique: {phys.get('Physique', '')}.",
        f"Skin realism: {phys.get('Skin_Texture', '')}",
        f"Visual standard: {phys.get('Visual_Standard', '')}",
        f"Atmosphere: {env.get('mood', 'Dark, intense, stoic martial atmosphere.')}",
        f"Style: {env.get('style', 'cinematic 4K martial photography')}",
        f"Available environments: {'; '.join(env_names)}",
    ]
    return "\n".join(lines)


def random_narrative_angle() -> str:
    angles = _P.get("narrative_angles", [])
    if angles:
        return random.choice(angles)
    if NARRATIVE_ANGLES:
        return NARRATIVE_ANGLES[0].get("description", "Challenge soft modern habits.")
    return "Challenge soft modern habits with a warrior truth."


def random_environment() -> str:
    envs = _P.get("environments", [])
    if envs:
        return random.choice(envs)
    if ENVIRONMENTS:
        e0 = ENVIRONMENTS[0]
        return e0.get("desc", e0.get("name", "Dark training ground in the rain."))
    return "Dark martial courtyard under torchlight."
