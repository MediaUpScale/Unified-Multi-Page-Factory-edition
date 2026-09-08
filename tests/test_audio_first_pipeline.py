# -*- coding: utf-8 -*-
"""Audio-first chunking, scene prompts, Mei chrome, VisualQA JSON."""
from __future__ import annotations

from pathlib import Path

from agents.media.scene_prompt_generator import (
    cosine_similarity,
    domain_banned_subjects,
    generate_scene_prompts,
    infer_visual_domain,
    sanitize_channel_style,
)
from channels_config.shared import mei_chrome
from core.audio_chunker import AudioChunk, chunk_word_timings
from modules.reel_visual_qa import (
    _strip_json,
    apply_banned_subject_gate,
    resolve_cached_channel_background,
)
from quality.VisualQA_Agent.channel_rag import CHANNEL_DNA_SEED, get_channel_rules


def test_chunk_word_timings_3_to_5_seconds():
    words: list[tuple[str, float, float]] = []
    t = 0.0
    script = (
        "What if the first sightings happened thousands of years ago. "
        "Cave walls in France show disc shaped craft above hunters. "
        "Sumerian tablets describe gods arriving from the sky. "
        "Follow the next clue."
    ).split()
    for w in script:
        words.append((w, t, t + 0.35))
        t += 0.38
    chunks = chunk_word_timings(words)
    assert chunks, "expected at least one chunk"
    assert all(c.duration_s <= 5.6 for c in chunks)
    assert any(c.duration_s >= 2.4 for c in chunks)
    joined = " ".join(c.text for c in chunks)
    assert "sightings" in joined
    assert "tablets" in joined


def test_scene_prompts_anti_slop_no_pyramid_unless_spoken():
    chunks = [
        AudioChunk(0, 0.0, 4.0, "Cave walls show disc shaped craft above hunters"),
        AudioChunk(1, 4.0, 8.0, "Sumerian tablets describe gods arriving from the sky"),
    ]
    scenes = generate_scene_prompts(
        "full script about ancient ufo sightings",
        chunks,
        channel_dna={"lighting_style": "cinematic documentary"},
        channel_id="ancient_knowledge",
        use_llm=False,
    )
    assert len(scenes) == 2
    for scene in scenes:
        prompt = scene["image_generation_prompt"].lower()
        spoken = scene["spoken_text"].lower()
        assert "pyramid" not in prompt or "pyramid" in spoken
        assert "disc" in prompt or "tablet" in prompt or "craft" in prompt
        assert scene["negative_prompt"]


def test_dedupe_rewrites_when_prompts_too_similar():
    a = "photoreal cave painting of a disc craft over hunters"
    b = "photoreal cave painting of a disc craft over hunters wide"
    assert cosine_similarity(a, b) > 0.75
    chunks = [
        AudioChunk(0, 0.0, 3.5, "Cave walls show disc shaped craft"),
        AudioChunk(1, 3.5, 7.0, "Sumerian tablets describe gods from the sky"),
    ]
    scenes = generate_scene_prompts("script", chunks, channel_id="ancient_knowledge", use_llm=False)
    # Second prompt must change camera/entity after spoken text changed
    assert scenes[0]["spoken_text"] != scenes[1]["spoken_text"]
    assert "CAMERA REWRITE" in scenes[1]["image_generation_prompt"] or (
        cosine_similarity(
            scenes[0]["image_generation_prompt"],
            scenes[1]["image_generation_prompt"],
        )
        <= 0.75
        or "tablet" in scenes[1]["image_generation_prompt"].lower()
    )


def test_mei_chrome_is_single_source():
    from channels_config.master_mei import page_config as mei

    assert mei_chrome.SUBTITLE_FONTSIZE == mei.SUBTITLE_FONTSIZE
    assert mei_chrome.SUBTITLE_FILL == tuple(mei.SUBTITLE_FILL)
    assert mei_chrome.SUBTITLE_STROKE_WIDTH == mei.SUBTITLE_STROKE_WIDTH


def test_page_ctx_inherits_mei_subtitle_chrome():
    from channel_loader import load_page_context
    from channels_config.master_mei import page_config as mei

    ctx = load_page_context(
        "ancient_knowledge", avatar_mode="OFF", post_format="IMAGE_BACKGROUND"
    )
    assert ctx.subtitle_fontsize == mei.SUBTITLE_FONTSIZE
    assert ctx.subtitle_fill == tuple(mei.SUBTITLE_FILL)
    assert ctx.subtitle_stroke_width == mei.SUBTITLE_STROKE_WIDTH
    assert ctx.logo_width_px == 300
    assert ctx.logo_y_offset_px == 90


def test_channel_rules_fallback_to_seed():
    rules = get_channel_rules("ancient_knowledge")
    assert rules["forbidden_tokens"]
    assert "ancient_knowledge" in CHANNEL_DNA_SEED


def test_visualqa_json_strips_markdown():
    raw = """Here you go:
```json
{"is_relevant": true, "relevance_score": 0.82, "detected_subjects": ["tablet"], "rejection_reason": null}
```
"""
    data = _strip_json(raw)
    assert data["is_relevant"] is True
    assert data["relevance_score"] == 0.82
    assert data["rejection_reason"] is None


def test_cached_background_writes_placeholder(tmp_path: Path):
    dest = tmp_path / "fb.png"
    out = resolve_cached_channel_background(
        channel="ancient_knowledge", dest=dest, search_dirs=[],
    )
    assert out.is_file()
    assert out.stat().st_size > 100


