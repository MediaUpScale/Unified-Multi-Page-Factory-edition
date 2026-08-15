# -*- coding: utf-8 -*-
"""Channel-RAG bridge — the single choke-point every generation agent
uses to pull channel-specific guidance BEFORE building its own prompt.

Backend: VisualQA_Agent.channel_rag (JSON store + optional ChromaDB
mirror). See :func:`VisualQA_Agent.channel_rag.get_channel_rules` for
the low-level lookup.

Agents currently wired in
-------------------------
* Script / voiceover agent  ->  core_engine.reel_sequence_engine
                                :func:`build_sequence_script_prompt`
* Image-prompt agent        ->  main.py per-act AK loop (also uses
                                the pool planner in
                                ``avatar_engine.prompt_alignment``)
* Video / motion agent      ->  core_engine.reel_sequence_engine
                                :func:`compile_sequence_reel`
* Music-prompt agent        ->  avatar_engine.audio_engine
                                :func:`generate_dynamic_music_prompt`

Content-type gap (as of 2026-08-15)
-----------------------------------
The RAG's per-channel record today only ships **image-style** sections
(``forbidden_tokens``, ``mandatory_elements``, ``lighting_style``,
``target_audience_rules``, ``visual_concepts``). It has no dedicated
``script_rules``, ``motion_rules`` or ``music_rules`` sections yet.

For the missing content types the bridge:
    1. Logs a WARNING ``CHANNEL_RAG_GAP | channel=X | section=Y missing``
    2. Returns an empty string
    3. The calling agent then falls back to its existing hardcoded
       page_config defaults (safe no-op).

To close a gap, add a section to the channel's dict in
``VisualQA_Agent.channel_rag.CHANNEL_DNA_SEED`` and re-seed. The bridge
will pick it up automatically -- no code change needed here.
"""
from __future__ import annotations

import logging
from typing import Any

_LOG = logging.getLogger(__name__)

# Section keys the bridge queries per agent -- kept as constants so a
# future editor can rename or extend them in one place.
_SEC_IMAGE_MANDATORY = "mandatory_elements"
_SEC_IMAGE_LIGHTING = "lighting_style"
_SEC_IMAGE_FORBIDDEN = "forbidden_tokens"
_SEC_IMAGE_AUDIENCE = "target_audience_rules"
_SEC_IMAGE_CONCEPTS = "visual_concepts"
# Not yet in the RAG -- see gap note in the docstring.
_SEC_SCRIPT_RULES = "script_rules"
_SEC_MOTION_RULES = "motion_rules"
_SEC_MUSIC_RULES = "music_rules"

_GAP_LOGGED: "set[tuple[str, str]]" = set()


def _load_rules(channel_name: str) -> "dict[str, Any] | None":
    name = (channel_name or "").strip()
    if not name:
        return None
    try:
        from VisualQA_Agent.channel_rag import get_channel_rules  # noqa: PLC0415

        return get_channel_rules(name)
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("CHANNEL_RAG | lookup failed for %s (%s)", name, exc)
        return None


def _flag_gap(channel_name: str, section: str) -> None:
    key = (channel_name or "-", section)
    if key in _GAP_LOGGED:
        return
    _GAP_LOGGED.add(key)
    _LOG.warning(
        "CHANNEL_RAG_GAP | channel=%s | section=%s missing -- agent falling "
        "back to hardcoded defaults. Add this section to CHANNEL_DNA_SEED "
        "in VisualQA_Agent/channel_rag.py to eliminate the gap.",
        channel_name or "-",
        section,
    )


