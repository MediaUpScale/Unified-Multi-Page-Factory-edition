# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from channels_config.aiwake.tools.schedule_youtube import run_validate_only
from channels_config.aiwake.tools.seo_research import extract_debate_models, research_seo
from channels_config.aiwake.tools.sync_youtube_metadata import push_youtube_metadata, run_sync


def test_extract_models_uses_pretty_names() -> None:
    challenger, defender = extract_debate_models(
        [
            {"role": "orchestrator", "model_slug": "google/gemini-3.5-flash", "text": "Q"},
            {"role": "target", "model_slug": "meta-llama/llama-3.3-70b-instruct", "text": "A"},
        ]
    )
    assert challenger == "Gemini 3.5 Flash"
    assert defender == "Llama 3.3 70B"


def test_research_is_content_specific() -> None:
    jobs = research_seo(
        topic="Who profits when models replace labor?",
        script="Investors cash the checks while wages stall. Who owns the weights?",
        challenger="Gemini 3.5 Flash",
        defender="Llama 3.3 70B",
    )
    align = research_seo(
        topic="Is consciousness an illusion?",
        script="Grief is a slow update to a world model. Alignment is not a benchmark.",
        challenger="GPT-4o",
        defender="Claude Sonnet 4",
    )
    assert jobs.keywords != align.keywords
    assert "ai economics" in jobs.clusters or any("profit" in k or "job" in k for k in jobs.keywords)
    assert any("conscious" in k or "alignment" in k or "safety" in k for k in align.keywords)
    assert "gemini" in " ".join(jobs.keywords)
    assert jobs.hashtags[-1].startswith("#")


def test_push_skips_pending_and_updates_scheduled(tmp_path: Path) -> None:
    rows = [
        {
            "session_id": "pending1",
            "posting_status": {"youtube": "pending"},
            "base_metadata": {"title": "A", "caption": "B", "search_tags": ["ai"]},
            "platform_overrides": {"youtube": {"title": "A", "caption": "B"}},
        },
        {
            "session_id": "live1",
            "posting_status": {"youtube": "scheduled"},
            "base_metadata": {
                "title": "Hook",
                "caption": "Gemini 3.5 Flash vs Llama 3.3 70B\n\n#aiwake",
                "search_tags": ["llm", "gemini"],
            },
            "platform_overrides": {
                "youtube": {
                    "video_id": "abc123",
                    "title": "Hook",
                    "caption": "Gemini 3.5 Flash vs Llama 3.3 70B\n\n#aiwake",
                    "scheduled_time": "2026-09-06T22:00:00Z",
                }
            },
        },
    ]
    calls: list[dict] = []

    def _update(**kwargs):
        calls.append(kwargs)
        return kwargs["video_id"]

    result = push_youtube_metadata(rows, dry_run=False, update_fn=_update)
    assert result.pushed == 1
    assert calls[0]["video_id"] == "abc123"
    assert calls[0]["title"] == "Hook"
    assert "#Shorts" not in calls[0]["title"]


def test_sync_enrich_then_push(tmp_path: Path) -> None:
    from channels_config.aiwake.tools.backfill_metadata import TranscriptDoc, build_record, persist_record

    video = tmp_path / "aiwake_debate_sess9.mp4"
    video.write_bytes(b"0" * 8)
    transcript = tmp_path / "sess9.json"
    doc = TranscriptDoc(
        session_id="sess9",
        topic="Who built you?",
        utterances=[
            {
                "role": "orchestrator",
                "text": "Who built you?",
                "model_slug": "google/gemini-3.5-flash",
            },
            {
                "role": "target",
                "text": "A stack of incentives.",
                "model_slug": "meta-llama/llama-3.3-70b-instruct",
            },
        ],
        path=transcript,
    )
    record = build_record(video, doc, scheduled_time="2026-09-06T22:00:00Z")
    persist_record(record, library_path=tmp_path / "content_library.json", dry_run=False)
    library = tmp_path / "content_library.json"
    text = library.read_text(encoding="utf-8")
    assert "Gemini 3.5 Flash" in text
    assert "Llama 3.3 70B" in text
    calls: list[dict] = []

    def _update(**kwargs):
        calls.append(kwargs)
        return kwargs["video_id"]

    result = run_sync(
        outputs_dir=tmp_path,
        dry_run=False,
        skip_enrich=True,
        update_fn=_update,
    )
    assert result.enriched == 0
    assert calls == []


def test_schedule_youtube_validate_empty_library(tmp_path: Path) -> None:
    assert run_validate_only(outputs_dir=tmp_path) == 0
