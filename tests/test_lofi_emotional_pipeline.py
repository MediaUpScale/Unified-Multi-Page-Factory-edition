from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from agents.mcp.text_model import TextResult, _complete_gemini_flash
from agents.writer.freeform_writer import (
    ScriptDraft,
    _SYSTEM,
    _normalize_total_word_budget,
    _output_contract,
    system_prompt_for,
)
from agents.writer.script_brain import BrainResult, draft_to_script
from agents.writer.writer_brief import WriterBrief
from core.economic_reel_lofi.niche_presets import (
    PARENTING,
    PARENTING_LOCATION_ANCHOR,
    RELATIONSHIP,
    RELATIONSHIP_LOCATION_ANCHOR,
    build_flux_prompt,
    get_niche_preset,
    inject_prompt_fields,
    wrap_visual_prompt,
)
from core.economic_reel_lofi import config as lofi_cfg
from core.economic_reel_lofi.image_gen import (
    enforce_full_bleed,
    generate_scene_image_gemini,
)
from core.economic_reel_lofi.pipeline import (
    _assemble_stage3_prompts,
    _gate2_review_rows,
    _generate_validated_script,
    _load_locked_script,
    _print_script_report,
    _produce_one,
    _purge_stale_temp_voice_files,
)
from core.economic_reel_lofi.style_modules.riso_retro_flat_v4 import STYLE as RISO_STYLE
from core.economic_reel_lofi.visual_concept import _fallback_concept
from core.economic_reel_lofi.visual_identity import assign_palette_arc
from core.economic_reel_lofi.voiceover import ensure_script_voiceover


def test_emotional_brief_prioritizes_depth_without_literal_props() -> None:
    brief = WriterBrief.from_emotional(theme="detachment", subtheme="quiet_boundary")
    block = brief.assignment_block()
    assert brief.mode == "emotional"
    assert "micro-philosophical" in block
    assert "do not force an object" in block
    assert "therapy slogans" in block
    assert "Doorway Motif" in block or "doorway silhouette" in block
    assert "strictly 5–7" in block
    assert "fewer than 45 characters" in block


def test_fast_prompt_embeds_all_six_golden_examples_and_candidate_schema() -> None:
    for number in range(1, 7):
        assert f"EXAMPLE {number}" in _SYSTEM
    assert "Once Kafka said" in _SYSTEM
    assert "Never ask a liar why they lied" in _SYSTEM
    assert "When benefits stop" in _SYSTEM
    contract = _output_contract(WriterBrief.from_emotional(theme="boundaries"))
    assert '"writer_mode"' not in contract
    assert '"niche"' not in contract
    assert '"location_anchor"' in contract
    assert "visual_concept" in contract
    assert '"beats": [' in contract
    assert "exactly eight" in _SYSTEM
    assert "5–7-word hook" in _SYSTEM
    assert RELATIONSHIP_LOCATION_ANCHOR in _SYSTEM
    assert "Doorway Motif" in _SYSTEM
    assert "Graphic Minimalist Prop" in _SYSTEM
    assert "never neon" in _SYSTEM


def test_parenting_preset_swaps_few_shots_and_aesthetic_wrapper() -> None:
    brief = WriterBrief.from_emotional(theme="presence", module="parenting")
    prompt = system_prompt_for(brief)
    assert "They won't remember how clean the house was" in prompt
    assert "One day you will put your child down" in prompt
    assert "Never ask a liar why they lied" not in prompt
    assert PARENTING_LOCATION_ANCHOR in prompt
    assert "dark-haired parent and young child" in prompt
    assert get_niche_preset("parenting") is PARENTING
    wrapped = wrap_visual_prompt(
        "A cinematic, melancholic shot of a dimly lit hallway with two umbrellas leaning on separate walls.",
        "relationship",
    )
    assert wrapped.startswith(RELATIONSHIP.aesthetic_prefix)
    assert "Nostalgic warm color palette" in wrapped
    assert "vertical 9:16 full-bleed composition" in wrapped.lower()
    assert "vintage poster" in wrapped.lower()
    assert "35mm" not in wrapped.lower()
    parent_wrap = wrap_visual_prompt("small shoes by a sunlit doorway", "parenting")
    assert parent_wrap.startswith(PARENTING.aesthetic_prefix)
    assert "photorealistic" in PARENTING.negative_prompt


