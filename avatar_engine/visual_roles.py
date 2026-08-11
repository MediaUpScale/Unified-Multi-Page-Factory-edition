# -*- coding: utf-8 -*-
"""
Master Mei — 10-frame narrative lore + 3 Visual Roles (anti-contamination).

FRAME LORE (strict distribution for 10-act reels)
------------------------------------------------
1       INTRO          — Master Mei in dynamic high-altitude nature / open-air shrine
2–4     MATRIX / MAYA  — biomechanical illusion, gears, panopticon slaves
5–7     TRAINING       — Master Mei instructing monks in harsh elements
8–9     TRAP vs WILL   — dopamine crowds vs monk breaking free
10      OUTRO / CTA    — Master Mei on mountain ridge / open-air shrine, eyes on camera

Avatar / DNA injection: ROLE A frames only (1, 5–7, 10).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
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

# Duration-scaled lore tables — primary 90–120s → 8 / 9 / 10 frames
# Scene 1 = INTRO (hook ≤8s); Scene 3 = GEARS (biomechanical vault override);
# Penultimate = BREAKFREE (Infinite Pod Network override)
_FRAME_LORE_7: tuple[tuple[str, str], ...] = (
    (ROLE_MASTER, BEAT_INTRO),       # 1
    (ROLE_SLAVE, BEAT_MAYA),         # 2
    (ROLE_SLAVE, BEAT_GEARS),        # 3
    (ROLE_MASTER, BEAT_TRAINING),    # 4 — sole training (cap=1)
    (ROLE_SLAVE, BEAT_DOPAMINE),     # 5
    (ROLE_DISCIPLE, BEAT_BREAKFREE), # 6 — penultimate (N-1)
    (ROLE_MASTER, BEAT_OUTRO),       # 7
)

_FRAME_LORE_8: tuple[tuple[str, str], ...] = (
    (ROLE_MASTER, BEAT_INTRO),       # 1 — hook ≤8s
    (ROLE_SLAVE, BEAT_MAYA),         # 2
    (ROLE_SLAVE, BEAT_GEARS),        # 3 — biomechanical slaves OVERRIDE
    (ROLE_SLAVE, BEAT_PANOPTICON),   # 4
    (ROLE_MASTER, BEAT_TRAINING),    # 5
    (ROLE_SLAVE, BEAT_DOPAMINE),     # 6
    (ROLE_DISCIPLE, BEAT_BREAKFREE), # 7 — penultimate (N-1)
    (ROLE_MASTER, BEAT_OUTRO),       # 8
)

_FRAME_LORE_9: tuple[tuple[str, str], ...] = (
    (ROLE_MASTER, BEAT_INTRO),       # 1
    (ROLE_SLAVE, BEAT_MAYA),         # 2
    (ROLE_SLAVE, BEAT_GEARS),        # 3
    (ROLE_SLAVE, BEAT_PANOPTICON),   # 4
    (ROLE_MASTER, BEAT_TRAINING),    # 5
    (ROLE_MASTER, BEAT_TRAINING),    # 6 — max 2 consecutive
    (ROLE_SLAVE, BEAT_DOPAMINE),     # 7
    (ROLE_DISCIPLE, BEAT_BREAKFREE), # 8 — penultimate (N-1)
    (ROLE_MASTER, BEAT_OUTRO),       # 9
)

_FRAME_LORE_10: tuple[tuple[str, str], ...] = (
    (ROLE_MASTER, BEAT_INTRO),       # 1
    (ROLE_SLAVE, BEAT_MAYA),         # 2
    (ROLE_SLAVE, BEAT_GEARS),        # 3
    (ROLE_SLAVE, BEAT_PANOPTICON),   # 4
    (ROLE_MASTER, BEAT_TRAINING),    # 5
    (ROLE_MASTER, BEAT_TRAINING),    # 6 — max 2 consecutive (no 3rd training)
    (ROLE_SLAVE, BEAT_DOPAMINE),     # 7
    (ROLE_SLAVE, BEAT_GEARS),        # 8 — matrix pressure before liberation
    (ROLE_DISCIPLE, BEAT_BREAKFREE), # 9 — penultimate (N-1)
    (ROLE_MASTER, BEAT_OUTRO),       # 10
)

_PROMPTS_DIR: Path = (
    Path(__file__).resolve().parents[1]
    / "channels_config"
    / "master_mei"
    / "prompts"
)
_PENULTIMATE_FILE: Path = _PROMPTS_DIR / "penultimate_frame.txt"
_SCENE_03_FILE: Path = _PROMPTS_DIR / "scene_03_biomechanical_slaves.txt"
_SCENE_01_FILE: Path = _PROMPTS_DIR / "scene_01_hook_meditation.txt"
_SCENE_07_FILE: Path = _PROMPTS_DIR / "scene_07_human_cyborg_closeup.txt"
_SCENE_08_FILE: Path = _PROMPTS_DIR / "scene_08_unplug_escape.txt"
_SCENE_FINAL_FILE: Path = _PROMPTS_DIR / "scene_final_hook.txt"
_MEI_ANCHOR_FILE: Path = _PROMPTS_DIR / "master_mei_base_anchor.txt"

# Hardcoded FLUX identity lock — Scene 1 + Final Hook (snow-white DNA)
MEI_BASE_ANCHOR: str = (
    "Master Mei — wise East Asian martial arts grandmaster meditating serenely. "
    "Pure radiant snow-white hair styled in a high topknot secured with a polished "
    "metallic or bamboo hairpin, featuring two long distinct strands of white hair "
    "falling down both sides of his chest and shoulders. Iconic extra-long ultra-thick "
    "snow-white eyebrows sweeping dramatically outward past his temples. Full majestic "
    "snow-white beard extending all the way down to mid-chest with a matching flowing "
    "snow-white mustache. Weathered skin, deep-set eyes, calm meditative expression. "
    "White inner robe, black outer vest with gold lapel embroidery, wooden mala prayer "
    "beads resting on his chest."
)

# Dynamic Master Mei environments — templates are REFERENCES only; backdrops must
# vary with each episode's philosophical narrative (weather / lighting / setting).
MEI_ENV_LLM_DIRECTIVE: str = (
    "Act templates are REFERENCES only. Dynamically modify backdrops, weather, and "
    "lighting (golden-hour sunlight, snowy mountain fog, storm cliffs, misty ridges) "
    "to mirror the core philosophical concept of the specific script. Prefer "
    "high-altitude epic nature (~70%) over open-air mountain shrines (~30%). "
    "NEVER default to generic indoor temple halls."
)

# ~70% — high-altitude epic nature
MEI_PRIMARY_ENVIRONMENTS: tuple[str, ...] = (
    "towering jagged Himalayan precipice above a sea of clouds",
    "high alpine cliff ledge overlooking endless cloud valleys",
    "misty mountain ridge summit under cold wind",
    "natural mountain spring carved into living rock at high altitude",
    "rocky Alps precipice at golden hour above rolling fog",
    "snow-dusted peak ledge with vast alpine vista",
    "sheer storm-lit cliff above a boiling sea of clouds",
    "high-altitude stone outcrop above endless mist valleys",
)

# ~30% — open-air ancient stone shrines / outdoor mountain courtyard temples
MEI_SECONDARY_ENVIRONMENTS: tuple[str, ...] = (
    "open-air ancient stone shrine on a mountain terrace during rain",
    "outdoor mountain courtyard temple veiled in thick fog",
    "rain-soaked open-air stone shrine overlooking alpine ridges",
    "misty outdoor mountain temple courtyard with wet stone and incense haze",
)

_MEI_PHILOSOPHY_ATMOSPHERE: tuple[tuple[str, str], ...] = (
    (
        r"\b(?:storm|wrath|rage|war|battle|refuse|refusal|fortress|armor)\b",
        "storm-lit cliffs, dramatic thunderclouds, cold cutting wind",
    ),
    (
        r"\b(?:snow|winter|cold|discipline|hardship|forge|trial|pain)\b",
        "snowy mountain fog, icy wind, pale winter light",
    ),
    (
        r"\b(?:dawn|awaken|rise|sunrise|clarity|truth|sovereign|legacy)\b",
        "serene golden-hour sunrise light, vast alpine vista",
    ),
    (
        r"\b(?:shadow|illusion|matrix|trap|drain|dark|maya|dopamine)\b",
        "misty ridge under brooding overcast sky, soft fog banks",
    ),
    (
        r"\b(?:still|calm|meditat|silence|peace|quiet|breath)\b",
        "soft mountain haze, quiet dawn rim light, absolute stillness",
    ),
)

_FALLBACK_PENULTIMATE: str = (
    "===VARIATION_A===\n"
    "Cyberpunk dystopian Earth, attention monopoly era: High-action cinematic shot inside "
    "a massive atmospheric pod vault under neon emergency billboards and hanging cable "
    "bundles. In the foreground, a muscular warrior violently smashes open his polymer "
    "incubation chamber. Shattered glass, steam, and thick liquid splash across the metal "
    "floor as he tears heavy spinal umbilical cables from his back, creating bright "
    "electric sparks. In the background, endless glowing incubation pods stretch into the "
    "dark abyss under red emergency lights while industrial haze and cybernetic conduits "
    "frame the escape amid attention monopoly arrays and crimson AI beacons.\n"
    "===VARIATION_B===\n"
    "Cyberpunk dystopian Earth, attention monopoly era: Vast wide-angle view of a towering "
    "matrix of translucent human incubation pods submerged in viscous fluid. In the "
    "immediate foreground, a liberated human forcibly rips off a skull-mounted sensory "
    "chassis, bending metal clamps with raw power. Severed fiber-optic cables spark on "
    "the wet grid floor. Monolithic central AI array glowing crimson in the far dark "
    "background amid industrial haze, neon billboards, and cybernetic conduits."
)
_FALLBACK_SCENE_03: str = (
    "Cyberpunk dystopian Earth, attention monopoly era: Dark cinematic shot of two "
    "emaciated male slaves kneeling in a Matrix post-apocalyptic wasteland. Background "
    "towers feature massive holographic billboards displaying garbled propaganda text. "
    "The left slave wears a massive crude VR headset surgically bolted to his skull, "
    "stealing his sight, with intense white light forcefully leaking from the seams onto "
    "his face. The right slave agonizes with intricate microchips, thick black wires, "
    "and circuit boards crammed into his wide-open mouth, throat, and brain stem. Pale "
    "skin, grime, oppressive bio-horror."
)
_FALLBACK_SCENE_08: str = (
    "Cyberpunk dystopian Earth, attention monopoly era: Wide cinematic shot. An ancient "
    "samurai in detailed dark armor swings his sharp katana, slicing through a giant "
    "flickering holographic screen of matrix code and propaganda. Sparks and fractured "
    "digital code spill into the air where the blade cuts. Behind the shattered hologram, "
    "a dark desolate machine city is revealed. Rain-slicked ground, cyan and orange "
    "lighting, dramatic movement, high contrast, gritty atmosphere."
)
_FALLBACK_SCENE_07: str = (
    "Cyberpunk dystopian Earth, attention monopoly era: Dark cinematic shot of a single "
    "human slave trapped inside a hyper-advanced computing apparatus. Massive rusted "
    "gears and hydraulic pistons are fused directly into his flesh and jaw. He screams, "
    "straining every muscle to pull away from thick black data cables plugged into his "
    "mouth and ears. Towering dystopian city in the background, rain-slicked metal, "
    "flickering cyan propaganda light, oppressive bio-mechanical horror."
)
_FALLBACK_SCENE_01: str = (
    f"{MEI_BASE_ANCHOR} Meditating in lotus posture atop a towering jagged mountain "
    "cliff above a sea of clouds. Serene sunrise light, vast alpine vista, dramatic "
    "cinematic framing, absolute stillness, spiritual authority without technology."
)
_FALLBACK_SCENE_FINAL: str = (
    f"{MEI_BASE_ANCHOR} Standing on a misty high-altitude mountain ridge above a sea "
    "of clouds, looking directly at the camera with uncompromising spiritual command. "
    "Moody cinematic dawn light, no technology, no cybernetics, pure ancestral presence."
)

# Global QA negatives — firearms / action-movie tropes banned unless explicitly requested
GLOBAL_FIREARM_BAN: str = (
    "firearm, firearms, handgun, handguns, revolver, revolvers, rifle, rifles, "
    "gun, guns, pistol, pistols, shotgun, assault rifle, modern action-movie trope, "
    "tactical weapon, SWAT, military gunfight"
)


def _load_prompt_file(path: Path, fallback: str) -> str:
    try:
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
    except OSError:
        pass
    return fallback


_VARIATION_SPLIT_RE = re.compile(r"(?m)^===VARIATION_[AB]===\s*$")


def _parse_prompt_variations(raw: str) -> list[str]:
    """Split A/B RAG templates; return non-empty variation bodies."""
    text = (raw or "").strip()
    if not text:
        return []
    if "===VARIATION_" not in text:
        return [text]
    parts = [p.strip() for p in _VARIATION_SPLIT_RE.split(text) if p and p.strip()]
    return parts or [text]


def _pick_prompt_variation(raw: str, *, seed: str = "") -> str:
    """Deterministically rotate A/B variations from a seed (topic / episode)."""
    variants = _parse_prompt_variations(raw)
    if not variants:
        return (raw or "").strip()
    if len(variants) == 1:
        return variants[0]
    digest = hashlib.md5((seed or "mei").encode("utf-8", errors="ignore")).hexdigest()
    idx = int(digest[:8], 16) % len(variants)
    return variants[idx]


def _load_mei_base_anchor() -> str:
    return _load_prompt_file(_MEI_ANCHOR_FILE, MEI_BASE_ANCHOR)


def _load_penultimate_prompt(*, seed: str = "") -> str:
    from avatar_engine.prompt_builder import finalize_flux_prompt

    raw = _pick_prompt_variation(
        _load_prompt_file(_PENULTIMATE_FILE, _FALLBACK_PENULTIMATE),
        seed=f"penultimate|{seed}",
    )
    return finalize_flux_prompt(raw, require_prefix=True)


def _load_scene_03_prompt(*, seed: str = "") -> str:
    from avatar_engine.prompt_builder import finalize_flux_prompt

    raw = _pick_prompt_variation(
        _load_prompt_file(_SCENE_03_FILE, _FALLBACK_SCENE_03),
        seed=f"scene03|{seed}",
    )
    return finalize_flux_prompt(raw, require_prefix=True)


def _load_scene_08_prompt(*, seed: str = "") -> str:
    from avatar_engine.prompt_builder import finalize_flux_prompt

    raw = _pick_prompt_variation(
        _load_prompt_file(_SCENE_08_FILE, _FALLBACK_SCENE_08),
        seed=f"scene08|{seed}",
    )
    return finalize_flux_prompt(raw, require_prefix=True)


def _load_scene_07_prompt(*, seed: str = "") -> str:
    from avatar_engine.prompt_builder import finalize_flux_prompt

    raw = _pick_prompt_variation(
        _load_prompt_file(_SCENE_07_FILE, _FALLBACK_SCENE_07),
        seed=f"scene07|{seed}",
    )
    return finalize_flux_prompt(raw, require_prefix=True)


def _mei_atmosphere_from_philosophy(spoken_beat: str = "", subject: str = "") -> str:
    """Map script philosophy keywords → weather / lighting for Mei environments."""
    text = f"{spoken_beat or ''} {subject or ''}".strip().lower()
    if not text:
        return "serene sunrise light, vast alpine vista, dramatic cinematic framing"
    for pattern, atmosphere in _MEI_PHILOSOPHY_ATMOSPHERE:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return atmosphere
    return "serene sunrise light, vast alpine vista, dramatic cinematic framing"


def pick_mei_meditation_environment(
    *,
    episode_seed: str = "",
    spoken_beat: str = "",
    hook_env: str = "",
) -> str:
    """
    Pick a Master Mei meditation / authority setting.

    Frequency: ~70% high-altitude epic nature, ~30% open-air mountain shrines.
    ``hook_env`` (when supplied) wins so callers can pin a chosen backdrop.
    """
    forced = (hook_env or "").strip()
    if forced:
        return forced
    seed = f"{episode_seed}|mei_env|{(spoken_beat or '')[:120]}"
    digest = hashlib.md5(seed.encode("utf-8", errors="ignore")).hexdigest()
    roll = int(digest[:8], 16) % 100
    pool = MEI_PRIMARY_ENVIRONMENTS if roll < 70 else MEI_SECONDARY_ENVIRONMENTS
    if not pool:
        return "towering jagged mountain cliff above a sea of clouds"
    idx = int(digest[8:16], 16) % len(pool)
    return pool[idx]


def compose_scene_01_prompt(
    *,
    hook_env: str = "",
    spoken_beat: str = "",
    subject: str = "",
    episode_seed: str = "",
) -> str:
    """
    Scene 1 — DNA lock + dynamic environment (no cyberpunk prefix).

    Templates are references only; backdrop / weather / lighting track the
    episode's philosophical narrative.
    """
    from avatar_engine.prompt_builder import finalize_flux_prompt

    anchor = _load_mei_base_anchor()
    env = pick_mei_meditation_environment(
        episode_seed=episode_seed or subject,
        spoken_beat=spoken_beat or subject,
        hook_env=hook_env,
    )
    atmosphere = _mei_atmosphere_from_philosophy(spoken_beat, subject)
    raw = (
        f"{anchor.rstrip('.')}. Meditating in lotus posture atop {env}. "
        f"{atmosphere}. Absolute stillness, spiritual authority without technology."
    )
    return finalize_flux_prompt(raw, require_prefix=False)


def compose_scene_final_prompt(
    *,
    hook_env: str = "",
    spoken_beat: str = "",
    subject: str = "",
    episode_seed: str = "",
) -> str:
    """Final Hook — same DNA + dynamic outdoor setting, eyes on camera."""
    from avatar_engine.prompt_builder import finalize_flux_prompt

    anchor = _load_mei_base_anchor()
    env = pick_mei_meditation_environment(
        episode_seed=f"final|{episode_seed or subject}",
        spoken_beat=spoken_beat or subject,
        hook_env=hook_env,
    )
    atmosphere = _mei_atmosphere_from_philosophy(spoken_beat, subject)
    raw = (
        f"{anchor.rstrip('.')}. Standing on {env}, looking directly at the camera "
        f"with uncompromising spiritual command. {atmosphere}. No technology, "
        f"no cybernetics, pure ancestral presence."
    )
    return finalize_flux_prompt(raw, require_prefix=False)


def _load_scene_01_prompt(
    *,
    hook_env: str = "",
    spoken_beat: str = "",
    subject: str = "",
    episode_seed: str = "",
) -> str:
    """Scene 1 hook — Master Mei snow-white DNA + dynamic environment."""
    # Always compose dynamically when seed/env/beat available; else file fallback
    if hook_env or spoken_beat or subject or episode_seed:
        return compose_scene_01_prompt(
            hook_env=hook_env,
            spoken_beat=spoken_beat,
            subject=subject,
            episode_seed=episode_seed,
        )
    from avatar_engine.prompt_builder import finalize_flux_prompt

    raw = _load_prompt_file(_SCENE_01_FILE, _FALLBACK_SCENE_01)
    anchor = _load_mei_base_anchor()
    lo = raw.lower()
    if "topknot" not in lo and "snow-white" not in lo and "mala" not in lo:
        raw = f"{anchor.rstrip('.')}. {raw}"
    # Ban leftover indoor-temple defaults when file is stale
    if re.search(r"(?i)\binside\b.*\btemple\b|\btemple\s+hall\b|\bindoor\b", raw):
        return compose_scene_01_prompt(episode_seed="default_hook")
    return finalize_flux_prompt(raw, require_prefix=False)


def _load_scene_final_prompt(
    *,
    hook_env: str = "",
    spoken_beat: str = "",
    subject: str = "",
    episode_seed: str = "",
) -> str:
    """Final hook — same snow-white identity + dynamic outdoor setting."""
    if hook_env or spoken_beat or subject or episode_seed:
        return compose_scene_final_prompt(
            hook_env=hook_env,
            spoken_beat=spoken_beat,
            subject=subject,
            episode_seed=episode_seed,
        )
    from avatar_engine.prompt_builder import finalize_flux_prompt

    raw = _load_prompt_file(_SCENE_FINAL_FILE, _FALLBACK_SCENE_FINAL)
    anchor = _load_mei_base_anchor()
    lo = raw.lower()
    if "topknot" not in lo and "snow-white" not in lo and "mala" not in lo:
        raw = f"{anchor.rstrip('.')}. {raw}"
    if re.search(r"(?i)\btemple\s+hall\b|\binside\b.*\btemple\b|\bindoor\b", raw):
        return compose_scene_final_prompt(episode_seed="default_final")
    return finalize_flux_prompt(raw, require_prefix=False)


PENULTIMATE_LIBERATION_PROMPT: str = _load_penultimate_prompt()
SCENE_03_BIOMECHANICAL_PROMPT: str = _load_scene_03_prompt()
SCENE_07_CYBORG_CLOSEUP_PROMPT: str = _load_scene_07_prompt()
SCENE_08_UNPLUG_ESCAPE_PROMPT: str = _load_scene_08_prompt()
SCENE_01_HOOK_PROMPT: str = _load_scene_01_prompt()
SCENE_FINAL_HOOK_PROMPT: str = _load_scene_final_prompt()
SCENE_08_NEGATIVE: str = (
    f"{GLOBAL_FIREARM_BAN}, Master Mei, temple, meditation, graphite, "
    "pencil drawing, charcoal sketch, cartoon, anime, smiling, healthy glow, "
    "idle pose, looking at viewer"
)

PENULTIMATE_LIBERATION_NEGATIVE: str = (
    "text, watermark, typography, close-up, cropped head, face zoom, subtitles, "
    "UI elements, video game HUD, cartoon, anime, glossy CGI superhero, "
    "single monk portrait, idle pose, looking at viewer, bright daylight, "
    f"{GLOBAL_FIREARM_BAN}"
)
SCENE_03_NEGATIVE: str = (
    "text, watermark, typography, Master Mei face, ancestral temple, lush nature, "
    "meditation garden, bright daylight, cartoon, anime, "
    f"{GLOBAL_FIREARM_BAN}"
)

_ORDINARY_BAN: str = (
    "ordinary modern people, casual t-shirt, bland crowd, lifestyle photography, "
    "smartphone selfie, teenager bedroom, office worker, gym influencer, "
    "smiling lifestyle, soft wellness, plain contemporary interior"
)

ROLE_A_SUBJECT: str = (
    f"Master Mei, {MEI_BASE_ANCHOR} "
    "— cinematic high production value, photorealistic"
)

ROLE_A_ENVIRONMENTS: tuple[str, ...] = (
    MEI_PRIMARY_ENVIRONMENTS + MEI_SECONDARY_ENVIRONMENTS
)

ROLE_A_STYLE: str = (
    "Cinematic 8k photorealistic raw photography, detailed skin textures, "
    "octane render, volumetric natural mist and dawn light — traditional ancestral "
    "samurai-temple aesthetic ONLY. Warm ember gold, charcoal stone, muted jade. "
    "Absolutely no neon, no cyberpunk city, no technology, no ordinary modern people"
)

ROLE_A_NEGATIVE: str = (
    f"{_ORDINARY_BAN}, "
    "text, subtitles, words, typography, watermark, signature, caption, quotes, "
    "letters, script, labels, UI elements, overlay text, lower thirds, "
    "static idle monk close-up, single idle monk portrait, passive meditation close-up, "
    "bionic implants, cybernetics, VR headset, glowing wires, neural cables, "
    "neon lights, cyberpunk city, smartphone, digital chains, robot arm, "
    "mechanical eye, futuristic tech on body, biomechanical, CRT monitors, "
    "deformed hands, extra limbs, blurry face"
)

ROLE_B_SUBJECT: str = (
    "2 to 5 hardened Shaolin-style martial arts disciples, male and female, "
    "full bodies in extreme physical action, intense determined expressions, "
    "visible sweat and strain, high-intensity martial discipline, "
    "traditional martial training garments — NOT ordinary modern people, "
    "NEVER a single idle monk close-up"
)

ROLE_B_STYLE: str = (
    "Cinematic 8k photorealistic raw photography, high-intensity martial discipline, "
    "dynamic wide-to-medium action framing, dramatic rim lighting, "
    "sweat and water spray, gritty realism, organic grounded aesthetic"
)

_IDLE_MONK_BAN: str = (
    "static idle monk close-up, single idle monk portrait, passive meditation close-up, "
    "blank stare monk, posing monk, frozen standing monk, headshot of one monk, "
    "passive close-up of single disciple, close-up, tight shot, face zoom, macro, "
    "single monk portrait, sweating close-up, cropped head"
)

_WIDE_ANGLE_LOCK: str = (
    "cinematic wide angle shot, extreme long shot, full body view, epic scope"
)

_EPIC_HERO_CLIMAX: tuple[str, ...] = (
    (
        "EPIC HERO A — LIBERATION: cinematic wide angle shot, extreme long shot, "
        "full body view, epic scope — full-body warrior/disciple struggling and "
        "ripping free from glowing cybernetic cords and Matrix tethers, dynamic "
        "struggle, environment fully visible"
    ),
    (
        "EPIC HERO B — ORDEAL: cinematic wide angle shot, extreme long shot, "
        "full body view, epic scope — full-body warrior confronting near-impossible "
        "physical obstacles (icy mountain climb or stormy waterfall), grit and motion"
    ),
    (
        "EPIC HERO C — FORGE: cinematic wide angle shot, extreme long shot, "
        "full body view, epic scope — disciples executing intense training in full "
        "view while Master Mei watches from a high background vantage point"
    ),
)

ROLE_B_NEGATIVE: str = (
    f"{_ORDINARY_BAN}, {_IDLE_MONK_BAN}, "
    "text, subtitles, words, typography, watermark, signature, caption, quotes, "
    "letters, script, labels, UI elements, overlay text, lower thirds, "
    "Master Mei, elder sage, white beard mentor, bionic implants, cybernetics, "
    "VR headset, glowing wires, neon megacity, casual t-shirt, jeans, sneakers, "
    "deformed hands, extra limbs, blurry face, looking at viewer"
)

# Generalized Discipline / Forge training environments — UNIQUE per episode (never
# recycle the same post-balancing / kata template every reel).
_TRAINING_ENVIRONMENTS: tuple[str, ...] = (
    (
        "ALPINE CLIFF BALANCE: 2 to 5 disciples balancing on one leg atop high wooden "
        "posts over mist-shrouded mountain abysses — full bodies visible, extreme "
        "focus and physical strain, wind whipping robes"
    ),
    (
        "TORRENTIAL TEMPLE RAIN: 2 to 5 disciples performing synchronized heavy "
        "staff and sword katas under pounding freezing rain outside an ancient "
        "temple — water splashes, grit, full-body martial motion"
    ),
    (
        "EXTREME STRENGTH: 2 to 5 disciples carrying massive carved stone blocks "
        "up steep mountain staircases through snow or sandstorm — visible muscle "
        "strain, sweat, full bodies in resistance training"
    ),
    (
        "WATERFALL IMPACT MEDITATION: 2 to 5 disciples standing under a crushing "
        "mountain waterfall while holding low horse stances — water hammering "
        "shoulders, steam, full-body endurance, robes soaked"
    ),
    (
        "UNDERWATER BREATH DISCIPLINE: 2 to 5 disciples submerged in a clear "
        "mountain lake performing slow martial forms underwater — bubbles, "
        "extreme lung control, full bodies in motion beneath the surface"
    ),
    (
        "HEAVY IRON RING TRAINING: 2 to 5 disciples swinging and catching massive "
        "iron rings / stone weights in a torchlit courtyard — explosive full-body "
        "power drills, dust, impact, dynamic motion"
    ),
    (
        "MOUNTAIN BLIZZARD MANEUVERS: 2 to 5 disciples executing spear and staff "
        "forms on a snow ridge in a howling blizzard — frost, wind shear, "
        "full-body combat footwork against whiteout"
    ),
    (
        "VOLCANIC ASH ENDURANCE: 2 to 5 disciples running uphill through volcanic "
        "ash and heat haze while carrying weapons — grit, silhouette motion, "
        "extreme cardiovascular forge"
    ),
    (
        "BAMBOO FOREST SPRINT DUELS: 2 to 5 disciples weaving through dense bamboo "
        "in high-speed paired sparring — broken stalks flying, dynamic chase, "
        "no idle standing"
    ),
    (
        "CLIFFSIDE ROPE ASCENTS: 2 to 5 disciples climbing vertical wet cliffs on "
        "hemp ropes with weapons strapped — hanging full bodies, strain, "
        "vertical motion, no ground portraits"
    ),
)

ROLE_C_SUBJECT: str = (
    "High-concept cinematic cyberpunk digital slaves — pale exhausted faces, "
    "clean futuristic neural interfaces, sleek fiber-optic tethers to necks, "
    "advanced sensory-deprivation VR headsets — NOT ordinary people in t-shirts"
)

ROLE_C_STYLE: str = (
    "Cinematic 8k photorealistic raw photography, high-concept dark cyberpunk dystopia, "
    "Matrix / Maya illusion aesthetic, neon glow, high contrast, moody haze, "
    "clean futuristic technology, extreme production value — never ordinary lifestyle"
)

# STRICT: high-concept cyberpunk only — never gore / body horror
_SAFETY_BAN: str = (
    "NO blood, NO gore, NO open flesh wounds, NO graphic body horror, NO mutilation, "
    "NO exposed organs, NO Giger biomechanical horror — clean sleek cybernetic integration only"
)

ROLE_C_NEGATIVE: str = (
    f"{_ORDINARY_BAN}, {_SAFETY_BAN}, "
    "text, subtitles, words, typography, watermark, signature, caption, quotes, "
    "letters, script, labels, UI elements, overlay text, lower thirds, "
    "Master Mei, elder sage, white beard, topknot master, ancient Asian master, "
    "temple balcony, bamboo forest, tatami room, waterfall training monks, "
    "casual t-shirt, jeans, sneakers, bland crowd, blood, gore, open wounds, "
    "deformed hands, extra limbs, blurry face, looking at viewer"
)

# ---------------------------------------------------------------------------
# Visual Scene Pool — 70% Wide / 30% Detail (anti-VR-redundancy)
# ---------------------------------------------------------------------------
SHOT_WIDE_MASS: str = "wide_mass_matrix"
SHOT_WIDE_SUBJUGATED: str = "wide_subjugated"
SHOT_WIDE_LUDOVICO: str = "wide_forced_consumption"
SHOT_MICRO_FULLBODY: str = "micro_fullbody_agony"
SHOT_DETAIL_BIOMECH: str = "detail_biomech"
SHOT_DETAIL_VISCERAL: str = "detail_visceral"
SHOT_SEDUCTION: str = "seduction_device"

_WIDE_SHOTS: frozenset[str] = frozenset({
    SHOT_WIDE_MASS, SHOT_WIDE_SUBJUGATED, SHOT_WIDE_LUDOVICO,
})
_DETAIL_SHOTS: frozenset[str] = frozenset({
    SHOT_MICRO_FULLBODY, SHOT_DETAIL_BIOMECH, SHOT_DETAIL_VISCERAL, SHOT_SEDUCTION,
})
_VR_CLOSEUP_SHOTS: frozenset[str] = frozenset({
    SHOT_DETAIL_BIOMECH, SHOT_DETAIL_VISCERAL,
})

# Shot #2 — Macro / World Concept (Massive Matrix vista)
_WIDE_MASS_MATRIX: str = (
    "MACRO WORLD-CONCEPT VISTA: massive scale Matrix landscape — colossal futuristic "
    "propaganda towers, vast endless crowds of subjugated masses wired into "
    "computational matrix cocoons and towering digital screens, desolate wasteland "
    "horizon, ambient energy fields controlling the herd in uniform trance"
)

# Shot #3/#4 — Micro / Individual Agony (full body, 1–2 men)
_MICRO_FULLBODY_AGONY: str = (
    "MICRO FULL-BODY INDIVIDUAL AGONY: 1 to 2 adult men in full-body composition "
    "(kneeling or seated) on a desolate devastated wasteland — full torsos, arms, "
    "and legs visible in complete posture of agony, submission, and physical slavery. "
    "Bodies heavily integrated into ultra-modern futuristic biomechanical gear: "
    "intricate circuits, thick glowing fiber-optic cables, neural chips embedded in "
    "skulls, hydraulics, metallic gears, diesel oil stains across skin. Barren "
    "dystopian landscape dominated by glowing dopamine-inducing machinery symbolizing "
    f"total captivity to instant pleasure. Full body — not a face crop. {_SAFETY_BAN}"
)

_WIDE_SUBJUGATED: str = (
    "WIDE-ANGLE SUBJUGATED MASSES: kneeling and bound slaves tied with glowing cables "
    "across a devastated plaza, zombie-like vacant stares, no struggle, no breakaway — "
    "panopticon monitors looming overhead"
)

_WIDE_LUDOVICO: str = (
    "WIDE/MEDIUM FORCED CONSUMPTION (Ludovico-style): rows of individuals strapped to "
    "mechanical chairs and couches with clean mechanical eye-openers forcing them to "
    "absorb digital entertainment while heavily sedated — cinematic dark fantasy, "
    "institutional scale"
)

# Biomechanical Integration (Detail — clean tech, no gore)
_DETAIL_BIOMECH: str = (
    "BIOMECHANICAL DETAIL: sleek cybernetic wires, fiber optics, and metallic circuits "
    "entering through mouths, ears, and eyes — permanently fusing into skulls as clean "
    "high-tech sensory control interfaces (NOT open wounds). Glowing circuitry under skin. "
    f"{_SAFETY_BAN}"
)

# Single visceral close-up (MAX 1 per video)
_DETAIL_VISCERAL: str = (
    "SINGLE VISCERAL CLOSE-UP (use sparingly): modern cybernetic engines and glowing "
    "circuitry physically merging into the flesh of agonized digital slaves — high-concept "
    f"cyberpunk fusion, facial torment WITHOUT injury. {_SAFETY_BAN}"
)

_SEDUCTION_DEVICE: str = (
    "SEDUCTION / INSTANT DOPAMINE: sleek personal devices, subtle glowing headbands, "
    "alluring holographic interfaces, soft neon UI reflecting on trance faces — "
    "seductive clean tech addiction, no gore, NO repetitive VR-goggle portrait"
)

# Legacy aliases used by sync helpers
_WIDE_WASTELAND: str = _WIDE_MASS_MATRIX
_CLOSEUP_IMPLANT: str = _DETAIL_BIOMECH

_SHOT_PROMPT: dict[str, str] = {
    SHOT_WIDE_MASS: _WIDE_MASS_MATRIX,
    SHOT_WIDE_SUBJUGATED: _WIDE_SUBJUGATED,
    SHOT_WIDE_LUDOVICO: _WIDE_LUDOVICO,
    SHOT_MICRO_FULLBODY: _MICRO_FULLBODY_AGONY,
    SHOT_DETAIL_BIOMECH: _DETAIL_BIOMECH,
    SHOT_DETAIL_VISCERAL: _DETAIL_VISCERAL,
    SHOT_SEDUCTION: _SEDUCTION_DEVICE,
}

_ROLE_C_KEYWORDS: re.Pattern[str] = re.compile(
    r"\b(?:machine|dopamine|digital|matrix|slaver(?:y|ies)?|chains?|"
    r"scroll|distract|addict|phone|maya|propaganda|panopticon|gear|"
    r"seduc|allure|tempt|instant|enslav|implant|headset|vr)\b",
    re.IGNORECASE,
)

# Spoken-beat sync cues (within Matrix / Trap bands)
_SYNC_SEDUCTION: re.Pattern[str] = re.compile(
    r"\b(?:seduc|allure|tempt|dopamine|instant|pleasure|scroll|phone|device|feed)\b",
    re.IGNORECASE,
)
_SYNC_INSIDIOUS: re.Pattern[str] = re.compile(
    r"\b(?:enslav|implant|skull|spine|eye|headset|vr|neural|wire|cable|integration|trap)\b",
    re.IGNORECASE,
)
_SYNC_SYSTEMIC: re.Pattern[str] = re.compile(
    r"\b(?:system|mass|crowd|tower|panopticon|gear|propaganda|wasteland|surveillance|herd)\b",
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


def _max_consec_training_for_n(n: int) -> int:
    if n <= 8:
        return 1
    return 2


def _enforce_training_cap(
    lore: list[tuple[str, str]],
    *,
    max_consec: int,
) -> list[tuple[str, str]]:
    """Hard-cap consecutive BEAT_TRAINING frames; convert excess to dopamine trap."""
    out = list(lore)
    run = 0
    for i, (role, beat) in enumerate(out):
        if beat == BEAT_TRAINING:
            run += 1
            if run > max_consec:
                out[i] = (ROLE_SLAVE, BEAT_DOPAMINE)
                run = 0
        else:
            run = 0
    return out


def frame_lore_for_acts(n_acts: int) -> list[tuple[str, str]]:
    """
    Return ``(role, beat)`` per act index.

    Exact locks for 7 / 8 / 9 / 10 frames (legacy / 90–120s profiles).
    Other N: resample nearest table; bookends + Scene 3 GEARS + penultimate BREAKFREE.
    """
    n = max(1, int(n_acts))
    if n == 7:
        return list(_FRAME_LORE_7)
    if n == 8:
        return list(_FRAME_LORE_8)
    if n == 9:
        return list(_FRAME_LORE_9)
    if n == 10:
        return list(_FRAME_LORE_10)

    # Resample nearest locked table (prefer 8–10 for 90–120s)
    if n < 8:
        base = _FRAME_LORE_7
    elif n < 9:
        base = _FRAME_LORE_8
    elif n < 10:
        base = _FRAME_LORE_9
    else:
        base = _FRAME_LORE_10
    base_n = len(base)
    out: list[tuple[str, str]] = []
    for i in range(n):
        src = int(round(i * (base_n - 1) / max(1, n - 1))) if n > 1 else 0
        src = max(0, min(base_n - 1, src))
        out.append(base[src])
    out[0] = (ROLE_MASTER, BEAT_INTRO)
    if n > 2:
        out[2] = (ROLE_SLAVE, BEAT_GEARS)  # Scene 3 lock
    if n > 1:
        out[-1] = (ROLE_MASTER, BEAT_OUTRO)
    if n > 2:
        out[-2] = (ROLE_DISCIPLE, BEAT_BREAKFREE)  # penultimate always
    return _enforce_training_cap(out, max_consec=_max_consec_training_for_n(n))


_FIREARM_RE = re.compile(
    r"\b(?:firearm|handgun|revolver|rifle|shotgun|pistol|assault\s*rifle|"
    r"gunfight|tactical\s*weapon)\b",
    re.IGNORECASE,
)


def validate_mei_visual_prompt(
    prompt: str,
    *,
    act_index: int = 0,
    n_acts: int = 10,
    beat: str = "",
) -> tuple[bool, list[str], str]:
    """
    Visual QA gate — validate a generated image prompt before FLUX render.

    Returns ``(ok, violations, repaired_prompt)``.
    Integrates as a strict override layer; does not replace role builders.
    """
    text = (prompt or "").strip()
    violations: list[str] = []
    repaired = text
    n = max(1, int(n_acts))
    idx = max(0, int(act_index))
    lore = frame_lore_for_acts(n)
    role, lore_beat = lore[min(idx, len(lore) - 1)]
    beat = _normalize_beat(beat) or lore_beat

    if _FIREARM_RE.search(repaired):
        violations.append("FIREARM_BAN: firearms / modern action-movie weapons prohibited")
        repaired = _FIREARM_RE.sub("inert mechanical apparatus", repaired)

    # Strip legacy graphite pollution before any scene repair
    if re.search(r"(?i)graphite|pencil\s+drawing|charcoal\s+sketch|Original\s+scene\s+concept", repaired):
        violations.append("GRAPHITE_PURGE: legacy drawing prefix removed")
        repaired = re.sub(
            r"(?i)\b(?:a\s+precise\s+)?graphite\s+(?:drawing|scene|sketch|illustration)[^.]*\.?",
            " ",
            repaired,
        )
        repaired = re.sub(r"(?i)\bOriginal\s+scene\s+concept\s*:\s*", "", repaired)

    _qa_seed = f"{n}:{idx}:{beat}"

    if idx == 0 or beat == BEAT_INTRO:
        hook = _load_scene_01_prompt(episode_seed=_qa_seed)
        need = (
            "meditat", "topknot", "snow-white", "mala", "gold",
            "eyebrow", "beard", "strand",
        )
        outdoor = (
            "mountain", "cliff", "ridge", "alpine", "himalayan", "alps",
            "cloud", "shrine", "courtyard", "precipice", "spring",
        )
        dna_hits = sum(1 for k in need if k in repaired.lower())
        outdoor_hits = sum(1 for k in outdoor if k in repaired.lower())
        indoor_default = bool(
            re.search(r"(?i)\binside\b.*\btemple\b|\btemple\s+hall\b|\bindoor\b", repaired)
        )
        if dna_hits < 3 or outdoor_hits < 1 or indoor_default:
            violations.append(
                "SCENE_1: missing Master Mei DNA / dynamic outdoor setting — replacing"
            )
            repaired = hook

    if idx == 2:
        s3 = _load_scene_03_prompt(seed=_qa_seed)
        need = (
            "attention monopoly", "kneeling", "vr headset", "propaganda",
            "microchips", "wasteland", "bio-horror",
        )
        if sum(1 for k in need if k in repaired.lower()) < 2:
            violations.append("SCENE_3: missing post-apocalyptic slaves — replacing with FLUX RAG prompt")
            repaired = s3

    if idx == 6 and beat == BEAT_DOPAMINE:
        s7 = _load_scene_07_prompt(seed=_qa_seed)
        need = ("computing apparatus", "hydraulic", "cables", "screams", "propaganda", "gears")
        if sum(1 for k in need if k in repaired.lower()) < 2:
            violations.append("SCENE_7: missing computing-apparatus captive — replacing with FLUX RAG prompt")
            repaired = s7

    # Scene 8 (1-based) = act_index 7 — samurai slicing matrix illusion (9–10 frame lore)
    if idx == 7 and beat != BEAT_OUTRO and n >= 9:
        s8 = _load_scene_08_prompt(seed=_qa_seed)
        need = ("samurai", "katana", "holographic", "matrix", "propaganda", "machine city")
        if sum(1 for k in need if k in repaired.lower()) < 2:
            violations.append("SCENE_8: missing samurai matrix-slice — replacing with FLUX RAG prompt")
            repaired = s8

    if (idx == n - 2 or beat == BEAT_BREAKFREE) and not (idx == 7 and n >= 9):
        pen = _load_penultimate_prompt(seed=_qa_seed)
        need = ("pod", "incubation", "cable", "matrix", "warrior", "liberat")
        if sum(1 for k in need if k in repaired.lower()) < 2:
            violations.append("PENULTIMATE: missing matrix pod escape — replacing with FLUX RAG prompt")
            repaired = pen

    if idx == n - 1 or beat == BEAT_OUTRO:
        final = _load_scene_final_prompt(episode_seed=_qa_seed)
        need = ("topknot", "snow-white", "mala", "gold", "eyebrow", "beard")
        outdoor = (
            "mountain", "cliff", "ridge", "alpine", "cloud", "shrine",
            "courtyard", "precipice", "temple",
        )
        dna_hits = sum(1 for k in need if k in repaired.lower())
        outdoor_hits = sum(1 for k in outdoor if k in repaired.lower())
        indoor_default = bool(
            re.search(r"(?i)\btemple\s+hall\b|\binside\b.*\btemple\b|\bindoor\b", repaired)
        )
        if dna_hits < 3 or outdoor_hits < 1 or indoor_default:
            violations.append(
                "FINAL_HOOK: missing Master Mei DNA / dynamic outdoor setting — replacing"
            )
            repaired = final

    # Always append firearm ban to negative-adjacent cue in positive (FLUX sees it in neg separately)
    if GLOBAL_FIREARM_BAN.split(",")[0] not in repaired.lower():
        pass  # ban enforced via negative_for_role / caller negatives

    ok = len(violations) == 0
    return ok, violations, re.sub(r"\s{2,}", " ", repaired).strip()


def compute_mei_act_durations(
    n_acts: int,
    total_duration: float,
    *,
    hook_max_s: float = 8.0,
    body_min_s: float = 10.0,
    body_max_s: float = 12.0,
) -> list[float]:
    """
    Scene 1 ≤ ``hook_max_s``; remaining scenes share the rest (~10–12s target).

    Audio-driven totals may stretch body scenes slightly beyond ``body_max_s``.
    """
    n = max(1, int(n_acts))
    total = max(1.0, float(total_duration))
    if n == 1:
        return [total]
    # Prefer hook=8 when remaining body can stay near 10–12s
    ideal_hook = min(hook_max_s, total - body_min_s * (n - 1))
    hook = max(3.0, min(hook_max_s, ideal_hook))
    body = (total - hook) / (n - 1)
    # If body would be tiny, fall back toward equal split but keep hook ≤8
    if body < body_min_s * 0.75:
        hook = min(hook_max_s, total / n)
        body = (total - hook) / (n - 1)
    durs = [float(hook)] + [float(body)] * (n - 1)
    # Fix float drift on last act
    drift = total - sum(durs)
    durs[-1] = max(0.5, durs[-1] + drift)
    return durs


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
        base = ROLE_A_NEGATIVE
    elif role == ROLE_DISCIPLE:
        base = ROLE_B_NEGATIVE
    else:
        base = ROLE_C_NEGATIVE
    return f"{base}, {GLOBAL_FIREARM_BAN}"


def environment_for_role(role: str, act_index: int, *, episode_seed: str = "") -> str:
    if role == ROLE_MASTER:
        return pick_mei_meditation_environment(
            episode_seed=f"{episode_seed}|act{act_index}",
        )
    if role == ROLE_DISCIPLE:
        return pick_training_environment(act_index=act_index, episode_seed=episode_seed)
    return (
        "desolate cyberpunk wasteland under propaganda screens",
        "panopticon tower casting surveillance light on wired masses",
        "dark high-tech chamber of neural-collar slaves",
    )[act_index % 3]


def pick_training_environment(
    *,
    act_index: int = 0,
    episode_seed: str = "",
) -> str:
    """Pick a unique training environment (hash-rotated; avoids same kata every reel)."""
    n = len(_TRAINING_ENVIRONMENTS)
    if not n:
        return "DYNAMIC FORGE: 2 to 5 disciples in extreme martial motion"
    if episode_seed:
        digest = hashlib.md5(f"{episode_seed}|{act_index}".encode("utf-8")).hexdigest()
        idx = int(digest[:8], 16) % n
    else:
        idx = int(act_index) % n
    return _TRAINING_ENVIRONMENTS[idx]


def build_training_discipline_prompt(
    *,
    visual_dna: str = "",
    act_index: int = 0,
    include_mei_supervisor: bool = True,
    episode_seed: str = "",
) -> tuple[str, str]:
    """
    GENERAL RULE — Discipline / Forge / martial training frames.

    NEVER static passive close-ups of a single idle monk staring at camera.
    NEVER reuse the exact same post-balancing / kata scene every episode.
    ALWAYS invent/select a unique high-intensity environment with 2–5 disciples
    in active motion, Master Mei supervising in the background when enabled.
    """
    env = pick_training_environment(act_index=act_index, episode_seed=episode_seed)
    mei = _clean_mei_dna(visual_dna) if include_mei_supervisor else ""
    supervisor = (
        f"In the BACKGROUND: {mei}, stoically supervising the forge with "
        f"absolute stillness and authority — NOT a close-up portrait of Mei. "
        if mei
        else ""
    )
    hero = _EPIC_HERO_CLIMAX[2]  # Option C — training + Mei high vantage
    positive = (
        f"{ROLE_A_STYLE if include_mei_supervisor else ROLE_B_STYLE}. "
        f"{_WIDE_ANGLE_LOCK}. {hero}. "
        f"DYNAMIC EPIC WIDE DISCIPLINE SHOT: 2 to 5 disciples undergoing "
        f"extreme martial arts, weapons, breath, or physical endurance training under "
        f"severe adversity — FULL BODY VIEW in ACTIVE MOTION, visible physical strain, "
        f"sweat and water splashes, gritty realism, dynamic lighting. "
        f"{supervisor}"
        f"UNIQUE ENVIRONMENT / ACTION THIS EPISODE: {env}. "
        f"Traditional organic world only — no cybernetics. "
        f"STRICT PROHIBITION: never close-up, tight shot, face zoom, macro, "
        f"single monk portrait, or sweating close-up. "
        f"Pure visual scene, no text, no words, no typography."
    )
    negative = (
        f"{ROLE_A_NEGATIVE if include_mei_supervisor else ROLE_B_NEGATIVE}, "
        f"{_IDLE_MONK_BAN}, "
        "text, watermark, typography, close-up, cropped head, face zoom, "
        "subtitles, UI elements, "
        "words, font, letters, sample, signature, caption, quotes, labels, "
        "tight shot, macro, single monk portrait, sweating close-up, "
        "selfie pose, looking at viewer, blank stare into camera, "
        "recycled kata pose, identical training scene"
    )
    return re.sub(r"\s{2,}", " ", positive).strip(), negative


_BEAT_ALIASES: dict[str, str] = {
    "dopamine": BEAT_DOPAMINE,
    "dopamine_trap": BEAT_DOPAMINE,
    "breakfree": BEAT_BREAKFREE,
    "break_free": BEAT_BREAKFREE,
    "matrix": BEAT_MAYA,
    "maya": BEAT_MAYA,
    "gears": BEAT_GEARS,
    "panopticon": BEAT_PANOPTICON,
    "intro": BEAT_INTRO,
    "outro": BEAT_OUTRO,
    "training": BEAT_TRAINING,
    "cta": BEAT_OUTRO,
}


def _normalize_beat(beat: str) -> str:
    key = (beat or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _BEAT_ALIASES.get(key, key)


def plan_matrix_shots(
    n_acts: int,
    roles: Sequence[str],
    beats: Sequence[str] | None = None,
    spoken_chunks: Sequence[str] | None = None,
) -> list[str | None]:
    """
    Assign shot keys for each act.

    Among Matrix/slave frames enforce ~70% wide vistas / ~30% biomechanical detail,
    max ONE visceral close-up per video, and ban consecutive VR-style close-ups.
    """
    n = max(1, int(n_acts))
    roles_l = list(roles or [])
    beats_l = list(beats or assign_frame_beats(n))
    chunks = list(spoken_chunks or [])
    while len(roles_l) < n:
        roles_l.append(ROLE_SLAVE)
    while len(beats_l) < n:
        beats_l.append(BEAT_MAYA)

    matrix_idx = [
        i for i in range(n)
        if roles_l[i] == ROLE_SLAVE
        or _normalize_beat(beats_l[i]) in (
            BEAT_MAYA, BEAT_GEARS, BEAT_PANOPTICON, BEAT_DOPAMINE,
        )
    ]
    n_m = len(matrix_idx)
    plan: list[str | None] = [None] * n
    if n_m == 0:
        return plan

    n_detail = max(1, int(round(n_m * 0.30))) if n_m >= 2 else 0
    n_detail = min(n_detail, max(0, n_m - 1))  # keep majority wide when possible
    n_wide = n_m - n_detail

    wide_cycle = [SHOT_WIDE_MASS, SHOT_WIDE_SUBJUGATED, SHOT_WIDE_LUDOVICO]
    detail_cycle = [SHOT_DETAIL_BIOMECH, SHOT_SEDUCTION]
    wide_i = detail_i = 0
    visceral_used = False
    assigned: list[str] = []

    for rank, act_i in enumerate(matrix_idx):
        beat = _normalize_beat(beats_l[act_i])
        chunk = chunks[act_i] if act_i < len(chunks) else ""
        want_detail = rank >= n_wide  # trailing slots → detail (30%)
        # Spoken-beat soft bias
        if _SYNC_SEDUCTION.search(chunk) or beat == BEAT_DOPAMINE:
            want_detail = True if n_detail > 0 else False
        if _SYNC_SYSTEMIC.search(chunk) or beat in (BEAT_GEARS, BEAT_PANOPTICON):
            want_detail = False

        prev = assigned[-1] if assigned else None
        if want_detail and len([s for s in assigned if s in _DETAIL_SHOTS]) < n_detail:
            # Anti-redundancy: never consecutive VR close-ups of single subjects
            if prev in _VR_CLOSEUP_SHOTS:
                shot = wide_cycle[wide_i % len(wide_cycle)]
                wide_i += 1
            elif not visceral_used and rank == matrix_idx[-1] and n_detail >= 1:
                shot = SHOT_DETAIL_VISCERAL
                visceral_used = True
            else:
                shot = detail_cycle[detail_i % len(detail_cycle)]
                detail_i += 1
                if shot in _VR_CLOSEUP_SHOTS and prev in _VR_CLOSEUP_SHOTS:
                    shot = SHOT_WIDE_MASS
        else:
            if beat == BEAT_DOPAMINE:
                shot = SHOT_WIDE_LUDOVICO if rank % 2 == 0 else SHOT_SEDUCTION
                if shot == SHOT_SEDUCTION and prev in _VR_CLOSEUP_SHOTS:
                    shot = SHOT_WIDE_LUDOVICO
            else:
                shot = wide_cycle[wide_i % len(wide_cycle)]
                wide_i += 1

        # Final guard: consecutive VR close-ups → force wide
        if prev in _VR_CLOSEUP_SHOTS and shot in _VR_CLOSEUP_SHOTS:
            shot = SHOT_WIDE_MASS
        assigned.append(shot)
        plan[act_i] = shot

    # Enforce ratio: if detail overshot, convert extras to wide (keep ≤1 visceral)
    detail_positions = [i for i, s in enumerate(plan) if s in _DETAIL_SHOTS]
    while len(detail_positions) > n_detail:
        victim = detail_positions.pop(0)
        if plan[victim] == SHOT_DETAIL_VISCERAL and not any(
            plan[j] == SHOT_DETAIL_VISCERAL for j in detail_positions
        ):
            continue  # keep the sole visceral if it's the only one
        plan[victim] = SHOT_WIDE_MASS

    # Cap visceral at 1
    seen_visceral = False
    for i, s in enumerate(plan):
        if s == SHOT_DETAIL_VISCERAL:
            if seen_visceral:
                plan[i] = SHOT_WIDE_SUBJUGATED
            else:
                seen_visceral = True

    # Macro → Micro lock (1-based Shot #2 / Shot #3-or-#4)
    # Shot #2 (act 1): always MACRO world-concept Matrix vista
    if n >= 2 and (roles_l[1] == ROLE_SLAVE or plan[1] is not None):
        plan[1] = SHOT_WIDE_MASS
    # Shot #3 (act 2) preferred for MICRO full-body agony; else Shot #4 (act 3)
    micro_slot = None
    for cand in (2, 3):
        if cand < n and (roles_l[cand] == ROLE_SLAVE or plan[cand] is not None):
            micro_slot = cand
            break
    if micro_slot is not None:
        plan[micro_slot] = SHOT_MICRO_FULLBODY
    return plan


def build_role_prompt(
    *,
    role: str,
    visual_dna: str = "",
    spoken_beat: str = "",
    subject: str = "",
    act_index: int = 0,
    hook_env: str = "",
    beat: str = "",
    shot_key: str | None = None,
    episode_seed: str = "",
) -> tuple[str, str]:
    """
    Build (positive_prompt, negative_prompt) for one lore-aligned scene.
    """
    # Hash seed so training environments diversify across topics / episodes
    _seed = (episode_seed or subject or spoken_beat or "").strip()
    role = (role or ROLE_SLAVE).strip().lower()
    if role not in VALID_ROLES:
        role = ROLE_SLAVE

    # Resolve beat from lore if not supplied
    if not beat:
        lore = frame_lore_for_acts(max(act_index + 1, 10))
        beat = lore[min(act_index, len(lore) - 1)][1]
    beat = _normalize_beat(beat)

    # spoken_beat used only for shot sync classification — NEVER appended to
    # the positive prompt (FLUX paints typography when fed dialogue/quotes).
    chunk = (spoken_beat or subject or "").strip()
    style = style_for_role(role)
    negative = negative_for_role(role)

    if beat == BEAT_INTRO or (role == ROLE_MASTER and beat == BEAT_INTRO):
        # Scene 1 — DNA lock + dynamic high-altitude / open-air shrine environment
        positive = _load_scene_01_prompt(
            hook_env=hook_env,
            spoken_beat=chunk,
            subject=subject,
            episode_seed=_seed,
        )
        negative = (
            ROLE_A_NEGATIVE + ", looking at viewer, selfie pose, "
            f"{GLOBAL_FIREARM_BAN}, cybernetics on Master Mei, neon city, "
            "graphite, pencil drawing, charcoal sketch, illustration, "
            "generic indoor temple hall, dark robes only, "
            "black silk kimono without white inner robe"
        )
    elif beat == BEAT_OUTRO:
        # Final Hook — same DNA + dynamic outdoor setting, eyes on camera
        positive = _load_scene_final_prompt(
            hook_env=hook_env,
            spoken_beat=chunk,
            subject=subject,
            episode_seed=_seed,
        )
        negative = (
            f"{_ORDINARY_BAN}, text, subtitles, words, typography, watermark, "
            f"{GLOBAL_FIREARM_BAN}, "
            "bionic implants, cybernetics, VR headset, glowing wires, "
            "neon city fused onto Master Mei, deformed hands, extra limbs, blurry face, "
            "graphite, pencil drawing, charcoal sketch, generic indoor temple hall"
        )
    elif beat == BEAT_TRAINING or (
        role == ROLE_MASTER
        and _ROLE_B_KEYWORDS.search(chunk)
        and beat not in (BEAT_INTRO, BEAT_OUTRO)
    ):
        # GENERAL RULE: Discipline / Forge — dynamic 2–5 disciples + Mei supervising
        positive, negative = build_training_discipline_prompt(
            visual_dna=visual_dna,
            act_index=act_index,
            include_mei_supervisor=True,
            episode_seed=_seed,
        )
    elif beat == BEAT_GEARS and act_index == 2:
        # Scene 3 — post-apocalyptic Matrix slaves FLUX lock
        positive = _load_scene_03_prompt(seed=_seed)
        negative = (
            f"{SCENE_03_NEGATIVE}, graphite, pencil drawing, charcoal sketch, illustration, "
            "Master Mei, temple, meditation"
        )
    elif act_index == 7 and beat != BEAT_OUTRO:
        # Scene 8 — samurai slicing matrix illusion
        positive = _load_scene_08_prompt(seed=_seed)
        negative = SCENE_08_NEGATIVE
    elif beat == BEAT_BREAKFREE:
        # Penultimate — Infinite Matrix pod escape (when not Scene 8 slot)
        positive = _load_penultimate_prompt(seed=_seed)
        negative = (
            f"{PENULTIMATE_LIBERATION_NEGATIVE}, {_IDLE_MONK_BAN}, "
            "graphite, pencil drawing, charcoal sketch, illustration, "
            "close-up, tight shot, face zoom, macro, cropped head"
        )
    elif act_index == 6 and beat == BEAT_DOPAMINE:
        # Scene 7 — computing apparatus captive
        positive = _load_scene_07_prompt(seed=_seed)
        negative = (
            f"{GLOBAL_FIREARM_BAN}, Master Mei, temple, meditation, graphite, "
            "pencil drawing, charcoal sketch, cartoon, anime, smiling, healthy glow"
        )
    elif beat in (BEAT_MAYA, BEAT_GEARS, BEAT_PANOPTICON, BEAT_DOPAMINE):
        sync = _sync_shot_for_chunk(chunk, beat, shot_key=shot_key)
        positive = (
            f"{ROLE_C_STYLE}. "
            f"{sync['shot']}. "
            f"Prefer wide mass vistas; avoid repetitive single-person "
            f"VR-goggle close-ups. Pale human flesh with implanted wires, visible suffering. "
            f"Pure visual scene, no text."
        )
        negative = (
            f"{ROLE_C_NEGATIVE}, {GLOBAL_FIREARM_BAN}, "
            "graphite, pencil drawing, charcoal sketch, "
            "identical headset close-up consecutive shots"
        )
    elif role == ROLE_MASTER:
        mei = _clean_mei_dna(visual_dna)
        env = (hook_env or "").strip() or environment_for_role(role, act_index)
        positive = (
            f"{style}. {mei}. Environment: {env}. Pure visual scene, no text."
        )
    elif role == ROLE_DISCIPLE:
        # GENERAL RULE applies to any disciple martial/endurance frame
        positive, negative = build_training_discipline_prompt(
            visual_dna=visual_dna,
            act_index=act_index,
            include_mei_supervisor=bool(visual_dna),
            episode_seed=_seed,
        )
    else:
        # Role C fallback — still apply Narrative Synchronization Matrix
        sync = _sync_shot_for_chunk(chunk, beat or BEAT_MAYA, shot_key=shot_key)
        positive = (
            f"{ROLE_C_STYLE}. "
            f"{sync['shot']}. "
            f"{_SAFETY_BAN}. Pure visual scene, no text."
        )
        negative = (
            f"{ROLE_C_NEGATIVE}, repetitive VR goggle portrait, identical headset close-up"
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


def _sync_shot_for_chunk(
    chunk: str,
    beat: str,
    *,
    shot_key: str | None = None,
) -> dict[str, str]:
    """
    Map planned shot key (preferred) or spoken beat → visual language.

    Shot plan enforces 70% wide mass vistas / 30% biomechanical detail.
    """
    key = (shot_key or "").strip().lower()
    if key in _SHOT_PROMPT:
        return {
            "label": key.upper(),
            "shot": f"{_SHOT_PROMPT[key]}. {_SAFETY_BAN}",
        }

    text = chunk or ""
    if _SYNC_SEDUCTION.search(text) or beat == BEAT_DOPAMINE:
        return {"label": "SEDUCTION_DOPAMINE", "shot": f"{_SEDUCTION_DEVICE}. {_SAFETY_BAN}"}
    if _SYNC_SYSTEMIC.search(text) or beat in (BEAT_GEARS, BEAT_PANOPTICON):
        return {
            "label": "SYSTEMIC_SLAVERY",
            "shot": (
                f"{_WIDE_MASS_MATRIX}; masses tied to giant biomechanical gears "
                f"and panopticon monitors — {_SAFETY_BAN}"
            ),
        }
    if _SYNC_INSIDIOUS.search(text):
        return {"label": "BIOMECH_DETAIL", "shot": f"{_DETAIL_BIOMECH}. {_SAFETY_BAN}"}
    # Default to WIDE mass vista (not another VR close-up)
    return {"label": "WIDE_MASS_DEFAULT", "shot": f"{_WIDE_MASS_MATRIX}. {_SAFETY_BAN}"}
