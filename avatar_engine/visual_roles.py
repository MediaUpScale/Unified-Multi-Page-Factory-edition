# -*- coding: utf-8 -*-
"""
Master Mei — 10-frame narrative lore + 3 Visual Roles (anti-contamination).

FRAME LORE (strict distribution for 10-act reels)
------------------------------------------------
1       INTRO          — Master Mei in primal nature (no tech)
2–4     MATRIX / MAYA  — biomechanical illusion, gears, panopticon slaves
5–7     TRAINING       — Master Mei instructing monks in harsh elements
8–9     TRAP vs WILL   — dopamine crowds vs monk breaking free
10      OUTRO / CTA    — Master Mei in ancestral temple, eyes on camera

Avatar / DNA injection: ROLE A frames only (1, 5–7, 10).
"""
from __future__ import annotations

import re
from typing import Sequence

# ---------------------------------------------------------------------------
# Role IDs
# ---------------------------------------------------------------------------
ROLE_MASTER: str = "master"
ROLE_DISCIPLE: str = "disciple"
ROLE_SLAVE: str = "slave"

VALID_ROLES: frozenset[str] = frozenset({ROLE_MASTER, ROLE_DISCIPLE, ROLE_SLAVE})

# Lore beat tags (finer than role — drives prompt templates)
BEAT_INTRO: str = "intro"
BEAT_MAYA: str = "maya"
BEAT_GEARS: str = "gears"
BEAT_PANOPTICON: str = "panopticon"
BEAT_TRAINING: str = "training"
BEAT_DOPAMINE: str = "dopamine_trap"
BEAT_BREAKFREE: str = "break_free"
BEAT_OUTRO: str = "outro"

# Fixed 10-frame lore: (role, beat_key)
_FRAME_LORE_10: tuple[tuple[str, str], ...] = (
    (ROLE_MASTER, BEAT_INTRO),       # 1
    (ROLE_SLAVE, BEAT_MAYA),         # 2
    (ROLE_SLAVE, BEAT_GEARS),        # 3
    (ROLE_SLAVE, BEAT_PANOPTICON),   # 4
    (ROLE_MASTER, BEAT_TRAINING),    # 5
    (ROLE_MASTER, BEAT_TRAINING),    # 6
    (ROLE_MASTER, BEAT_TRAINING),    # 7
    (ROLE_SLAVE, BEAT_DOPAMINE),     # 8
    (ROLE_DISCIPLE, BEAT_BREAKFREE), # 9
    (ROLE_MASTER, BEAT_OUTRO),       # 10
)

_ORDINARY_BAN: str = (
    "ordinary modern people, casual t-shirt, bland crowd, lifestyle photography, "
    "smartphone selfie, teenager bedroom, office worker, gym influencer, "
    "smiling lifestyle, soft wellness, plain contemporary interior"
)

ROLE_A_SUBJECT: str = (
    "Ancient Asian master Master Mei, 72 years old, lean build, long white hair in a "
    "traditional topknot, long flowing white beard, serene stern countenance, wearing "
    "dark traditional linen robes with subtle gold trim — cinematic high production value"
)

ROLE_A_ENVIRONMENTS: tuple[str, ...] = (
    "ancient mountain temple courtyard at dawn, primal untouched nature",
    "misty waterfall ledge beside ancestral stone shrine",
    "primal bamboo forest path with thick morning fog",
    "high misty mountain peak under cold wind, no neon, no city",
)

ROLE_A_STYLE: str = (
    "Cinematic 8k photorealistic raw photography, detailed skin textures, "
    "octane render, volumetric natural mist and dawn light — traditional ancestral "
    "samurai-temple aesthetic ONLY. Warm ember gold, charcoal stone, muted jade. "
    "Absolutely no neon, no cyberpunk city, no technology, no ordinary modern people"
)

ROLE_A_NEGATIVE: str = (
    f"{_ORDINARY_BAN}, "
    "bionic implants, cybernetics, VR headset, glowing wires, neural cables, "
    "neon lights, cyberpunk city, smartphone, digital chains, robot arm, "
    "mechanical eye, futuristic tech on body, biomechanical, CRT monitors, "
    "deformed hands, extra limbs, blurry face"
)

ROLE_B_SUBJECT: str = (
    "Hardened Shaolin-style martial arts monks and disciples, male and female, "
    "intense determined expressions, visible sweat, high-intensity physical discipline, "
    "traditional martial training garments — NOT ordinary modern people"
)

