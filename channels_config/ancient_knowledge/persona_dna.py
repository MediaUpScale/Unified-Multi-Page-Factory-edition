# -*- coding: utf-8 -*-
"""
Ancient Knowledge — persona DNA accessors.

Provides the same interface as other pages' persona_dna.py modules
so the core engine can call them uniformly without page-specific logic.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

_DNA_PATH = Path(__file__).parent / "master_dna.json"
_dna: dict = {}

def _load() -> dict:
    global _dna
    if not _dna:
        try:
            _dna = json.loads(_DNA_PATH.read_text(encoding="utf-8"))
        except Exception:
            _dna = {}
    return _dna


def persona_context_block() -> str:
    """Return a compact persona context string for LLM system prompts."""
    d = _load()
    persona = d.get("persona", {})
    name = persona.get("name", "Ancient Knowledge")
    mission = persona.get("core_mission", "")
    disclaimer = persona.get("disclaimer", "")
    pillars = persona.get("content_pillars", [])
    pillar_str = "; ".join(pillars[:4]) if pillars else ""
    return (
        f"CHANNEL: {name}\n"
        f"MISSION: {mission}\n"
        f"CONTENT PILLARS: {pillar_str}\n"
        f"DISCLAIMER: {disclaimer}"
    )


def contextual_cta_keyword(subject: str = "") -> str:
    """Return a random CTA option from the DNA file."""
    d = _load()
    ctas = d.get("cta_options", ["Follow for more ancient mysteries."])
    return random.choice(ctas)


def random_narrative_angle() -> str:
    """Return a random narrative angle for script generation."""
    d = _load()
    angles = d.get("narrative_angles", [])
    return random.choice(angles) if angles else "What if everything we know is wrong?"


def random_environment() -> str:
    """Return a random visual environment for image prompts."""
    d = _load()
    envs = d.get("environments", [])
    return random.choice(envs) if envs else "Ancient ruins at dusk."


# Module-level constants expected by shared engine imports
BATCH_DEFAULT_SIZE: int = 1
BATCH_DELIMITER_OPEN: str = "---"
BATCH_ROTATION_PATTERN: str = ""
BATCH_WORDS_PER_NARRATIVE: int = 350
CTA_VOICE_INSTRUCTION: str = (
    "End with an open question or invitation to comment. "
    "Do NOT use generic 'DM me' or 'click link' CTAs."
)
NARRATIVE_ANGLES: list = [
    "The evidence that mainstream archaeology refuses to acknowledge",
    "What if everything we know about this is wrong?",
    "A discovery so unusual it changed everything for researchers",
    "Ancient records describe something that should not have existed yet",
]
