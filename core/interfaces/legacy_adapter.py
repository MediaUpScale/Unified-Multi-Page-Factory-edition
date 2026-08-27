# -*- coding: utf-8 -*-
"""
LegacyChannelAdapter — wraps existing persona_dna.py + page_config.py.

Used for every channel that is not yet isolated behind its own adapter.
Does not change Anna / Mei / Wonder Feed / LOFI behaviour.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any

from core.interfaces.channel import BaseChannelConfig, VisualRules


class LegacyChannelAdapter(BaseChannelConfig):
    """Thin wrapper around the current shared DNA loaders."""

    def __init__(self, channel_id: str | None = None) -> None:
        self._channel_id = (
            (channel_id or os.environ.get("ACTIVE_PAGE") or "anna_protocol")
            .strip()
            .lower()
        )
        self._page_cfg = self._load_page_config(self._channel_id)

    @property
    def channel_id(self) -> str:
        return self._channel_id

    @property
    def display_name(self) -> str:
        from agents.media.persona_dna import ANNA_DISPLAY_NAME

        return str(
            self._page_cfg.get("PAGE_DISPLAY_NAME")
            or ANNA_DISPLAY_NAME
            or self._channel_id.replace("_", " ").title()
        )

    def get_system_prompt(self) -> str:
        from agents.media.persona_dna import persona_context_block

        return persona_context_block()

    def get_visual_rules(self) -> dict[str, Any]:
        from agents.media.persona_dna import ENVIRONMENTS

        envs: list[str] = []
        for item in ENVIRONMENTS or []:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                desc = str(item.get("desc") or "").strip()
                envs.append(f"{name}: {desc}".strip(": "))
            elif item:
                envs.append(str(item))
        rules: VisualRules = {
            "draw_style": "SKETCH" if self._page_cfg.get("ENABLE_SKETCH_STYLE") else "NATURAL",
            "avatar_mode": str(self._page_cfg.get("DEFAULT_AVATAR_MODE") or "ON"),
            "aspect_ratio": str(self._page_cfg.get("IMAGE_ASPECT_RATIO") or "4:5"),
            "atmosphere_style": str(self._page_cfg.get("ATMOSPHERE_STYLE") or ""),
            "illustration_style": str(self._page_cfg.get("ILLUSTRATION_STYLE") or ""),
            "environments": envs,
            "forbidden_styles": [],
            "negative_terms": list(self._page_cfg.get("PROMPT_NEGATIVE_TERMS") or []),
            "negative_prompt": "",
            "prompt_lock": "",
            "enable_sketch": bool(self._page_cfg.get("ENABLE_SKETCH_STYLE")),
            "camera_default": "",
            "depth_layers": "",
            "colour_palette": [],
            "master_criteria": [],
            "lighting_style": "",
        }
        return dict(rules)

    def get_ctas(self) -> list[str]:
        from agents.media.persona_dna import CTA_KEYWORDS

        keywords = [str(k) for k in (CTA_KEYWORDS or []) if str(k).strip()]
        reel_cta = str(self._page_cfg.get("REEL_CTA_TEXT") or "").strip()
        if reel_cta and reel_cta not in keywords:
            keywords.append(reel_cta)
        return keywords or ["follow"]

    def get_narrative_angles(self, mode: str = "") -> list[str]:
        from agents.media.persona_dna import NARRATIVE_ANGLES

        del mode
        out: list[str] = []
        for item in NARRATIVE_ANGLES or []:
            if isinstance(item, dict):
                code = str(item.get("code") or "").strip()
                desc = str(item.get("description") or "").strip()
                line = f"{code}: {desc}".strip(": ")
                if line:
                    out.append(line)
            elif item:
                out.append(str(item))
        return out

    def get_rag_context(self, topic: str) -> str:
        """Anna-shaped pages retrieve from the PDF research corpus."""
        blocks = [f"TOPIC: {(topic or '').strip() or '(none)'}"]
        try:
            import config as app_config
            from agents.rag.pdf_loader import (
                corpus_to_prompt_context,
                load_digital_product_corpus,
            )

            pdf_dir = Path(getattr(app_config, "DIGITAL_PRODUCTS_PATH", "") or "")
            chunk = int(getattr(app_config, "PDF_CHUNK_CHAR_LIMIT", 80_000) or 80_000)
            bundle = load_digital_product_corpus(pdf_dir, chunk_char_limit=chunk) if pdf_dir else {}
            context = corpus_to_prompt_context(bundle) if bundle else ""
            if context:
                blocks.append(context[:12_000])
        except Exception:
            pass
        return "\n\n".join(blocks)

    @staticmethod
    def _load_page_config(page_id: str) -> dict[str, Any]:
        try:
            mod = importlib.import_module(f"channels_config.{page_id}.page_config")
            return {k: v for k, v in vars(mod).items() if not k.startswith("_")}
        except Exception:
            return {}