ROLE_B_STYLE: str = (
    "Cinematic 8k photorealistic raw photography, high-intensity martial discipline, "
    "dramatic rim lighting, sweat and water spray, organic grounded aesthetic"
)

ROLE_B_NEGATIVE: str = (
    f"{_ORDINARY_BAN}, "
    "Master Mei, elder sage, white beard mentor, bionic implants, cybernetics, "
    "VR headset, glowing wires, neon megacity, casual t-shirt, jeans, sneakers, "
    "deformed hands, extra limbs, blurry face, looking at viewer"
)

ROLE_C_SUBJECT: str = (
    "High-concept cinematic cyberpunk digital slaves — pale exhausted faces, "
    "glowing neural collars, neon wires connected to brain and neck, "
    "futuristic VR face visors — NOT ordinary modern people in t-shirts"
)

ROLE_C_STYLE: str = (
    "Cinematic 8k photorealistic raw photography, high-concept dark cyberpunk dystopia, "
    "Matrix / Maya illusion aesthetic, neon glow, high contrast, moody haze, "
    "extreme production value — never ordinary lifestyle photography"
)

ROLE_C_NEGATIVE: str = (
    f"{_ORDINARY_BAN}, "
    "Master Mei, elder sage, white beard, topknot master, ancient Asian master, "
    "temple balcony, bamboo forest, tatami room, waterfall training monks, "
    "casual t-shirt, jeans, sneakers, bland crowd, "
    "deformed hands, extra limbs, blurry face, looking at viewer"
)

_ROLE_C_KEYWORDS: re.Pattern[str] = re.compile(
    r"\b(?:machine|dopamine|digital|matrix|slaver(?:y|ies)?|chains?|"
    r"scroll|distract|addict|phone|maya|propaganda|panopticon|gear)\b",
    re.IGNORECASE,
)
_ROLE_B_KEYWORDS: re.Pattern[str] = re.compile(
    r"\b(?:discipline|training|train|will|citadel|focus|pain|forge|"
    r"waterfall|stance|sweat|disciple|monk|martial)\b",
    re.IGNORECASE,
)
_ROLE_A_KEYWORDS: re.Pattern[str] = re.compile(
    r"\b(?:master|mei|wisdom|solitude|meditat|stillness|calm|philosophy|"
    r"truth|ancient|temple|reflection)\b",
    re.IGNORECASE,
)


