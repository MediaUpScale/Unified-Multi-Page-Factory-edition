# -*- coding: utf-8 -*-
"""
Down & Dirty persona — loaded from pages_config/down_dirty/master_dna.json.

Avatar-OFF page: no physical avatar, purely atmospheric urban/cyberpunk imagery.
Do NOT import this file directly — use the root persona_dna module.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

_MASTER_DNA_PATH = Path(__file__).resolve().parent / "master_dna.json"


def _load() -> dict:
    return json.loads(_MASTER_DNA_PATH.read_text(encoding="utf-8"))


_P = _load()

ANNA_DISPLAY_NAME: str = _P.get("Subject", "The Voice")
ANNA_AGE_YEARS: int = 0  # Avatar-OFF

_voice = _P.get("Brand_Voice_&_Mission", {})
ANNA_PHYSICAL: str = "Avatar-OFF mode: no human subject."
ANNA_VOICE_PRINCIPLES: str = (
    f"Tone: {_voice.get('Tone', '')}. "
    f"Mission: {_voice.get('Mission_Statement', '')}. "
    f"Themes: {_voice.get('Themes', '')}. "
)

ANNA_EXPERTISE_LENS: str = (
    f"Target audience: {_voice.get('Target_Audience', '')}. "
    "Translates financial mechanisms and leverage principles into precise, actionable language."
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
BATCH_WORDS_PER_NARRATIVE: str = _BATCH.get("Words_Per_Narrative", "130-200")
BATCH_ROTATION_PATTERN: str = _BATCH.get("Rotation_Pattern", "")

_CTA_INV = _P.get("Call_To_Action_Inventory", {})
CTA_KEYWORDS: list[str] = _CTA_INV.get("Authorized_Keywords", [])
_THEME_MAP: dict[str, str] = _CTA_INV.get("Theme_Keyword_Map", {})
CTA_VOICE_INSTRUCTION: str = _CTA_INV.get("CTA_Voice_Instruction", "")

ENVIRONMENTS: list[dict] = _P.get("Environments", [])
CAMERA_ANGLES: list[str] = _P.get("Camera_Angles", [])
TIMES_OF_DAY: list[str] = _P.get("Times_Of_Day", [])
ANNA_ACTIONS: list[str] = []
LEGACY_RULE: dict = {"Probability": 0.0}


def contextual_cta_keyword(topic: str) -> str:
    topic_lower = topic.lower()
    for fragment in sorted(_THEME_MAP.keys(), key=len, reverse=True):
        if fragment in topic_lower:
            return _THEME_MAP[fragment]
    return random_cta_keyword()


def random_cta_keyword() -> str:
    return random.choice(CTA_KEYWORDS) if CTA_KEYWORDS else "LEVERAGE"


def philosophy_block() -> str:
    metaphors = "\n".join(f"  - {m}" for m in PHILOSOPHY_SCIENCE_METAPHORS)
    angles = "\n".join(
        f"  - {a.get('code', '')}: {a.get('description', '')}"
        for a in NARRATIVE_ANGLES
    )
    return (
        f"PHILOSOPHY CORE:\n"
        f"  Belief: {PHILOSOPHY_CORE_BELIEF}\n"
        f"  The Conflict: {PHILOSOPHY_THE_CONFLICT}\n\n"
        f"SYSTEM METAPHORS:\n{metaphors}\n\n"
        f"NARRATIVE ANGLES:\n{angles}"
    )


def persona_context_block() -> str:
    mechanics = "\n".join(f"  - {m}" for m in CORE_MECHANICS)
    return (
        f"Page: {ANNA_DISPLAY_NAME} (Down & Dirty).\n"
        f"Core directive: {CORE_DIRECTIVE}\n"
        f"Audience lens: {ANNA_EXPERTISE_LENS}\n"
        f"Voice: {ANNA_VOICE_PRINCIPLES}\n"
        f"Core mechanics:\n{mechanics}\n\n"
        f"{philosophy_block()}"
    )


def visual_style_block() -> str:
    env_names = [e["name"] for e in ENVIRONMENTS]
    return (
        "Avatar-OFF: no human subject. Moody cyberpunk urban imagery.\n"
        f"Available environments: {'; '.join(env_names)}"
    )