def _render_list(items: "list[Any] | None", *, bullet: str = "- ") -> str:
    if not items:
        return ""
    lines: list[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            lines.append(f"{bullet}{text}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API -- one function per agent
# ---------------------------------------------------------------------------
def get_image_guidance(channel_name: str) -> str:
    """Return a prompt-ready block for the image-prompt agent.

    Bundles the RAG's forbidden_tokens, mandatory_elements, lighting_style
    and visual_concepts into a single injectable string. Empty string if
    the channel is unknown or the RAG is unavailable.
    """
    rules = _load_rules(channel_name)
    if not rules:
        return ""
    forbidden = _render_list(rules.get(_SEC_IMAGE_FORBIDDEN))
    mandatory = _render_list(rules.get(_SEC_IMAGE_MANDATORY))
    lighting = str(rules.get(_SEC_IMAGE_LIGHTING) or "").strip()
    concepts = rules.get(_SEC_IMAGE_CONCEPTS) or {}
    concept_lines = "\n".join(
        f"- {k}: {v}" for k, v in concepts.items()
    ) if isinstance(concepts, dict) else ""
    sections: list[str] = []
    if mandatory:
        sections.append(f"CHANNEL IMAGE MANDATORY ELEMENTS:\n{mandatory}")
    if lighting:
        sections.append(f"CHANNEL IMAGE LIGHTING STYLE:\n{lighting}")
    if forbidden:
        sections.append(f"CHANNEL IMAGE FORBIDDEN TOKENS:\n{forbidden}")
    if concept_lines:
        sections.append(f"CHANNEL IMAGE VISUAL CONCEPTS:\n{concept_lines}")
    return "\n\n".join(sections)


def get_script_guidance(channel_name: str) -> str:
    """Return a prompt-ready block for the script / voiceover agent.

    Reads the RAG's ``script_rules`` section (currently not populated for
    any channel -- see the module docstring gap note). Falls back to a
    generic ``target_audience_rules`` snippet when script_rules is absent,
    because that is the closest existing signal for narration voice.
    """
    rules = _load_rules(channel_name)
    if not rules:
        return ""
    script_rules = rules.get(_SEC_SCRIPT_RULES)
    if script_rules:
        if isinstance(script_rules, str):
            return f"CHANNEL SCRIPT / VOICEOVER RULES:\n{script_rules.strip()}"
        if isinstance(script_rules, list):
            return "CHANNEL SCRIPT / VOICEOVER RULES:\n" + _render_list(script_rules)
        if isinstance(script_rules, dict):
            lines = "\n".join(f"- {k}: {v}" for k, v in script_rules.items())
            return f"CHANNEL SCRIPT / VOICEOVER RULES:\n{lines}"
    # Gap fallback -- return the audience rules line (best-available signal)
    _flag_gap(channel_name, _SEC_SCRIPT_RULES)
    audience = str(rules.get(_SEC_IMAGE_AUDIENCE) or "").strip()
    if audience:
        return (
            "CHANNEL AUDIENCE (from RAG target_audience_rules -- "
            "no dedicated script_rules yet):\n" + audience
        )
    return ""


def get_motion_guidance(channel_name: str) -> str:
    """Return a prompt-ready block for the video / motion agent.

    Reads the RAG's ``motion_rules`` section (currently not populated for
    any channel -- see the module docstring gap note). Returns an empty
    string on gap; the motion compiler continues with its hardcoded
    _MOTION_PROFILES cycle.
    """
    rules = _load_rules(channel_name)
    if not rules:
        return ""
    motion_rules = rules.get(_SEC_MOTION_RULES)
    if motion_rules:
        if isinstance(motion_rules, str):
            return f"CHANNEL MOTION RULES:\n{motion_rules.strip()}"
        if isinstance(motion_rules, list):
            return "CHANNEL MOTION RULES:\n" + _render_list(motion_rules)
        if isinstance(motion_rules, dict):
            lines = "\n".join(f"- {k}: {v}" for k, v in motion_rules.items())
            return f"CHANNEL MOTION RULES:\n{lines}"
    _flag_gap(channel_name, _SEC_MOTION_RULES)
    return ""


def get_music_guidance(channel_name: str) -> str:
    """Return a prompt-ready block for the music-prompt agent.

    Reads the RAG's ``music_rules`` section (currently not populated for
    any channel -- see the module docstring gap note). Falls back to the
    ``lighting_style`` signal as a mood proxy (dark cinematic lighting
    strongly correlates with the mystery music profile the audio engine
    already uses).
    """
    rules = _load_rules(channel_name)
    if not rules:
        return ""
    music_rules = rules.get(_SEC_MUSIC_RULES)
    if music_rules:
        if isinstance(music_rules, str):
            return f"CHANNEL MUSIC RULES:\n{music_rules.strip()}"
        if isinstance(music_rules, list):
            return "CHANNEL MUSIC RULES:\n" + _render_list(music_rules)
        if isinstance(music_rules, dict):
            lines = "\n".join(f"- {k}: {v}" for k, v in music_rules.items())
            return f"CHANNEL MUSIC RULES:\n{lines}"
    _flag_gap(channel_name, _SEC_MUSIC_RULES)
    # Best-available proxy: the image lighting_style signals mood.
    lighting = str(rules.get(_SEC_IMAGE_LIGHTING) or "").strip()
    if lighting:
        return (
            "CHANNEL MOOD PROXY (from RAG lighting_style -- no dedicated "
            "music_rules yet):\n" + lighting
        )
    return ""


def get_full_bundle(channel_name: str) -> "dict[str, str]":
    """Return all four guidance blocks in one call -- convenience helper."""
    return {
        "image": get_image_guidance(channel_name),
        "script": get_script_guidance(channel_name),
        "motion": get_motion_guidance(channel_name),
        "music": get_music_guidance(channel_name),
    }