def max_master_scenes(n_acts: int) -> int:
    """Unused in lore-locked mode; kept for callers."""
    return max(2, min(4, (int(n_acts) + 1) // 2))


def frame_lore_for_acts(n_acts: int) -> list[tuple[str, str]]:
    """
    Return ``(role, beat)`` per act index.

    For 10 acts: exact FRAME LORE lock.
    For other N: resample the 10-beat arc proportionally.
    """
    n = max(1, int(n_acts))
    if n == 10:
        return list(_FRAME_LORE_10)
    # Resample lore arc across N frames
    out: list[tuple[str, str]] = []
    for i in range(n):
        src = int(round(i * 9 / max(1, n - 1))) if n > 1 else 0
        src = max(0, min(9, src))
        out.append(_FRAME_LORE_10[src])
    # Enforce bookends
    out[0] = (ROLE_MASTER, BEAT_INTRO)
    if n > 1:
        out[-1] = (ROLE_MASTER, BEAT_OUTRO)
    return out


def role_from_spoken_beat(chunk: str) -> str:
    text = (chunk or "").strip()
    if not text:
        return ""
    if _ROLE_C_KEYWORDS.search(text):
        return ROLE_SLAVE
    if _ROLE_B_KEYWORDS.search(text):
        return ROLE_DISCIPLE
    if _ROLE_A_KEYWORDS.search(text):
        return ROLE_MASTER
    return ""


def assign_visual_roles(
    n_acts: int,
    themes: Sequence[str] | None = None,
    spoken_chunks: Sequence[str] | None = None,
) -> list[str]:
    """
    Assign roles via strict 10-frame narrative lore (primary),
    with spoken-beat soft bias only inside Matrix / Trap bands.
    """
    del themes  # lore lock supersedes theme labels
    lore = frame_lore_for_acts(n_acts)
    chunks = list(spoken_chunks or [])
    roles: list[str] = []
    for i, (role, beat) in enumerate(lore):
        chunk = chunks[i] if i < len(chunks) else ""
        # Soft keyword nudge only within Matrix (2-4) and Trap (8) bands
        if beat in (BEAT_MAYA, BEAT_GEARS, BEAT_PANOPTICON, BEAT_DOPAMINE):
            roles.append(ROLE_SLAVE)
        elif beat == BEAT_BREAKFREE:
            # Prefer disciple break-free; keyword can keep slave if heavily digital
            kb = role_from_spoken_beat(chunk)
            roles.append(ROLE_SLAVE if kb == ROLE_SLAVE and "break" not in (chunk or "").lower() else ROLE_DISCIPLE)
        else:
            roles.append(role)
    return roles


def assign_frame_beats(
    n_acts: int,
    spoken_chunks: Sequence[str] | None = None,
) -> list[str]:
    """Return lore beat keys aligned to acts (for prompt builder / inspector)."""
    del spoken_chunks
    return [beat for _, beat in frame_lore_for_acts(n_acts)]


def mei_slots_from_roles(roles: Sequence[str]) -> set[int]:
    return {i for i, r in enumerate(roles) if r == ROLE_MASTER}


def style_for_role(role: str) -> str:
    if role == ROLE_MASTER:
        return ROLE_A_STYLE
    if role == ROLE_DISCIPLE:
        return ROLE_B_STYLE
    return ROLE_C_STYLE


def negative_for_role(role: str) -> str:
    if role == ROLE_MASTER:
        return ROLE_A_NEGATIVE
    if role == ROLE_DISCIPLE:
        return ROLE_B_NEGATIVE
    return ROLE_C_NEGATIVE


def environment_for_role(role: str, act_index: int) -> str:
    if role == ROLE_MASTER:
        return ROLE_A_ENVIRONMENTS[act_index % len(ROLE_A_ENVIRONMENTS)]
    if role == ROLE_DISCIPLE:
        return (
            "single focused monk tearing free from glowing digital chains in neon rain",
            "martial monk mid-stance breaking a dopamine VR tether, determination",
            "lone disciple running from megacity glow toward mountain temple silhouette",
        )[act_index % 3]
    return (
        "desolate cyberpunk wasteland under propaganda screens",
        "panopticon tower casting surveillance light on wired masses",
        "dark high-tech chamber of neural-collar slaves",
    )[act_index % 3]


def build_role_prompt(
    *,
    role: str,
    visual_dna: str = "",
    spoken_beat: str = "",
    subject: str = "",
    act_index: int = 0,
    hook_env: str = "",
    beat: str = "",
) -> tuple[str, str]:
    """
    Build (positive_prompt, negative_prompt) for one lore-aligned scene.
    """
    role = (role or ROLE_SLAVE).strip().lower()
    if role not in VALID_ROLES:
        role = ROLE_SLAVE

    # Resolve beat from lore if not supplied
    if not beat:
        lore = frame_lore_for_acts(max(act_index + 1, 10))
        beat = lore[min(act_index, len(lore) - 1)][1]

    chunk = (spoken_beat or subject or "").strip()
    style = style_for_role(role)
    negative = negative_for_role(role)

    if beat == BEAT_INTRO or (role == ROLE_MASTER and beat == BEAT_INTRO):
        mei = _clean_mei_dna(visual_dna)
        env = (hook_env or "").strip() or (
            "primal untouched ancient mountain temple, misty waterfalls, "
            "primal bamboo forest — NO modern technology"
        )
        positive = (
            f"{style}. "
            f"[FRAME 1 INTRO]: {mei}, standing OR meditating in primal nature — "
            f"absolute stillness, serene authority. ENVIRONMENT: {env}. "
            f"NO modern technology on his body. Spoken beat: {chunk}."
        )
        negative = ROLE_A_NEGATIVE + ", looking at viewer, selfie pose"
    elif beat == BEAT_OUTRO:
        mei = _clean_mei_dna(visual_dna)
        positive = (
            f"{style}. "
            f"[FRAME 10 OUTRO / CTA]: {mei}, standing in an exuberant ancestral "
            f"temple hall with warm torchlight and gold accents, LOOKING DIRECTLY "
            f"AT THE CAMERA with absolute authority and implacable focus — "
            f"REFERENCE LIKENESS REQUIRED. Spoken beat: {chunk}."
        )
        # Outro uniquely allows eye contact
        negative = (
            f"{_ORDINARY_BAN}, bionic implants, cybernetics, VR headset, glowing wires, "
            "neon city fused onto Master Mei, deformed hands, extra limbs, blurry face"
        )
    elif beat == BEAT_TRAINING:
        mei = _clean_mei_dna(visual_dna)
        variants = (
            "Master Mei actively instructing martial monks carrying heavy stone blocks "
            "up ancient steps in harsh cold rain — sweat, grit, high physical intensity",
            "Master Mei standing over disciples in waterfall training, barking form "
            "corrections, cold rain and mist exploding around them",
            "Master Mei demonstrating a martial stance while monks strain under "
            "stone loads in storm light — cinematic hardship conditioning",
        )
        action = variants[act_index % len(variants)]
        positive = (
            f"{style}. "
            f"[FRAMES 5-7 RIGOROUS TRAINING]: {mei}. {action}. "
            f"Traditional organic world ONLY — no cybernetics on Master Mei. "
            f"Spoken beat: {chunk}."
        )
    elif beat == BEAT_MAYA:
        positive = (
            f"{ROLE_C_STYLE}. "
            f"[FRAMES 2-4 MATRIX / ILLUSION OF MAYA]: Rich metaphoric imagery — "
            f"a biomechanical Maya goddess/entity holding glowing digital chains "
            f"around human necks in a desolate cyberpunk wasteland. "
            f"High-concept cinematic, NOT ordinary people. Spoken beat: {chunk}."
        )
        negative = ROLE_C_NEGATIVE
    elif beat == BEAT_GEARS:
        positive = (
            f"{ROLE_C_STYLE}. "
            f"[FRAMES 2-4 MATRIX]: Masses of cyberpunk slaves tethered to massive "
            f"mechanical gears and propaganda screens in a desolate wasteland — "
            f"glowing neural collars, neon wires, VR face visors. Spoken beat: {chunk}."
        )
        negative = ROLE_C_NEGATIVE
    elif beat == BEAT_PANOPTICON:
        positive = (
            f"{ROLE_C_STYLE}. "
            f"[FRAMES 2-4 MATRIX]: Panopticon surveillance towers loom over herds of "
            f"wired digital slaves in neon rain — biomechanical control imagery, "
            f"high-concept dystopia. Spoken beat: {chunk}."
        )
        negative = ROLE_C_NEGATIVE
    elif beat == BEAT_DOPAMINE:
        positive = (
            f"{ROLE_C_STYLE}. "
            f"[FRAMES 8-9 DIGITAL TRAP]: Dystopian crowds consumed by cheap dopamine "
            f"devices, glowing screens fused to faces, slumped posture, neural tethers — "
            f"high-concept Matrix addiction. Spoken beat: {chunk}."
        )
        negative = ROLE_C_NEGATIVE
    elif beat == BEAT_BREAKFREE:
        positive = (
            f"{ROLE_B_STYLE}. "
            f"[FRAMES 8-9 DISCIPLINE]: A SINGLE focused martial monk tearing free from "
            f"glowing digital chains / VR tether — sweat, determination, break from the "
            f"dopamine masses. Absolutely no Master Mei. Spoken beat: {chunk}."
        )
        negative = ROLE_B_NEGATIVE
    elif role == ROLE_MASTER:
        mei = _clean_mei_dna(visual_dna)
        env = (hook_env or "").strip() or environment_for_role(role, act_index)
        positive = (
            f"{style}. [SUBJECT]: {mei}. ENVIRONMENT: {env}. Spoken beat: {chunk}."
        )
    elif role == ROLE_DISCIPLE:
        positive = (
            f"{style}. [SUBJECT]: {ROLE_B_SUBJECT}, intense training. "
            f"ENVIRONMENT: {environment_for_role(role, act_index)}. Spoken beat: {chunk}."
        )
    else:
        positive = (
            f"{style}. [SUBJECT]: {ROLE_C_SUBJECT}. "
            f"ENVIRONMENT: {environment_for_role(role, act_index)}. Spoken beat: {chunk}."
        )

    positive = re.sub(r"\s{2,}", " ", positive).strip()
    return positive, negative


def _clean_mei_dna(visual_dna: str) -> str:
    mei = (visual_dna or "").strip() or ROLE_A_SUBJECT
    mei = re.sub(
        r"\b(?:cyber|bionic|neon|vr|headset|neural|wire|cable|implant)\w*\b",
        "",
        mei,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s{2,}", " ", mei).strip(" ,.")
