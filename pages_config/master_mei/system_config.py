# -*- coding: utf-8 -*-
"""
Master Mei SYSTEM_CONFIG — Definitive V4.0 Cinematic Story Arc.

Safety rollback:
  If FALLBACK_TO_V2_DIRECTIVES (or FALLBACK_TO_V1_DIRECTIVES) is True,
  script and image prompt generation reverts to the original V2 framework.
"""
from __future__ import annotations

from typing import Any

SYSTEM_CONFIG: dict[str, Any] = {
    "ACTIVE_ENGINE_VERSION": "V4.0_CINEMATIC_STORY_ARC",
    "ENABLE_SAFETY_ROLLBACK": True,
    "FEATURE_FLAGS": {
        "ENABLE_CINEMATIC_YELLOW_SUBTITLES": True,
        "ENABLE_EXPLICIT_PHILOSOPHER_CITATIONS": True,
        "ENABLE_3_ACT_NARRATIVE_STORY": True,
        "ENABLE_WESTERN_MAJORITY_SLAVE_MASSES": True,
        "ENABLE_BOOSTED_AUDIO_AND_SFX": True,
        # Legacy V3 flags kept for callers; inactive while V4 is active
        "ENABLE_DIDACTIC_PHILOSOPHY_EXPLANATION": True,
        "ENABLE_3_ACT_STORY_ARC": True,
        "ENABLE_TEMPTATION_SIREN_EPISODES": True,
        "ENABLE_CYBER_SAMURAI_SUBTITLES": False,
        "FALLBACK_TO_V1_DIRECTIVES": False,
        "FALLBACK_TO_V2_DIRECTIVES": False,
    },
}


def get_system_config() -> dict[str, Any]:
    """Return a shallow copy of the active SYSTEM_CONFIG."""
    cfg = dict(SYSTEM_CONFIG)
    cfg["FEATURE_FLAGS"] = dict(SYSTEM_CONFIG.get("FEATURE_FLAGS") or {})
    return cfg


def feature_enabled(flag: str) -> bool:
    flags = SYSTEM_CONFIG.get("FEATURE_FLAGS") or {}
    return bool(flags.get(flag, False))


def _rollback_active() -> bool:
    if not SYSTEM_CONFIG.get("ENABLE_SAFETY_ROLLBACK", True):
        return False
    return feature_enabled("FALLBACK_TO_V1_DIRECTIVES") or feature_enabled(
        "FALLBACK_TO_V2_DIRECTIVES"
    )


def is_v4_active() -> bool:
    """True when V4 cinematic engine should drive scripts/visuals/audio."""
    if _rollback_active():
        return False
    ver = str(SYSTEM_CONFIG.get("ACTIVE_ENGINE_VERSION", ""))
    return ver.startswith("V4") and (
        feature_enabled("ENABLE_3_ACT_NARRATIVE_STORY")
        or feature_enabled("ENABLE_3_ACT_STORY_ARC")
    )


def is_v3_active() -> bool:
    """
    Compatibility: True for V3 *or* V4 story engines (post-V2 narrative path).

    Callers that only need 'not V2' should keep using this.
    Prefer ``is_v4_active()`` for V4-specific behaviour.
    """
    if _rollback_active():
        return False
    if is_v4_active():
        return True
    ver = str(SYSTEM_CONFIG.get("ACTIVE_ENGINE_VERSION", ""))
    return ver.startswith("V3") and feature_enabled("ENABLE_3_ACT_STORY_ARC")


def is_siren_enabled() -> bool:
    return is_v3_active() and feature_enabled("ENABLE_TEMPTATION_SIREN_EPISODES")


def is_cyber_samurai_subtitles_enabled() -> bool:
    """V3 cyan style — disabled under V4 (yellow cinematic replaces it)."""
    if is_v4_active():
        return False
    return is_v3_active() and feature_enabled("ENABLE_CYBER_SAMURAI_SUBTITLES")


def is_cinematic_yellow_subtitles_enabled() -> bool:
    return is_v4_active() and feature_enabled("ENABLE_CINEMATIC_YELLOW_SUBTITLES")


def is_didactic_philosophy_enabled() -> bool:
    return is_v3_active() and (
        feature_enabled("ENABLE_EXPLICIT_PHILOSOPHER_CITATIONS")
        or feature_enabled("ENABLE_DIDACTIC_PHILOSOPHY_EXPLANATION")
    )


def is_explicit_philosopher_citations_enabled() -> bool:
    return is_v4_active() and feature_enabled("ENABLE_EXPLICIT_PHILOSOPHER_CITATIONS")


def is_western_majority_masses_enabled() -> bool:
    return is_v4_active() and feature_enabled("ENABLE_WESTERN_MAJORITY_SLAVE_MASSES")


def is_boosted_audio_enabled() -> bool:
    return is_v4_active() and feature_enabled("ENABLE_BOOSTED_AUDIO_AND_SFX")
