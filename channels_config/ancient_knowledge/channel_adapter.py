# -*- coding: utf-8 -*-
"""
AncientKnowledgeAdapter — Schema B only.

Reads channels_config/ancient_knowledge/master_dna.json and page_config.py.
Does not import agents.media.persona_dna. Does not load Anna / Mei / LOFI DNA.

Visual rules natively include the photoreal monument lock that currently
lives as a page_id branch in main.py.
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any

from core.interfaces.channel import BaseChannelConfig, VisualRules

_CHANNEL_DIR = Path(__file__).resolve().parent
_DNA_PATH = _CHANNEL_DIR / "master_dna.json"

# Hard isolation: never inherit sketch / couple / wellness language.
_FORBIDDEN_CROSS_CHANNEL = (
    "relationship", "attachment", "therapy", "couple", "man and woman",
    "graphite", "pencil drawing", "charcoal", "Dica de Dona de Casa",
    "Holistic Legacy", "Anna", "microbiome", "newborn", "gut flora",
)

_OFF_NICHE_NEEDLES = (
    "microbiome", "newborn", "gut flora", "gut health", "vaginal",
    "c-section", "c section", "probiotic", "holistic", "wellness",
    "longevity", "castor oil", "biochem", "attachment", "relationship",
    "marriage", "parenting", "baby", "infant", "delivery debate",
    "nurturing", "housewife", "celtic salt", "cold plunge", "anna protocol",
    "dica de dona", "payhip", "protocol notes",
)

_NICHE_TOPICS: tuple[str, ...] = (
    "Ancient Enigmas hidden in megalithic stone",
    "Lost OOPArts — Out of Place Artifacts that defy the timeline",
    "Unexplained Pyramids of Antarctica under the ice",
    "Area 51 and alleged extraterrestrial contact with ancient sites",
    "Egyptian hieroglyphs depicting helicopters and modern technology",
    "Göbekli Tepe untouched — deliberately buried 10,000 years ago",
    "Anunnaki legends on the Sumerian tablets",
    "The Great Pyramid precision no modern crane can match",
    "Baalbek Trilithon — 1,500-tonne blocks no machine can lift",
    "Puma Punku H-blocks machined at 13,000 feet",
    "Sacsayhuamán interlocking stones with no mortar and no gaps",
    "Antikythera Mechanism — a 2,000-year-old analogue computer",
    "Baghdad Battery — Mesopotamian galvanic cells",
    "Vimana flying machines in the Sanskrit epics",
    "Crystal Skulls of Mesoamerica with no tool marks",
    "Yonaguni Monument — Japan's submerged stepped pyramid",
    "Dwarka sunken city dated to 9,000 BC",
    "Nazca Lines only meaningful from the air",
    "Dendera Temple light-bulb reliefs",
    "The Osireion at Abydos predating known Egyptian history",
    "Sphinx enclosure erosion from a pre-ice-age flood",
    "Paracas elongated skulls 25 percent larger than human",
    "Mohenjo-daro vitrified stone and the ancient nuclear blast theory",
    "The Sirius Mystery — Dogon knowledge of Sirius B",
    "Tartaria mudflood and buried first-floor windows",
    "Younger Dryas comet impact that reset civilisation",
    "Atlantis geological evidence still debated",
    "Easter Island Moai standing on the ocean floor",
    "Oak Island Money Pit — two centuries with no answer",
    "Library of Alexandria — what was burned and why",
    "Saqqara Bird that flies as an aerodynamic glider",
    "Dropa stones and the Bayan Har Shan disks",
    "Ica stones showing dinosaurs and surgery",
    "Klerksdorp spheres of pyrophyllite",
    "London Hammer encased in ancient rock",
    "Coso artifact — a spark plug in a geode",
    "Quimbaya airplanes of pre-Columbian gold",
    "Teotihuacan mercury tunnels under the Pyramid of the Sun",
    "Gunung Padang the buried pyramid of Java",
    "Bosnian Pyramid of the Sun controversy",
    "Underwater ruins of Cuba's hexagonal structures",
    "Stone spheres of Costa Rica",
    "Newgrange engineered for the winter solstice sun",
    "Carnac alignments and the lost surveyors",
    "Coral Castle and the builder who moved megaliths alone",
    "Sacsayhuamán zigzag walls as earthquake engineering",
    "Great Pyramid hidden chambers found by muon scan",
    "Anunnaki glyphs of sky-gods arriving in shining craft",
    "Göbekli Tepe animal carvings encoding a comet",
    "Puma Punku precision joints no bronze tool could cut",
)

_CAMERA_OPENERS: tuple[str, ...] = (
    "Wide aerial drone shot of {subject}, camera 400 metres above, open sky on all sides, monument filling the centre — never framed by architecture.",
    "Close-up macro photography of glowing alien inscriptions and impossible mechanical tooling on ancient stone; the artefact surface fills the entire frame.",
    "Over-the-shoulder shot of a 1920s archaeologist in period clothing examining a bizarre out-of-place relic on a wooden crate at the excavation.",
    "Low-angle worm's-eye photograph looking UP the face of {subject}, dramatic sky, rim-lit megalithic blocks, viewer standing at the base in open air.",
    "Oblique three-quarter aerial of {subject} with a disc-shaped craft hovering above the ruins, dust kicked up across the plateau.",
    "Museum-table still life: extreme close-up of a rare OOPArt under a single raking lamp, tool marks and unknown alloys filling the frame.",
    "Scale/epic wide shot: tiny human figures at the foot of {subject}, impossible construction scale, storm light, no interior frames.",
    "Handheld 1920s field-camera photograph of scholars clustered around a freshly unearthed anomalous device at the dig, desert horizon behind them.",
)

_DOORWAY_BAN = (
    "ABSOLUTELY NO archways, NO doorways, NO looking through windows, "
    "NO framing the subject with dark foregrounds. "
    "DO NOT use doorway framing, DO NOT use archway silhouettes, "
    "avoid looking from inside to outside, no portals, no silhouetted stone gates."
)

_PHOTOREAL_BODY = (
    "MEDIUM: Ultra-realistic photography — NOT digital art, NOT illustration, NOT CGI render. "
    "SUBJECT: {subject}. "
    "LIGHTING: Dramatic single-source cinematic light — warm amber torchlight, ancient fire, "
    "or cold ethereal blue-white moonlight. Volumetric light shafts in atmospheric haze. "
    "Strong directional RIM LIGHTING on carved stone and artefacts. High-contrast chiaroscuro. "
    "TEXTURE: Cinematic 35mm film grain, desaturated ochre and shadow-black grade. "
    "VISUAL FOCUS: Recognisable real-world monument or artefact with an impossible anomalous "
    "detail integrated naturally — precision machining, alien glyphs, out-of-place technology, "
    "or a hovering craft. "
    "DEPTH: open landscape or artefact-fill. Background — temple silhouette, starfield, or horizon. "
    "No clean CGI, no smooth plastic surfaces, no modern clothing except 1920s field kit. "
    "{doorway_ban} "
    "CRITICAL — DO NOT generate: a framed painting, a picture hanging on a wall, "
    "a gallery, a mockup, a physical canvas, picture borders, image-within-image, "
    "a postcard, a mural, a plaque, or any rectangular frame element. "
    "The entire frame is filled edge-to-edge. No white space, no margins, no border. "
    "Epic cinematic shot, full-bleed."
)


class AncientKnowledgeAdapter(BaseChannelConfig):
    """Schema B adapter: persona / cta_options / narrative_angles / environments."""

    def __init__(self, dna_path: Path | None = None) -> None:
        self._dna_path = Path(dna_path) if dna_path else _DNA_PATH
        self._dna = self._load_dna()
        self._page_cfg = self._load_page_config()

    @property
    def channel_id(self) -> str:
        return str(self._dna.get("page_id") or "ancient_knowledge")

    @property
    def display_name(self) -> str:
        persona = self._dna.get("persona") or {}
        return str(
            persona.get("name")
            or self._dna.get("display_name")
            or "Ancient Knowledge"
        )

    def get_system_prompt(self) -> str:
        persona = self._dna.get("persona") or {}
        name = self.display_name
        mission = str(persona.get("core_mission") or "").strip()
        tone = str(persona.get("voice_tone") or "investigative, neutral, immersive").strip()
        disclaimer = str(persona.get("disclaimer") or "").strip()
        pillars = persona.get("content_pillars") or []
        pillar_block = "\n".join(f"  - {p}" for p in pillars) or "  - Ancient mysteries"
        niche = str(self._dna.get("niche") or self._page_cfg.get("CONTENT_NICHE") or "").strip()
        niche_disclaimer = str(self._page_cfg.get("NICHE_DISCLAIMER") or "").strip()
        return (
            f"CHANNEL: {name}\n"
            f"ARCHETYPE: {persona.get('archetype') or 'investigative documentary narrator'}\n"
            f"NICHE: {niche}\n"
            f"VOICE: {tone}. Never grandmotherly, never clinical-wellness, never relationship-coach.\n"
            f"MISSION: {mission}\n"
            f"CONTENT PILLARS:\n{pillar_block}\n"
            f"DISCLAIMER: {disclaimer}\n"
            f"{niche_disclaimer}\n"
            "HARD BOUNDARY: Do not use holistic/longevity language, biochemical protocols, "
            "attachment theory, or any other channel's persona. Hedge every theory. "
            "Open from a real recognisable world anchor, then the impossible element."
        ).strip()

    def get_visual_rules(self) -> dict[str, Any]:
        identity = self._dna.get("visual_identity") or {}
        style = str(
            self._page_cfg.get("ILLUSTRATION_STYLE")
            or self._page_cfg.get("ATMOSPHERE_STYLE")
            or identity.get("style")
            or ""
        ).rstrip(" .")
        environments = [str(e) for e in (self._dna.get("environments") or []) if e]
        forbidden = list(identity.get("forbidden_styles") or [])
        negatives = list(self._page_cfg.get("PROMPT_NEGATIVE_TERMS") or [])
        rules: VisualRules = {
            "draw_style": "NATURAL",
            "avatar_mode": "OFF",
            "aspect_ratio": str(self._page_cfg.get("IMAGE_ASPECT_RATIO") or "4:5"),
            "atmosphere_style": str(self._page_cfg.get("ATMOSPHERE_STYLE") or style),
            "illustration_style": style,
            "environments": environments,
            "forbidden_styles": forbidden,
            "negative_terms": negatives,
            "negative_prompt": (
                "illustration, sketch, cartoon, graphite, CGI, 3D render, "
                "front-facing postcard, flat lighting, modern elements, "
                "archway, doorway, portal, window frame, silhouette frame"
            ),
            "prompt_lock": "",
            "enable_sketch": False,
            "camera_default": (
                "Randomised: wide aerial drone, macro artefact close-up, "
                "or 1920s archaeologist over-the-shoulder. Never doorway framing."
            ),
            "depth_layers": (
                "open landscape or artefact-fill. Background — temple silhouette, "
                "starfield, or horizon. No dark foreground frames."
            ),
            "colour_palette": list(identity.get("colour_palette") or []),
            "master_criteria": [
                "Must have dynamic lighting",
                "Must not look like a generic front-facing 3D render",
                "Must match the ancient mystery aesthetic",
            ],
            "lighting_style": (
                "dramatic single-source cinematic light — warm amber torchlight "
                "or cold ethereal moonlight, volumetric shafts, rim lighting, chiaroscuro"
            ),
        }
        return dict(rules)

    def compose_image_prompt(self, subject: str) -> str:
        """Randomised camera opener + photoreal body + mandatory no-doorway ban."""
        style = str(self.get_visual_rules().get("illustration_style") or "").rstrip(" .")
        clean = (subject or "").strip() or "an iconic megalithic monument"
        for term in _FORBIDDEN_CROSS_CHANNEL:
            clean = re.sub(re.escape(term), "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"[ \t]{2,}", " ", clean).strip(" ,.") or "an iconic megalithic monument"
        opener = random.choice(_CAMERA_OPENERS).format(subject=clean)
        body = _PHOTOREAL_BODY.format(subject=clean, doorway_ban=_DOORWAY_BAN)
        style_bit = f"{style}. " if style else ""
        return f"{opener} {style_bit}{body}"

    def get_niche_topics(self) -> list[str]:
        pool = [str(t).strip() for t in (self._page_cfg.get("TOPIC_POOL") or []) if str(t).strip()]
        extra = [t for t in _NICHE_TOPICS if t not in pool]
        return pool + extra

    def topic_is_off_niche(self, topic: str) -> bool:
        lo = (topic or "").strip().lower()
        if not lo:
            return True
        return any(needle in lo for needle in _OFF_NICHE_NEEDLES)

    def pick_niche_topic(self) -> str:
        pool = self.get_niche_topics()
        return random.choice(pool) if pool else "Göbekli Tepe untouched — deliberately buried 10,000 years ago"

    def get_ctas(self) -> list[str]:
        options = [str(c).strip() for c in (self._dna.get("cta_options") or []) if str(c).strip()]
        reel_cta = str(self._page_cfg.get("REEL_CTA_TEXT") or "").strip()
        if reel_cta and reel_cta not in options:
            options.append(reel_cta)
        return options or ["Follow Ancient Knowledge for more hidden mysteries."]

    def get_narrative_angles(self, mode: str = "") -> list[str]:
        """Investigative angles only. Warrior / psychology modes do not leak in."""
        del mode  # this channel has one narrative family
        angles = [str(a).strip() for a in (self._dna.get("narrative_angles") or []) if str(a).strip()]
        return angles or [
            "What if everything we know about this is wrong?",
            "The evidence that mainstream archaeology refuses to acknowledge",
        ]

    def get_rag_context(self, topic: str) -> str:
        """
        Ancient Knowledge retrieval: VisualQA aesthetic DNA + local visual concepts.

        Does not call the PDF research loader (that is Anna's corpus).
        """
        blocks: list[str] = [f"TOPIC: {(topic or '').strip() or '(none)'}"]
        visual_block = self._visualqa_rules_block()
        if visual_block:
            blocks.append(visual_block)
        concept = self._match_visual_concept(topic or "")
        if concept:
            blocks.append(f"MATCHED VISUAL CONCEPT: {concept}")
        identity = self._dna.get("visual_identity") or {}
        if identity:
            blocks.append(
                "VISUAL IDENTITY: "
                f"style={identity.get('style', '')}; "
                f"mood={identity.get('mood', '')}; "
                f"forbidden={', '.join(identity.get('forbidden_styles') or [])}"
            )
        disclaimer = str(self._page_cfg.get("NICHE_DISCLAIMER") or "").strip()
        if disclaimer:
            blocks.append(disclaimer)
        return "\n\n".join(blocks)

    # ------------------------------------------------------------------
    # Private loaders — Schema B + local page_config only
    # ------------------------------------------------------------------

    def _load_dna(self) -> dict[str, Any]:
        if not self._dna_path.is_file():
            return {"page_id": "ancient_knowledge", "persona": {}, "cta_options": []}
        try:
            data = json.loads(self._dna_path.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {"page_id": "ancient_knowledge", "persona": {}}

    @staticmethod
    def _load_page_config() -> dict[str, Any]:
        try:
            from channels_config.ancient_knowledge import page_config as _pc

            return {k: v for k, v in vars(_pc).items() if not k.startswith("_")}
        except Exception:
            return {}

    def _visualqa_rules_block(self) -> str:
        try:
            from quality.VisualQA_Agent.channel_rag import (
                get_channel_rules,
                rules_as_prompt_block,
                seed_default_channels,
            )

            seed_default_channels(force=False)
            rules = get_channel_rules(self.channel_id)
            return "VISUALQA CHANNEL DNA:\n" + rules_as_prompt_block(rules)
        except Exception:
            return ""

    def _match_visual_concept(self, topic: str) -> str:
        lo = (topic or "").lower()
        try:
            from quality.VisualQA_Agent.channel_rag import get_channel_rules, seed_default_channels

            seed_default_channels(force=False)
            concepts = (get_channel_rules(self.channel_id).get("visual_concepts") or {})
        except Exception:
            concepts = {}
        if not isinstance(concepts, dict):
            return ""
        for key, desc in concepts.items():
            if str(key).lower() in lo:
                return f"{key}: {desc}"
        needles = {
            "pyramid": "pyramid",
            "giza": "pyramid",
            "baalbek": "megalith",
            "göbekli": "pre_flood",
            "gobekli": "pre_flood",
            "atlantis": "pre_flood",
            "antikythera": "oopart",
            "vimana": "oopart",
            "yonaguni": "lost_city",
            "dwarka": "lost_city",
        }
        for needle, key in needles.items():
            if needle in lo and key in concepts:
                return f"{key}: {concepts[key]}"
        return ""