def test_draft_to_script_keeps_writer_visual_concepts() -> None:
    brief = WriterBrief.from_emotional(theme="distance", module="relationship")
    draft = ScriptDraft(
        lines=[
            "The loudest departure always begins long before anyone packs a bag.",
            "I stopped translating absence into excuses that protected everyone else.",
            "Because love without presence slowly becomes another form of waiting.",
            "The boundary felt cruel only while abandonment still felt familiar.",
            "Then distance showed me what closeness had kept carefully hidden.",
            "Some endings hurt because they return our neglected selves.",
            "Maturity begins when grief no longer negotiates against self-respect.",
        ],
        location_anchor="A dim diner booth beside a rain-slicked window",
        brief=brief,
        visual_concepts=[
            "A melancholic wide view across the rain-slicked diner window."
        ]
        + [f"quiet still {i}" for i in range(2, 8)],
        meta={"niche": "relationship"},
    )
    script = draft_to_script(draft)
    inject_prompt_fields(script)
    assert script["niche"] == "relationship"
    assert script["location_anchor"] == RELATIONSHIP_LOCATION_ANCHOR
    assert "dark-haired woman" in script["lines"][0]["visual_concept"]
    assert "A melancholic wide view" in script["lines"][0]["visual_concept"]
    assert script["lines"][0]["visual_source"] == "writer_single_pass"


def test_prompt_fields_are_programmatic_and_single_location_anchored() -> None:
    anchor = "A kitchen table with cold coffee cups at dusk"
    script = {
        "niche": "relationship",
        "location_anchor": anchor,
        "lines": [
            {
                "scene": i,
                "text": "Silence settled where our honest answers once belonged.",
                "visual_concept": f"{angle} view of one untouched cup under fading light",
            }
            for i, angle in enumerate(
                (
                    "wide",
                    "medium",
                    "over-the-shoulder",
                    "macro",
                    "low-angle",
                    "side-profile",
                    "high-angle",
                    "close detail",
                ),
                start=1,
            )
        ],
    }
    inject_prompt_fields(script)
    assert script["location_anchor"] == RELATIONSHIP_LOCATION_ANCHOR
    for i, beat in enumerate(script["lines"], start=1):
        assert beat["visual_concept"].startswith(RELATIONSHIP_LOCATION_ANCHOR)
        assert "vintage warm-paper world" in beat["visual_concept"]
        assert beat["final_positive_prompt"].startswith(RELATIONSHIP.aesthetic_prefix)
        assert beat["negative_prompt"] == RELATIONSHIP.negative_prompt
        assert "photorealistic" in beat["negative_prompt"]
        expected_palette = "WARM" if i <= 3 else ("COLD" if i <= 6 else "CONTRAST")
        assert RISO_STYLE.palettes[expected_palette] in beat["final_positive_prompt"]


def test_riso_flux_builder_uses_three_act_palette_and_style_negative() -> None:
    assert RISO_STYLE.model == "black-forest-labs/FLUX.1-dev"
    assert RISO_STYLE.steps == 20
    assert RISO_STYLE.guidance_scale == 5.5
    assert RISO_STYLE.profile_name == "style-riso_painting_retro_vintage"
    assert "full-bleed composition" in RISO_STYLE.format
    assert "white border" in RISO_STYLE.style_negative
    assert "neon" in RISO_STYLE.style_negative
    positive, negative = build_flux_prompt("woman waiting beside tea", 7)
    assert positive.startswith(RISO_STYLE.open)
    assert RISO_STYLE.technique.strip() in positive
    assert RISO_STYLE.palettes["CONTRAST"] in positive
    assert RISO_STYLE.linework_guard in positive
    assert "35mm" not in positive.lower()
    assert "vintage poster" in positive.lower()
    assert "full-bleed" in positive.lower()
    assert "matte finish" in positive.lower()
    assert negative == RISO_STYLE.style_negative


def test_full_bleed_crop_removes_generated_paper_margin(tmp_path: Path) -> None:
    image_path = tmp_path / "bordered.png"
    image = Image.new("RGB", (100, 100), "white")
    image.paste("red", (5, 5, 95, 95))
    image.save(image_path)

    enforce_full_bleed(image_path, trim_frac=0.05)

    with Image.open(image_path) as result:
        assert result.size == (100, 100)
        assert result.getpixel((0, 0))[0] > 240
        assert result.getpixel((0, 0))[1] < 20


