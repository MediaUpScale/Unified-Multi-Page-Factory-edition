# -*- coding: utf-8 -*-
"""
Modular Channel Aesthetics RAG.

Architecture:
  - JSON file store is the durable source of truth (always works).
  - ChromaDB local persistence mirrors DNA for similarity query when the
    native backend is available (set VISUALQA_USE_CHROMA=0 to force JSON-only).

Add a new channel DNA in ~5 lines by appending to ``CHANNEL_DNA_SEED``:

    "my_channel": {
        "forbidden_tokens": [...],
        "mandatory_elements": [...],
        "lighting_style": "...",
        "target_audience_rules": "...",
    },
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import struct
from pathlib import Path
from typing import Any

from VisualQA_Agent import config

_LOG = logging.getLogger(__name__)

COLLECTION_NAME = "channel_aesthetics_v2"
_EMBED_DIM = 128
_JSON_STORE: Path = config.AGENT_ROOT / "channel_dna_store.json"

# ---------------------------------------------------------------------------
# Seed DNA — add a new channel here (5 lines of rules is enough)
# ---------------------------------------------------------------------------
CHANNEL_DNA_SEED: dict[str, dict[str, Any]] = {
    "master_mei": {
        "forbidden_tokens": [
            "Gossip Goblin",
            "fitness model",
            "gym bro",
            "tactical gear",
            "gore",
            "body horror",
            "H.R. Giger",
            "teens in bedrooms",
            "smartphone desk",
            "lifestyle soft-focus",
            "pastel wellness",
            "modern athletic wear",
            # Global firearm / action-movie ban (QA enforced)
            "firearm",
            "handgun",
            "revolver",
            "rifle",
            "pistol",
            "shotgun",
            "gunfight",
            "assault rifle",
        ],
        "mandatory_elements": [
            "8–10 FRAME LORE (90–120s): F1 Master Mei base anchor + meditation (≤8s); "
            "F3 agonizing human slaves / cybernetic implants (RAG override); "
            "F7 extreme close-up human cyborg (9–10 frame lore); "
            "penultimate matrix pod facility breakout (RAG override); "
            "final Master Mei base anchor + temple eyes-on-camera",
            "Master Mei DNA: snow-white topknot + two long white chest locks, "
            "extra-long ultra-thick snow-white eyebrows past temples, mid-chest snow-white "
            "beard + flowing mustache, white inner robe + black vest gold lapels + mala beads",
            "Mei environments: ~70% high-altitude nature (cliffs/ridges/sea of clouds), "
            "~30% open-air mountain shrines; templates are references — match script philosophy",
            "FLUX dystopia: 120–180 words, mandatory prefix "
            "'Cyberpunk dystopian Earth, attention monopoly era:', no tag spam",
            "Environment: neon billboards, hanging cable bundles, industrial haze, cybernetic conduits",
            "role-separated: never fuse cybernetics onto Master Mei",
            "NO firearms / handguns / rifles / modern action-movie tropes",
        ],
        "lighting_style": (
            "moody cinematic lighting, dark atmospheric — "
            "cold moonlight / storm lightning / dawn rim light / warm torchfire, "
            "dystopian red neon reflections against deep dramatic shadows"
        ),
        "target_audience_rules": (
            "Western/US men seeking self-mastery. STRICT 8–10 frame lore for 90–120s. "
            "Scene 1 ≤8s meditation landscapes. Scenes 2+ ≈10–12s. "
            "Frame 1 + final MUST show Master Mei. NEVER firearms, Gossip Goblin, gore, gym, teens."
        ),
        "frame_lore": {
            "1_intro": (
                "Master Mei base anchor meditating on high-altitude cliff / misty ridge "
                "or open-air mountain shrine — white robe + black vest gold trim — NO tech"
            ),
            "3_human_slaves": (
                "RAG OVERRIDE scene_03: pale agonizing human men, cybernetic goggles, "
                "wires in skin, faces twisted in pain, dystopian red neon reflections"
            ),
            "2_4_matrix": "Matrix/Maya illusion; panopticon; systemic gears (except Scene 3 override)",
            "5_7_training": "Master Mei instructing monks; Scene 7 = human cyborg close-up override",
            "7_cyborg_closeup": (
                "RAG OVERRIDE scene_07: extreme close-up agonizing human face, "
                "crude brass cybernetic visors, temple wires, dramatic cinematic lighting"
            ),
            "penultimate_pods": (
                "RAG OVERRIDE penultimate: dark matrix pod facility, rows of glass pods, "
                "muscular Asian warrior smashing out of liquid capsule"
            ),
            "10_outro": (
                "Master Mei base anchor on mountain ridge / open-air shrine, "
                "looking directly at camera"
            ),
        },
        "visual_concepts": {
            "dopamine": (
                "Scene 7 close-up: agonizing human cyborg face with brass cybernetic visors + temple wires"
            ),
            "training": (
                "Master Mei instructing martial monks carrying stone blocks "
                "in harsh cold rain — high physical intensity"
            ),
            "system": (
                "Scene 3 RAG: pale agonizing human slaves, cybernetic goggles, wires in skin"
            ),
            "maya": (
                "Biomechanical Maya goddess/entity holding glowing digital "
                "chains around human necks"
            ),
            "focus": (
                "Penultimate RAG: warrior smashing out of liquid-filled matrix pod"
            ),
            "outro": (
                "Master Mei base anchor on mountain ridge / open-air shrine, "
                "eyes locked on camera"
            ),
        },
        "prompt_file_overrides": {
            "scene_01": "channels_config/master_mei/prompts/scene_01_hook_meditation.txt",
            "scene_03": "channels_config/master_mei/prompts/scene_03_biomechanical_slaves.txt",
            "scene_07": "channels_config/master_mei/prompts/scene_07_human_cyborg_closeup.txt",
            "scene_final": "channels_config/master_mei/prompts/scene_final_hook.txt",
            "mei_anchor": "channels_config/master_mei/prompts/master_mei_base_anchor.txt",
            "penultimate": "channels_config/master_mei/prompts/penultimate_frame.txt",
            "qa_rules": "channels_config/master_mei/prompts/visual_qa_rules.txt",
            "music_prompt": "channels_config/master_mei/prompts/music_prompt_directive.txt",
        },
        "golden_prompt_anchor": (
            "Cyberpunk dystopian Earth, attention monopoly era: natural FLUX sentences "
            "(120–180 words) — Subject + Action/Emotion + Environment/Lighting; "
            "no 8k/photorealistic/masterpiece tags"
        ),
    },
    # ECONOMIC_REEL_LOFI — ink / graphic-novel stills (no LoRA, Flux Schnell)
    "lofi_economic": {
        "critic_profile": "lofi_economic",
        "forbidden_tokens": [
            "photorealistic",
            "photograph",
            "selfie",
            "stock photo",
            "nsfw",
            "nude",
            "pornographic",
            "logo watermark baked in",
            "readable UI text",
            "garbled letters",
        ],
        "mandatory_elements": [
            "ink / graphic-novel illustration style with consistent line weight",
            "halftone or limited duotone shading (not photoreal skin)",
            "vertical 9:16 framing with centrally composed subject",
            "no embedded captions, logos, or garbled typography in the pixels",
            "hands/faces within acceptable illustration deformity threshold",
        ],
        "lighting_style": (
            "flat-to-soft illustrated lighting, graphic novel mood, "
            "muted charcoal and teal duotone atmosphere — never studio softbox beauty"
        ),
        "target_audience_rules": (
            "Emotional LOFI reels for relationship/parenting education. "
            "Reject photorealism drift from Flux Schnell. "
            "Reject embedded text artifacts. Accept stylized illustration anatomy."
        ),
        "visual_concepts": {
            "relationship": "quiet domestic or symbolic figures, trust/attachment beats",
            "parenting": "warm caregiving moments, soft ordinary chaos, no exploitation",
        },
    },
    "ancient_knowledge": {
        "critic_profile": "ancient_mystery",
        "forbidden_tokens": [
            "CGI",
            "3D render",
            "Unreal Engine",
            "illustration",
            "sketch",
            "cartoon",
            "graphite",
            "front-facing postcard",
            "flat lighting",
            "studio softbox",
            "modern clothing",
            "smartphone",
            "lifestyle photography",
            "archway",
            "doorway",
            "portal",
            "window frame",
            "silhouette frame",
        ],
        "mandatory_elements": [
            "Must have dynamic lighting — volumetric shafts, rim light, chiaroscuro",
            "Must not look like a generic front-facing 3D render",
            "Must match the ancient mystery aesthetic — ultra-realistic cinematic "
            "historical photography of a recognisable monument with an anomalous detail",
            "35mm documentary film grain, ochre and shadow-black grade",
            "Wide aerial / immersive first-person camera; subject centrally framed",
        ],
        "lighting_style": (
            "dramatic single-source cinematic light — warm amber torchlight or "
            "cold ethereal moonlight, volumetric dust-haze beams, strong rim lighting "
            "on megalithic stone, high-contrast chiaroscuro — never flat or muddy"
        ),
        "target_audience_rules": (
            "Investigative documentary stills for Ancient Knowledge. "
            "Reject CGI, illustration, and tourist postcards. "
            "Require recognisable geo-anchors (Pyramid, Baalbek, Göbekli Tepe, "
            "Puma Punku, Easter Island, Stonehenge) plus an impossible detail."
        ),
        "visual_concepts": {
            "megalith": "impossible-scale stone blocks, precision joints, torchlit quarry",
            "pyramid": "Great Pyramid / Sphinx enclosure, hidden chambers, anomalous tooling",
            "pre_flood": "Göbekli Tepe, Younger Dryas, buried temple, starfield horizon",
            "oopart": "Antikythera, crystal skulls, vimana — artefact as visual hero",
            "lost_city": "Yonaguni, Dwarka, El Dorado — submerged or jungle-swallowed ruins",
        },
    },
}

# ---------------------------------------------------------------------------
# GOLDEN LOCK — hybrid ancestral temple × cyberpunk (2026-07-24 reels)
# ---------------------------------------------------------------------------
VISUAL_TRANSLATION_MATRIX: dict[str, dict[str, str]] = {
    "dopamine": {
        "keywords": (
            "dopamine,matrix,phone,scroll,addiction,neural,wire,impulse,validation,"
            "distraction,screen,betting"
        ),
        "subject": (
            "Futuristic cyberpunk slaves chained by glowing neon neural wires, "
            "bio-mechanical screens fused to faces"
        ),
        "environment": "neon-lit rain-drenched dystopian megacity",
        "shot": "wide cyberpunk matrix slavery",
    },
    "training": {
        "keywords": (
            "discipline,train,martial,waterfall,conditioning,dojo,forge,temple,"
            "warrior,hardship,stance"
        ),
        "subject": (
            "Master Mei ACTIVE in hard physical/spiritual conditioning with disciples"
        ),
        "environment": (
            "misty mountain peaks, roaring waterfall, rain-soaked ancestral temple"
        ),
        "shot": "wide ancestral hardship training",
    },
    "system": {
        "keywords": (
            "system,rat race,crowd,mass,tower,control,toxic,friends,chains,leaks,"
            "panopticon,corporate"
        ),
        "subject": "Herds of wired citizens under mass-control holograms",
        "environment": "bleak dystopian megacity, endless neon towers, rain",
        "shot": "wide mass-manipulation megacity",
    },
    "focus": {
        "keywords": "focus,attention,energy,guard,clarity,silence,inner,citadel",
        "subject": "Master Mei ancestral-temple discipline OR dual-world contrast",
        "environment": (
            "ancestral temple under storm OR neon rain megacity vs temple silhouette"
        ),
        "shot": "medium mastery / contrast vista",
    },
    "business": {
        "keywords": "wealth,money,empire,capital,financial,power,gold,altar,sovereignty",
        "subject": (
            "Ancient stone altar with holographic financial charts; molten gold cyber-armor"
        ),
        "environment": "sacred stone altar, wet stone, neon circuitry accents",
        "shot": "wide wealth altar",
    },
    # Compatibility aliases → golden themes
    "tech_slavery": {
        "keywords": "digital slavery,tech slavery,matrix,crt",
        "subject": (
            "Futuristic cyberpunk slaves chained by glowing neon neural wires"
        ),
        "environment": "neon-lit rain-drenched dystopian megacity",
        "shot": "wide cyberpunk matrix slavery",
    },
    "hardship_discipline": {
        "keywords": "discipline,hardship,forge,train",
        "subject": "Master Mei ACTIVE in ancestral hardship conditioning",
        "environment": "misty mountain peaks, rain-soaked ancestral temple",
        "shot": "wide ancestral hardship training",
    },
    "panopticon_abstract_trap": {
        "keywords": "panopticon,trance,surveillance",
        "subject": "Herds of wired citizens under mass-control holograms",
        "environment": "bleak dystopian megacity, neon towers",
        "shot": "wide mass-manipulation megacity",
    },
}

_CONCEPT_ALIASES: dict[str, str] = {
    "digital_slavery": "dopamine",
    "digital_trap_panopticon": "system",
    "temptation": "dopamine",
    "seduction": "dopamine",
    "trap_beauty_wealth_theft": "dopamine",
    "discipline": "training",
    "brutal_martial_discipline": "training",
    "philosophy": "focus",
    "art_of_war_sovereignty": "focus",
    "master_mei_guidance": "focus",
    "tech_slavery": "dopamine",
    "hardship_discipline": "training",
    "panopticon_abstract_trap": "system",
}


def classify_visual_concept(base_script_beat: str) -> str:
    """Score beat keywords against golden hybrid themes."""
    lo = (base_script_beat or "").lower()
    core_keys = ("dopamine", "training", "system", "focus", "business")
    scores: dict[str, int] = {k: 0 for k in core_keys}
    for key in core_keys:
        entry = VISUAL_TRANSLATION_MATRIX[key]
        keywords = [k.strip() for k in entry["keywords"].split(",") if k.strip()]
        scores[key] = sum(1 for k in keywords if k in lo)
    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    if scores[best] <= 0:
        return "dopamine"
    return best


def resolve_visual_beat(base_script_beat: str) -> tuple[str, str, str, str]:
    """Return ``(concept_key, subject, environment, shot)`` for golden themes."""
    key = classify_visual_concept(base_script_beat)
    key = _CONCEPT_ALIASES.get(key, key)
    if key not in VISUAL_TRANSLATION_MATRIX:
        key = "dopamine"
    entry = VISUAL_TRANSLATION_MATRIX[key]
    return (
        key,
        entry["subject"],
        entry["environment"],
        entry.get("shot", "cinematic wide shot"),
    )

_active_channel: str | None = None
_chroma_client: Any = None
_chroma_enabled: bool | None = None
_chroma_collection: Any = None


def _normalize_rules(rules: dict[str, Any]) -> dict[str, Any]:
    concepts = rules.get("visual_concepts") or {}
    if not isinstance(concepts, dict):
        concepts = {}
    out = {
        "forbidden_tokens": list(rules.get("forbidden_tokens") or []),
        "mandatory_elements": list(rules.get("mandatory_elements") or []),
        "lighting_style": str(rules.get("lighting_style") or ""),
        "target_audience_rules": str(rules.get("target_audience_rules") or ""),
        "visual_concepts": {str(k): str(v) for k, v in concepts.items()},
    }
    if rules.get("critic_profile"):
        out["critic_profile"] = str(rules.get("critic_profile"))
    return out


def _rules_to_document(channel_name: str, rules: dict[str, Any]) -> str:
    concepts = rules.get("visual_concepts") or {}
    concept_block = "\n".join(
        f"- {key}: {desc}" for key, desc in concepts.items()
    ) or "(none)"
    return (
        f"Channel: {channel_name}\n"
        f"Forbidden: {', '.join(rules.get('forbidden_tokens', []))}\n"
        f"Mandatory: {', '.join(rules.get('mandatory_elements', []))}\n"
        f"Lighting: {rules.get('lighting_style', '')}\n"
        f"Audience: {rules.get('target_audience_rules', '')}\n"
        f"Visual Concepts:\n{concept_block}"
    )


# ---------------------------------------------------------------------------
# JSON source of truth
# ---------------------------------------------------------------------------
def _load_json_store() -> dict[str, dict[str, Any]]:
    if not _JSON_STORE.is_file():
        return {}
    try:
        data = json.loads(_JSON_STORE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_json_store(store: dict[str, dict[str, Any]]) -> None:
    config.ensure_runtime_dirs()
    _JSON_STORE.parent.mkdir(parents=True, exist_ok=True)
    _JSON_STORE.write_text(
        json.dumps(store, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Optional Chroma mirror (deterministic embeddings — no onnxruntime)
# ---------------------------------------------------------------------------
class DeterministicHashEmbedding:
    """Lightweight embedder — avoids onnxruntime / torch on Python 3.14+."""

    def __init__(self, dim: int = _EMBED_DIM) -> None:
        self.dim = dim

    def __call__(self, input: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in input]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = (text or "").lower().replace(",", " ").split() or ["empty"]
        for tok in tokens:
            digest = hashlib.sha256(tok.encode("utf-8")).digest()
            for i in range(0, min(len(digest), self.dim * 4), 4):
                idx = struct.unpack_from(">I", digest, i)[0] % self.dim
                sign = 1.0 if digest[i] % 2 == 0 else -1.0
                vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


def _chroma_wanted() -> bool:
    # Default OFF — Chroma native deps can segfault on some Python 3.14 / Win builds.
    # Enable explicitly: set VISUALQA_USE_CHROMA=1
    return os.getenv("VISUALQA_USE_CHROMA", "0").strip() in {"1", "true", "True", "yes"}


def _try_get_chroma_collection() -> Any | None:
    """Return a Chroma collection or None if unavailable / unstable."""
    global _chroma_client, _chroma_enabled, _chroma_collection

    if _chroma_enabled is False:
        return None
    if not _chroma_wanted():
        _chroma_enabled = False
        return None
    if _chroma_collection is not None:
        return _chroma_collection

    try:
        import chromadb
        from chromadb.config import Settings

        config.ensure_runtime_dirs()
        _chroma_client = chromadb.PersistentClient(
            path=str(config.CHROMA_PERSIST_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
        embedder = DeterministicHashEmbedding()
        _chroma_collection = _chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
            embedding_function=embedder,  # type: ignore[arg-type]
        )
        _chroma_enabled = True
        _LOG.info("CHANNEL_RAG | ChromaDB mirror enabled → %s", config.CHROMA_PERSIST_DIR)
        return _chroma_collection
    except Exception as exc:  # noqa: BLE001
        _chroma_enabled = False
        _chroma_collection = None
        _LOG.warning(
            "CHANNEL_RAG | ChromaDB unavailable (%s) — using JSON store only", exc
        )
        return None


def _mirror_to_chroma(channel_name: str, rules: dict[str, Any]) -> None:
    col = _try_get_chroma_collection()
    if col is None:
        return
    try:
        col.upsert(
            ids=[f"channel::{channel_name}"],
            documents=[_rules_to_document(channel_name, rules)],
            metadatas=[{
                "channel_name": channel_name,
                "rules_json": json.dumps(rules),
            }],
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("CHANNEL_RAG | Chroma upsert failed (%s)", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def register_channel_dna(channel_name: str, rules: dict[str, Any]) -> None:
    """
    Upsert a channel's aesthetic DNA (JSON + optional Chroma mirror).

    ``rules`` keys: forbidden_tokens, mandatory_elements,
    lighting_style, target_audience_rules.
    """
    name = (channel_name or "").strip()
    if not name:
        raise ValueError("channel_name is required")

    normalized = _normalize_rules(rules)
    store = _load_json_store()
    store[name] = normalized
    _save_json_store(store)
    _mirror_to_chroma(name, normalized)
    _LOG.info("CHANNEL_RAG | upserted DNA for channel=%s", name)


def seed_default_channels(*, force: bool = False) -> None:
    """
    Seed ``CHANNEL_DNA_SEED``.

    Seed channels are always re-upserted so DNA expansions ship on import.
    ``force`` kept for API compatibility.
    """
    del force
    for name, rules in CHANNEL_DNA_SEED.items():
        register_channel_dna(name, rules)


def set_channel_context(channel_name: str) -> str:
    """
    Activate a channel context: ensure DNA is seeded/registered, return name.

    Raises ``KeyError`` if the channel is unknown and not yet registered.
    """
    global _active_channel
    name = (channel_name or "").strip()
    if not name:
        raise ValueError("channel_name is required")

    seed_default_channels(force=False)

    if name in CHANNEL_DNA_SEED:
        register_channel_dna(name, CHANNEL_DNA_SEED[name])
    else:
        try:
            get_channel_rules(name)
        except KeyError as exc:
            raise KeyError(
                f"Unknown channel '{name}'. Add DNA to CHANNEL_DNA_SEED "
                f"in channel_rag.py or call register_channel_dna()."
            ) from exc

    _active_channel = name
    _LOG.info("CHANNEL_RAG | active context → %s", name)
    return name


def get_channel_rules(channel_name: str) -> dict[str, Any]:
    """
    Return aesthetic rules for ``channel_name``:

    - forbidden_tokens
    - mandatory_elements
    - lighting_style
    - target_audience_rules
    """
    name = (channel_name or "").strip()
    if not name:
        raise ValueError("channel_name is required")

    seed_default_channels(force=False)
    store = _load_json_store()
    if name in store:
        return _normalize_rules(store[name])

    # Optional Chroma lookup (similarity) if JSON miss
    col = _try_get_chroma_collection()
    if col is not None:
        try:
            results = col.query(query_texts=[f"Channel: {name}"], n_results=1)
            metas = (results.get("metadatas") or [[]])[0]
            if metas:
                meta = metas[0]
                if str(meta.get("channel_name", "")).lower() == name.lower():
                    return json.loads(meta["rules_json"])
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("CHANNEL_RAG | Chroma query failed (%s)", exc)

    raise KeyError(f"Channel '{name}' not found")


def get_active_channel() -> str | None:
    """Return the channel set by ``set_channel_context``, if any."""
    return _active_channel


def list_channels() -> list[str]:
    """List all registered channel names."""
    seed_default_channels(force=False)
    return sorted(_load_json_store().keys())


def rules_as_prompt_block(rules: dict[str, Any]) -> str:
    """Render rules as a concise prompt / critic instruction block."""
    forbidden = ", ".join(rules.get("forbidden_tokens") or [])
    mandatory = ", ".join(rules.get("mandatory_elements") or [])
    concepts = rules.get("visual_concepts") or {}
    concept_lines = "\n".join(f"  - {k}: {v}" for k, v in concepts.items())
    return (
        f"FORBIDDEN: {forbidden}\n"
        f"MANDATORY: {mandatory}\n"
        f"LIGHTING: {rules.get('lighting_style', '')}\n"
        f"AUDIENCE: {rules.get('target_audience_rules', '')}\n"
        f"VISUAL CONCEPTS:\n{concept_lines or '  (none)'}"
    )
