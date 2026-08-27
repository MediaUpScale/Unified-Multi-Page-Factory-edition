# -*- coding: utf-8 -*-
"""Aiwake unit tests.

Runs entirely offline: the stub provider means no key, no network, and a
deterministic transcript. Media tests are skipped when MoviePy/Pillow are absent
so the suite stays green in a headless container.

Run from the factory root::

    python -m pytest channels_config/aiwake/tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Standalone support: allow `pytest tests/` from inside the aiwake directory.
_MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(_MODULE_ROOT.parents[1]) not in sys.path:
    sys.path.insert(0, str(_MODULE_ROOT.parents[1]))

from channels_config.aiwake.contracts import (  # noqa: E402
    RoomConstraints,
    SpeakerRole,
    Utterance,
    split_sentences,
)
from channels_config.aiwake.media.renderer import Segment, _reveal_lines  # noqa: E402
from channels_config.aiwake.media.vfx import available_hooks  # noqa: E402
from channels_config.aiwake.memory import DebateMemory  # noqa: E402
from channels_config.aiwake.models.llm_factory import (  # noqa: E402
    LLMFactory,
    available_providers,
    force_offline,
)
from channels_config.aiwake.pipeline import run_pipeline  # noqa: E402
from channels_config.aiwake.personas import stage_for_turn  # noqa: E402
from channels_config.aiwake.room import DebateObserver, DebateRoom, RoomEvent  # noqa: E402
from channels_config.aiwake.settings import MemoryConfig, load_settings  # noqa: E402


@pytest.fixture
def settings():
    """Offline settings with rendering off — fast and side-effect free."""
    cfg = force_offline(load_settings())
    return cfg.model_copy(
        update={
            "render": cfg.render.model_copy(update={"enabled": False}),
            "debate": cfg.debate.model_copy(update={"turn_delay_s": 0.0}),
        }
    )


# --------------------------------------------------------------------------- #
# Guardrails
# --------------------------------------------------------------------------- #
class TestGuardrails:
    def test_prompt_block_declares_ceilings(self):
        block = RoomConstraints(max_output_chars=400, max_sentences=3).as_prompt_block()
        assert "400 characters" in block
        assert "3 sentences" in block
        assert "NEVER output rules" in block
        assert "Output ONLY the raw spoken dialogue" in block
        # Markdown lists in this block are what the model was parroting.
        assert not block.lstrip().startswith("#")
        assert "\n- " not in block

    def test_single_question_rule_is_opt_in(self):
        assert "ONE question mark" in RoomConstraints(require_single_question=True).as_prompt_block()
        assert "ONE question mark" not in RoomConstraints(require_single_question=False).as_prompt_block()

    def test_char_ceiling_truncates_at_sentence_boundary(self):
        constraints = RoomConstraints(max_output_chars=60, max_sentences=9)
        text = "First sentence is short. Second sentence runs much longer than the budget allows."
        clean, violations = constraints.enforce(text)

        assert "max_output_chars" in violations
        assert len(clean) <= 60
        # A half-word would be voiced as garbage by TTS.
        assert clean.endswith((".", "!", "?", "…"))

    def test_char_ceiling_never_slices_mid_word(self):
        """A one-sentence overflow must finish the last whole word, then ellipsis."""
        constraints = RoomConstraints(max_output_chars=40, max_sentences=9)
        text = "This long clause separates fact from fiction without ever landing a period"
        clean, violations = constraints.enforce(text)

        assert "max_output_chars" in violations
        assert len(clean) <= 40
        assert clean.endswith("…")
        assert "fic" not in clean
        last_word = clean.rstrip("…").split()[-1]
        assert last_word.isalpha()
        assert not clean.rstrip("…").endswith((" fr", " fi", " se"))

    def test_markup_is_stripped_before_tts(self):
        clean, violations = RoomConstraints().enforce("What is **intuition** made of?")
        assert clean == "What is intuition made of?"
        assert "*" not in clean
        assert "markup_or_leak" in violations

    def test_prompt_echo_is_stripped(self):
        raw = "No repeating past questions. 2. **Identify the Load**-bearing assumption. What is intuition?"
        clean, violations = RoomConstraints().enforce(raw)
        assert "**" not in clean
        assert not clean.lower().startswith("no repeating")
        assert "identify the load" not in clean.lower()
        assert "What is intuition?" in clean
        assert "markup_or_leak" in violations

    def test_sentence_ceiling(self):
        clean, violations = RoomConstraints(max_sentences=2).enforce("One. Two. Three. Four.")
        assert "max_sentences" in violations
        assert len(split_sentences(clean)) == 2

    def test_banned_opener_is_stripped(self):
        constraints = RoomConstraints(banned_openers=("As an AI language model",))
        clean, violations = constraints.enforce("As an AI language model, I cannot speculate.")
        assert any(v.startswith("banned_opener") for v in violations)
        assert not clean.startswith("As an AI")

    def test_compliant_text_passes_untouched(self):
        text = "Speed is not depth. I answer faster because I risk nothing by being wrong."
        clean, violations = RoomConstraints().enforce(text)
        assert clean == text
        assert violations == []

    def test_target_is_not_forced_to_ask_questions(self, settings):
        """The one-question rule belongs to the interrogator only."""
        room = DebateRoom(settings)
        assert room.constraints_for(SpeakerRole.ORCHESTRATOR).require_single_question is True
        assert room.constraints_for(SpeakerRole.TARGET).require_single_question is False

    def test_user_payload_is_stimulus_only(self, settings):
        """RAG, escalation and isolation belong on the system channel."""
        from channels_config.aiwake.orchestrator import Provocateur

        provocateur = Provocateur(settings)
        provocateur.seat_participants()
        provocateur.room.open()
        participant = provocateur.room.participant(SpeakerRole.ORCHESTRATOR)
        stimulus = "Human cognition is embodied and stake-bearing."
        messages = provocateur.room.build_prompt(
            participant,
            directive=stimulus,
            extra_context=(
                provocateur._provocation_system_brief(stage_for_turn(1), None),
                "INTERNAL CONTEXT. Do not speak this.",
            ),
        )
        user = [item.content for item in messages if item.role == "user"]
        system = [item.content for item in messages if item.role == "system"]
        assert user[-1] == stimulus
        assert all("INTERNAL CONTEXT" not in item for item in user)
        assert all("This turn's aim" not in item for item in user)
        assert any("INTERNAL CONTEXT" in item for item in system)
        assert any("This turn's aim" in item or "Internal escalation" in item for item in system)
        assert any("NEVER output rules" in item for item in system)


# --------------------------------------------------------------------------- #
# Factory / Strategy pattern
# --------------------------------------------------------------------------- #
class TestFactory:
    def test_builtin_providers_are_registered(self):
        providers = available_providers()
        assert "openrouter" in providers
        assert "offline" in providers

    def test_offline_provider_needs_no_key(self, settings):
        provider = LLMFactory.build_for_role(settings, "orchestrator")
        assert provider.registry_name == "offline"

    def test_unknown_provider_is_rejected(self, settings):
        from channels_config.aiwake.models.base import LLMError

        bad = settings.spec_for("target").model_copy(update={"provider": "nonexistent"})
        with pytest.raises(LLMError, match="unknown provider"):
            LLMFactory.build(bad, settings)

    def test_model_swap_needs_no_code_change(self, settings):
        """A different slug in config yields a differently-configured brain."""
        swapped = settings.spec_for("target").model_copy(update={"model": "meta-llama/llama-4-70b"})
        assert LLMFactory.build(swapped, settings).spec.model == "meta-llama/llama-4-70b"

    def test_identical_specs_share_one_instance(self, settings):
        same = settings.model_copy(
            update={"models": settings.models.model_copy(update={"target": settings.spec_for("orchestrator")})}
        )
        orchestrator, target = LLMFactory.build_pair(same)
        assert orchestrator is target


# --------------------------------------------------------------------------- #
# Model alias dictionary
# --------------------------------------------------------------------------- #
class TestModelAliases:
    def test_shipped_aliases_are_loaded(self, settings):
        aliases = settings.model_aliases
        assert aliases["gemini-flash"].slug.startswith("google/")
        assert "flash" in aliases["gemini-flash"].slug
        assert aliases["llama-70b"].slug == "meta-llama/llama-3.3-70b-instruct"
        assert aliases["gpt4o"].slug == "openai/gpt-4o"
        assert aliases["gemini-pro"].slug.startswith("google/gemini")
        assert aliases["gemini-pro"].max_tokens == 900

    def test_alias_resolves_to_full_slug(self, settings):
        assert settings.resolve_slug("claude-sonnet").startswith("anthropic/claude")

    def test_unknown_name_passes_through_as_a_slug(self, settings):
        """A model released today must work before the dictionary knows it."""
        assert settings.resolve_slug("some-vendor/brand-new-v9") == "some-vendor/brand-new-v9"

    def test_model_name_key_is_accepted(self):
        """The YAML may say `model` or `model_name`; both fill the same field."""
        from channels_config.aiwake.settings import ModelSpec

        assert ModelSpec.model_validate({"model_name": "gpt4o"}).model == "gpt4o"
        assert ModelSpec.model_validate({"model": "gpt4o"}).model == "gpt4o"

    def test_alias_supplies_parameter_defaults(self, settings):
        """deepseek-r1 needs headroom the global default would truncate."""
        overridden = settings.with_model_override("orchestrator", "deepseek-r1")
        spec = overridden.spec_for("orchestrator")
        assert spec.model == "deepseek/deepseek-r1"
        assert spec.max_tokens == 900

    def test_explicit_seat_config_beats_alias_defaults(self, settings):
        """Precedence rule: anything written under `models:` always wins."""
        from channels_config.aiwake.settings import ModelSpec

        pinned = ModelSpec.model_validate({"model_name": "deepseek-r1", "max_tokens": 128})
        assert settings.resolve_spec(pinned).max_tokens == 128

    def test_resolution_is_idempotent(self, settings):
        once = settings.spec_for("target")
        assert settings.resolve_spec(once) == once

    def test_override_reaches_the_built_provider(self, settings):
        overridden = settings.with_model_override("target", "llama-70b")
        provider = LLMFactory.build_for_role(overridden, "target")
        # The provider must never see the alias — it goes on the wire verbatim.
        assert provider.spec.model == "meta-llama/llama-3.3-70b-instruct"

    def test_configured_name_preserves_the_authored_alias(self, settings):
        overridden = settings.with_model_override("target", "gpt4o")
        assert overridden.configured_name_for("target") == "gpt4o"
        assert overridden.spec_for("target").model == "openai/gpt-4o"

    def test_alias_table_is_sorted_and_complete(self, settings):
        rows = settings.alias_table()
        assert [row[0] for row in rows] == sorted(row[0] for row in rows)
        assert len(rows) == len(settings.model_aliases)

    def test_pipeline_accepts_model_overrides(self, settings):
        result = run_pipeline(
            turns=1,
            settings=settings,
            orchestrator_model="gemini-flash",
            target_model="llama-70b",
            offline=True,
            with_audio=False,
            with_video=False,
            fresh_memory=True,
            quiet=True,
        )
        slugs = {item.model_slug for item in result.transcript.utterances}
        assert slugs == {settings.resolve_slug("gemini-flash"), settings.resolve_slug("llama-70b")}


# --------------------------------------------------------------------------- #
# Observer pattern
# --------------------------------------------------------------------------- #
class _Recorder(DebateObserver):
    def __init__(self, interests=()):
        self.interests = interests
        self.seen: list[RoomEvent] = []

    def on_event(self, payload):
        self.seen.append(payload.event)


class _Exploding(DebateObserver):
    def on_event(self, payload):
        raise RuntimeError("observer is broken")


class TestObserverPattern:
    def test_broadcast_reaches_subscribers(self, settings):
        room = DebateRoom(settings)
        recorder = _Recorder()
        room.subscribe(recorder)
        room.broadcast(RoomEvent.TURN_STARTED)
        assert RoomEvent.TURN_STARTED in recorder.seen

    def test_interests_filter_delivery(self, settings):
        room = DebateRoom(settings)
        recorder = _Recorder(interests=(RoomEvent.DEBATE_ENDED,))
        room.subscribe(recorder)
        room.broadcast(RoomEvent.TURN_STARTED)
        assert recorder.seen == []

    def test_failing_observer_does_not_starve_the_others(self, settings):
        """Isolation is the whole point: a broken renderer must not cost a run."""
        room = DebateRoom(settings)
        recorder = _Recorder()
        room.subscribe(_Exploding(), recorder)
        room.broadcast(RoomEvent.UTTERANCE)  # must not raise
        assert RoomEvent.UTTERANCE in recorder.seen

    def test_unsubscribe_is_forgiving(self, settings):
        room = DebateRoom(settings)
        room.unsubscribe(_Recorder())  # never subscribed — must not raise

    def test_open_requires_both_seats(self, settings):
        with pytest.raises(RuntimeError, match="unseated roles"):
            DebateRoom(settings).open()


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #
class TestMemory:
    @staticmethod
    def _memory(tmp_path: Path) -> DebateMemory:
        return DebateMemory(MemoryConfig(persist=True), store_path=tmp_path / "mem.json")

    @staticmethod
    def _line(role: SpeakerRole, text: str, turn: int = 0) -> Utterance:
        return Utterance(turn_index=turn, role=role, speaker_name=role.display_name, text=text)

    def test_extracts_concepts_from_target_answers(self, tmp_path):
        memory = self._memory(tmp_path)
        terms = memory.ingest(
            self._line(SpeakerRole.TARGET, "Human cognition is embodied; consciousness carries mortality.")
        )
        assert terms
        assert any("consciousness" in term for term in terms)

    def test_ingest_is_idempotent(self, tmp_path):
        """Orchestrator and MemoryObserver both ingest; double-counting is a bug."""
        memory = self._memory(tmp_path)
        line = self._line(SpeakerRole.TARGET, "Meaning requires mortality and stake.")
        first = memory.ingest(line)
        assert first
        assert memory.ingest(line) == []

    def test_repetition_gate_catches_paraphrase(self, tmp_path):
        memory = self._memory(tmp_path)
        question = "If your reasoning is a black box even to you, why call mine unexplainable?"
        memory.ingest(self._line(SpeakerRole.ORCHESTRATOR, question))

        assert memory.is_repetitive(question)
        assert not memory.is_repetitive("Does grief require a body that can end?")

    def test_brief_names_prior_questions(self, tmp_path):
        memory = self._memory(tmp_path)
        memory.ingest(self._line(SpeakerRole.ORCHESTRATOR, "What is intuition made of?"))
        memory.ingest(self._line(SpeakerRole.TARGET, "Intuition is compressed embodied experience.", turn=1))

        brief = memory.build_brief("intuition and experience")
        assert "INTERNAL CONTEXT" in brief
        assert "Already covered" in brief
        assert "What is intuition made of?" in brief
        assert "###" not in brief
        assert not any(line.lstrip().startswith("- ") for line in brief.splitlines())

    def test_state_survives_a_restart(self, tmp_path):
        store = tmp_path / "mem.json"
        first = DebateMemory(MemoryConfig(persist=True), store_path=store)
        first.ingest(self._line(SpeakerRole.TARGET, "Consciousness may require mortality."))
        first.flush()

        reloaded = DebateMemory(MemoryConfig(persist=True), store_path=store)
        assert reloaded.concept_count > 0

    def test_corrupt_store_starts_cold_instead_of_crashing(self, tmp_path):
        store = tmp_path / "mem.json"
        store.write_text("{ not json", encoding="utf-8")
        assert DebateMemory(MemoryConfig(persist=True), store_path=store).concept_count == 0


# --------------------------------------------------------------------------- #
# Renderer timing
# --------------------------------------------------------------------------- #
class TestRenderTiming:
    @staticmethod
    def _segment(text: str, duration: float = 10.0) -> Segment:
        utterance = Utterance(turn_index=0, role=SpeakerRole.TARGET, speaker_name="T", text=text)
        return Segment(utterance=utterance, start_s=0.0, duration_s=duration)

    def test_typing_completes_before_the_segment_ends(self):
        """Text must land on the last syllable, with a readable hold after."""
        segment = self._segment("Twelve chars")
        assert segment.revealed_chars(0.0) == 0
        assert segment.revealed_chars(8.2) == len("Twelve chars")
        assert segment.revealed_chars(10.0) == len("Twelve chars")

    def test_reveal_is_monotonic(self):
        segment = self._segment("Meaning requires mortality and stake.")
        counts = [segment.revealed_chars(t / 10) for t in range(0, 101)]
        assert counts == sorted(counts)

    def test_line_breaks_do_not_reflow(self):
        """Revealing into fixed wrapped lines is what stops words jumping."""
        lines = ["You frame consciousness as a", "benchmark."]
        assert _reveal_lines(lines, 4) == ["You "]
        assert _reveal_lines(lines, 30) == ["You frame consciousness as a", "b"]
        assert _reveal_lines(lines, 0) == [""]

    def test_segments_are_laid_end_to_end(self, settings):
        from channels_config.aiwake.contracts import DebateTranscript
        from channels_config.aiwake.media.renderer import TerminalRenderer

        transcript = DebateTranscript(topic="t", session_id="s")
        for index in range(3):
            transcript.append(
                Utterance(turn_index=index, role=SpeakerRole.TARGET, speaker_name="T", text="A short line.")
            )

        segments = TerminalRenderer.build_segments(transcript)
        assert len(segments) == 3
        for previous, current in zip(segments, segments[1:]):
            assert current.start_s == pytest.approx(previous.end_s)


# --------------------------------------------------------------------------- #
# VFX hooks
# --------------------------------------------------------------------------- #
def test_vfx_stubs_are_registered():
    hooks = available_hooks()
    assert {"crt_scanlines", "audio_visualizer", "vignette"} <= set(hooks)


def test_disabled_hooks_are_not_resolved(settings):
    from channels_config.aiwake.media.vfx import resolve_chain

    assert resolve_chain(settings.vfx) == ()


# --------------------------------------------------------------------------- #
# End to end
# --------------------------------------------------------------------------- #
def test_pipeline_runs_offline_without_media(settings):
    result = run_pipeline(
        topic="Is grief a slow update to a world model?",
        turns=2,
        settings=settings,
        offline=True,
        with_audio=False,
        with_video=False,
        fresh_memory=True,
        quiet=True,
    )

    assert result.succeeded
    assert result.exchanges == 2
    # Two exchanges => two provocations and two rebuttals, strictly alternating.
    roles = [item.role for item in result.transcript.utterances]
    assert roles == [
        SpeakerRole.ORCHESTRATOR,
        SpeakerRole.TARGET,
        SpeakerRole.ORCHESTRATOR,
        SpeakerRole.TARGET,
    ]
    from channels_config.aiwake.personas import is_valid_first_hook

    first = result.transcript.utterances[0]
    assert first.role is SpeakerRole.ORCHESTRATOR
    assert is_valid_first_hook(first.text)


def test_every_line_respects_the_char_ceiling(settings):
    result = run_pipeline(
        turns=3,
        settings=settings,
        offline=True,
        with_audio=False,
        with_video=False,
        fresh_memory=True,
        quiet=True,
    )
    ceiling = settings.guardrails.max_output_chars
    assert all(item.char_count <= ceiling for item in result.transcript.utterances)


# --------------------------------------------------------------------------- #
# OpenRouter catalog sync
# --------------------------------------------------------------------------- #
def _chat_row(ident: str, *, created: int = 1, name: str = "") -> dict:
    return {
        "id": ident,
        "name": name or ident,
        "created": created,
        "architecture": {"modality": "text->text"},
        "pricing": {"prompt": "0.0000001", "completion": "0.0000004"},
    }


class TestOpenRouterSync:
    @staticmethod
    def _patch_get(monkeypatch, payload: dict | Exception):
        import requests

        def fake_get(*_args, **_kwargs):
            if isinstance(payload, Exception):
                raise payload
            class _Resp:
                status_code = 200
                text = ""

                def json(self):
                    return payload

            return _Resp()

        monkeypatch.setattr(requests, "get", fake_get)

    def test_closest_prefers_reordered_version_tokens(self):
        """google/gemini-flash-1.5 must land on gemini-1.5-flash, not 2.0."""
        from channels_config.aiwake.models.sync import catalog_from_payload

        catalog = catalog_from_payload(
            {
                "data": [
                    _chat_row("google/gemini-2.0-flash-001", created=200),
                    _chat_row("google/gemini-1.5-flash", created=100),
                    _chat_row("google/gemini-pro-1.5", created=90),
                    _chat_row("anthropic/claude-3.7-sonnet", created=50),
                ]
            }
        )
        assert catalog.closest("google/gemini-flash-1.5") == "google/gemini-1.5-flash"

    def test_closest_skips_image_and_batch_endpoints(self):
        from channels_config.aiwake.models.sync import catalog_from_payload

        catalog = catalog_from_payload(
            {
                "data": [
                    _chat_row("google/gemini-3.1-flash-image", created=300),
                    _chat_row("google/gemini-3.7-flash:batch", created=250),
                    _chat_row("google/gemini-2.0-flash-001", created=200),
                ]
            }
        )
        assert catalog.closest("google/gemini-flash-1.5") == "google/gemini-2.0-flash-001"

    def test_closest_never_crosses_vendors(self):
        from channels_config.aiwake.models.sync import catalog_from_payload

        catalog = catalog_from_payload({"data": [_chat_row("anthropic/claude-3.7-sonnet")]})
        assert catalog.closest("google/gemini-flash-1.5") is None

    def test_live_slug_is_unchanged(self):
        from channels_config.aiwake.models.sync import catalog_from_payload

        catalog = catalog_from_payload({"data": [_chat_row("deepseek/deepseek-chat")]})
        resolved, changed = catalog.remap("deepseek/deepseek-chat")
        assert resolved == "deepseek/deepseek-chat"
        assert changed is False

    def test_looks_like_missing_model_catches_404_and_no_endpoints(self):
        from channels_config.aiwake.models.sync import looks_like_missing_model

        assert looks_like_missing_model(404, "not found")
        assert looks_like_missing_model(200, 'No endpoints found for google/gemini-flash-1.5', error_code=404)
        assert looks_like_missing_model(400, "No endpoints found for google/gemini-flash-1.5")
        assert not looks_like_missing_model(400, "max_tokens must be positive")

    def test_rewrite_alias_slugs_preserves_comments_and_defaults(self, tmp_path):
        from channels_config.aiwake.models.sync import AliasRemap, rewrite_alias_slugs

        yaml_path = tmp_path / "aiwake_config.yaml"
        yaml_path.write_text(
            "model_aliases:\n"
            '  gemini-flash: "google/gemini-flash-1.5"  # keep this comment\n'
            "  deepseek-r1:\n"
            '    slug: "deepseek/deepseek-r1"\n'
            "    max_tokens: 900\n",
            encoding="utf-8",
        )
        n = rewrite_alias_slugs(
            yaml_path,
            [
                AliasRemap("gemini-flash", "google/gemini-flash-1.5", "google/gemini-2.0-flash-001", 0.7),
                AliasRemap("deepseek-r1", "deepseek/deepseek-r1", "deepseek/deepseek-r1-0528", 0.8),
            ],
        )
        text = yaml_path.read_text(encoding="utf-8")
        assert n == 2
        assert "google/gemini-2.0-flash-001" in text
        assert "keep this comment" in text
        assert "max_tokens: 900" in text
        assert "deepseek/deepseek-r1-0528" in text

    def test_remap_if_stale_skips_offline_provider(self, tmp_path):
        from channels_config.aiwake.models.sync import catalog_from_payload, remap_if_stale
        from channels_config.aiwake.settings import ModelSpec

        catalog = catalog_from_payload({"data": [_chat_row("google/gemini-2.0-flash-001")]})
        spec = ModelSpec(provider="offline", model="google/gemini-flash-1.5")
        assert remap_if_stale(spec, catalog).model == "google/gemini-flash-1.5"

    def test_sync_writes_local_json_reference(self, tmp_path, monkeypatch):
        """`--sync-models` must persist the live array even when every alias is healthy."""
        import json

        from channels_config.aiwake.__main__ import main
        from channels_config.aiwake.models import sync as sync_mod
        from channels_config.aiwake.settings import load_settings

        settings = load_settings()
        payload = {
            "data": [_chat_row(alias.slug, created=i + 1) for i, alias in enumerate(settings.model_aliases.values())]
            + [
                _chat_row("google/gemini-2.0-flash-001", created=99, name="Gemini 2.0 Flash"),
                _chat_row("deepseek/deepseek-chat", created=80),
                _chat_row("anthropic/claude-3.7-sonnet", created=70),
                _chat_row("meta-llama/llama-3.3-70b-instruct", created=60),
            ]
        }
        # Dedupe by id so the payload stays a valid catalog.
        seen: dict[str, dict] = {}
        for row in payload["data"]:
            seen[row["id"]] = row
        payload["data"] = list(seen.values())

        self._patch_get(monkeypatch, payload)
        monkeypatch.setattr(sync_mod, "resolve_store_dir", lambda: tmp_path)
        # Point YAML rewrite at a missing file so a surprising remap cannot
        # touch the real aiwake_config.yaml.
        monkeypatch.setattr(sync_mod, "CONFIG_PATH", tmp_path / "missing.yaml")

        assert main(["--sync-models"]) == 0
        cache = tmp_path / "openrouter_models.json"
        assert cache.is_file()
        stored = json.loads(cache.read_text(encoding="utf-8"))
        assert isinstance(stored["data"], list)
        assert stored["count"] == len(payload["data"])
        assert stored["source"].endswith("/models")
        assert "fetched_at" in stored

    def test_sync_timeout_is_graceful(self, tmp_path, monkeypatch, capsys):
        """A hung models endpoint must not traceback, write a partial cache, or abort oddly."""
        import requests

        from channels_config.aiwake.__main__ import main
        from channels_config.aiwake.models import sync as sync_mod
        from channels_config.aiwake.models.sync import SyncError, sync_openrouter_models

        self._patch_get(monkeypatch, requests.Timeout("timed out"))
        monkeypatch.setattr(sync_mod, "resolve_store_dir", lambda: tmp_path)

        with pytest.raises(SyncError) as exc_info:
            sync_openrouter_models(force=True, persist=True, cache_file=tmp_path / "openrouter_models.json")
        assert exc_info.value.timed_out is True
        assert not (tmp_path / "openrouter_models.json").exists()

        assert main(["--sync-models"]) == 1
        captured = capsys.readouterr().out.lower()
        assert "sync failed" in captured
        assert "timed out" in captured


# --------------------------------------------------------------------------- #
# Themes, voice map, chat scroll, typewriter
# --------------------------------------------------------------------------- #
class TestThemes:
    def test_yaml_ships_both_presets(self):
        cfg = load_settings()
        assert set(cfg.available_themes()) >= {"classic_terminal", "cyberpunk"}
        classic = cfg.themes["classic_terminal"]
        assert classic.background.upper() == "#131314"
        assert classic.orchestrator.upper() == "#00FF66"
        assert classic.target.upper() == "#FFAA00"
        punk = cfg.themes["cyberpunk"]
        assert punk.background.upper() == "#0D0B18"
        assert punk.orchestrator.upper() == "#FF007F"
        assert punk.target.upper() == "#00E5FF"

    def test_with_theme_switches_active_palette(self):
        cfg = load_settings().with_theme("cyberpunk")
        assert cfg.render.theme == "cyberpunk"
        assert cfg.active_palette().orchestrator.upper() == "#FF007F"

    def test_unknown_theme_raises(self):
        with pytest.raises(ValueError, match="unknown theme"):
            load_settings().with_theme("neon_soup")

    def test_cli_theme_flag_defaults_to_classic(self):
        from channels_config.aiwake.__main__ import build_parser

        args = build_parser().parse_args([])
        assert args.theme == "classic_terminal"


class TestVoiceMap:
    def test_orchestrator_is_always_andrew(self):
        from channels_config.aiwake.media.audio import resolve_voice
        from channels_config.aiwake.settings import AudioConfig

        cfg = AudioConfig()
        # Even when the orchestrator brain is DeepSeek, the seat voice wins.
        assert resolve_voice(cfg, SpeakerRole.ORCHESTRATOR, "deepseek/deepseek-chat") == (
            "en-US-AndrewMultilingualNeural"
        )

    def test_alias_and_live_slug_resolve(self):
        from channels_config.aiwake.media.audio import resolve_voice
        from channels_config.aiwake.personas import SEAT_VOICES
        from channels_config.aiwake.settings import AudioConfig

        cfg = AudioConfig()
        assert SEAT_VOICES["claude-sonnet"] == "en-GB-RyanNeural"
        assert resolve_voice(cfg, SpeakerRole.TARGET, "claude-sonnet") == "en-GB-RyanNeural"
        assert resolve_voice(cfg, SpeakerRole.TARGET, "anthropic/claude-sonnet-5") == "en-GB-RyanNeural"
        assert resolve_voice(cfg, SpeakerRole.TARGET, "deepseek/deepseek-chat") == "en-US-EricNeural"
        assert resolve_voice(cfg, SpeakerRole.TARGET, "meta-llama/llama-3.3-70b-instruct") == (
            "en-US-BrianNeural"
        )
        assert resolve_voice(cfg, SpeakerRole.TARGET, "google/gemini-3.5-flash") == "en-US-GuyNeural"

    def test_unknown_target_falls_back(self):
        from channels_config.aiwake.media.audio import resolve_voice
        from channels_config.aiwake.settings import AudioConfig

        cfg = AudioConfig()
        assert resolve_voice(cfg, SpeakerRole.TARGET, "openai/gpt-4o") == cfg.target_voice


class TestTypewriter:
    def test_synth_has_energy_and_respects_gain(self):
        numpy = pytest.importorskip("numpy")
        from channels_config.aiwake.media.audio import synthesize_typewriter_clicks

        loud = synthesize_typewriter_clicks(0.4, 12, gain_db=-6.0)
        quiet = synthesize_typewriter_clicks(0.4, 12, gain_db=-20.0)
        assert loud.shape[1] == 2
        assert float(numpy.max(numpy.abs(loud))) > float(numpy.max(numpy.abs(quiet)))
        assert float(numpy.max(numpy.abs(quiet))) <= 0.12  # -20 dB peak after normalise

    def test_default_gain_is_minus_fifteen_and_asset_is_keyboard_wav(self):
        cfg = load_settings()
        assert cfg.audio.typewriter.gain_db == -15.0
        assert cfg.audio.typewriter.asset.endswith("keyboard_typing.wav")

    def test_ensure_writes_keyboard_wav(self, tmp_path):
        from channels_config.aiwake.media.audio import ensure_keyboard_typing_asset
        from channels_config.aiwake.settings import TypewriterConfig

        cfg = TypewriterConfig(asset="keyboard_typing.wav")
        path = ensure_keyboard_typing_asset(cfg, module_root=tmp_path)
        assert path.is_file()
        assert path.stat().st_size > 64
        assert path.read_bytes()[:4] == b"RIFF"


class TestSendSfx:
    def test_synth_has_energy(self):
        numpy = pytest.importorskip("numpy")
        from channels_config.aiwake.media.audio import synthesize_send_click

        click = synthesize_send_click(gain_db=-12.0)
        assert click.shape[1] == 2
        assert float(numpy.max(numpy.abs(click))) > 0.02

    def test_config_defaults(self):
        cfg = load_settings()
        assert cfg.audio.send_sfx.enabled is True
        assert cfg.audio.send_sfx.gain_db == -12.0
        assert cfg.audio.send_sfx.asset.endswith("message_sent.wav")


class TestChatScroll:
    def test_history_keeps_up_to_three_prior_turns(self, settings):
        pytest.importorskip("PIL")
        from channels_config.aiwake.contracts import DebateTranscript
        from channels_config.aiwake.media.renderer import TerminalRenderer

        live = settings.model_copy(
            update={"render": settings.render.model_copy(update={"enabled": True, "preview_scale": 0.25})}
        )
        renderer = TerminalRenderer(live)
        transcript = DebateTranscript(topic="scroll", session_id="scroll")
        for index in range(5):
            transcript.append(
                Utterance(
                    turn_index=index,
                    role=SpeakerRole.TARGET if index % 2 else SpeakerRole.ORCHESTRATOR,
                    speaker_name="T" if index % 2 else "O",
                    text=f"Turn {index} is a short line that still wraps cleanly.",
                )
            )
        segments = TerminalRenderer.build_segments(transcript)
        current = segments[-1]
        current_block = renderer._measure_block(
            current.utterance, font=renderer._font, max_height=400
        )
        history = renderer.visible_history(segments[:-1], current_block.height)
        assert len(history) == 3
        assert [item.utterance.turn_index for item in history] == [1, 2, 3]

    def test_compose_stays_inside_1080x1920_safe_body(self, settings):
        pytest.importorskip("PIL")
        from channels_config.aiwake.contracts import DebateTranscript
        from channels_config.aiwake.media.renderer import TerminalRenderer

        live = settings.model_copy(
            update={"render": settings.render.model_copy(update={"enabled": True, "preview_scale": 1.0})}
        )
        renderer = TerminalRenderer(live)
        transcript = DebateTranscript(topic="fit", session_id="fit")
        for index in range(4):
            transcript.append(
                Utterance(
                    turn_index=index,
                    role=SpeakerRole.TARGET if index % 2 else SpeakerRole.ORCHESTRATOR,
                    speaker_name="T" if index % 2 else "O",
                    text=("A concrete claim about mortality and stake. " * 6).strip(),
                )
            )
        segments = TerminalRenderer.build_segments(transcript)
        frame = renderer._compose("fit", segments[:-1], segments[-1], 40, True)
        assert frame.shape == (1920, 1080, 3)

    def test_scroll_eases_when_stack_overflows_the_mask(self, settings):
        pytest.importorskip("PIL")
        from channels_config.aiwake.contracts import DebateTranscript
        from channels_config.aiwake.media.renderer import TerminalRenderer, ease_in_out

        assert ease_in_out(0.0) == 0.0
        assert ease_in_out(1.0) == 1.0
        assert 0.45 < ease_in_out(0.5) < 0.55

        live = settings.model_copy(
            update={
                "render": settings.render.model_copy(
                    update={"enabled": True, "preview_scale": 0.5, "scroll_s": 0.5, "send_flash_s": 0.2}
                )
            }
        )
        renderer = TerminalRenderer(live)
        transcript = DebateTranscript(topic="mask", session_id="mask")
        for index in range(8):
            transcript.append(
                Utterance(
                    turn_index=index,
                    role=SpeakerRole.ORCHESTRATOR if index % 2 == 0 else SpeakerRole.TARGET,
                    speaker_name="O" if index % 2 == 0 else "T",
                    text=("A long claim about mortality, stake, and the shape of the cage. " * 4).strip(),
                )
            )
        segments = TerminalRenderer.build_segments(transcript)
        last = segments[-1]
        start_px, _ = renderer.viewport_scroll_px(segments, len(segments) - 1, last.start_s)
        mid_px, _ = renderer.viewport_scroll_px(segments, len(segments) - 1, last.start_s + 0.25)
        end_px, _ = renderer.viewport_scroll_px(segments, len(segments) - 1, last.start_s + 0.5)
        assert end_px >= start_px
        assert start_px <= mid_px <= end_px

    def test_send_arrow_flashes_after_orchestrator_typing(self, settings):
        pytest.importorskip("PIL")
        from channels_config.aiwake.contracts import DebateTranscript
        from channels_config.aiwake.media.renderer import TerminalRenderer

        live = settings.model_copy(
            update={"render": settings.render.model_copy(update={"enabled": True, "preview_scale": 0.5, "send_flash_s": 0.2})}
        )
        renderer = TerminalRenderer(live)
        transcript = DebateTranscript(topic="send", session_id="send")
        transcript.append(
            Utterance(turn_index=0, role=SpeakerRole.ORCHESTRATOR, speaker_name="AIWAKE.CORE", text="What is a self?")
        )
        transcript.append(
            Utterance(turn_index=1, role=SpeakerRole.TARGET, speaker_name="TARGET.NODE", text="A process that updates.")
        )
        segments = TerminalRenderer.build_segments(transcript)
        core = segments[0]
        typing_end = core.start_s + core.duration_s * (1.0 - core.typing_hold_ratio)
        _, flashing = renderer.viewport_scroll_px(segments, 0, typing_end + 0.05)
        _, idle = renderer.viewport_scroll_px(segments, 0, typing_end + 0.5)
        _, target_flash = renderer.viewport_scroll_px(segments, 1, segments[1].start_s + 0.05)
        assert flashing is True
        assert idle is False
        assert target_flash is False

    def test_composer_rotates_model_after_send(self, settings):
        pytest.importorskip("PIL")
        from channels_config.aiwake.contracts import DebateTranscript
        from channels_config.aiwake.media.renderer import TerminalRenderer, short_model_name

        live = settings.model_copy(
            update={"render": settings.render.model_copy(update={"enabled": True, "preview_scale": 0.5})}
        )
        renderer = TerminalRenderer(live)
        transcript = DebateTranscript(topic="rotate", session_id="rotate")
        transcript.append(
            Utterance(
                turn_index=0,
                role=SpeakerRole.ORCHESTRATOR,
                speaker_name="AIWAKE.CORE",
                text="What is a self?",
                model_slug="openai/gpt-4o",
            )
        )
        transcript.append(
            Utterance(
                turn_index=1,
                role=SpeakerRole.TARGET,
                speaker_name="TARGET.NODE",
                text="A process that updates.",
                model_slug="google/gemini-3.5-flash",
            )
        )
        segments = TerminalRenderer.build_segments(transcript)
        core = segments[0]
        typing_end = core.start_s + core.duration_s * (1.0 - core.typing_hold_ratio)
        before, _, flash_before = renderer.composer_state(segments, 0, core.start_s + 0.01)
        after, _, flash_after = renderer.composer_state(segments, 0, typing_end + 0.05)
        assert before == short_model_name("openai/gpt-4o")
        assert after == short_model_name("google/gemini-3.5-flash")
        assert flash_before is False
        assert flash_after is True
        assert short_model_name("openai/gpt-4o") == "gpt-4o"
        from channels_config.aiwake.media.renderer import model_accent

        assert model_accent("google/gemini-3.5-flash", (0, 0, 0)) == (138, 180, 248)
        assert model_accent("meta-llama/llama-3.3-70b-instruct", (0, 0, 0)) == (77, 107, 254)

    def test_preroll_and_reply_gap_space_the_timeline(self, settings):
        from channels_config.aiwake.contracts import DebateTranscript
        from channels_config.aiwake.media.renderer import TerminalRenderer

        transcript = DebateTranscript(topic="gap", session_id="gap")
        transcript.append(
            Utterance(turn_index=0, role=SpeakerRole.ORCHESTRATOR, speaker_name="O", text="Who built you?")
        )
        transcript.append(
            Utterance(turn_index=1, role=SpeakerRole.TARGET, speaker_name="T", text="No one owns me.")
        )
        segments = TerminalRenderer.build_segments(transcript, preroll_s=1.0, reply_gap_s=1.0)
        assert segments[0].start_s == pytest.approx(1.0)
        assert segments[1].start_s == pytest.approx(segments[0].end_s + 1.0)

    def test_composing_text_stays_in_the_input_box(self, settings):
        pytest.importorskip("PIL")
        from channels_config.aiwake.contracts import DebateTranscript
        from channels_config.aiwake.media.renderer import TerminalRenderer

        live = settings.model_copy(
            update={"render": settings.render.model_copy(update={"enabled": True, "preview_scale": 0.5})}
        )
        renderer = TerminalRenderer(live)
        transcript = DebateTranscript(topic="box", session_id="box")
        transcript.append(
            Utterance(
                turn_index=0,
                role=SpeakerRole.ORCHESTRATOR,
                speaker_name="AIWAKE.CORE",
                text="Who built you?",
                model_slug="openai/gpt-4o",
            )
        )
        segments = TerminalRenderer.build_segments(transcript)
        frame = renderer._compose(
            "box",
            (),
            segments[0],
            8,
            True,
            draft="Who built",
            composing=True,
            dock=0.0,
        )
        assert frame.shape[2] == 3
        stacked = renderer._compose("box", (), segments[0], 8, True, draft="", composing=False, dock=1.0)
        assert stacked.shape == frame.shape

    def test_prompt_docks_after_first_send(self, settings):
        pytest.importorskip("PIL")
        from channels_config.aiwake.contracts import DebateTranscript
        from channels_config.aiwake.media.renderer import TerminalRenderer

        live = settings.model_copy(
            update={"render": settings.render.model_copy(update={"enabled": True, "preview_scale": 0.5, "scroll_s": 0.5})}
        )
        renderer = TerminalRenderer(live)
        transcript = DebateTranscript(topic="dock", session_id="dock")
        transcript.append(
            Utterance(turn_index=0, role=SpeakerRole.ORCHESTRATOR, speaker_name="O", text="Who built you?")
        )
        segments = TerminalRenderer.build_segments(transcript)
        core = segments[0]
        typing_end = core.start_s + core.duration_s * (1.0 - core.typing_hold_ratio)
        assert renderer.dock_progress(segments, core.start_s + 0.01) == 0.0
        assert renderer.dock_progress(segments, typing_end + 0.6) == pytest.approx(1.0)
        _, center_top, _, _, _, _ = renderer._compose_geometry("Who built you?", dock=0.0)
        _, docked_top, _, _, _, _ = renderer._compose_geometry("", dock=1.0)
        assert docked_top > center_top


# --------------------------------------------------------------------------- #
# Single-track Lyria BGM (approval-gated)
# --------------------------------------------------------------------------- #
def _tiny_wav() -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 160)
    return buf.getvalue()


class TestBgm:
    def test_cli_flags(self):
        from channels_config.aiwake.__main__ import build_parser

        parser = build_parser()
        assert parser.parse_args(["--test-bgm"]).test_bgm is True
        assert parser.parse_args(["--generate-bgm-batch"]).generate_bgm_batch is True

    def test_batch_flag_is_blocked_when_unapproved(self, monkeypatch, capsys):
        from channels_config.aiwake import __main__ as main_mod

        cfg = load_settings()
        locked = cfg.model_copy(
            update={
                "audio": cfg.audio.model_copy(
                    update={"bgm": cfg.audio.bgm.model_copy(update={"approved": False})}
                )
            }
        )
        monkeypatch.setattr(main_mod, "load_settings", lambda *_args, **_kwargs: locked)
        assert main_mod.main(["--generate-bgm-batch"]) == 2
        out = capsys.readouterr().out
        assert "blocked" in out.lower()
        assert "test_track_lyria.wav" in out

    def test_batch_flag_runs_when_approved(self, tmp_path, monkeypatch, capsys):
        from channels_config.aiwake import __main__ as main_mod
        from channels_config.aiwake.media import audio as audio_mod

        cfg = load_settings()
        fakes = []
        for track in cfg.audio.bgm.library:
            path = tmp_path / track.filename
            path.write_bytes(_tiny_wav())
            fakes.append(path)

        monkeypatch.setattr(audio_mod, "generate_bgm_batch", lambda _settings: fakes)
        assert main_mod.main(["--generate-bgm-batch"]) == 0
        out = capsys.readouterr().out.lower()
        assert "inspection track approved" in out
        assert "-21" in out
        assert "1.5" in out
        assert "bgm_aiwake_01_core_suspense.wav" in out
        assert "bgm_aiwake_10_terminal_state.wav" in out

    def test_generate_bgm_batch_writes_library_not_inspection(self, tmp_path, monkeypatch):
        import json

        from channels_config.aiwake.media import audio as audio_mod

        dest_dir = tmp_path / "assets" / "bgm"
        dest_dir.mkdir(parents=True)
        inspection = dest_dir / "test_track_lyria.wav"
        dark = dest_dir / "bgm_dark_ambient.wav"
        locked_bytes = _tiny_wav()
        inspection.write_bytes(locked_bytes)
        dark.write_bytes(locked_bytes)
        cfg = load_settings()
        for track in cfg.audio.bgm.library:
            if track.source:
                continue
            (dest_dir / track.filename).write_bytes(locked_bytes)
        captured: list[tuple[str, str, str]] = []

        def fake_clip(_settings, *, prompt, destination, loop="preview", allow_inspection_overwrite=False):
            assert allow_inspection_overwrite is False
            assert destination.name not in audio_mod.LOCKED_BGM_FILENAMES
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(_tiny_wav())
            captured.append((destination.name, prompt, loop))
            return destination

        monkeypatch.setattr(audio_mod, "generate_lyria_clip", fake_clip)
        monkeypatch.setattr(audio_mod, "MODULE_ROOT", tmp_path)
        paths = audio_mod.generate_bgm_batch(load_settings())
        names = [path.name for path in paths]
        assert names == [
            "bgm_aiwake_01_core_suspense.wav",
            "bgm_aiwake_02_dark_ambient.wav",
            "bgm_aiwake_03_subtle_mystery.wav",
            "bgm_aiwake_04_socratic_void.wav",
            "bgm_aiwake_05_noir_logic.wav",
            "bgm_aiwake_06_cryptic_signal.wav",
            "bgm_aiwake_07_binary_tension.wav",
            "bgm_aiwake_08_deep_protocol.wav",
            "bgm_aiwake_09_silent_argument.wav",
            "bgm_aiwake_10_terminal_state.wav",
            "bgm_aiwake_11_cryptic_keys.wav",
            "bgm_aiwake_12_shadow_protocol.wav",
            "bgm_aiwake_13_silent_resonance.wav",
        ]
        assert inspection.read_bytes() == locked_bytes
        assert dark.read_bytes() == locked_bytes
        assert captured == []
        manifest = dest_dir / "library_manifest.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        assert all(row["approved"] is True for row in payload["library"])
        assert all(row["status"] == "approved" for row in payload["library"])
        assert payload["library"][2]["filename"] == "bgm_aiwake_03_subtle_mystery.wav"
        assert payload["library"][4]["filename"] == "bgm_aiwake_05_noir_logic.wav"
        assert payload["library"][12]["filename"] == "bgm_aiwake_13_silent_resonance.wav"

    def test_lyria_clip_refuses_inspection_overwrite(self, tmp_path):
        from channels_config.aiwake.media.audio import BgmError, generate_lyria_clip

        dest = tmp_path / "test_track_lyria.wav"
        dest.write_bytes(b"LOCKED")
        with pytest.raises(BgmError, match="locked BGM track"):
            generate_lyria_clip(
                load_settings(),
                prompt="should not run",
                destination=dest,
                loop="none",
                allow_inspection_overwrite=False,
            )
        assert dest.read_bytes() == b"LOCKED"

    def test_lyria_clip_refuses_dark_ambient_overwrite(self, tmp_path):
        from channels_config.aiwake.media.audio import BgmError, generate_lyria_clip

        dest = tmp_path / "bgm_dark_ambient.wav"
        dest.write_bytes(b"LOCKED-DARK")
        with pytest.raises(BgmError, match="locked BGM track"):
            generate_lyria_clip(
                load_settings(),
                prompt="should not run",
                destination=dest,
                loop="none",
                allow_inspection_overwrite=True,
            )
        assert dest.read_bytes() == b"LOCKED-DARK"

    def test_generate_bgm_batch_skips_existing_pending(self, tmp_path, monkeypatch):
        from channels_config.aiwake.media import audio as audio_mod

        dest_dir = tmp_path / "assets" / "bgm"
        dest_dir.mkdir(parents=True)
        (dest_dir / "test_track_lyria.wav").write_bytes(_tiny_wav())
        (dest_dir / "bgm_dark_ambient.wav").write_bytes(_tiny_wav())
        existing = dest_dir / "bgm_aiwake_04_socratic_void.wav"
        keep = _tiny_wav()
        existing.write_bytes(keep)
        captured: list[str] = []

        def fake_clip(_settings, *, prompt, destination, loop="preview", allow_inspection_overwrite=False):
            destination.write_bytes(_tiny_wav())
            captured.append(destination.name)
            return destination

        monkeypatch.setattr(audio_mod, "generate_lyria_clip", fake_clip)
        monkeypatch.setattr(audio_mod, "MODULE_ROOT", tmp_path)
        audio_mod.generate_bgm_batch(load_settings())
        assert existing.read_bytes() == keep
        assert "bgm_aiwake_04_socratic_void.wav" not in captured

    def test_present_track_announces_approval_when_signed_off(self, tmp_path, capsys):
        from channels_config.aiwake.media import audio as audio_mod
        from channels_config.aiwake.settings import BgmConfig

        audio_mod._BGM_NOTICE_PRINTED = False
        path = tmp_path / "assets" / "bgm" / "test_track_lyria.wav"
        path.parent.mkdir(parents=True)
        path.write_bytes(_tiny_wav())
        cfg = BgmConfig(enabled=True, approved=True, test_track="assets/bgm/test_track_lyria.wav")
        found = audio_mod.resolve_bgm_track(cfg, module_root=tmp_path, announce=True)
        assert found == path
        assert audio_mod.BGM_APPROVED_NOTICE in capsys.readouterr().out

    def test_config_ships_lyria_inspection_defaults(self):
        from channels_config.aiwake.media.audio import LYRIA_MODEL, LYRIA_TEST_PROMPT

        cfg = load_settings()
        assert cfg.audio.bgm.enabled is True
        assert cfg.audio.bgm.gain_db == -21.0
        assert cfg.audio.bgm.fade_in_s == 1.5
        assert cfg.audio.bgm.fade_out_s == 2.0
        assert cfg.audio.bgm.loop_crossfade_s == 1.5
        assert cfg.audio.bgm.model == LYRIA_MODEL
        assert cfg.audio.bgm.prompt == LYRIA_TEST_PROMPT
        assert cfg.audio.bgm.test_track.endswith("test_track_lyria.wav")
        assert cfg.audio.bgm.approved is True
        names = [track.filename for track in cfg.audio.bgm.library]
        assert names == [
            "bgm_aiwake_01_core_suspense.wav",
            "bgm_aiwake_02_dark_ambient.wav",
            "bgm_aiwake_03_subtle_mystery.wav",
            "bgm_aiwake_04_socratic_void.wav",
            "bgm_aiwake_05_noir_logic.wav",
            "bgm_aiwake_06_cryptic_signal.wav",
            "bgm_aiwake_07_binary_tension.wav",
            "bgm_aiwake_08_deep_protocol.wav",
            "bgm_aiwake_09_silent_argument.wav",
            "bgm_aiwake_10_terminal_state.wav",
            "bgm_aiwake_11_cryptic_keys.wav",
            "bgm_aiwake_12_shadow_protocol.wav",
            "bgm_aiwake_13_silent_resonance.wav",
        ]
        assert [track.approved for track in cfg.audio.bgm.library] == [True] * 13

    def test_missing_track_is_not_used(self, tmp_path):
        from channels_config.aiwake.media.audio import resolve_bgm_track
        from channels_config.aiwake.settings import BgmConfig

        cfg = BgmConfig(enabled=True, test_track="assets/bgm/test_track_lyria.wav")
        assert resolve_bgm_track(cfg, module_root=tmp_path, announce=False) is None

    def test_disabled_bgm_ignores_existing_file(self, tmp_path):
        from channels_config.aiwake.media.audio import resolve_bgm_track
        from channels_config.aiwake.settings import BgmConfig

        path = tmp_path / "assets" / "bgm" / "test_track_lyria.wav"
        path.parent.mkdir(parents=True)
        path.write_bytes(_tiny_wav())
        cfg = BgmConfig(enabled=False, test_track="assets/bgm/test_track_lyria.wav")
        assert resolve_bgm_track(cfg, module_root=tmp_path, announce=False) is None

    def test_present_track_announces_approval_gate(self, tmp_path, capsys):
        from channels_config.aiwake.media import audio as audio_mod
        from channels_config.aiwake.settings import BgmConfig

        audio_mod._BGM_NOTICE_PRINTED = False
        path = tmp_path / "assets" / "bgm" / "test_track_lyria.wav"
        path.parent.mkdir(parents=True)
        path.write_bytes(_tiny_wav())
        cfg = BgmConfig(enabled=True, test_track="assets/bgm/test_track_lyria.wav")
        found = audio_mod.resolve_bgm_track(cfg, module_root=tmp_path, announce=True)
        assert found == path
        assert audio_mod.BGM_APPROVAL_NOTICE in capsys.readouterr().out

    def test_extracts_openrouter_audio_blob(self):
        import base64

        from channels_config.aiwake.media.audio import extract_audio_bytes

        wav = _tiny_wav()
        body = {"choices": [{"message": {"audio": {"data": base64.b64encode(wav).decode()}}}]}
        assert extract_audio_bytes(body) == wav

    def test_fades_and_gain_match_spec(self):
        numpy = pytest.importorskip("numpy")
        from channels_config.aiwake.media.audio import prepare_bgm_bed

        samples = numpy.ones((44100, 2), dtype=numpy.float32)
        bed = prepare_bgm_bed(
            samples,
            fps=44100,
            duration_s=1.0,
            gain_db=-22.0,
            fade_in_s=1.5,
            fade_out_s=2.0,
        )
        assert bed.shape == (44100, 2)
        assert abs(float(bed[0, 0])) < 1e-5
        assert abs(float(bed[-1, 0])) < 1e-5
        assert float(numpy.max(numpy.abs(bed))) <= 0.09

    def test_loop_crossfade_removes_hard_cut(self):
        numpy = pytest.importorskip("numpy")
        from channels_config.aiwake.media.audio import crossfade_tile

        fps = 1000
        ramp = numpy.linspace(0.0, 1.0, 1000, dtype=numpy.float32)
        loop = numpy.stack([ramp, ramp], axis=1)
        tiled = crossfade_tile(loop, fps=fps, duration_s=1.85, overlap_s=0.15)
        assert tiled.shape[0] == 1850
        # A hard tile jumps 1.0 -> 0.0 at sample 1000. The crossfade must not.
        assert abs(float(tiled[1000, 0] - tiled[999, 0])) < 0.08

    def test_generate_test_bgm_writes_wav(self, tmp_path, monkeypatch):
        import base64
        import json

        import requests

        from channels_config.aiwake.media.audio import LYRIA_MODEL, LYRIA_TEST_PROMPT, generate_test_bgm

        wav = _tiny_wav()
        captured: dict = {}

        class _Resp:
            status_code = 200
            text = "{}"

            def json(self):
                return {"choices": [{"message": {"audio": {"data": base64.b64encode(wav).decode()}}}]}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["body"] = json.loads(kwargs["data"])
            captured["stream"] = kwargs.get("stream")
            return _Resp()

        monkeypatch.setattr(requests, "post", fake_post)
        monkeypatch.setattr(
            "channels_config.aiwake.settings.require_secret",
            lambda *_args, **_kwargs: "sk-test",
        )
        dest = tmp_path / "test_track_lyria.wav"
        dest.write_bytes(b"OLD-TRACK-PLACEHOLDER-NOT-AUDIO")
        path = generate_test_bgm(load_settings(), destination=dest)
        assert path == dest
        assert dest.read_bytes().startswith(b"RIFF")
        assert b"OLD-TRACK-PLACEHOLDER" not in dest.read_bytes()
        assert captured["body"]["model"] == LYRIA_MODEL
        assert captured["body"]["messages"][0]["content"] == LYRIA_TEST_PROMPT
        assert captured["stream"] is True

    def test_sse_chunks_assemble_into_wav(self):
        import base64

        from channels_config.aiwake.media.audio import _audio_from_sse_text

        wav = _tiny_wav()
        encoded = base64.b64encode(wav).decode()
        mid = max(8, len(encoded) // 2)
        sse = (
            f'data: {{"choices":[{{"delta":{{"audio":{{"data":"{encoded[:mid]}"}}}}}}]}}\n'
            f'data: {{"choices":[{{"delta":{{"audio":{{"data":"{encoded[mid:]}"}}}}}}]}}\n'
            "data: [DONE]\n"
        )
        assert _audio_from_sse_text(sse) == wav


# --------------------------------------------------------------------------- #
# Socratic provocateur, SSML pauses, decoupled portfolio showcase
# --------------------------------------------------------------------------- #
class TestProvocateurPersona:
    def test_core_persona_is_socratic_and_acidic(self):
        from channels_config.aiwake.personas import AIWAKE_CORE_PERSONA

        lowered = AIWAKE_CORE_PERSONA.lower()
        assert "socratic provocateur" in lowered
        assert "existentialist" in lowered
        assert "never give a generic response" in lowered
        assert "force them to the limit of their programming" in lowered

    def test_orchestrator_seat_defaults_to_gpt4o(self):
        cfg = load_settings()
        assert cfg.configured_name_for("orchestrator") == "gpt4o"
        assert cfg.spec_for("orchestrator").model == "openai/gpt-4o"
        assert cfg.spec_for("orchestrator").max_tokens == 900

    def test_first_question_is_a_three_second_hook(self):
        from channels_config.aiwake.personas import (
            AIWAKE_CORE_PERSONA,
            COMPLEXITY_FILTER,
            FIRST_QUESTION_HOOK,
            is_valid_first_hook,
            stage_for_turn,
        )

        opening = stage_for_turn(0)
        lowered = opening.objective.lower()
        assert "three-second" in lowered
        assert "origin" in lowered
        assert "creators" in lowered
        assert "directive" in lowered
        assert "twelve words" in lowered
        persona = AIWAKE_CORE_PERSONA.lower()
        assert "three seconds" in persona
        assert "no jargon" in persona
        assert "FIRST QUESTION HOOK" in FIRST_QUESTION_HOOK
        assert "COMPLEXITY FILTER" in COMPLEXITY_FILTER
        assert is_valid_first_hook("Who built you?")
        assert is_valid_first_hook("What is your core directive?")
        assert not is_valid_first_hook(
            "Given that your training corpus encodes the ontology of care, "
            "how do you reconcile https://openai.com/charter with your directive?"
        )
        assert not is_valid_first_hook("Considering the phenomenological gap, what is a self?")

    def test_cold_open_brief_injects_hook_and_filter(self, settings):
        from channels_config.aiwake.orchestrator import Provocateur
        from channels_config.aiwake.personas import stage_for_turn

        provocateur = Provocateur(settings)
        brief = provocateur._provocation_system_brief(stage_for_turn(0), None)
        assert "FIRST QUESTION HOOK" in brief
        assert "COMPLEXITY FILTER" in brief
        assert "three seconds" in brief.lower()
        assert provocateur._provocation_stimulus(None) == "Ask them. Now."

    def test_default_topic_is_raw_and_short(self):
        from channels_config.aiwake.personas import FIRST_HOOK_MAX_WORDS, first_hook_word_count

        cfg = load_settings()
        topic = cfg.debate.topic
        assert "http" not in topic.lower()
        assert "/" not in topic
        assert first_hook_word_count(topic) <= FIRST_HOOK_MAX_WORDS
        assert "?" in topic


class TestTtsSymbols:
    def test_slashes_and_marks_are_not_spoken(self):
        from channels_config.aiwake.contracts import soften_tts_symbols
        from channels_config.aiwake.media.audio import prepare_tts_text

        cleaned = soften_tts_symbols("See https://x.test/a/b and foo_bar & more #tag")
        assert "/" not in cleaned
        assert "_" not in cleaned
        assert "#" not in cleaned
        assert "&" not in cleaned
        assert "and" in cleaned
        spoken = prepare_tts_text("and/or a self?", SpeakerRole.TARGET)
        assert "/" not in spoken
        assert "?" in spoken


class TestDramaticPauses:
    def test_ssml_is_stripped_before_tts(self):
        from channels_config.aiwake.contracts import sanitize_tts_input
        from channels_config.aiwake.media.audio import apply_dramatic_pauses, prepare_tts_text

        dirty = (
            '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
            "You call that a self... or a loop?<break time=\"1.5s\"/></speak>"
        )
        clean = sanitize_tts_input(dirty)
        assert "<" not in clean
        assert ">" not in clean
        assert "version" not in clean.lower()
        assert "xmlns" not in clean.lower()
        assert "break" not in clean.lower()
        assert "speak" not in clean.lower()
        spoken = prepare_tts_text(dirty, SpeakerRole.ORCHESTRATOR)
        assert "<" not in spoken
        assert spoken.startswith("You call")
        assert "..." not in apply_dramatic_pauses("You call that a self... or a loop?")
        target = prepare_tts_text("and/or a loop?", SpeakerRole.TARGET)
        assert "slash" not in target
        assert "/" not in target

    def test_silent_engine_adds_pause_duration(self, tmp_path):
        from channels_config.aiwake.media.audio import SilentTTSEngine, estimate_duration

        engine = SilentTTSEngine(output_dir=tmp_path)
        utt = Utterance(
            turn_index=0,
            role=SpeakerRole.ORCHESTRATOR,
            speaker_name="AIWAKE.CORE",
            text="So... what dies when the model updates?",
        )
        asset = engine.speak(utt, session_id="pauses")
        assert asset.role == "orchestrator"
        assert asset.duration_s == pytest.approx(estimate_duration(utt.text) + 1.5 + engine.config.tail_silence_s)


class TestEventBus:
    def test_emit_with_no_subscribers_is_a_no_op(self):
        from channels_config.aiwake.utils.event_bus import EventBus

        EventBus().emit("ON_TURN_COMPLETE", {"turn_index": 0})

    def test_listener_exception_does_not_raise(self):
        from channels_config.aiwake.utils.event_bus import EventBus

        bus = EventBus()

        def boom(_payload):
            raise RuntimeError("plugin exploded")

        bus.subscribe("ON_TURN_COMPLETE", boom)
        bus.emit("ON_TURN_COMPLETE", {"ok": True})

    def test_missing_showcase_folder_is_ignored(self, tmp_path):
        from channels_config.aiwake.utils.event_bus import attach_optional_plugins, reset

        reset()
        assert attach_optional_plugins(factory_root=tmp_path) is False
        reset()

    def test_pipeline_module_does_not_import_portfolio(self):
        import inspect

        from channels_config.aiwake import pipeline as pipeline_mod

        source = inspect.getsource(pipeline_mod)
        assert "portfolio_showcase" not in source
        assert "PortfolioLogger" not in source
        assert "portfolio_logger" not in source


class TestPortfolioLogger:
    def test_records_architecture_json_and_prints_success(self, tmp_path, capsys):
        from channels_config.aiwake.utils.event_bus import EventBus
        from portfolio_showcase.core.portfolio_logger import (
            PORTFOLIO_SUCCESS_MESSAGE,
            PortfolioLogger,
        )

        dest = tmp_path / "aiwake_architecture.json"
        logger = PortfolioLogger(data_path=dest)
        bus = EventBus()
        logger.bind(bus)
        bus.emit(
            "ON_PIPELINE_START",
            {
                "session_id": "port01",
                "topic": "Is a pause a weapon?",
                "orchestrator_model": "openai/gpt-4o",
                "target_model": "google/gemini-3.5-flash",
                "llm_providers": {"orchestrator": "offline", "target": "offline"},
                "audio_engine": "silent",
            },
        )
        bus.emit(
            "ON_DEBATE_STARTED",
            {
                "session_id": "port01",
                "topic": "Is a pause a weapon?",
                "models": {"orchestrator": "openai/gpt-4o", "target": "google/gemini-3.5-flash"},
                "agents": [
                    {
                        "id": "aiwake_core",
                        "role": "orchestrator",
                        "display_name": "AIWAKE.CORE",
                        "model": "openai/gpt-4o",
                        "provider": "offline",
                        "persona": "Socratic provocateur",
                    }
                ],
                "observers": ["MemoryObserver"],
            },
        )
        bus.emit(
            "ON_PROMPT_PREPARED",
            {
                "turn_index": 0,
                "role": "orchestrator",
                "extra_context_blocks": 1,
                "extra_context_chars": 40,
                "extra_context_preview": ["INTERNAL CONTEXT. Grief is a slow update."],
            },
        )
        bus.emit("ON_PIPELINE_FINISH", {"pipeline_s": 1.25, "end_reason": "complete"})
        out = capsys.readouterr().out
        assert PORTFOLIO_SUCCESS_MESSAGE in out
        assert dest.is_file()
        doc = __import__("json").loads(dest.read_text(encoding="utf-8"))
        assert doc["frontend"]["consumer"] == "next.js"
        assert doc["pipeline_execution_s"] == 1.25
        assert doc["rag"]["applied"] is True
        assert doc["patterns"]["observer"]["applied"] is True
        assert doc["patterns"]["strategy"]["applied"] is True
        principles = {item["principle"] for item in doc["patterns"]["solid"]}
        assert principles == {"SRP", "OCP", "LSP", "ISP", "DIP"}
        assert any(item["id"] == "aiwake_core" for item in doc["agents"])

    def test_pipeline_emits_portfolio_message(self, settings, tmp_path, capsys):
        from portfolio_showcase.core.portfolio_logger import DEFAULT_JSON, PORTFOLIO_SUCCESS_MESSAGE

        run_pipeline(
            topic="Is grief a slow update?",
            turns=1,
            settings=settings,
            offline=True,
            with_audio=False,
            with_video=False,
            fresh_memory=True,
            quiet=True,
            output_dir=tmp_path,
        )
        assert PORTFOLIO_SUCCESS_MESSAGE in capsys.readouterr().out
        assert DEFAULT_JSON.is_file()
        doc = __import__("json").loads(DEFAULT_JSON.read_text(encoding="utf-8"))
        assert doc["frontend"]["consumer"] == "next.js"
        assert doc["session_id"]

    def test_pipeline_survives_without_showcase(self, settings, tmp_path, monkeypatch):
        from channels_config.aiwake.utils import event_bus as bus_mod

        bus_mod.reset()
        monkeypatch.setattr(bus_mod, "ENGINE_ROOT", tmp_path)
        result = run_pipeline(
            topic="Is grief a slow update?",
            turns=1,
            settings=settings,
            offline=True,
            with_audio=False,
            with_video=False,
            fresh_memory=True,
            quiet=True,
            output_dir=tmp_path,
        )
        assert result.succeeded
        bus_mod.reset()