def test_parenting_prompt_fields_lock_parent_child_arc() -> None:
    script = {
        "niche": "parenting",
        "location_anchor": "somewhere else",
        "lines": [
            {
                "scene": i,
                "text": "Time moves quietly through the rooms we share.",
                "visual_concept": "soft twilight, paper grain, quiet gesture",
            }
            for i in range(1, 9)
        ],
    }

    inject_prompt_fields(script)

    assert script["location_anchor"] == PARENTING_LOCATION_ANCHOR
    for beat in script["lines"]:
        assert beat["visual_concept"].startswith(PARENTING_LOCATION_ANCHOR)
        assert "parent" in beat["visual_concept"].lower()
        assert "child" in beat["visual_concept"].lower()


def test_total_narration_budget_is_normalized_without_llm_repair() -> None:
    lines = ["One two three four five six seven eight nine ten."] * 8
    normalized = _normalize_total_word_budget(lines)
    assert sum(len(line.split()) for line in normalized) == 79
    assert all(7 <= len(line.split()) <= 12 for line in normalized)


def test_gemini38_provider_uses_only_configured_primary_model() -> None:
    fake = TextResult("{}", "gemini", "models/gemini-3.8-flash", 1, 1, 0.0)
    with patch(
        "agents.mcp.text_model._complete_gemini",
        return_value=fake,
    ) as complete:
        result = _complete_gemini_flash("prompt", system="system")
    assert result is fake
    assert complete.call_count == 1
    assert complete.call_args.kwargs["model_chain"] == ["models/gemini-3.8-flash"]
    config = complete.call_args.kwargs["generation_config"]
    thinking = getattr(config, "thinking_config", None)
    assert thinking is None or int(getattr(thinking, "thinking_budget", 0) or 0) == 0
    assert float(config.temperature) == 0.35
    assert float(config.top_p) == 0.85
    assert int(config.max_output_tokens) == 768
    assert config.response_json_schema["properties"]["beats"]["minItems"] == 8


def test_fast_stage1_accepts_first_draft_without_judge_validator_or_repairs() -> None:
    lines = [
        "Silence taught me which promises were never meant to stay.",
        "I stopped translating absence into excuses that protected everyone else.",
        "Because love without presence slowly becomes another form of waiting.",
        "The boundary felt cruel only while abandonment still felt familiar.",
        "Then distance showed me what closeness had kept carefully hidden.",
        "Some endings hurt because they return our neglected selves.",
        "Maturity begins when grief no longer negotiates against self-respect.",
    ]
    lines.append("Leaving became the first honest kindness I offered myself.")
    draft = ScriptDraft(
        lines=lines,
        location_anchor="A kitchen table with cold coffee cups at dusk",
        human_situation="A person stops explaining absence.",
        visual_concepts=[f"relative camera angle {i} across the same cold cups" for i in range(8)],
        meta={"niche": "relationship"},
    )
    with (
        patch(
            "agents.writer.freeform_writer.write_draft",
            return_value=draft,
        ) as writer,
        patch(
            "core.economic_reel_lofi.pipeline.validate_script",
            side_effect=AssertionError("validator must be bypassed"),
        ),
        patch(
            "core.economic_reel_lofi.pipeline._repair_targeted_line",
            side_effect=AssertionError("repair must be bypassed"),
        ),
        patch(
            "agents.writer.script_brain.compose",
            side_effect=AssertionError("judge brain must be bypassed"),
        ),
        patch(
            "core.economic_reel_lofi.pipeline.rag.select_concrete_details",
            return_value=[],
        ),
        patch("core.economic_reel_lofi.pipeline.rag.note_batch_script"),
    ):
        script, errors, manual = _generate_validated_script(
            module="relationship",
            theme_row={"theme": "distance", "subtheme": "boundaries"},
            scene_count=9,
            duration_s=27,
            writer_mode="emotional",
            persist_on_pass=False,
            strict_judge=False,
        )
    assert writer.call_count == 1
    assert not errors and manual is False
    assert script is not None
    assert script["writer_diagnostics"]["attempt_count"] == 1
    assert script["writer_diagnostics"]["judge_calls"] == 0
    assert script["writer_diagnostics"]["repair_calls"] == 0


