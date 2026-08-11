# -*- coding: utf-8 -*-
"""
Master Mei Visual Engine — 3 Visual Roles (anti-contamination).

Keyframe cadence: 8–10 acts (hard-capped in page_config).
  ROLE A  Master Mei — traditional wisdom (avatar/DNA ONLY here)
  ROLE B  Disciples — organic physical training (no Master DNA)
  ROLE C  Digital Slaves — dystopian tech addiction (NEVER Master Mei)

Aesthetic: role-separated contrast — never fuse cyberpunk onto Master Mei.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Sequence

from avatar_engine.visual_compiler import (
    ILLUSTRATION_STYLE_GOLDEN,
    MASTER_STYLE_ANCHOR_DEFAULT,
    SAFETY_STAMP,
    THEME_KEYWORDS,
    VISUAL_MATRIX,
    compile_flux_prompt,
    compile_theme_prompt,
    shot_type_for_act,
)

_LOG = logging.getLogger(__name__)

CANONICAL_AVATAR_RELPATH: str = "channels_config/master_mei/avatar_reference/avatar.png"

# Re-export for callers that import from mei_visual
__all__ = (
    "MASTER_STYLE_ANCHOR_DEFAULT",
    "SAFETY_STAMP",
    "NARRATIVE_MODULES",
    "TEST_PREVIEW_MODULES",
    "TEST_PREVIEW_MODULES_3",
    "compile_flux_prompt",
    "build_test_preview_prompts",
    "build_master_mei_script_act_prompts",
    "compute_mei_appearance_slots",
    "classify_narrative_module",
    "MEI_DNA_FALLBACK",
)

# ---------------------------------------------------------------------------
# Anatomy + gender locks (used for Mei DNA shots + subject enrichment)
# ---------------------------------------------------------------------------
ANATOMY_LOCK: str = (
    "two intact arms, natural human proportions, sweat grime scars and skin tension, "
    "no broken sword, no warped blade, no floating metal, no surface-level accessories"
)

# Dense flesh-bolted cyberware — Giger-style hardware, not surface accessories.
IMPLANTED_CYBERWARE: str = (
    "dense cybernetic hardware embedded directly into organic flesh, heavy black "
    "hydraulic hoses, copper wiring, crude steel brackets bolted into skin and jawlines, "
    "leaking black oil and dark grease"
)

FEMALE_CORRUPTED_BEAUTY: str = (
    "gaunt scarred exhausted female industrial hybrid — weathered asymmetrical face, "
    "fiber-optic neck cables piercing scarred flesh, circuit board or mechanical "
    "half-face plate, copper wiring stitched into pale distressed skin"
)

MALE_GAUNT_STRAIN: str = (
    "gaunt scarred exhausted industrial male slave ages 20-35, weathered deformed face, "
    "heavy iron collar, thick tubes piercing muscle with raw scar tissue, "
    "exposed copper wiring stitched into pale distressed skin"
)

ANTI_GLOSS_STAMP: str = SAFETY_STAMP

_ATTIRE_FEMALE_ENSLAVED: str = IMPLANTED_CYBERWARE
_ATTIRE_MALE_FORGE: str = IMPLANTED_CYBERWARE
_ATTIRE_SLAVE: str = IMPLANTED_CYBERWARE

# ---------------------------------------------------------------------------
# Narrative modules — styles sourced from VISUAL_MATRIX (single source of truth)
# ---------------------------------------------------------------------------
NARRATIVE_MODULES: dict[str, dict[str, str]] = {
    "trap": {
        "label": "Act I — The Trap / Digital Slavery",
        "style": VISUAL_MATRIX["trap"],
        "attire": "",
    },
    "dopamine": {
        "label": "Trap — bio-mechanical feeds & cyber pods",
        "style": VISUAL_MATRIX["dopamine"],
        "attire": "",
    },
    "propaganda": {
        "label": "Trap — VR headsets & holographic propaganda",
        "style": VISUAL_MATRIX["propaganda"],
        "attire": "",
    },
    "system": {
        "label": "Trap — panopticon watchtowers & wired herds",
        "style": VISUAL_MATRIX["system"],
        "attire": "",
    },
    "forge": {
        "label": "Act II — The Forge / Master Mei training",
        "style": VISUAL_MATRIX["forge"],
        "attire": "",
    },
    "training": {
        "label": "Forge — Mei overseeing disciple kata",
        "style": VISUAL_MATRIX["training"],
        "attire": "",
    },
    "liberation": {
        "label": "Act III — Liberation / severing neural cords",
        "style": VISUAL_MATRIX["liberation"],
        "attire": "",
    },
    "rebellion": {
        "label": "Liberation — disciples shattering chains",
        "style": VISUAL_MATRIX["rebellion"],
        "attire": "",
    },
    "focus": {
        "label": "Liberation — sovereign martial stance",
        "style": VISUAL_MATRIX["focus"],
        "attire": "",
    },
    "business": {
        "label": "Liberation — sovereignty / strategy altar",
        "style": VISUAL_MATRIX["business"],
        "attire": "",
    },
    # Compatibility aliases
    "tech_slavery": {"label": "alias→trap", "style": VISUAL_MATRIX["trap"], "attire": ""},
    "trap_beauty": {"label": "alias→propaganda", "style": VISUAL_MATRIX["propaganda"], "attire": ""},
    "warrior_forge": {"label": "alias→training", "style": VISUAL_MATRIX["training"], "attire": ""},
    "panopticon": {"label": "alias→system", "style": VISUAL_MATRIX["system"], "attire": ""},
    "art_of_war": {"label": "alias→forge", "style": VISUAL_MATRIX["forge"], "attire": ""},
    "digital_blindness": {"label": "alias→propaganda", "style": VISUAL_MATRIX["propaganda"], "attire": ""},
}

# --test-images: one beat per story act
TEST_PREVIEW_MODULES: tuple[str, ...] = (
    "trap",
    "forge",
    "liberation",
)
TEST_PREVIEW_MODULES_3: tuple[str, ...] = (
    "trap",
    "forge",
    "liberation",
)

# Optional sparse Mei supervision fragment (warrior_forge only, never likeness-locked).
_MEI_SUPERVISION_FRAGMENT: str = (
    "Master Mei, the stern Asian elder with white topknot and long white beard in dark "
    "gold robes, standing on a rusted high walkway above, silently supervising disciple training"
)

# Beat → module classification keywords
_MODULE_BEAT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "dopamine": THEME_KEYWORDS["dopamine"],
    "propaganda": THEME_KEYWORDS["propaganda"],
    "system": THEME_KEYWORDS["system"],
    "rebellion": THEME_KEYWORDS["rebellion"],
    "training": THEME_KEYWORDS["training"],
    "business": THEME_KEYWORDS["business"],
    "focus": THEME_KEYWORDS["focus"],
}

# Story-phase → preferred visual modules
_PHASE_MODULE_CYCLE: dict[str, tuple[str, ...]] = {
    "trap": ("trap", "dopamine", "propaganda", "system"),
    "forge": ("forge", "training"),
    "liberation": ("liberation", "rebellion", "focus", "business"),
    # legacy aliases
    "hook": ("trap", "dopamine"),
    "conflict": ("trap", "dopamine", "propaganda", "system"),
    "pivot": ("forge", "training"),
    "conclusion": ("liberation", "rebellion", "focus"),
}

# Western/Occidental majority (~80%) + minority Asian women among enslaved masses.
# Master Mei remains the sole Oriental/Asian ancestral wisdom figure in Act II/Final.
DIVERSE_ETHNICITIES: tuple[str, ...] = (
    "an attractive young Caucasian man in his mid-20s",
    "a stylish attractive young Caucasian woman in her mid-20s",
    "an attractive young Hispanic man in his early 30s",
    "a stylish attractive young Hispanic woman in her late 20s",
    "an attractive young Black man in his early 30s",
    "a stylish attractive young Black woman in her late 20s",
    "an attractive young mixed-ethnicity Western man in his 30s",
    "a stylish attractive young mixed-ethnicity Western woman in her 20s",
    "a stylish attractive young Asian woman in her mid-20s",  # minority among masses
    "an attractive young Caucasian man in his late 20s",
)

DIVERSITY_LOCK: str = (
    "SUBJECT DIVERSITY: Act I enslaved masses (gaunt, passive, wired); "
    "Act II Master Mei + active sweating disciples; "
    "Act III liberated disciples in kinetic martial action. "
    "Master Mei likeness-locked throughout Act II + Final."
)

CONCRETENESS_LOCK: str = (
    "ABSOLUTE CONCRETENESS: translate abstract ideas into ultra-realistic biomechanical "
    "body horror — flesh-fused copper wiring, kneeling slave hordes, hydraulic forge training."
)

MONK_BAN: str = (
    "STRICT BAN: random old monks, generic temple elders, peaceful meditation gardens, "
    "clean modern rooms, female bare chests, nudity, glossy neon superhero suits, "
    "figurine posing, floating sci-fi glasses."
)

NEGATIVE_PROMPT_BAN_LIST: str = (
    "text, words, typography, font, letters, sample, watermark, signature, "
    "caption, quotes, UI elements, subtitles, labels, "
    "NO repetitive monk headshots, NO static talking faces, NO idle single monk close-up, "
    "NO blank stare into camera, NO generic temples, "
    "NO clean/happy futuristic cities, NO bare breasts, NO cleavage, NO nudity, "
    "NO missing limbs, NO broken sword, NO warped blade, NO floating metal, "
    "NO external VR goggles, NO floating sci-fi glasses, NO holding smartphone, "
    "NO cartoon, NO stylized 3D, NO glossy CGI, NO polished neon superhero suits, "
    "NO figurine posing, NO looking at camera like a product shot, NO clean skin, "
    "NO male fitness-model physique, NO gym bro posing, NO clean high-fashion posing."
)

FIXED_NEGATIVE_PROMPT: str = (
    "--no text, words, typography, font, letters, sample, watermark, signature, "
    "caption, quotes, UI elements, subtitles, labels, "
    "idle monk close-up, static single monk, blank stare, bare breasts, cleavage, "
    "nudity, missing limbs, broken sword, warped blade, floating metal, cartoon, "
    "glossy CGI, neon superhero suit, figurine posing, VR goggles, floating glasses, "
    "clean skin, fitness model, gym posing, high-fashion posing"
)

MEI_DNA_FALLBACK: str = (
    "Master Mei is an OLDER East Asian martial arts grandmaster (approx 72 years old), "
    "inspired by Pai Mei fused with an ancient Eastern master: implacable, "
    "uncompromising, stern, demanding. Pure radiant snow-white hair in a high topknot "
    "with hairpin plus two long distinct white strands falling down both sides of his "
    "chest and shoulders, iconic extra-long ultra-thick snow-white eyebrows sweeping "
    "past his temples, full majestic snow-white beard to mid-chest with matching "
    "flowing mustache, wise deeply lined face, calm authoritative presence. "
    "White inner robe, black outer vest with gold lapel embroidery, wooden mala beads. "
    "NEVER a middle-aged short-haired warrior. NEVER modern athletic wear. "
    "NEVER clean-shaven. NEVER black hair. NEVER generic indoor temples by default. "
    "STRICT SINGLE MASTER RULE: Master Mei is the ONLY master/elder allowed."
)

_BAN_PLAIN: str = (
    "STRICT BAN: ordinary teenagers in bedrooms, desks with smartphones, "
    "casual lifestyle rooms, mundane offices, soft wellness photography, "
    "plain modern selfie culture, close-ups of random/generic samurai faces, "
    "close-ups of random men, any elder/master who is not Master Mei. "
    "Environment for Master Mei: high-altitude epic nature (~70%) or open-air "
    "mountain shrine/courtyard (~30%) — never generic indoor temple halls, "
    "never cyberpunk implants, never neon wires on his body."
)

_PHOTOREAL: str = (
    "photorealistic 8k, cinematic lighting, detailed textures, octane render, "
    "cinematic 8k resolution, photorealistic raw photography, volumetric lighting, "
    "ultra-sharp, full-bleed, no borders, no CGI plastic look."
)

_MEI_FIRST_POSES: tuple[str, ...] = (
    "POSE: Master Mei meditating in lotus on a high-altitude cliff above a sea of clouds "
    "— kinetic stillness, never idle indoor temple, no neon, no cybernetics.",
    "POSE: Master Mei guiding a disciple's blade mid-kata under cold rain on a misty ridge.",
    "POSE: Master Mei channeling energy through an open-palm martial form on a storm cliff.",
)

_MEI_MIDDLE_POSES: tuple[str, ...] = (
    "POSE (DYNAMIC FORGE): Master Mei INSTRUCTING sweating disciples in rigorous cold/rain "
    "kata — stern correction of stance, rain hammering wet stone. Full-body action shot.",
    "POSE (DYNAMIC FORGE): Master Mei overseeing high-intensity martial conditioning — "
    "relentless pace, forge fire and neon rain, never a static portrait.",
)

_MEI_FINAL_POSES: tuple[str, ...] = (
    "POSE (LIBERATION CLOSE): Master Mei among liberated disciples after neural cords are "
    "severed — sovereign stillness after battle, rainy neon temple, not a blank stare.",
    "POSE (LIBERATION CLOSE): Master Mei standing with unbroken disciples, torn fiber-optic "
    "wires at their feet, implacable authority, kinetic aftermath framing.",
)

# Per-module beat → action hints (scoped so cross-theme words in a chunk
# cannot pull the wrong physical action into a forced pillar preview).
_MODULE_ACTION_HINTS: dict[str, tuple[tuple[str, str], ...]] = {
    "tech_slavery": (
        (r"\bcage\b|\bchain\b|\bleash\b|\bslave|\bkneel",
         "kneeling in rusted chains as cables and harpoons fuse into flesh"),
        (r"\bscroll\b|\bphone\b|\bdevice\b|\balgorithm|\bscreen|\bcrt",
         "glazed eyes locked on dirty flickering CRT screens"),
        (r"\bcomfort\b|\bsoft\b|\bbleed\b|\bvitality",
         "vitality draining into glowing dirty monitors through spinal ports"),
    ),
    "trap_beauty": (
        (r"\bsiren\b|\bbeauty\b|\btempt\b|\bdopamine\b|\bensnare|\billusion",
         "beauty corrupted as rusted jaw-clamps drain vitality into holograms"),
        (r"\bdistract\b|\bease\b|\bhollow|\bpuncture",
         "fiber-optic cables forced under skin in a filth-covered alley"),
    ),
    "warrior_forge": (
        (r"\bforge\b|\bhardship\b|\bpain\b|\btrial|\banvil|\bash",
         "striking cold iron anvils as hydraulic brackets tear at fused flesh"),
        (r"\bkatana\b|\bmartial\b|\bstance\b|\btrain|\bdiscipline|\bresilience",
         "forced into low stances by iron pistons stitched into the spine"),
        (r"\bstrength\b|\bground\b|\bshield|\bchain|\bmuscle",
         "dragging chained iron blocks, faces contorted in mechanical agony"),
    ),
    "panopticon": (
        (r"\bbreak\b|\bsever\b|\bsnap\b|\breclaim\b|\bsovereign|\bhold\b",
         "hardwired slaves staring up at a CRT surveillance monolith"),
        (r"\bfortress\b|\bcapital\b|\bwill\b|\bunassailable|\bmountain|\bclockwork",
         "coaxial cables bursting from necks into the rusted CRT monolith"),
    ),
}

_MODULE_DEFAULT_ACTIONS: dict[str, str] = {
    "tech_slavery": "kneeling as steel collar rigs bolt into neck and spine",
    "trap_beauty": "beauty drained by heavy iron headset into decaying holograms",
    "warrior_forge": "pulling rusted chains on cold anvils with hardware fused into shoulders",
    "panopticon": "standing wired into the CRT monolith via spinal conduits",
}


# ---------------------------------------------------------------------------
# Path / avatar helpers
# ---------------------------------------------------------------------------

def resolve_master_mei_avatar_path(engine_root: "str | Path | None" = None) -> Path:
    if engine_root is None:
        try:
            import config as app_config
            root = Path(app_config.ENGINE_ROOT)
        except Exception:
            root = Path(__file__).resolve().parents[1]
    else:
        root = Path(engine_root)
    return (root / CANONICAL_AVATAR_RELPATH).resolve()


def assert_avatar_exists(engine_root: "str | Path | None" = None) -> Path:
    path = resolve_master_mei_avatar_path(engine_root)
    if not path.is_file():
        _LOG.error(
            "MASTER_MEI ZERO-HALLUCINATION FAIL | avatar.png missing at %s",
            path,
        )
    else:
        _LOG.info("MASTER_MEI | avatar reference bound → %s", path)
    return path


# July-24 golden: Mei on hook + training/focus/business/finale — never dopamine/system B-roll
_MEI_THEMES: frozenset[str] = frozenset(
    {"hook", "training", "forge", "focus", "business", "finale", "final", "cta"}
)
_NO_MEI_THEMES: frozenset[str] = frozenset(
    {"dopamine", "propaganda", "system", "trap", "siren", "rebellion", "liberation"}
)


def compute_mei_appearance_slots(
    n_acts: int,
    themes: "Sequence[str] | None" = None,
) -> set[int]:
    """
    Avatar/DNA slots = ROLE A (Master Mei) only.

    Delegates to ``assign_visual_roles`` so disciples/slaves never receive
    Master Mei injection. Cap ~30% of scenes.
    """
    from avatar_engine.visual_roles import assign_visual_roles, mei_slots_from_roles

    roles = assign_visual_roles(n_acts, themes=themes)
    return mei_slots_from_roles(roles)


def ethnicity_for_act(act_index: int) -> str:
    """Round-robin diverse ethnicity base (clothing comes from the narrative module)."""
    return DIVERSE_ETHNICITIES[act_index % len(DIVERSE_ETHNICITIES)]


def classify_mei_visual_theme(text: str) -> str:
    """Return beat theme within the active story phase."""
    lo = (text or "").lower()
    scores = {
        key: sum(1 for kw in kws if kw in lo)
        for key, kws in THEME_KEYWORDS.items()
    }
    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    if scores[best] <= 0:
        return "trap"
    return best


def _classify_metaphor_for_beat(
    spoken_beat: str,
    subject: str = "",
    *,
    story_phase: str = "",
) -> str:
    """
    Map spoken beat → golden July-24 theme (keyword scores, no 3-act force).

    Matches logged sequences on v12/v14/v15-era reels:
      dopamine | training | focus | business | system | rebellion | siren
    """
    del story_phase
    beat = (spoken_beat or "").strip()
    lo = f"{subject} {beat}".lower()
    scores = {
        key: sum(1 for kw in kws if kw in lo)
        for key, kws in THEME_KEYWORDS.items()
    }
    if "panopticon" in lo or "watchtower" in lo:
        scores["system"] = scores.get("system", 0) + 3
    if "vr" in lo or "goggle" in lo or "propaganda" in lo or "headset" in lo or "fog" in lo:
        scores["propaganda"] = scores.get("propaganda", 0) + 2
        scores["dopamine"] = scores.get("dopamine", 0) + 1
    if "dojo" in lo or "kata" in lo or "sword" in lo or "disciple" in lo or "waterfall" in lo:
        scores["training"] = scores.get("training", 0) + 3
    if "solitude" in lo or "stillness" in lo or "meditat" in lo or "citadel" in lo:
        scores["focus"] = scores.get("focus", 0) + 3
    if ("tear" in lo or "sever" in lo or "rip" in lo) and (
        "wire" in lo or "cable" in lo or "chain" in lo or "neural" in lo
    ):
        scores["rebellion"] = scores.get("rebellion", 0) + 4
    if re.search(r"\bsiren\b|\blust\b|\bfemme\b|\btempt|\ballure\b|\bseduction\b", lo):
        scores["siren"] = scores.get("siren", 0) + 4

    if beat and max(scores.values()) > 0:
        return max(scores, key=scores.get)  # type: ignore[arg-type]
    return classify_mei_visual_theme(f"{subject} {beat}".strip()) or "dopamine"


def _mei_act_needs_avatar(theme: str) -> bool:
    """True for Forge / finale themes where Master Mei is the active guide."""
    return theme in ("forge", "training", "finale", "final", "cta")


# ---------------------------------------------------------------------------
# Narrative module classification & dynamic subject extraction
# ---------------------------------------------------------------------------

def _phase_for_act(act_index: int, n_acts: int) -> str:
    """Return story phase: trap | forge | liberation (legacy aliases mapped)."""
    from avatar_engine.mei_narrative import story_phase_for_act

    return story_phase_for_act(act_index, n_acts)


def classify_narrative_module(
    spoken_beat: str,
    *,
    act_index: int = 0,
    n_acts: int = 19,
    force_module: "str | None" = None,
) -> str:
    """
    Map a script beat → one of the 4 narrative modules.
    Prefer beat keyword scores; fall back to phase-aware cycling.
    """
    if force_module and force_module in NARRATIVE_MODULES:
        return force_module

    lo = (spoken_beat or "").lower()
    scores = {
        key: sum(1 for kw in kws if kw in lo)
        for key, kws in _MODULE_BEAT_KEYWORDS.items()
    }
    best = max(scores, key=scores.get)
    if scores[best] > 0:
        return best

    phase = _phase_for_act(act_index, n_acts)
    cycle = _PHASE_MODULE_CYCLE.get(phase) or TEST_PREVIEW_MODULES
    return cycle[act_index % len(cycle)]


def _action_from_beat(spoken_beat: str, module_key: str) -> str:
    """Derive a short physical action phrase scoped to the active narrative module."""
    text = (spoken_beat or "").strip()
    hints = _MODULE_ACTION_HINTS.get(module_key) or ()
    if text:
        for pat, action in hints:
            if re.search(pat, text, flags=re.IGNORECASE):
                return action
    return _MODULE_DEFAULT_ACTIONS.get(
        module_key, "standing in a dark industrial wasteland"
    )


def _is_female_ethnicity(descriptor: str) -> bool:
    lo = (descriptor or "").lower()
    return "woman" in lo or "female" in lo


def _attire_for_module(module_key: str, ethnicity: str, act_index: int) -> str:
    """Biomechanical implant detail only — no tactical apparel overrides."""
    del act_index, module_key, ethnicity
    return IMPLANTED_CYBERWARE


def extract_dynamic_subject(
    spoken_beat: str,
    *,
    act_index: int,
    module_key: str,
) -> str:
    """Enrichment helper — production prompts use visual_compiler matrix + beat."""
    ethnicity = ethnicity_for_act(act_index)
    attire = _attire_for_module(module_key, ethnicity, act_index)
    action = _action_from_beat(spoken_beat, module_key)
    female = _is_female_ethnicity(ethnicity)

    if module_key == "trap_beauty":
        return f"{FEMALE_CORRUPTED_BEAUTY}, {attire}, {action}, {IMPLANTED_CYBERWARE}"

    if module_key == "tech_slavery":
        if female:
            return (
                f"{FEMALE_CORRUPTED_BEAUTY}, {attire}, {IMPLANTED_CYBERWARE}, {action}, "
                "kneeling among young cybernetic subjects hardwired to CRT monitors"
            )
        return (
            f"{ethnicity}, {MALE_GAUNT_STRAIN}, {attire}, {IMPLANTED_CYBERWARE}, {action}, "
            "kneeling among young cybernetic subjects"
        )

    if module_key == "warrior_forge":
        return (
            f"a group of {MALE_GAUNT_STRAIN}, {_ATTIRE_MALE_FORGE}, {action}, "
            "pulling rusted chains attached to cold iron anvils, not a fitness model"
        )

    if module_key in ("art_of_war", "panopticon"):
        return (
            f"stoic armored warrior calculating strategy amid broken screens and "
            f"rusted clockwork monoliths, {attire}, {action}"
        )

    if female:
        return f"{FEMALE_CORRUPTED_BEAUTY}, {attire}, {IMPLANTED_CYBERWARE}, {action}"
    return f"{ethnicity}, {MALE_GAUNT_STRAIN}, {attire}, {IMPLANTED_CYBERWARE}, {action}"


def assemble_modular_prompt(
    *,
    spoken_beat: str,
    module_key: str,
    style_anchor: str | None = None,
    act_index: int = 0,
    prefer_module_shot: bool = False,
    dynamic_subject: str = "",
    include_mei_supervision: bool = False,
    scene_first: bool = False,
    reference_folder: "str | Path | None" = None,
    use_vision_style: bool = False,
    per_beat_vision: bool = False,
) -> str:
    """
    Narrative-driven compile. When ``use_vision_style`` and a reference folder
    are set, Gemini Vision reads ``style_reference`` images and drives the prompt.
    """
    _ = (dynamic_subject, include_mei_supervision, scene_first)
    if use_vision_style and reference_folder:
        from avatar_engine.style_reader import generate_reference_driven_prompt
        from avatar_engine.visual_compiler import VISUAL_MATRIX

        shot = shot_type_for_act(
            act_index, module_key, prefer_module_default=prefer_module_shot,
        )
        beat = f"{shot} {(spoken_beat or '').strip()}".strip()
        return generate_reference_driven_prompt(
            spoken_beat_script=beat,
            concept_key=module_key,
            reference_folder_path=reference_folder,
            per_beat_vision=per_beat_vision,
            matrix_scene=VISUAL_MATRIX.get(module_key),
        )

    shot = shot_type_for_act(
        act_index, module_key, prefer_module_default=prefer_module_shot,
    )
    return compile_flux_prompt(
        spoken_beat_script=spoken_beat or "",
        concept_key=module_key,
        style_anchor=style_anchor,
        shot_type=shot,
    )


def build_test_preview_prompts(
    *,
    script: str,
    subject: str = "",
    style_anchor: "str | None" = None,
    segment_fn=None,
    module_keys: "Sequence[str] | None" = None,
    reference_folder: "str | Path | None" = None,
    use_vision_style: bool = False,
) -> list[str]:
    """Build golden pillar test prompts (no Vision rewrite, no Master Mei lead)."""
    del reference_folder, use_vision_style
    anchor = (style_anchor or "").strip() or MASTER_STYLE_ANCHOR_DEFAULT
    keys = tuple(module_keys) if module_keys else TEST_PREVIEW_MODULES
    n = len(keys)
    if segment_fn is None:
        words = (script or subject or "").split()
        if not words:
            chunks = [subject or "digital slavery"] * n
        else:
            size = max(1, len(words) // n)
            chunks = [
                " ".join(words[i * size: (i + 1) * size]) or (subject or "discipline")
                for i in range(n)
            ]
    else:
        chunks = segment_fn(script or subject, n)

    prompts: list[str] = []
    for i, module_key in enumerate(keys):
        beat = chunks[i] if i < len(chunks) else (subject or "")
        prompts.append(
            compile_theme_prompt(
                theme=module_key,
                base_style=anchor,
                visual_dna="",
                spoken_beat=beat or subject,
                subject=subject or beat,
            )
        )
        _LOG.info(
            "TEST_PREVIEW[%d] module=%s vision=OFF | beat=%.60s…",
            i + 1,
            module_key,
            (beat or "")[:60],
        )
    return prompts


# ---------------------------------------------------------------------------
# Full production act prompt builder
# ---------------------------------------------------------------------------

def _mei_pose_for_slot(act_index: int, n_acts: int, slots: set[int]) -> str:
    if act_index == 0:
        return _MEI_FIRST_POSES[0]
    if act_index == n_acts - 1:
        return _MEI_FINAL_POSES[act_index % len(_MEI_FINAL_POSES)]
    return _MEI_MIDDLE_POSES[0]


def build_master_mei_script_act_prompts(
    *,
    subject: str,
    script: str,
    n_acts: int,
    base_style: str,
    visual_dna: str,
    hook_env: str,
    segment_fn=None,
    master_style_anchor: "str | None" = None,
    reference_folder: "str | Path | None" = None,
    use_vision_style: bool = False,
) -> tuple[list[str], set[int], list[str], list[str]]:
    """
    Build N act prompts with STRICT 3-role separation (anti-contamination).

    ROLE A Master Mei — DNA / avatar ONLY here (traditional, no cybernetics)
    ROLE B Disciples — organic physical training (no Master DNA)
    ROLE C Digital Slaves — dystopia only (NEVER Master Mei)

    Returns ``(prompts, mei_slots, roles, negatives)``.
    ``base_style`` / ``master_style_anchor`` are ignored for global injection —
    each role carries its own pure style lock (no hybrid megacity×Mei blob).
    Vision rewrite stays OFF.
    """
    del reference_folder, use_vision_style, base_style, master_style_anchor

    from avatar_engine.visual_roles import (
        ROLE_MASTER,
        assign_frame_beats,
        assign_visual_roles,
        build_role_prompt,
        mei_slots_from_roles,
        plan_matrix_shots,
    )

    if segment_fn is None:
        def segment_fn(text: str, n: int) -> list[str]:  # type: ignore[misc]
            words = (text or "").split()
            if not words:
                return [""] * n
            size = max(1, len(words) // n)
            return [
                " ".join(words[i * size: (i + 1) * size]) or subject
                for i in range(n)
            ]

    dna = (visual_dna or MEI_DNA_FALLBACK).strip()
    # Strip cyber/tech tokens from DNA so Role A never carries implant language
    dna = re.sub(
        r"\b(?:cyber|bionic|neon|vr|headset|neural|wire|cable|implant|biomechanical)\w*\b",
        "",
        dna,
        flags=re.IGNORECASE,
    )
    dna = re.sub(r"\s{2,}", " ", dna).strip(" ,.")

    chunks = segment_fn(script or subject, n_acts)
    themes: list[str] = []
    for i in range(n_acts):
        chunk = chunks[i] if i < len(chunks) else ""
        if i == 0:
            themes.append("hook")
        elif i == n_acts - 1:
            themes.append("finale")
        else:
            themes.append(_classify_metaphor_for_beat(chunk, subject))

    roles = assign_visual_roles(n_acts, themes=themes, spoken_chunks=chunks)
    beats = assign_frame_beats(n_acts, spoken_chunks=chunks)
    slots = mei_slots_from_roles(roles)
    shot_plan = plan_matrix_shots(
        n_acts, roles, beats=beats, spoken_chunks=chunks,
    )

    prompts: list[str] = []
    negatives: list[str] = []
    for i in range(n_acts):
        chunk = chunks[i] if i < len(chunks) else ""
        role = roles[i]
        beat = beats[i] if i < len(beats) else ""
        pos, neg = build_role_prompt(
            role=role,
            visual_dna=dna if role == ROLE_MASTER else "",
            spoken_beat=chunk,
            subject=subject,
            act_index=i,
            hook_env=hook_env if (i == 0 and role == ROLE_MASTER) else "",
            beat=beat,
            shot_key=shot_plan[i] if i < len(shot_plan) else None,
            episode_seed=subject,
        )
        # Visual QA gate — Scene 1 / Scene 3 / penultimate + firearm ban
        try:
            from avatar_engine.visual_roles import validate_mei_visual_prompt

            _ok, _violations, _repaired = validate_mei_visual_prompt(
                pos, act_index=i, n_acts=n_acts, beat=beat,
            )
            if not _ok:
                _LOG.warning(
                    "VISUAL_QA | act=%d violations=%s — applying RAG override",
                    i + 1, _violations,
                )
                pos = _repaired
        except Exception as _qa_exc:  # noqa: BLE001
            _LOG.debug("VISUAL_QA skipped act=%d: %s", i + 1, _qa_exc)
        prompts.append(pos)
        negatives.append(neg)

    mei_pct = (100.0 * len(slots) / max(1, n_acts))
    _wide_n = sum(1 for s in shot_plan if s and str(s).startswith("wide_"))
    _det_n = sum(1 for s in shot_plan if s and not str(s).startswith("wide_") and s)
    _LOG.info(
        "MASTER_MEI 10-FRAME LORE | n_acts=%d | roles=%s | beats=%s | "
        "shots=%s (wide=%d detail=%d) | mei_slots=%s (%.0f%%) | DNA=ROLE_A_ONLY",
        n_acts, roles, beats, shot_plan, _wide_n, _det_n, sorted(slots), mei_pct,
    )
    return prompts, slots, roles, negatives
