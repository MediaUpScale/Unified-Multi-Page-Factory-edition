# -*- coding: utf-8 -*-
"""
Endless Summer Paradise persona — loaded from master_dna.json.

Avatar-OFF library-ingest page. Prefer ChannelFactory /
EndlessSummerParadiseAdapter over importing this module directly.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

_MASTER_DNA_PATH = Path(__file__).resolve().parent / "master_dna.json"


def _load() -> dict:
    return json.loads(_MASTER_DNA_PATH.read_text(encoding="utf-8"))


_P = _load()

ANNA_DISPLAY_NAME: str = _P.get("Subject", "Endless Summer Paradise")
ANNA_AGE_YEARS: int = 0

_voice = _P.get("Brand_Voice_&_Mission", {})
ANNA_PHYSICAL: str = "Avatar-OFF mode: library ingest, no generated human subject."
ANNA_VOICE_PRINCIPLES: str = (
    f"Tone: {_voice.get('Tone', '')}. "
    f"Mission: {_voice.get('Mission_Statement', '')}. "
    f"Themes: {_voice.get('Themes', '')}."
)
ANNA_EXPERTISE_LENS: str = (
    f"Target audience: {_voice.get('Target_Audience', '')}. "
    "Tropical aesthetic summer scenery curation."
)

CORE_DIRECTIVE: str = _P.get("Core_Directive", "")
CORE_MECHANICS: list[str] = []

_PHIL = _P.get("Philosophical_Core", {})
PHILOSOPHY_CORE_BELIEF: str = _PHIL.get("Core_Belief", "")
PHILOSOPHY_THE_CONFLICT: str = _PHIL.get("The_Conflict", "")
PHILOSOPHY_SCIENCE_METAPHORS: list[str] = _PHIL.get("Science_Metaphors", [])
PHILOSOPHY_VOICE_ADJECTIVES: list[str] = _PHIL.get("Voice_Adjectives", [])
PHILOSOPHY_BANNED_NAMES: list[str] = _PHIL.get("Banned_Name_References", [])

NARRATIVE_ANGLES: list[dict] = _P.get("Narrative_Angles", [])

_BATCH = _P.get("Batch_Research", {})
BATCH_DEFAULT_SIZE: int = _BATCH.get("Default_Batch_Size", 10)
BATCH_DELIMITER_OPEN: str = _BATCH.get("Delimiter_Open", "===NARRATIVE_")
BATCH_DELIMITER_CLOSE: str = _BATCH.get("Delimiter_Close", "===")
BATCH_WORDS_PER_NARRATIVE: str = _BATCH.get("Words_Per_Narrative", "80-140")
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
    return random.choice(CTA_KEYWORDS) if CTA_KEYWORDS else "SUMMER"


def persona_context_block() -> str:
    return (
        f"CHANNEL: {ANNA_DISPLAY_NAME}\n"
        f"{CORE_DIRECTIVE}\n"
        f"{ANNA_VOICE_PRINCIPLES}\n"
        f"{ANNA_EXPERTISE_LENS}"
    ).strip()