def test_strict_stage1_keeps_archived_judge_and_validator_path() -> None:
    brief = WriterBrief.from_emotional(theme="distance")
    draft = ScriptDraft(
        lines=[
            "Silence taught me which promises were never meant to stay.",
            "I stopped translating absence into excuses that protected everyone else.",
            "Because love without presence slowly becomes another form of waiting.",
            "The boundary felt cruel only while abandonment still felt familiar.",
            "Then distance showed me what closeness had kept carefully hidden.",
            "Some endings hurt because they return our neglected selves.",
            "Maturity begins when grief no longer negotiates against self-respect.",
        ],
        brief=brief,
    )
    brain = BrainResult(brief=brief, ok=True, draft=draft)
    accepted = {
        "theme": "distance",
        "writer_mode": "emotional",
        "arc_template": "thematic_arc",
        "lines": [{"scene": i + 1, "text": line} for i, line in enumerate(draft.lines)],
    }
    validation = SimpleNamespace(ok=True, reasons=[], script=accepted, feedback=lambda: "")
    with (
        patch("agents.writer.script_brain.compose", return_value=brain) as judge_brain,
        patch(
            "core.economic_reel_lofi.pipeline._repair_targeted_line",
            return_value=(None, 0),
        ),
        patch(
            "core.economic_reel_lofi.pipeline.validate_script",
            return_value=validation,
        ) as validator,
        patch(
            "core.economic_reel_lofi.pipeline.rag.select_concrete_details",
            return_value=[],
        ),
        patch("core.economic_reel_lofi.pipeline.rag.note_batch_script"),
    ):
        script, errors, manual = _generate_validated_script(
            module="relationship",
            theme_row={"theme": "distance"},
            scene_count=9,
            duration_s=27,
            writer_mode="emotional",
            strict_judge=True,
        )
    assert script is not None and not errors and manual is False
    assert judge_brain.call_count == 1
    assert validator.call_count >= 1


def test_default_beat_contract_allows_twelve_words() -> None:
    assert lofi_cfg.beat_word_budget(3.0) == 7
    assert lofi_cfg.beat_word_ceiling(3.0) == 12
    assert lofi_cfg.thematic_caption_limits() == (12, 84)


def test_audio_slots_follow_voice_with_calm_floor_and_no_cut() -> None:
    assert lofi_cfg.slot_duration_for_vo(1.0, trailing_silence_s=0.3) == (2.5, True)
    assert lofi_cfg.slot_duration_for_vo(3.5, trailing_silence_s=0.3) == (3.8, True)
    assert lofi_cfg.slot_duration_for_vo(4.2, trailing_silence_s=0.3) == (4.5, True)


def test_atmospheric_fallback_does_not_require_caption_noun() -> None:
    row = {"text": "Some silences are boundaries finally learning to breathe."}
    concept = _fallback_concept(
        row,
        episode_state={"mood": "quiet detachment", "motif": ""},
        index=0,
    )
    assert concept["source"] == "atmospheric_fallback"
    assert concept["beat_mood"] == "quiet detachment"
    assert concept["scene_description"]
    assert concept["licensed_objects"]


def test_stage2_mood_lighting_survives_palette_arc() -> None:
    row = {
        "scene": 1,
        "visual_source": "llm",
        "lighting_condition": "rainy_grey",
        "beat_mood": "restrained grief",
    }
    assign_palette_arc([row])
    assert row["lighting_condition"] == "rainy_grey"


def test_stage3_wraps_writer_concepts_with_niche_aesthetic(tmp_path: Path) -> None:
    script = {
        "niche": "parenting",
        "module": "parenting",
        "lines": [
            {
                "scene": 1,
                "text": "They will remember if you were present when they looked up.",
                "visual_concept": "small shoes waiting by a sunlit doorway",
            }
        ],
    }
    _assemble_stage3_prompts(
        script,
        theme_row={},
        lock_visuals=False,
        clips_dir=tmp_path,
        stamp="test",
        scene_count=1,
    )
    prompt = script["lines"][0]["visual_prompt"]
    assert prompt.startswith(PARENTING.aesthetic_prefix)
    assert "small shoes waiting by a sunlit doorway" in prompt
    assert RISO_STYLE.palettes["WARM"] in prompt
    assert "vertical 9:16 full-bleed composition" in prompt.lower()


