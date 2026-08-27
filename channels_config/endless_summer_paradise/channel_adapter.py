# -*- coding: utf-8 -*-
"""
EndlessSummerParadiseAdapter — isolated BaseChannelConfig for library ingest.

Reads channels_config/endless_summer_paradise/master_dna.json and page_config.py.
Does not import other pages' DNA. Generation engines should not invent travel
claims or resort affiliations for this channel.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.interfaces.channel import BaseChannelConfig, VisualRules

_CHANNEL_DIR = Path(__file__).resolve().parent
_DNA_PATH = _CHANNEL_DIR / "master_dna.json"


class EndlessSummerParadiseAdapter(BaseChannelConfig):
    """Tropical aesthetic adapter: persona / CTAs / environments / SEO tone."""

    def __init__(self, dna_path: Path | None = None) -> None:
        self._dna_path = Path(dna_path) if dna_path else _DNA_PATH
        self._dna = self._load_dna()
        self._page_cfg = self._load_page_config()

    @property
    def channel_id(self) -> str:
        return str(self._dna.get("page_id") or "endless_summer_paradise")

    @property
    def display_name(self) -> str:
        persona = self._dna.get("persona") or {}
        return str(
            persona.get("name")
            or self._dna.get("display_name")
            or self._page_cfg.get("PAGE_DISPLAY_NAME")
            or "Endless Summer Paradise"
        )

    def get_system_prompt(self) -> str:
        persona = self._dna.get("persona") or {}
        name = self.display_name
        mission = str(persona.get("core_mission") or "").strip()
        tone = str(
            persona.get("voice_tone") or "tropical, aesthetic, relaxing, atmospheric"
        ).strip()
        disclaimer = str(persona.get("disclaimer") or "").strip()
        pillars = persona.get("content_pillars") or []
        pillar_block = "\n".join(f"  - {p}" for p in pillars) or "  - Tropical summer scenery"
        niche = str(self._dna.get("niche") or self._page_cfg.get("CONTENT_NICHE") or "").strip()
        niche_disclaimer = str(self._page_cfg.get("NICHE_DISCLAIMER") or "").strip()
        return (
            f"CHANNEL: {name}\n"
            f"ARCHETYPE: {persona.get('archetype') or 'tropical atmosphere curator'}\n"
            f"NICHE: {niche}\n"
            f"VOICE: {tone}. Never finance, never horror, never wellness-protocol.\n"
            f"MISSION: {mission}\n"
            f"CONTENT PILLARS:\n{pillar_block}\n"
            f"DISCLAIMER: {disclaimer}\n"
            f"{niche_disclaimer}\n"
            "HARD BOUNDARY: Titles follow "
            "'[Hook] — 1950s Surreal Jell-O Waterpark | Brand/Sub-theme'. "
            "US English only (color, center, visualizing). "
            "Descriptions: keyword-rich top 3 lines, dense mid context, then "
            "playlist + subscribe CTA + one plain-text disclaimer — no emojis "
            "or yellow warning symbols."
        ).strip()

    def get_visual_rules(self) -> dict[str, Any]:
        identity = self._dna.get("visual_identity") or {}
        style = str(
            self._page_cfg.get("ILLUSTRATION_STYLE")
            or self._page_cfg.get("ATMOSPHERE_STYLE")
            or identity.get("style")
            or "tropical aesthetic, soft cinematic summer light"
        ).rstrip(" .")
        environments = [str(e) for e in (self._dna.get("environments") or []) if e]
        if not environments:
            for item in self._dna.get("Environments") or []:
                if isinstance(item, dict):
                    name = str(item.get("name") or "").strip()
                    desc = str(item.get("desc") or "").strip()
                    environments.append(f"{name}: {desc}".strip(": "))
                elif item:
                    environments.append(str(item))
        forbidden = list(identity.get("forbidden_styles") or [])
        negatives = list(self._page_cfg.get("PROMPT_NEGATIVE_TERMS") or [])
        rules: VisualRules = {
            "draw_style": "NATURAL",
            "avatar_mode": "OFF",
            "aspect_ratio": str(self._page_cfg.get("IMAGE_ASPECT_RATIO") or "16:9"),
            "atmosphere_style": str(self._page_cfg.get("ATMOSPHERE_STYLE") or style),
            "illustration_style": style,
            "environments": environments,
            "forbidden_styles": forbidden,
            "negative_terms": negatives,
            "negative_prompt": (
                "horror, cyberpunk, graphite sketch, finance charts, "
                "hard-sell travel ad, resort logo watermark"
            ),
            "prompt_lock": "",
            "enable_sketch": False,
            "camera_default": "Wide aerial or slow push through tropical scenery.",
            "depth_layers": (
                "foreground — soft palms / translucent architecture. "
                "Background — lagoon, ocean horizon, golden haze."
            ),
            "colour_palette": list(identity.get("colour_palette") or []),
            "master_criteria": [
                "Must feel tropical and calming",
                "Must read as high-visual summer aesthetic",
                "Must avoid hard-sell travel advertising language",
            ],
            "lighting_style": "soft cinematic summer light — golden hour or clean noon translucency",
        }
        return dict(rules)

    def get_ctas(self) -> list[str]:
        options = [str(c).strip() for c in (self._dna.get("cta_options") or []) if str(c).strip()]
        return options or [
            "Subscribe to Endless Summer Paradise for more tropical scenery.",
        ]

    def get_narrative_angles(self, mode: str = "") -> list[str]:
        del mode
        angles = [
            str(a).strip()
            for a in (self._dna.get("narrative_angles") or [])
            if str(a).strip()
        ]
        if angles:
            return angles
        out: list[str] = []
        for item in self._dna.get("Narrative_Angles") or []:
            if isinstance(item, dict):
                code = str(item.get("code") or "").strip()
                desc = str(item.get("description") or "").strip()
                line = f"{code}: {desc}".strip(": ")
                if line:
                    out.append(line)
        return out or [
            "Soft tropical light that feels like an endless afternoon",
            "Beach and lagoon calm for relaxing summer search intent",
        ]

    def get_rag_context(self, topic: str) -> str:
        """No PDF corpus — return SEO niche block + topic for metadata drafting."""
        niche = str(self._page_cfg.get("NICHE_DISCLAIMER") or "").strip()
        tags = ", ".join(str(t) for t in (self._page_cfg.get("YOUTUBE_DEFAULT_TAGS") or [])[:12])
        return (
            f"TOPIC: {(topic or '').strip() or 'tropical summer scenery'}\n"
            f"CHANNEL_TAGS: {tags}\n"
            f"{niche}"
        ).strip()

    def _load_dna(self) -> dict[str, Any]:
        if not self._dna_path.is_file():
            return {}
        try:
            data = json.loads(self._dna_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _load_page_config(self) -> dict[str, Any]:
        path = _CHANNEL_DIR / "page_config.py"
        if not path.is_file():
            return {}
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "endless_summer_paradise_page_config", path
        )
        if spec is None or spec.loader is None:
            return {}
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return {
            k: getattr(mod, k)
            for k in dir(mod)
            if k.isupper() and not k.startswith("_")
        }