def test_antikythera_domain_anchors_and_negatives():
    script = (
        "Off Antikythera, Greece, a shipwreck revealed bronze. "
        "It was an ancient analogue computer. Archimedes. Hellenistic gears."
    )
    assert infer_visual_domain(script) == "hellenistic_greece"
    chunks = [AudioChunk(0, 0.0, 4.0, "Inside, intricate gears tracked celestial movements.")]
    scenes = generate_scene_prompts(
        script, chunks, {"topic": "The Antikythera Mechanism"},
        channel_id="ancient_knowledge", use_llm=False,
    )
    prompt = scenes[0]["image_generation_prompt"].lower()
    negative = scenes[0]["negative_prompt"].lower()
    assert "ancient greece" in prompt or "hellenistic" in prompt
    assert "bronze" in prompt or "gear" in prompt or "gears" in prompt
    assert "pyramid" not in prompt
    assert "pyramid" in negative
    assert "pharaoh" in negative
    assert "pyramid" in scenes[0]["banned_subjects"]


def test_sanitize_style_strips_pyramid_when_unspoken():
    style = "Iconic monument (Pyramid, Baalbek megalith) anchoring the frame"
    cleaned = sanitize_channel_style(
        style, "intricate bronze gears", ["pyramid", "pyramids"],
    ).lower()
    assert "pyramid" not in cleaned
    assert "baalbek" in cleaned


def test_hard_reject_pyramid_not_in_spoken_window():
    verdict = apply_banned_subject_gate(
        {
            "is_relevant": True,
            "relevance_score": 0.95,
            "detected_subjects": ["Antikythera Mechanism", "Pyramid", "gears"],
            "rejection_reason": None,
        },
        spoken_text="Inside, intricate gears tracked celestial movements.",
        banned_subjects=domain_banned_subjects(
            "hellenistic_greece",
            channel_id="ancient_knowledge",
            spoken_text="Inside, intricate gears tracked celestial movements.",
        ),
    )
    assert verdict["is_relevant"] is False
    assert verdict["hard_reject"] is True
    assert "pyramid" in (verdict["rejection_reason"] or "").lower()


def test_hard_reject_ignores_unrelated_silhouette_subject():
    verdict = apply_banned_subject_gate(
        {
            "is_relevant": True,
            "relevance_score": 0.95,
            "detected_subjects": ["ancient greek scholar", "silhouette", "clockwork gears"],
            "rejection_reason": None,
        },
        spoken_text="origin, defying known capabilities. Who possessed such",
        banned_subjects=["pyramid", "egyptian", "pharaoh", "desert landscape"],
    )
    assert verdict["is_relevant"] is True
    assert not verdict.get("hard_reject")


def test_hard_reject_skips_when_spoken_names_pyramid():
    verdict = apply_banned_subject_gate(
        {
            "is_relevant": True,
            "relevance_score": 0.9,
            "detected_subjects": ["pyramid", "giza"],
            "rejection_reason": None,
        },
        spoken_text="The Great Pyramid at Giza still hides sealed chambers.",
        banned_subjects=["pyramid", "giza"],
    )
    assert verdict["is_relevant"] is True
    assert not verdict.get("hard_reject")


def test_b2_bucket_resolves_at_call_time(monkeypatch=None):
    import os
    from agents.media.b2_client import resolve_b2_bucket_name

    previous = os.environ.get("B2_BUCKET_NAME")
    try:
        os.environ["B2_BUCKET_NAME"] = "MediaupscaleStorage"
        assert resolve_b2_bucket_name() == "MediaupscaleStorage"
        os.environ["B2_BUCKET_NAME"] = ""
        try:
            resolve_b2_bucket_name()
            raise AssertionError("expected empty-bucket ValueError")
        except ValueError as exc:
            assert "B2_BUCKET_NAME" in str(exc)
    finally:
        if previous is None:
            os.environ.pop("B2_BUCKET_NAME", None)
        else:
            os.environ["B2_BUCKET_NAME"] = previous


def test_egypt_topic_does_not_ban_pyramid():
    script = "The Great Pyramid at Giza and the Sphinx still hide sealed chambers."
    assert infer_visual_domain(script) == "ancient_egypt"
    banned = domain_banned_subjects("ancient_egypt", spoken_text="The Sphinx watches Giza.")
    assert "pyramid" not in banned
    assert "sphinx" not in banned
    chunks = [AudioChunk(0, 0.0, 4.0, "The Great Pyramid at Giza still hides sealed chambers.")]
    scenes = generate_scene_prompts(
        script, chunks, {"topic": "Giza pyramid"},
        channel_id="ancient_knowledge", use_llm=False,
    )
    assert "pyramid" not in scenes[0]["banned_subjects"]


def test_unmatched_topic_has_no_global_landmark_ban():
    banned = domain_banned_subjects("", spoken_text="Cave walls show disc shaped craft")
    assert banned == []
    verdict = apply_banned_subject_gate(
        {
            "is_relevant": True,
            "relevance_score": 0.9,
            "detected_subjects": ["pyramid", "silhouette"],
            "rejection_reason": None,
        },
        spoken_text="Cave walls show disc shaped craft",
        banned_subjects=banned,
    )
    assert verdict["is_relevant"] is True
    assert not verdict.get("hard_reject")


def test_bind_active_page_rebins_output_paths():
    import config as app_config

    previous = app_config.ACTIVE_PAGE
    try:
        app_config.bind_active_page("ancient_knowledge")
        assert app_config.ACTIVE_PAGE == "ancient_knowledge"
        assert app_config.PAGE_OUTPUTS_DIR.name == "ancient_knowledge"
        assert (app_config.PAGE_OUTPUTS_DIR / "clips").exists()
    finally:
        app_config.bind_active_page(previous)