def test_gate2_payload_contains_complete_prompt_artifact() -> None:
    rows = _gate2_review_rows(
        [
            {
                "scene": 1,
                "text": "Silence kept the boundary I could not name.",
                "meaning": "quiet self-protection",
                "beat_mood": "melancholic restraint",
                "visual_prompt": "riso positive",
                "negative_prompt": "structural negative",
                "subject_type": "silhouette",
            }
        ]
    )
    assert rows[0]["beat_mood"] == "melancholic restraint"
    assert rows[0]["visual_prompt"] == "riso positive"
    assert rows[0]["negative_prompt"] == "structural negative"


def test_script_report_prints_parseable_candidate_json(capsys) -> None:
    script = {
        "writer_mode": "emotional",
        "theme": "distance",
        "niche": "relationship",
        "location_anchor": "A diner booth beside a rain-slicked window",
        "lines": [
            {
                "scene": 1,
                "text": "Distance began where honesty stopped feeling safe.",
                "visual_concept": "two umbrellas leaning on opposite walls",
                "final_positive_prompt": "cinematic prompt",
                "negative_prompt": "bad anatomy, clear front portrait",
            }
        ],
    }
    _print_script_report(script, index=1, qty=1)
    output = capsys.readouterr().out
    payload = output.split("SCRIPT_CANDIDATE_JSON\n", 1)[1].split(
        "\nEND_SCRIPT_CANDIDATE_JSON", 1
    )[0]
    parsed = json.loads(payload)
    assert parsed["writer_mode"] == "emotional"
    assert parsed["niche"] == "relationship"
    assert parsed["location_anchor"]
    assert parsed["beats"][0]["visual_concept"]
    assert parsed["beats"][0]["final_positive_prompt"] == "cinematic prompt"
    assert parsed["beats"][0]["negative_prompt"]


def test_script_only_hard_stops_before_visual_and_tts_apis(tmp_path: Path) -> None:
    (tmp_path / "clips").mkdir()
    (tmp_path / "assets").mkdir()
    lines = [
        {"scene": i + 1, "text": f"This is approved emotional beat number {i + 1}."}
        for i in range(9)
    ]
    script = {
        "theme": "distance",
        "writer_mode": "emotional",
        "arc_template": "thematic_arc",
        "hook_type": "bold_claim",
        "lines": lines,
    }
    validation = SimpleNamespace(ok=True, reasons=[], script=script)
    with (
        patch("core.economic_reel_lofi.pipeline.validate_script", return_value=validation),
        patch(
            "core.economic_reel_lofi.visual_concept.translate_episode_visuals",
            side_effect=AssertionError("visual API must not run"),
        ),
        patch(
            "core.economic_reel_lofi.voiceover.ensure_script_voiceover",
            side_effect=AssertionError("TTS API must not run"),
        ),
    ):
        result = _produce_one(
            page_id="wonder_feed",
            module="relationship",
            duration_s=27,
            clips_dir=tmp_path / "clips",
            assets_dir=tmp_path / "assets",
            index=1,
            script_only=True,
            locked_script=script,
        )
    assert result.ok is True
    assert result.pipeline_stage == "1_script"
    assert result.video_path is None


