# -*- coding: utf-8 -*-
"""VisualDirectorAgent — compose / rewrite image prompts and trigger generation."""
from __future__ import annotations

import logging
import re
from pathlib import Path
import threading

from core_engine.agentic_pipeline.agents.base import BaseAgent
from core_engine.agentic_pipeline.criteria import (
    CAMERA_REWRITE_CYCLE,
    NEGATIVE_PROMPT,
    STYLE_ANCHOR,
)
from core_engine.agentic_pipeline.llm import complete_text
from core_engine.agentic_pipeline.state import PipelineState

_LOG = logging.getLogger(__name__)

_REWRITE_SYSTEM = (
    "You are a FLUX prompt engineer for Ancient Knowledge stills. "
    "Rewrite the prompt using ONLY positive concrete nouns. "
    "Keep ultra-realistic cinematic photography. "
    "Change camera angle and lighting when given critic feedback. "
    "Never write 'NO …' lists. Output ONLY the rewritten prompt."
)

_FORBIDDEN_SCENE_RE = re.compile(
    r"\b(archways?|doorways?|portals?|window frames?|"
    r"looking through (?:a |the )?(?:door|window|arch)|"
    r"silhouette frames?|dark foreground frames?)\b",
    re.I,
)

_NO_FRAME_TAIL = (
    "ABSOLUTELY NO archways, NO doorways, NO looking through windows, "
    "NO framing the subject with dark foregrounds."
)


def _sanitize_prompt(prompt: str) -> str:
    """Strip doorway/arch framing language before the image API is billed."""
    body = prompt or ""
    if _NO_FRAME_TAIL in body:
        body = body.replace(_NO_FRAME_TAIL, " ")
    cleaned = _FORBIDDEN_SCENE_RE.sub("open-air panoramic view", body)
    cleaned = " ".join(cleaned.split())
    return f"{cleaned} {_NO_FRAME_TAIL}".strip()


class VisualDirectorAgent(BaseAgent):
    """Owns ``image_prompt`` and appends to ``generated_image_paths``."""

    name = "visual_director"

    def __init__(
        self,
        *,
        output_dir: Path | str | None = None,
        image_adapter: object | None = None,
    ) -> None:
        self._output_dir = Path(output_dir) if output_dir else None
        self._adapter = image_adapter
        self._thread_adapter = threading.local()

    def run(self, state: PipelineState) -> PipelineState:
        """Compose or rewrite the prompt, then generate one image."""
        state.last_node = self.name
        self._bind_cost_tracker(state)
        if state.qa_feedback:
            state.image_prompt = self.rewrite_prompt(state)
        elif not state.image_prompt:
            state.image_prompt = self.compose_prompt(state)

        state.prompt_history.append(state.image_prompt)
        try:
            path = self.generate_image(state)
        except Exception as exc:  # noqa: BLE001
            _LOG.error("VisualDirector generate failed (%s)", exc, exc_info=True)
            state.record_error(self.name, str(exc), attempt=state.retry_count)
            return state

        if path:
            state.generated_image_paths.append(str(path))
            _LOG.info(
                "VisualDirector | #%s attempt=%s → %s",
                state.post_index + 1, state.retry_count + 1, path,
            )
        return state

    def compose_prompt(self, state: PipelineState) -> str:
        subject = (
            f"{state.geo_anchor}: {state.visual_hook or state.theme or state.seed_topic}"
            if state.geo_anchor
            else (state.visual_hook or state.theme or state.seed_topic)
        )
        try:
            from core_engine.interfaces.factory import ChannelFactory

            ch = ChannelFactory.create(state.channel_id)
            prompt = ch.compose_image_prompt(subject)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("Adapter compose_image_prompt failed (%s) — local fallback.", exc)
            cam = CAMERA_REWRITE_CYCLE[state.post_index % len(CAMERA_REWRITE_CYCLE)]
            prompt = (
                f"{cam.split(':', 1)[-1].strip()} {STYLE_ANCHOR} "
                f"SUBJECT: {subject}. "
                "ABSOLUTELY NO archways, NO doorways, NO looking through windows, "
                "NO framing the subject with dark foregrounds."
            )
        if "ABSOLUTELY NO archways" not in prompt:
            prompt = f"{prompt} {_NO_FRAME_TAIL}"
        return _sanitize_prompt(prompt)

    def rewrite_prompt(self, state: PipelineState) -> str:
        """First retry is a free mechanical camera shift; later retries may call the LLM."""
        cam = CAMERA_REWRITE_CYCLE[state.retry_count % len(CAMERA_REWRITE_CYCLE)]
        previous = state.image_prompt or self.compose_prompt(state)
        if state.retry_count <= 1:
            _LOG.info(
                "VisualDirector mechanical rewrite | slot=%s attempt=%s | %s",
                state.post_index + 1, state.retry_count + 1, cam[:80],
            )
            return _sanitize_prompt(f"{previous} {cam}")

        instruction = (
            f"PREVIOUS PROMPT:\n{previous}\n\n"
            f"CRITIC FEEDBACK (must fix):\n{state.qa_feedback}\n\n"
            f"MANDATORY CAMERA SHIFT:\n{cam}\n\n"
            f"THEME: {state.theme}\nGEO: {state.geo_anchor}\n"
            f"VISUAL HOOK: {state.visual_hook}\n\n"
            "Rewrite as one concise English prompt (<= 140 words). "
            "Keep the same monument. Change angle, lighting, or depth layers. "
            "Positive nouns for the scene. End with: "
            f"{_NO_FRAME_TAIL}"
        )
        try:
            rewritten = complete_text(instruction, system=_REWRITE_SYSTEM)
            rewritten = " ".join((rewritten or "").split())
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("Prompt rewrite LLM failed (%s) — mechanical camera shift.", exc)
            state.record_error(self.name, f"rewrite: {exc}", attempt=state.retry_count)
            rewritten = ""
        if not rewritten:
            rewritten = f"{previous} {cam}"
        return _sanitize_prompt(rewritten)

    def generate_image(self, state: PipelineState) -> Path | None:
        adapter = self._adapter or self._lazy_adapter()
        out_dir = Path(state.output_dir) if state.output_dir else self._output_dir
        if out_dir is None:
            import config as app_config

            out_dir = Path(app_config.ASSETS_DIR) / "agentic"
        out_dir = Path(out_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        state.image_prompt = _sanitize_prompt(state.image_prompt or "")
        if not state.image_prompt:
            _LOG.error("VisualDirector refused empty prompt — skipping billed generate.")
            return None
        stem = f"ak_p{state.post_index + 1:02d}_r{state.retry_count + 1:02d}"
        generate = getattr(adapter, "generate")
        path = generate(
            state.image_prompt,
            output_stem=stem,
            output_directory=out_dir,
            avatar_mode="OFF",
            aspect_ratio="4:5",
            negative_prompt=NEGATIVE_PROMPT,
        )
        if path:
            self._track_image(state, count=1)
        return Path(path) if path else None

    def _lazy_adapter(self):
        cached = getattr(self._thread_adapter, "adapter", None)
        if cached is not None:
            return cached
        from avatar_engine.providers.image_provider import get_image_adapter

        cached = get_image_adapter(page_cost_tier="nano", tier="cheap")
        self._thread_adapter.adapter = cached
        return cached
