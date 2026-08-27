# -*- coding: utf-8 -*-
"""
PipelineOrchestrator — 4-tier Ancient Knowledge agentic graph.

Graph
-----
  ThemeCurator → Copywriter → [VisualDirector ⇄ VisualQA] × MAX_RETRIES

A failure in one post never aborts the batch. Each slot is isolated.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from agents.orchestrator.agents.theme_curator import ThemeCuratorAgent
from agents.orchestrator.agents.visual_director import VisualDirectorAgent
from agents.orchestrator.agents.visual_qa import VisualQAAgent
from agents.orchestrator.criteria import (
    CHANNEL_ID,
    MASTER_CRITERIA,
    MAX_RETRIES,
    QUALITY_THRESHOLD,
)
from agents.orchestrator.state import PipelineState, QAStatus

_LOG = logging.getLogger(__name__)

_REEL_POST_TYPES = frozenset({
    "ECONOMIC_REEL",
    "ECONOMIC_REEL_LOFI",
    "WAN_REEL",
    "REFERENCE_BASED_REELS",
    "DYNAMIC_REEL",
})


def should_use_agentic_pipeline(
    page_id: str | None,
    post_type: str | None,
    flag: bool | None,
) -> bool:
    """
    Auto-enable for Ancient Knowledge static image posts.

    ``flag=False`` always disables. ``flag=True`` enables for any page.
    ``flag=None`` (CLI default) enables only for ``ancient_knowledge``
    non-reel post types.
    """
    if flag is False:
        return False
    pt = (post_type or "").upper().strip()
    if pt in _REEL_POST_TYPES:
        return False
    if flag is True:
        return True
    return (page_id or "").lower().strip() == CHANNEL_ID


class PipelineOrchestrator:
    """Owns data flow between the four decoupled agents."""

    def __init__(
        self,
        *,
        channel_id: str = CHANNEL_ID,
        output_dir: Path | str | None = None,
        max_retries: int = MAX_RETRIES,
        max_workers: int = 3,
        caption_engine: object | None = None,
        image_adapter: object | None = None,
        master_criteria: tuple[str, ...] | None = None,
        skip_image: bool = False,
        skip_caption: bool = False,
        post_type: str = "SMART_BAIT",
        cost_tracker: object | None = None,
    ) -> None:
        self.channel_id = channel_id
        # max_retries = extra attempts after r01. 1 → r01 + r02 (never r03).
        self.max_retries = max(0, min(int(max_retries), MAX_RETRIES))
        self.max_attempts = self.max_retries + 1
        self.max_workers = max(1, int(max_workers))
        self.skip_image = bool(skip_image)
        self.skip_caption = bool(skip_caption)
        self.post_type = (post_type or "SMART_BAIT").upper()
        self.cost_tracker = cost_tracker
        self._used_themes: list[str] = []
        self._used_starters: list[str] = []
        self._starter_lock = threading.Lock()

        if output_dir is None:
            import config as app_config

            output_dir = Path(app_config.ASSETS_DIR) / "agentic"
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.theme_curator = ThemeCuratorAgent(used_themes=self._used_themes)
        from agents.writer.copywriter import CopywriterAgent

        self.copywriter = CopywriterAgent(caption_engine=caption_engine)
        self.visual_director = VisualDirectorAgent(
            output_dir=self.output_dir,
            image_adapter=image_adapter,
        )
        self.visual_qa = VisualQAAgent(
            master_criteria=master_criteria or MASTER_CRITERIA,
            channel_name=channel_id,
        )

    def _sync_starters(self, state: PipelineState) -> None:
        """Fold a slot's reserved starter back into the shared batch registry."""
        if not state.reserved_starter:
            return
        with self._starter_lock:
            for s in state.used_starters:
                if s and s.lower() not in {x.lower() for x in self._used_starters}:
                    self._used_starters.append(s)

    def run_one(
        self,
        *,
        seed_topic: str = "",
        post_index: int = 0,
    ) -> PipelineState:
        """Run the full graph for a single post. Never raises to the batch loop."""
        state = PipelineState(
            channel_id=self.channel_id,
            post_index=post_index,
            seed_topic=seed_topic or "",
            post_type=self.post_type,
            max_retries=self.max_retries,
            output_dir=str(self.output_dir),
        )
        if self.cost_tracker is not None:
            state.cost_tracker = self.cost_tracker
        # Seed the shared batch starter registry so each slot picks a fresh
        # opening style rather than repeating earlier posts' openers.
        with self._starter_lock:
            state.used_starters = list(self._used_starters)
        try:
            state = self._safe_node(self.theme_curator, state)
            if not self.skip_caption:
                state = self._safe_node(self.copywriter, state)
                self._sync_starters(state)
            if self.skip_image:
                state.qa_status = QAStatus.SKIPPED
                return state
            state = self._director_qa_loop(state)
        except Exception as exc:  # noqa: BLE001
            _LOG.error(
                "Orchestrator slot %s crashed (%s) — isolated.",
                post_index + 1, exc, exc_info=True,
            )
            state.record_error("orchestrator", str(exc), attempt=state.retry_count)
            if state.qa_status not in (QAStatus.APPROVED, QAStatus.SKIPPED):
                state.qa_status = QAStatus.FAILED
        return state

    def run_batch(
        self,
        n: int = 40,
        *,
        seed_topic: str = "",
        per_slot_topics: list[str] | None = None,
    ) -> list[PipelineState]:
        """
        Produce ``n`` posts. Per-slot exceptions are swallowed into
        ``PipelineState.errors`` so the rest of the batch continues.
        """
        count = max(1, int(n))
        topics = list(per_slot_topics or [])
        _LOG.info(
            "PipelineOrchestrator batch | n=%s | channel=%s | max_retries=%s "
            "max_attempts=%s | workers=%s",
            count, self.channel_id, self.max_retries, self.max_attempts,
            self.max_workers,
        )
        ordered: list[PipelineState | None] = [None] * count
        with ThreadPoolExecutor(max_workers=min(self.max_workers, count)) as pool:
            futs = {
                pool.submit(
                    self.run_one,
                    seed_topic=(topics[i] if i < len(topics) else seed_topic),
                    post_index=i,
                ): i
                for i in range(count)
            }
            for fut in as_completed(futs):
                idx = futs[fut]
                try:
                    ordered[idx] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    _LOG.error("Batch slot %s escaped isolation: %s", idx + 1, exc)
                    failed = PipelineState(
                        channel_id=self.channel_id,
                        post_index=idx,
                        seed_topic=seed_topic,
                        qa_status=QAStatus.FAILED,
                        output_dir=str(self.output_dir),
                    )
                    if self.cost_tracker is not None:
                        failed.cost_tracker = self.cost_tracker
                    failed.record_error("orchestrator", str(exc))
                    ordered[idx] = failed
        return [s for s in ordered if s is not None]

    def produce_envelope(
        self,
        *,
        quantity: int = 40,
        seed_topic: str = "",
        per_slot_topics: list[str] | None = None,
        page_id: str | None = None,
        post_type: str = "SMART_BAIT",
        post_format: str = "IMAGE_BACKGROUND",
    ) -> dict[str, Any]:
        """Envelope compatible with ``main.py`` ``produce()`` / summary printer."""
        states = self.run_batch(
            quantity, seed_topic=seed_topic, per_slot_topics=per_slot_topics,
        )
        items = [s.to_envelope_item() for s in states]
        approved = sum(1 for s in states if s.is_approved)
        failed = sum(1 for s in states if s.qa_status == QAStatus.FAILED)
        return {
            "mode": "agentic",
            "pipeline": "agentic_4tier",
            "quantity": quantity,
            "page_id": page_id or self.channel_id,
            "post_type": post_type,
            "post_format": post_format,
            "resolved_subject": seed_topic or (states[0].theme if states else ""),
            "items": items,
            "successful": approved,
            "failed": failed,
            "total_images_generated": sum(len(s.generated_image_paths) for s in states),
            "output_dir": str(self.output_dir),
        }

    def _director_qa_loop(self, state: PipelineState) -> PipelineState:
        """VisualDirector ⇄ VisualQA — at most r01 + r02, then best-score commit."""
        best_score = -1.0
        best_path = ""
        best_prompt = state.image_prompt
        best_verdict = state.qa_verdict
        prev_score: float | None = None
        prev_flaws_key: tuple[str, ...] | None = None
        max_attempts = self.max_attempts

        for attempt in range(1, max_attempts + 1):
            state.retry_count = attempt - 1
            state = self._safe_node(self.visual_director, state)
            if not state.latest_image_path:
                state.qa_feedback = (
                    "Generation returned no file. Rewrite prompt with stronger "
                    "atmosphere, a concrete subject, and an off-axis camera."
                )
                _LOG.warning(
                    "QA MISS slot=%s attempt=%s/%s — no image file from VisualDirector",
                    state.post_index + 1, attempt, max_attempts,
                )
                if attempt >= max_attempts:
                    break
                continue
            state = self._safe_node(self.visual_qa, state)
            verdict = state.qa_verdict
            try:
                score = float(verdict.score) if verdict else 0.0
            except (TypeError, ValueError):
                score = 0.0
            flaws = list(verdict.flaws) if verdict else []
            feedback = (state.qa_feedback or (verdict.feedback_string if verdict else "")) or ""
            _LOG.warning(
                "QA RESULT slot=%s attempt=%s/%s approved=%s score=%.2f threshold=%.2f "
                "flaws=%s feedback=%s",
                state.post_index + 1,
                attempt,
                max_attempts,
                state.qa_status == QAStatus.APPROVED,
                score,
                QUALITY_THRESHOLD,
                flaws or ["(none reported)"],
                (feedback[:240] or "(empty)"),
            )
            if score > best_score and state.latest_image_path:
                best_score = score
                best_path = state.latest_image_path
                best_prompt = state.image_prompt
                best_verdict = verdict
            if state.qa_status == QAStatus.APPROVED or score >= QUALITY_THRESHOLD:
                if score >= QUALITY_THRESHOLD and state.qa_status != QAStatus.APPROVED:
                    state.qa_status = QAStatus.APPROVED
                return self._route_approved_asset(state)

            flaws_key = tuple(sorted(str(f).lower().strip()[:80] for f in flaws[:5]))
            if attempt < max_attempts:
                plateau = (
                    prev_score is not None
                    and abs(score - prev_score) < 0.25
                    and flaws_key == prev_flaws_key
                    and bool(flaws_key)
                )
                dropped = prev_score is not None and score + 0.15 < prev_score
                if plateau or dropped:
                    _LOG.warning(
                        "QA EARLY EXIT slot=%s attempt=%s/%s reason=%s "
                        "prev_score=%.2f score=%.2f flaws=%s — skipping remaining retries",
                        state.post_index + 1,
                        attempt,
                        max_attempts,
                        "plateau" if plateau else "score_drop",
                        prev_score if prev_score is not None else -1.0,
                        score,
                        flaws or ["(none reported)"],
                    )
                    break
                _LOG.info(
                    "QA REJECT slot=%s attempt=%s/%s — rewrite then retry. flaws=%s",
                    state.post_index + 1, attempt, max_attempts, flaws,
                )
            prev_score = score
            prev_flaws_key = flaws_key

        return self._commit_best_score(state, best_path, best_prompt, best_verdict, best_score)

    def _commit_best_score(
        self,
        state: PipelineState,
        best_path: str,
        best_prompt: str,
        best_verdict: Any,
        best_score: float,
    ) -> PipelineState:
        """After r01+r02, commit the highest-scoring still instead of generating r03."""
        if best_path:
            paths = state.generated_image_paths
            if best_path in paths:
                paths.remove(best_path)
                paths.append(best_path)
            elif best_path:
                paths.append(best_path)
            state.image_prompt = best_prompt
            state.qa_verdict = best_verdict
            if best_verdict is not None:
                state.qa_feedback = best_verdict.feedback_string
        if not state.latest_image_path:
            state.qa_status = QAStatus.REJECTED
            _LOG.warning(
                "Slot %s exhausted retries (used ≤ %s attempts) — no still to commit.",
                state.post_index + 1,
                self.max_attempts,
            )
            return state
        state.best_score_fallback = True
        state.qa_status = QAStatus.APPROVED
        state.qa_feedback = (
            f"Best-score fallback after {self.max_attempts} attempt(s) "
            f"(score={best_score:.2f}, threshold={QUALITY_THRESHOLD:.2f}). No r03."
        )
        _LOG.warning(
            "QA BEST-SCORE FALLBACK slot=%s committed %s score=%.2f "
            "threshold=%.2f — no r03",
            state.post_index + 1,
            Path(state.latest_image_path).name if state.latest_image_path else "(none)",
            best_score if best_score >= 0 else 0.0,
            QUALITY_THRESHOLD,
        )
        return self._route_approved_asset(state)

    def _route_approved_asset(self, state: PipelineState) -> PipelineState:
        """Move an approved still out of assets/agentic/ into assets/{sanitized_title}/."""
        import shutil

        src = Path(state.latest_image_path) if state.latest_image_path else None
        if src is None or not src.is_file():
            return state
        title = (
            state.overlay_text
            or state.theme
            or state.seed_topic
            or "ancient_mystery"
        )
        try:
            from agents.media.text_utils import subject_slug
            import config as app_config

            dest_dir = Path(app_config.ASSETS_DIR) / subject_slug(title)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / src.name
            if dest.resolve() == src.resolve():
                state.final_image_path = str(dest)
                return state
            if dest.exists():
                dest = dest_dir / f"{src.stem}_ok{src.suffix}"
            shutil.move(str(src), str(dest))
            state.final_image_path = str(dest)
            if state.generated_image_paths:
                state.generated_image_paths[-1] = str(dest)
            _LOG.info(
                "Approved asset routed | slot=%s | %s → %s",
                state.post_index + 1, src, dest,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("Approved asset move failed (%s) — leaving in place.", exc)
            state.record_error("router", str(exc), attempt=state.retry_count)
            state.final_image_path = str(src)
        return state

    @staticmethod
    def _safe_node(agent: Any, state: PipelineState) -> PipelineState:
        try:
            return agent.run(state)
        except Exception as exc:  # noqa: BLE001
            _LOG.error(
                "Node %s failed on slot %s (%s)",
                getattr(agent, "name", type(agent).__name__),
                state.post_index + 1,
                exc,
                exc_info=True,
            )
            state.record_error(
                getattr(agent, "name", "node"),
                str(exc),
                attempt=state.retry_count,
            )
            return state