def test_tts_first_reuses_audio_without_mutating_approved_text_or_visuals(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def fake_generate(text: str, output: Path, **_: object):
        calls.append(text)
        Path(output).write_bytes(b"fake mp3")
        return Path(output), [("word", 0.0, 0.2)]

    state: dict = {}
    script = {
        "theme": "distance",
        "lines": [{"scene": 1, "text": "Silence became the boundary I was afraid to name."}],
    }
    with (
        patch(
            "agents.media.audio_engine.generate_voiceover_with_timestamps",
            side_effect=fake_generate,
        ),
        patch(
            "core.economic_reel_lofi.assembler.measure_vo_speech_duration",
            return_value=2.6,
        ),
    ):
        ensure_script_voiceover(state, script, tmp_path)
        ensure_script_voiceover(state, script, tmp_path)
        script["lines"][0]["text"] = "Distance became the truth I could finally name."
        script["lines"][0]["visual_prompt"] = "stale"
        ensure_script_voiceover(state, script, tmp_path)

    assert len(calls) == 2
    assert calls == [
        "Silence became the boundary I was afraid to name.",
        "Distance became the truth I could finally name.",
    ]
    assert state["tts_stage_complete"] is True
    assert state["scene_durations"] == [2.8]
    assert state["audio_durations_s"] == [2.6]
    assert script["lines"][0]["visual_prompt"] == "stale"


def test_locked_atmospheric_hold_reuses_stills_after_legacy_prop_only_failure(
    tmp_path: Path,
) -> None:
    stills = [str(tmp_path / f"scene_{i:02d}.png") for i in range(1, 3)]
    source = tmp_path / "hold.json"
    source.write_text(
        json.dumps(
            {
                "script": {
                    "lines": [
                        {
                            "scene": i,
                            "text": f"Approved line {i}",
                            "visual_source": "writer_single_pass",
                        }
                        for i in range(1, 3)
                    ]
                },
                "scene_images": stills,
                "visual_qa_flags": [
                    "scene_1: SPOKEN-PROP: unspoken objects in pixels: window",
                    "scene_2: SPOKEN-PROP: unspoken objects in pixels: window",
                ],
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_locked_script(source)

    assert loaded["_locked_sidecar_assets"]["scene_images"] == stills
    assert loaded["_locked_sidecar_assets"]["manual_accept_scenes"] == [1, 2]


def test_stale_temp_voice_files_are_purged_before_assembly(tmp_path: Path) -> None:
    stale = tmp_path / "temp_voice_old.mp3"
    kept = tmp_path / "vo_scene_01.mp3"
    stale.write_bytes(b"stale")
    kept.write_bytes(b"approved")

    removed = _purge_stale_temp_voice_files(tmp_path)

    assert removed == [stale]
    assert not stale.exists()
    assert kept.exists()


def test_elastic_timeline_clamps_hook_and_adds_outro_tail(tmp_path: Path) -> None:
    generated: list[tuple[str, float]] = []
    lines = [
        {"scene": i + 1, "text": f"Exact approved line number {i + 1} stays fully unchanged."}
        for i in range(8)
    ]
    script = {"theme": "distance", "lines": lines}

    def fake_generate(text: str, output: Path, **kwargs: object):
        generated.append((text, float(kwargs["speed"])))
        Path(output).write_bytes(b"fake mp3")
        return Path(output), [("word", 0.0, 0.2)]

    measured = [2.9, 2.2, 2.3, 2.4, 2.5, 2.1, 2.6, 2.3]
    with (
        patch(
            "agents.media.audio_engine.generate_voiceover_with_timestamps",
            side_effect=fake_generate,
        ),
        patch(
            "core.economic_reel_lofi.assembler.measure_vo_speech_duration",
            side_effect=measured,
        ),
        patch(
            "agents.writer.spoken_budget.rewrite_single_line",
            side_effect=AssertionError("approved text must never be rewritten"),
        ),
    ):
        state: dict = {}
        ensure_script_voiceover(state, script, tmp_path)

    assert [text for text, _ in generated] == [row["text"] for row in lines]
    assert generated[0][1] >= 0.95
    assert state["audio_durations_s"] == measured
    assert state["scene_durations"] == [
        3.0,
        2.6,
        2.7,
        2.8,
        2.9,
        2.5,
        3.0,
        4.55,
    ]


def test_gemini_generator_normalizes_output_path(tmp_path: Path) -> None:
    class FakeAdapter:
        last_gemini_image_model_used = "models/gemini-2.5-flash-image"

        def __init__(self, **_: object) -> None:
            pass

        def generate(self, *_: object, output_directory: Path, **__: object) -> Path:
            path = Path(output_directory) / "generated.png"
            Image.new("RGB", (512, 1024), "navy").save(path)
            return path

    out = tmp_path / "scene_01.png"
    with patch(
        "agents.media.providers.image_provider.GeminiImageAdapter",
        FakeAdapter,
    ):
        path, meta = generate_scene_image_gemini("riso scene", out, width=720, height=1280)
    assert path == out
    assert Image.open(out).size == (720, 1280)
    assert meta["gen_model"] == "models/gemini-2.5-flash-image"
