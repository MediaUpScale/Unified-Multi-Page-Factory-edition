# -*- coding: utf-8 -*-
"""
Universal script-to-image prompt alignment (page-agnostic).

Enforces:
  1. Literal noun anchoring from the spoken beat
  2. Shot-type alternation (anti-monotony cycle)
  3. Subject frequency cap (don't repeat the hero subject every frame)
"""
from __future__ import annotations

import hashlib
import logging
import random
import re
from typing import Iterable

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Legacy 4-item shot cycle (kept for callers that use shot_directive_for_act
# without a per-episode planner). Prefer plan_shot_lighting_sequence() +
# build_aligned_visual_block(shot_override=..., lighting_override=...) for
# new callers — that path guarantees no-adjacent-repeat variety AND rotates
# lighting so every image doesn't default to warm amber backlight.
# ---------------------------------------------------------------------------
_SHOT_CYCLE: tuple[tuple[str, str], ...] = (
    (
        "Opening/Establishing",
        "Broad establishing shot of the environment and ambiance. "
        "Wide spatial context; atmosphere and setting dominate the frame.",
    ),
    (
        "Detail/Close-up",
        "Macro / detail close-up on artifacts, hands, textures, inscriptions, "
        "or the specific object named in the spoken beat. Extreme environmental "
        "scale is secondary to tactile surface detail.",
    ),
    (
        "Medium/Human Action",
        "Medium shot of a human observer, character, or focal subject interacting "
        "with the scene — reaching, studying, walking, or reacting.",
    ),
    (
        "Dramatic Vista",
        "Cinematic wide-angle vista / dramatic perspective. Sweeping depth, "
        "horizon or monument scale, strong leading lines.",
    ),
)


# ---------------------------------------------------------------------------
# Richer pools for the per-episode shot / lighting planner.
# Purpose: kill visual monotony where every image defaulted to the same
# "centered vertical framing, warm amber backlight, symmetric doorway" look.
# Each pool item is a complete, unambiguous directive that FLUX can act on
# without collapsing to its cinematic-centered default.
# ---------------------------------------------------------------------------
_SHOT_POOL: tuple[tuple[str, str], ...] = (
    (
        "Wide Establishing",
        "Wide establishing shot — subject small in the frame, expansive "
        "landscape or environment dominating the composition, "
        "horizon visible.",
    ),
    (
        "Extreme Detail Close-Up",
        "Extreme close-up on a single specific detail — carved texture, "
        "hand, artifact surface, engraving, or tool mark — filling the "
        "entire frame. No wide context.",
    ),
    (
        "Low-Angle Upward",
        "Low camera angle looking UP at the subject, exaggerated monumental "
        "scale, sky / ceiling / vault dominating the upper frame.",
    ),
    (
        "High-Angle Top-Down",
        "High camera angle looking DOWN at the subject, ground / floor / "
        "terrain plane dominating the lower two-thirds of the frame, "
        "subject viewed from above.",
    ),
    (
        "Three-Quarter Side",
        "Three-quarter side perspective, subject seen from an off-axis "
        "angle — never head-on, never symmetric. Diagonal depth cues.",
    ),
    (
        "Through-Foreground Framing",
        "Over-the-shoulder or through-foreground-object framing — a "
        "silhouette, carved edge, human hand, tree branch, or partial "
        "obstruction occupies the near foreground, subject beyond in "
        "mid-ground. Never a rectangular stone frame around the whole "
        "subject.",
    ),
    (
        "Medium Off-Center",
        "Medium shot with strong off-center rule-of-thirds composition. "
        "Subject positioned to one side; negative space or environment "
        "on the opposite side.",
    ),
)

# ---------------------------------------------------------------------------
# Subject-type pool — decides WHAT the image is about.
# Applied FIRST by plan_episode_visual_sequence(); the returned text
# generates the scene concept itself (replacing the old ARC descriptors
# that biased every image toward "wide temple with amber rim light").
#
# Each item is (name, template_fn(topic) -> str, relevance_fn(topic) -> bool).
# The relevance predicate is checked per-episode: if it returns False for
# the current topic, the item is dropped from that episode's rotation
# (topic-appropriateness gate). Predicates that always return True are
# universal categories that work for any AK episode.
#
# Templates deliberately do NOT say doorway/portal/corridor/threshold/
# archway/chamber-interior/framed-through so FLUX cannot default to the
# centered-doorway composition unless the spoken beat explicitly demands it.
# ---------------------------------------------------------------------------


def _topic_lower(topic: str) -> str:
    return (topic or "").lower()


def _rel_always(topic: str) -> bool:  # noqa: ARG001
    return True


def _rel_underwater(topic: str) -> bool:
    t = _topic_lower(topic)
    return any(k in t for k in (
        "underwater", "submerged", "sunken", "atlantis", "dwarka",
        "yonaguni", "flood", "deluge", "ocean floor", "seabed", "coral",
        "diver", "lost city", "bimini", "great flood", "sea level",
    ))


def _rel_volcanic(topic: str) -> bool:
    t = _topic_lower(topic)
    return any(k in t for k in (
        "pompeii", "volcan", "volcanic", "lava", "ash", "geothermal",
        "santorini", "thera", "vesuvius", "eruption", "akrotiri",
        "yellowstone", "hawaii", "krakatoa", "cataclysm",
    ))


def _rel_jungle(topic: str) -> bool:
    t = _topic_lower(topic)
    return any(k in t for k in (
        "angkor", "mayan", "maya ", "aztec", "amazon", "caral",
        "mesoamerica", "cambodia", "jungle", "rainforest", "chichen",
        "palenque", "tikal", "yucatan", "olmec", "borobudur", "khmer",
        "vietnam", "laos", "peruvian rainforest", "el mirador",
    ))


def _rel_desert(topic: str) -> bool:
    t = _topic_lower(topic)
    return any(k in t for k in (
        "egypt", "giza", "sahara", "nazca", "arabian", "arabia",
        "mesopotamia", "sumer", "babylon", "persian", "dendera",
        "karnak", "luxor", "sphinx", "pyramid", "petra", "desert",
        "sand", "dune", "arid", "peru", "atacama", "gobi", "sinai",
        "libya", "mali", "dogon",
    ))


def _rel_ice(topic: str) -> bool:
    t = _topic_lower(topic)
    return any(k in t for k in (
        "arctic", "antarctic", "siberia", "siberian", "mammoth",
        "younger dryas", "ice age", "permafrost", "glacier", "glacial",
        "tundra", "frozen", "polar", "greenland", "yakutia", "cataclysm",
    ))


def _rel_night_sky(topic: str) -> bool:  # noqa: ARG001
    # Universal: every ancient culture had celestial-alignment traditions.
    return True


_SUBJECT_POOL: "tuple[tuple[str, callable, callable], ...]" = (
    # ── Existing 5 (architecture / monument flavoured — kept unchanged) ──
    (
        "Artifact Macro",
        lambda topic: (
            f"SUBJECT — ARTIFACT MACRO: A single specific artifact, carving, "
            f"inscription, or worked-surface detail from {topic} — filling the "
            f"frame as the visual hero. Tactile surface texture, tool marks, "
            f"material grain. No wide environmental context."
        ),
        _rel_always,
    ),
    (
        "Wide Landscape Ruins",
        lambda topic: (
            f"SUBJECT — WIDE LANDSCAPE / RUINS: A broad establishing landscape "
            f"view of {topic} — the full site set in its natural terrain, "
            f"horizon visible, sky and ground dominating. Distant human "
            f"figure allowed only for scale reference."
        ),
        _rel_always,
    ),
    (
        "Anomalous Out-of-Place Object",
        lambda topic: (
            f"SUBJECT — ANOMALOUS OBJECT: A grounded, non-fantasy tool or "
            f"worked object from {topic} exhibiting impossible precision or "
            f"out-of-place workmanship for its era — the object itself is the "
            f"visual hero, shown in physical context. Real material, not CGI, "
            f"not sci-fi."
        ),
        _rel_always,
    ),
    (
        "Monument Unusual Vantage",
        lambda topic: (
            f"SUBJECT — MONUMENT FROM UNUSUAL VANTAGE: The main monument of "
            f"{topic} shown from an unfamiliar viewpoint — distant aerial "
            f"through atmospheric haze, fragment / ruin close-up, or a ground-"
            f"level angle. Never a centered head-on postcard view."
        ),
        _rel_always,
    ),
    (
        "Impossible Geological / Natural Detail",
        lambda topic: (
            f"SUBJECT — GEOLOGICAL / NATURAL ANOMALY: An impossible-seeming "
            f"geological or natural feature tied to {topic} — tool-marked "
            f"bedrock, unnatural erosion, unexplained alignment, or a "
            f"natural-yet-anomalous formation. Grounded, real geology, "
            f"not fantasy."
        ),
        _rel_always,
    ),
    # ── 7 new environment categories (2026-08-15) — topic-gated ──────────
    (
        "Human Explorer In Landscape",
        lambda topic: (
            f"SUBJECT — HUMAN EXPLORER IN THE LANDSCAPE: A small, distant, "
            f"non-identifiable human figure (documentary-style silhouette, "
            f"back-turned or side-on, weather-worn clothing) examining or "
            f"dwarfed by the {topic} site — the figure gives narrative POV "
            f"and scale reference. No hero close-up on the face, no modern "
            f"branded gear."
        ),
        _rel_always,
    ),
    (
        "Underwater Ocean-Floor Ruins",
        lambda topic: (
            f"SUBJECT — UNDERWATER OCEAN-FLOOR RUINS: Submerged ancient "
            f"stonework tied to {topic} on the seabed — coral-encrusted "
            f"masonry, blue-green murky water column, faint god-rays "
            f"filtering from the surface far above. Grounded photographic "
            f"underwater cinematography, not fantasy Atlantis art."
        ),
        _rel_underwater,
    ),
    (
        "Volcanic / Geothermal Environment",
        lambda topic: (
            f"SUBJECT — VOLCANIC / GEOTHERMAL ENVIRONMENT: {topic} set in "
            f"a volcanic landscape — ash-buried structures, cooled lava "
            f"fields, active steam vents, or a smouldering caldera "
            f"backdrop. Pompeii-style preserved masonry allowed. Real "
            f"geology, real ash, no CGI eruption."
        ),
        _rel_volcanic,
    ),
    (
        "Forest / Jungle-Reclaimed Ruins",
        lambda topic: (
            f"SUBJECT — FOREST / JUNGLE-RECLAIMED RUINS: Dense equatorial "
            f"canopy swallowing the {topic} site — thick roots through "
            f"masonry, moss and lichen on carved surfaces, dappled shafts "
            f"through leaves, Angkor-Wat / Palenque atmosphere. Humid air, "
            f"real botanical detail."
        ),
        _rel_jungle,
    ),
    (
        "Desert / Arid Vastness",
        lambda topic: (
            f"SUBJECT — DESERT / ARID VASTNESS: The {topic} site reduced "
            f"to a small fragment in an enormous desert vista — wind-"
            f"scoured dunes half-burying stonework, distant heat shimmer, "
            f"a lone ridge on a huge horizon. Sky and sand dominate; the "
            f"ruin is intentionally minor in the frame."
        ),
        _rel_desert,
    ),
    (
        "Ice / Glacial Environment",
        lambda topic: (
            f"SUBJECT — ICE / GLACIAL ENVIRONMENT: {topic} bound up in ice "
            f"or snow — frozen ground, permafrost-preserved remains, "
            f"snow-drifted stonework, deep-blue crevasse light, or a stark "
            f"cold aurora sky. Real polar landscape, no fantasy palette."
        ),
        _rel_ice,
    ),
    (
        "Night Sky / Astronomical Alignment",
        lambda topic: (
            f"SUBJECT — NIGHT SKY / ASTRONOMICAL ALIGNMENT: The night sky "
            f"itself dominates the frame — constellations, milky-way band, "
            f"or a specific celestial alignment tied to {topic}. Any "
            f"architecture is minor and low in the frame; the stars are "
            f"the hero. Long-exposure photographic feel, no CGI nebulae."
        ),
        _rel_night_sky,
    ),
)


_LIGHTING_POOL: tuple[tuple[str, str], ...] = (
    (
        "Warm Amber Backlight",
        "Warm amber backlight — golden rim on the subject's edges, softly "
        "glowing haze behind, colour temperature around 3000K.",
    ),
    (
        "Cold Blue Moonlit Sidelight",
        "Cold blue moonlit sidelight — subject partially lit from one side, "
        "deep unlit shadow on the opposite side, colour temperature "
        "around 7500K.",
    ),
    (
        "Harsh Directional Sunlight",
        "Harsh directional sunlight from above — hard-edged shadows, high "
        "contrast, arid mid-day feel, no haze.",
    ),
    (
        "Diffuse Overcast Soft",
        "Diffuse overcast soft light — no visible sun, even shadowless "
        "illumination, muted desaturated colours, flat mid-tones.",
    ),
    (
        "Firelight From Below",
        "Torch / firelight lit from BELOW the subject — warm orange upward "
        "glow, dancing shadows thrown onto the ceiling or upper walls, "
        "low-key mood.",
    ),
    (
        "Silhouette Against Bright",
        "Silhouette against a bright background — subject rendered near-"
        "black with no interior detail, backdrop luminous (open sky, "
        "sunlit sand, moonlit clouds, or bright water surface).",
    ),
    (
        "Single Hard Rim Side",
        "Single hard rim light from one side — sharp bright edge separation "
        "on the subject profile, most of the frame in near-darkness. "
        "Chiaroscuro.",
    ),
)


def _shuffled_pool(pool: tuple, rng: random.Random) -> list:
    items = list(pool)
    rng.shuffle(items)
    return items


# ---------------------------------------------------------------------------
# Channel-RAG-aware forbidden-subject filter.
# The user's PART 1 rule: "cap doorway/portal/corridor/threshold/passage to
# at most 1 per episode unless the topic requires it". We hook the existing
# VisualQA channel RAG (`VisualQA_Agent.channel_rag.get_channel_rules`) —
# its `forbidden_tokens` list already contains "archway", "doorway",
# "portal", "window frame", "silhouette frame" for ancient_knowledge. Any
# subject-pool item whose generated text contains a forbidden phrase is
# skipped and swapped for the next non-forbidden pool item. Falls back to
# an empty forbidden list if the RAG is unavailable / channel unknown.
# ---------------------------------------------------------------------------
# Broadened 2026-08-15 to cover the rectangle/gate variants FLUX was
# smuggling in via _GEO_ANCHORS templates and RAG mandatory_elements.
_DEFAULT_DOORWAY_WORDS: tuple[str, ...] = (
    "doorway", "door way", "portal", "archway", "corridor",
    "threshold", "passageway", "passage way",
    "chamber interior", "stone chamber", "framed by stone",
    "framed through", "gateway", "ceremonial gate",
    "temple corridor", "window frame",
)


def _load_forbidden_subject_words(channel_name: str) -> set[str]:
    forbidden: set[str] = set(_DEFAULT_DOORWAY_WORDS)
    if not channel_name:
        return forbidden
    try:
        from VisualQA_Agent.channel_rag import get_channel_rules  # noqa: PLC0415

        rules = get_channel_rules(channel_name)
        for tok in rules.get("forbidden_tokens") or []:
            if isinstance(tok, str) and tok.strip():
                forbidden.add(tok.strip().lower())
    except Exception:  # noqa: BLE001
        # RAG unknown or unavailable — keep the default doorway cap only.
        pass
    return forbidden


def plan_episode_visual_sequence(
    n_acts: int,
    *,
    topic: str,
    seed: str = "",
    channel_name: str = "",
    subject_pool: "tuple | None" = None,
    shot_pool: "tuple | None" = None,
    lighting_pool: "tuple | None" = None,
    max_doorway_uses: int = 1,
) -> "list[dict]":
    """Per-episode 3-dimensional visual plan.

    Returns a list of length ``n_acts`` where each entry is a dict:

        {
            "subject": (name, generated_scene_concept_text),
            "shot":    (name, instruction),
            "lighting":(name, instruction),
        }

    Guarantees
    ----------
    * No two CONSECUTIVE acts repeat the same subject type, shot type, OR
      lighting setup (all three dimensions rotate independently).
    * Subject-pool items whose generated text contains a forbidden word
      (from the channel RAG's ``forbidden_tokens`` — merged with a
      default doorway/portal/corridor cap) are used at most
      ``max_doorway_uses`` times per episode. If the cap is reached, that
      pool item is replaced by the next non-forbidden alternative for
      subsequent acts.
    * Same ``(seed, topic, channel_name)`` -> same plan (reproducibility).
    * Independent of act count -- works for 3 acts or 30.
    """
    n = max(1, int(n_acts))
    raw_subjects = tuple(subject_pool) if subject_pool else _SUBJECT_POOL
    shots = tuple(shot_pool) if shot_pool else _SHOT_POOL
    lights = tuple(lighting_pool) if lighting_pool else _LIGHTING_POOL
    rng = random.Random(
        hashlib.sha256(
            (seed or topic or channel_name or "").encode("utf-8", errors="ignore")
        ).hexdigest()
    )
    forbidden = _load_forbidden_subject_words(channel_name)

    # ── Topic-relevance filter ──────────────────────────────────────────
    # Each _SUBJECT_POOL entry can carry an optional 3rd element -- a
    # relevance predicate (topic:str) -> bool. When present, the entry is
    # dropped from this episode's rotation if the predicate returns False.
    # This is how "underwater ruins" stays out of a desert-pyramid episode
    # and "ice/glacial" stays out of an equatorial-jungle episode. Backward-
    # compatible: 2-tuple entries (no predicate) are treated as always
    # relevant.
    def _relevance(item) -> bool:
        if len(item) < 3:
            return True
        pred = item[2]
        try:
            return bool(pred(topic or ""))
        except Exception:  # noqa: BLE001
            return True

    filtered = tuple(item for item in raw_subjects if _relevance(item))
    if len(filtered) < 2:
        # Safety floor: never let the rotation collapse below 2 entries,
        # otherwise the "no consecutive repeats" rule cannot be satisfied.
        filtered = raw_subjects
    subjects = filtered
    try:
        _LOG.info(
            "SUBJECT POOL | topic=%s | retained %d/%d entries -> %s",
            (topic or "").strip()[:60] or "(none)",
            len(subjects), len(raw_subjects),
            ", ".join(item[0] for item in subjects),
        )
    except Exception:  # noqa: BLE001
        pass

    def _text_ok(text: str, current_hits: int) -> bool:
        low = (text or "").lower()
        if any(w in low for w in forbidden):
            return current_hits < max_doorway_uses
        return True

    # Materialise (name, generated_text) subject items with the topic bound.
    def _mat_subject(item) -> tuple[str, str]:
        name = item[0]
        template_fn = item[1]
        try:
            text = template_fn(topic or "the topic")
        except Exception:  # noqa: BLE001
            text = f"SUBJECT — {name.upper()}: A cinematic depiction of {topic}."
        return (name, text)

    def _cycle(pool: tuple, materialize=None) -> list:
        out: list = []
        current = _shuffled_pool(pool, rng)
        while len(out) < n:
            for item in current:
                if len(out) >= n:
                    break
                out.append(materialize(item) if materialize else item)
            if len(out) >= n:
                break
            next_batch = _shuffled_pool(pool, rng)
            if len(pool) > 1 and next_batch:
                last_key = out[-1][0] if isinstance(out[-1], tuple) else out[-1]
                first_key = (
                    (materialize(next_batch[0])[0] if materialize else next_batch[0][0])
                    if isinstance(next_batch[0], tuple)
                    else next_batch[0]
                )
                if first_key == last_key:
                    for j in range(1, len(next_batch)):
                        alt_key = (
                            (materialize(next_batch[j])[0] if materialize else next_batch[j][0])
                            if isinstance(next_batch[j], tuple)
                            else next_batch[j]
                        )
                        if alt_key != last_key:
                            next_batch[0], next_batch[j] = next_batch[j], next_batch[0]
                            break
            current = next_batch
        return out[:n]

    subj_seq = _cycle(subjects, materialize=_mat_subject)
    shot_seq = _cycle(shots)
    light_seq = _cycle(lights)

    # ── Topic-theme weighting (Round 6) ─────────────────────────────────
    # A pool entry is "themed" when it carries a non-universal relevance
    # predicate that fired for this episode's topic (e.g. `_rel_underwater`
    # for a submerged-Atlantis reel, `_rel_desert` for an Egyptian pyramid
    # reel, `_rel_jungle` for a Mesoamerican reel). Without weighting, an
    # environment-specific subject appearing once in a 12-entry retained
    # pool only lands ~1/12 slots (≈1 of 16 acts) even when the episode's
    # theme IS that environment — the underwater-Atlantis bug from Round 5.
    #
    # Fix: when at least one themed entry survived the topic-relevance
    # filter, inject themed subjects into ~35 % of the act slots (target
    # 30-40 %, clamped ≤ n // 2), spaced roughly evenly, still respecting
    # the no-consecutive-repeat rule. The remaining slots keep the round-
    # robin variety draw (Artifact Macro, Human Explorer, Night Sky, plus
    # the generic architecture entries).
    _UNIVERSAL_PREDS = (_rel_always, _rel_night_sky)
    themed = [
        item for item in subjects
        if len(item) >= 3 and item[2] not in _UNIVERSAL_PREDS
    ]
    if themed and n >= 3:
        target_theme_count = max(2, int(round(n * 0.35)))
        target_theme_count = min(target_theme_count, n // 2)
        stride = n / target_theme_count
        themed_shuffled = _shuffled_pool(tuple(themed), rng)
        injected_slots: list[int] = []
        for i in range(target_theme_count):
            slot_ideal = int(round(i * stride + stride * 0.5))
            candidate_item = themed_shuffled[i % len(themed_shuffled)]
            candidate = _mat_subject(candidate_item)
            chosen_slot = -1
            for delta in (0, 1, -1, 2, -2, 3, -3):
                slot = slot_ideal + delta
                if slot < 0 or slot >= n:
                    continue
                if slot in injected_slots:
                    continue
                prev_name = subj_seq[slot - 1][0] if slot > 0 else None
                next_name = subj_seq[slot + 1][0] if slot + 1 < n else None
                if candidate[0] == prev_name or candidate[0] == next_name:
                    continue
                chosen_slot = slot
                break
            if chosen_slot < 0:
                continue
            subj_seq[chosen_slot] = candidate
            injected_slots.append(chosen_slot)
        try:
            _LOG.info(
                "SUBJECT WEIGHTING | topic=%s | themed=%s | boosted %d/%d slots (target=%d)",
                (topic or "").strip()[:60] or "(none)",
                ",".join(item[0] for item in themed),
                len(injected_slots), n, target_theme_count,
            )
        except Exception:  # noqa: BLE001
            pass

    # Second pass — enforce forbidden-word cap on subject text. If a subject
    # exceeds the cap, swap it for the first non-forbidden alternative that
    # also doesn't equal the previous/next subject name.
    forbidden_hits = 0
    for i in range(len(subj_seq)):
        name, text = subj_seq[i]
        if not _text_ok(text, forbidden_hits):
            for alt in subjects:
                alt_mat = _mat_subject(alt)
                if alt_mat[0] == name:
                    continue
                if _text_ok(alt_mat[1], forbidden_hits):
                    if i > 0 and subj_seq[i - 1][0] == alt_mat[0]:
                        continue
                    if i + 1 < len(subj_seq) and subj_seq[i + 1][0] == alt_mat[0]:
                        continue
                    subj_seq[i] = alt_mat
                    name, text = alt_mat
                    break
        low = text.lower()
        if any(w in low for w in forbidden):
            forbidden_hits += 1

    return [
        {"subject": subj_seq[i], "shot": shot_seq[i], "lighting": light_seq[i]}
        for i in range(n)
    ]


def plan_shot_lighting_sequence(
    n_acts: int,
    *,
    seed: str = "",
    shot_pool: "tuple | None" = None,
    lighting_pool: "tuple | None" = None,
) -> "list[tuple[tuple[str, str], tuple[str, str]]]":
    """Per-episode (shot, lighting) plan.

    Returns a list of length ``n_acts`` where each entry is
    ``((shot_name, shot_instr), (lighting_name, lighting_instr))``.

    Guarantees
    ----------
    * No two CONSECUTIVE acts share the same shot type or the same
      lighting setup.
    * Within each rolling window of ``len(pool)`` acts, no shot or
      lighting is used twice (cycle through a shuffled pool, then
      re-shuffle so the boundary never repeats).
    * Same ``seed`` → same plan (reproducibility). Different ``seed``
      (e.g. episode stem) → different plan each episode.

    Independent of image / act count — works for 3 acts or 30.
    """
    n = max(1, int(n_acts))
    shots = tuple(shot_pool) if shot_pool else _SHOT_POOL
    lights = tuple(lighting_pool) if lighting_pool else _LIGHTING_POOL
    rng = random.Random(
        hashlib.sha256((seed or "").encode("utf-8", errors="ignore")).hexdigest()
    )

    def _cycle(pool: tuple) -> list:
        # Shuffle, then when the pool is exhausted re-shuffle in a way that
        # keeps the last item ≠ next item so the boundary never repeats.
        out: list = []
        current = _shuffled_pool(pool, rng)
        while len(out) < n:
            out.extend(current)
            if len(out) >= n:
                break
            next_batch = _shuffled_pool(pool, rng)
            # If the last emitted item equals the first of the next batch,
            # swap next_batch[0] with next_batch[1] (or any different one).
            if len(pool) > 1 and next_batch and out and next_batch[0] == out[-1]:
                for j in range(1, len(next_batch)):
                    if next_batch[j] != out[-1]:
                        next_batch[0], next_batch[j] = next_batch[j], next_batch[0]
                        break
            current = next_batch
        return out[:n]

    shot_seq = _cycle(shots)
    light_seq = _cycle(lights)
    return list(zip(shot_seq, light_seq))

# Lightweight noun lexicon for common VO entities (extendable; not exhaustive NLP)
_NOUN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bcaves?\b|\bcaverns?\b|\bgrottos?\b", "cave / subterranean cavern interior"),
    (r"\bbooks?\b|\bmanuscripts?\b|\bscrolls?\b|\btablets?\b|\blibraries?\b",
     "ancient books / manuscripts / clay tablets"),
    (r"\bstars?\b|\bstarry\b|\bconstellations?\b|\bcosmos\b|\bgalaxy\b|\bnebula\b",
     "starry night sky / cosmic starfield"),
    (r"\bpyramids?\b|\bgiza\b", "Great Pyramid / pyramid monument"),
    (r"\btemples?\b|\bshrines?\b|\bsanctuar(?:y|ies)\b", "ancient temple / shrine"),
    (r"\bruins?\b|\bmegaliths?\b|\bstones?\b|\bobelisks?\b", "megalithic ruins / stone blocks"),
    (r"\bocean\b|\bsea\b|\bwaves?\b|\bunderwater\b", "ocean / underwater seascape"),
    (r"\bdesert\b|\bsands?\b|\bdunes?\b", "desert dunes / arid sandscape"),
    (r"\bforest\b|\bjungle\b|\btrees?\b", "dense forest / jungle canopy"),
    (r"\bmountain\b|\bpeak\b|\bcliff\b", "mountain peak / cliff face"),
    (r"\bcity\b|\bmegacity\b|\btowers?\b|\bskyline\b", "city skyline / towers"),
    (r"\bfire\b|\btorch(?:es)?\b|\bflame\b", "torch fire / flame light"),
    (r"\bcrystal(?:s)?\b|\bskulls?\b", "crystal / crystal skull artifact"),
    (r"\bhands?\b|\bfingers?\b", "human hands in frame"),
    (r"\bdoor(?:way|s)?\b|\bgate\b|\barch(?:way|es)?\b", "stone doorway / arch"),
    (r"\bmap\b|\bcharts?\b|\bcompass\b", "ancient map / navigation chart"),
    (r"\bgold\b|\btreasure\b|\bcoins?\b", "gold treasure / coins"),
    (r"\bmoon\b|\blunar\b", "moon / lunar night sky"),
    (r"\bsun\b|\bsunset\b|\bsunrise\b|\bdawn\b", "sun / dawn-dusk sky"),
    (r"\brain\b|\bstorm\b|\blightning\b", "storm / rain atmosphere"),
)


def shot_directive_for_act(act_index: int) -> tuple[str, str]:
    """Return ``(shot_name, shot_instruction)`` for act index (0-based)."""
    return _SHOT_CYCLE[int(act_index) % len(_SHOT_CYCLE)]


def extract_literal_noun_anchors(spoken_text: str, *, max_anchors: int = 4) -> list[str]:
    """
    Extract physical setting / object anchors from a spoken beat.

    Falls back to significant content nouns when lexicon misses.
    """
    text = (spoken_text or "").strip()
    if not text:
        return []
    low = text.lower()
    found: list[str] = []
    seen: set[str] = set()
    for pat, label in _NOUN_PATTERNS:
        if re.search(pat, low, flags=re.IGNORECASE):
            key = label.lower()
            if key not in seen:
                seen.add(key)
                found.append(label)
            if len(found) >= max_anchors:
                return found

    # Fallback: keep concrete nouns (len>=4) that aren't stopwords
    _stop = {
        "that", "this", "with", "from", "they", "them", "have", "been", "were",
        "when", "what", "which", "their", "there", "about", "would", "could",
        "should", "into", "over", "under", "after", "before", "these", "those",
        "some", "more", "most", "also", "just", "only", "even", "very", "such",
        "like", "than", "then", "once", "still", "each", "other", "another",
        "ancient", "hidden", "secret", "people", "world", "history", "theory",
        "researchers", "legend", "according", "believe", "suggests", "proposes",
    }
    words = re.findall(r"[A-Za-z][A-Za-z\-']{3,}", text)
    for w in words:
        wl = w.lower()
        if wl in _stop or wl.endswith("ly") or wl.endswith("ing"):
            continue
        if wl not in seen:
            seen.add(wl)
            found.append(w)
        if len(found) >= max_anchors:
            break
    return found[:max_anchors]


def should_feature_main_subject(
    act_index: int,
    *,
    total_acts: int,
    consecutive_subject_beats: bool = False,
) -> bool:
    """
    Subject frequency cap: do not put the main subject in every frame.

    Allow when:
      - Act 1 (hook / establishing of topic)
      - Explicit consecutive script beats demand it
      - Roughly every 3rd act after the first (sparse reappearances)
    """
    if consecutive_subject_beats:
        return True
    if act_index == 0:
        return True
    if total_acts <= 2:
        return True
    return (act_index % 3) == 0


def consecutive_subject_dictated(
    prev_snippet: str,
    curr_snippet: str,
    main_subject: str,
) -> bool:
    """True when consecutive beats both clearly name the same main subject."""
    subj = (main_subject or "").strip().lower()
    if len(subj) < 4:
        return False
    # Use significant tokens from subject (≥4 chars)
    tokens = [t for t in re.findall(r"[a-z0-9']+", subj) if len(t) >= 4]
    if not tokens:
        return False
    prev_l = (prev_snippet or "").lower()
    curr_l = (curr_snippet or "").lower()
    prev_hit = any(t in prev_l for t in tokens)
    curr_hit = any(t in curr_l for t in tokens)
    return prev_hit and curr_hit


def build_aligned_visual_block(
    *,
    spoken_snippet: str,
    act_index: int,
    total_acts: int,
    main_subject: str = "",
    prev_snippet: str = "",
    force_subject: bool | None = None,
    shot_override: "tuple[str, str] | None" = None,
    lighting_override: "tuple[str, str] | None" = None,
) -> str:
    """
    Build the alignment directive block appended to every act image prompt.

    When ``shot_override`` and ``lighting_override`` are provided (each a
    ``(name, instruction)`` tuple, e.g. from ``plan_shot_lighting_sequence``),
    they replace the legacy 4-item shot cycle and inject an explicit
    per-image lighting setup so consecutive frames never share a shot or
    lighting default.
    """
    if shot_override is not None and shot_override:
        shot_name, shot_instr = shot_override[0], shot_override[1]
    else:
        shot_name, shot_instr = shot_directive_for_act(act_index)
    anchors = extract_literal_noun_anchors(spoken_snippet)
    consec = consecutive_subject_dictated(prev_snippet, spoken_snippet, main_subject)
    feature = (
        force_subject
        if force_subject is not None
        else should_feature_main_subject(
            act_index,
            total_acts=total_acts,
            consecutive_subject_beats=consec,
        )
    )

    lines = [
        f"SPOKEN BEAT (literal visualisation): \"{(spoken_snippet or '').strip()}\"",
        f"SHOT TYPE [{shot_name}]: {shot_instr}",
    ]
    if lighting_override is not None and lighting_override:
        _light_name, _light_instr = lighting_override[0], lighting_override[1]
        lines.append(f"LIGHTING [{_light_name}]: {_light_instr}")
    if anchors:
        lines.append(
            "LITERAL NOUN ANCHORS (MUST appear as physical set dressing / primary objects): "
            + "; ".join(anchors)
            + "."
        )
    else:
        lines.append(
            "LITERAL NOUN ANCHORS: Derive setting and primary objects strictly from "
            "the spoken beat — no generic filler backgrounds."
        )

    if feature and main_subject:
        lines.append(
            f"MAIN SUBJECT ALLOWED THIS FRAME: include '{main_subject}' as a "
            "recognisable element (not mandatory to dominate every corner)."
        )
    else:
        lines.append(
            "SUBJECT FREQUENCY CAP: Do NOT centre/repeat the channel's main topic "
            "subject this frame unless the spoken beat explicitly requires it. "
            "Prioritise the spoken nouns and shot type above."
        )

    lines.append(
        "ANTI-MONOTONY: This frame must be visually distinct from adjacent acts "
        "(different camera distance, focal object, spatial scale, and lighting)."
    )
    return " ".join(lines)


def enrich_act_prompts(
    base_prompts: Iterable[str],
    spoken_snippets: list[str],
    *,
    main_subject: str = "",
    force_subject_acts: Iterable[int] | None = None,
) -> list[str]:
    """
    Append alignment blocks to an existing list of act prompts (in-place style copy).
    """
    prompts = list(base_prompts)
    n = len(prompts)
    forced = set(force_subject_acts or [])
    out: list[str] = []
    for i, base in enumerate(prompts):
        snip = spoken_snippets[i] if i < len(spoken_snippets) else ""
        prev = spoken_snippets[i - 1] if i > 0 and i - 1 < len(spoken_snippets) else ""
        block = build_aligned_visual_block(
            spoken_snippet=snip or main_subject,
            act_index=i,
            total_acts=n,
            main_subject=main_subject,
            prev_snippet=prev,
            force_subject=True if i in forced else None,
        )
        out.append(f"{base.rstrip()} {block}")
    return out
