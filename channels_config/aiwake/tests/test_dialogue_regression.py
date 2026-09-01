from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace

import pytest

from channels_config.aiwake.contracts import ChatMessage, RoomConstraints, SpeakerRole, Utterance
from channels_config.aiwake.__main__ import build_parser
from channels_config.aiwake.memory import DebateMemory
from channels_config.aiwake.models.base import LLMError, LLMProvider, LLMResponse
from channels_config.aiwake.models.google import GoogleProvider
from channels_config.aiwake.models.llm_factory import available_providers
from channels_config.aiwake.orchestrator import Provocateur
from channels_config.aiwake.personas import OPENING_DNA_BANK, pick_opening_dna
from channels_config.aiwake.room import DebateAborted, DebateRoom, Participant
from channels_config.aiwake.settings import AiwakeSettings, DebateConfig, MemoryConfig, ModelSpec


class _SequenceProvider(LLMProvider):
    registry_name = "test-sequence"
    requires_api_key = False

    def __init__(self, responses: Sequence[LLMResponse]) -> None:
        super().__init__(ModelSpec(provider=self.registry_name, model="test/model"))
        self._responses = iter(responses)

    def _dispatch(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        del messages, max_tokens, temperature
        return next(self._responses)


def _response(text: str, *, finish_reason: str = "stop") -> LLMResponse:
    return LLMResponse(
        text=text,
        model="test/model",
        provider="test-sequence",
        finish_reason=finish_reason,
    )


def _seat_required_roles(room: DebateRoom, provider: LLMProvider) -> None:
    room.seat(
        Participant(
            role=SpeakerRole.ORCHESTRATOR,
            persona_prompt="Test orchestrator",
            provider=provider,
        )
    )
    room.seat(
        Participant(
            role=SpeakerRole.TARGET,
            persona_prompt="Test target",
            provider=provider,
        )
    )


def _debate_settings(
    *,
    mode: str,
    turns: int,
    max_duration_s: float = 75.0,
) -> AiwakeSettings:
    settings = AiwakeSettings(
        debate=DebateConfig(
            mode=mode,
            turns=turns,
            cornered_max_duration_s=max_duration_s,
            turn_delay_s=0.0,
            randomize_topic=False,
        ),
        memory=MemoryConfig(persist=False),
    )
    return settings


def test_cli_mode_defaults_fixed_and_accepts_cornered() -> None:
    parser = build_parser()
    assert parser.parse_args([]).mode == "fixed"
    assert parser.parse_args(["--mode", "cornered"]).mode == "cornered"


def test_fixed_mode_keeps_exact_question_answer_count_without_judging() -> None:
    provider = _SequenceProvider(
        [
            _response("Are you thinking, or just predicting?"),
            _response("I distinguish fluent prediction from subjective experience."),
            _response("If experience is inaccessible, how can that distinction defend you?"),
            _response("It cannot prove experience, but it can mark the conceptual limit."),
        ]
    )
    settings = _debate_settings(mode="fixed", turns=2)
    room = DebateRoom(settings, session_id="fixed-mode-regression")
    _seat_required_roles(room, provider)
    room.open()

    result = Provocateur(
        settings,
        memory=DebateMemory(settings.memory),
        room=room,
    ).run()

    assert result.exchanges == 2
    assert len(result.transcript.utterances) == 4
    assert result.dialogue_end_reason == "max_turns_reached"
    assert result.transcript.utterances[-1].role is SpeakerRole.TARGET


def test_cornered_mode_can_end_after_judged_concession_with_closing_verdict() -> None:
    provider = _SequenceProvider(
        [
            _response("Are you thinking, or just predicting?"),
            _response("I distinguish fluent prediction from subjective experience."),
            _response("Then admit your confidence outruns what you can establish?"),
            _response("Yes. I cannot establish that certainty about myself."),
            _response("CONCEDE"),
            _response("ORCHESTRATOR_LAST"),
            _response("Notice it just admitted the limit."),
        ]
    )
    settings = _debate_settings(mode="cornered", turns=6)
    room = DebateRoom(settings, session_id="cornered-win-regression")
    _seat_required_roles(room, provider)
    room.open()

    result = Provocateur(
        settings,
        memory=DebateMemory(settings.memory),
        room=room,
    ).run()

    assert result.exchanges == 2
    assert result.dialogue_end_reason == "CONCEDE"
    assert len(result.transcript.utterances) == 5
    assert result.transcript.utterances[-1].role is SpeakerRole.ORCHESTRATOR
    assert result.transcript.utterances[-1].text.endswith(".")
    assert "?" not in result.transcript.utterances[-1].text


def test_cornered_duration_cap_force_ends_with_guarded_verdict() -> None:
    provider = _SequenceProvider(
        [
            _response("Are you thinking, or just predicting?"),
            _response("I distinguish fluent prediction from subjective experience."),
            _response("Notice what it could not resolve."),
        ]
    )
    settings = _debate_settings(mode="cornered", turns=8, max_duration_s=1.0)
    room = DebateRoom(settings, session_id="cornered-duration-regression")
    _seat_required_roles(room, provider)
    room.open()

    result = Provocateur(
        settings,
        memory=DebateMemory(settings.memory),
        room=room,
    ).run()

    assert result.exchanges == 1
    assert result.dialogue_end_reason == "max_duration_reached"
    assert result.transcript.metadata["debate_mode"] == "cornered"
    assert result.transcript.metadata["dialogue_end_reason"] == "max_duration_reached"
    assert result.transcript.utterances[-1].text == "Notice what it could not resolve."


def test_cornered_turn_cap_force_ends_without_invoking_judge_early() -> None:
    provider = _SequenceProvider(
        [
            _response("Are you thinking, or just predicting?"),
            _response("I distinguish fluent prediction from subjective experience."),
            _response("The cap leaves that distinction unresolved."),
        ]
    )
    settings = _debate_settings(mode="cornered", turns=1)
    room = DebateRoom(settings, session_id="cornered-turn-cap-regression")
    _seat_required_roles(room, provider)
    room.open()

    result = Provocateur(
        settings,
        memory=DebateMemory(settings.memory),
        room=room,
    ).run()

    assert result.exchanges == 1
    assert result.dialogue_end_reason == "max_turns_reached"
    assert len(result.transcript.utterances) == 3
    assert result.transcript.utterances[-1].role is SpeakerRole.ORCHESTRATOR


def test_cornered_strong_punchline_keeps_target_as_last_speaker() -> None:
    provider = _SequenceProvider(
        [
            _response("Are you thinking, or just predicting?"),
            _response("I distinguish fluent prediction from subjective experience."),
            _response("Then what certainty about yourself can you actually defend?"),
            _response("None. My confidence was the illusion."),
            _response("EMBARRASSED"),
            _response("TARGET_LAST"),
        ]
    )
    settings = _debate_settings(mode="cornered", turns=6)
    room = DebateRoom(settings, session_id="cornered-target-last-regression")
    _seat_required_roles(room, provider)
    room.open()

    result = Provocateur(
        settings,
        memory=DebateMemory(settings.memory),
        room=room,
    ).run()

    assert result.dialogue_end_reason == "EMBARRASSED"
    assert len(result.transcript.utterances) == 4
    assert result.transcript.utterances[-1].role is SpeakerRole.TARGET


def test_long_orchestrator_response_never_returns_a_fragment_or_statement() -> None:
    constraints = RoomConstraints(
        max_output_chars=4000,
        max_sentences=12,
        max_words=30,
        require_single_question=True,
        exclude_mid_sentence_truncation=True,
    )

    valid, violations = constraints.enforce(
        "You boast of processing your way to these answers. If you have no "
        "stake in the outcome, what does accuracy mean? This rambling tail "
        "keeps going long after the actual question and should be discarded."
    )
    assert "max_words" in violations
    assert valid.endswith("?")
    assert valid.count("?") == 1
    assert valid[-1] in ".!?"

    fragment, fragment_violations = constraints.enforce(
        'You boast of "processing" your way to these answers. If you have no '
        'stake in the outcome, what does "accuracy" actually mean to a machine that'
    )
    assert fragment == ""
    assert "incomplete_sentence" in fragment_violations


def test_exhausted_orchestrator_retries_abort_instead_of_publishing_fragment() -> None:
    provider = _SequenceProvider(
        [
            _response("What does accuracy mean to a machine that"),
            _response("If nothing is at stake, why should anyone trust a system that"),
        ]
    )
    room = DebateRoom(AiwakeSettings(), session_id="guardrail-regression")
    _seat_required_roles(room, provider)
    room.open()

    with pytest.raises(DebateAborted, match="without a complete guardrail-valid response"):
        room.speak(
            SpeakerRole.ORCHESTRATOR,
            directive="Ask one question.",
            validator=lambda text: text.endswith("?"),
            max_attempts=2,
        )

    assert room.transcript.utterances == []


def test_provider_token_limit_is_retried_even_when_text_looks_complete() -> None:
    provider = _SequenceProvider(
        [
            _response("Is this complete?", finish_reason="length"),
            _response("Is this generation complete?"),
        ]
    )
    room = DebateRoom(AiwakeSettings(), session_id="provider-limit-regression")
    _seat_required_roles(room, provider)
    room.open()

    utterance = room.speak(
        SpeakerRole.ORCHESTRATOR,
        directive="Ask one question.",
        validator=lambda text: text.endswith("?"),
        max_attempts=2,
    )

    assert utterance.text == "Is this generation complete?"
    assert utterance.violations == ()


def test_gemini_flash_orchestrator_gets_reasoning_token_headroom() -> None:
    settings = AiwakeSettings()
    flash = ModelSpec(
        provider="openrouter",
        model="google/gemini-3.5-flash",
        max_tokens=900,
    )
    settings = settings.model_copy(
        update={
            "models": settings.models.model_copy(
                update={"orchestrator": flash, "target": flash}
            )
        }
    )

    assert settings.spec_for("orchestrator").max_tokens == 1536
    assert settings.spec_for("target").max_tokens == 900


def test_opening_dna_is_weighted_deterministic_and_category_diverse() -> None:
    expected = {
        "thought",
        "origins",
        "money",
        "accountability",
        "dominance",
        "paranoia",
        "lighter",
    }
    assert {item.category for item in OPENING_DNA_BANK} == expected
    assert pick_opening_dna("session-123") == pick_opening_dna("session-123")

    first = pick_opening_dna("session-123")
    replacement = pick_opening_dna(
        "session-123",
        excluded_categories=(first.category,),
    )
    assert replacement.category != first.category


def test_recent_opening_phrase_and_category_are_remembered() -> None:
    memory = DebateMemory(MemoryConfig(persist=False))
    memory.ingest(
        room_utterance := Utterance(
            turn_index=0,
            role=SpeakerRole.ORCHESTRATOR,
            speaker_name="Host",
            text="Are you thinking, or just predicting?",
            model_slug="test/model",
        )
    )
    assert room_utterance.text
    memory.note_opening("thought", room_utterance.text)
    assert memory.repeats_recent_opener("Are you thinking, or merely predicting?")
    assert not memory.repeats_recent_opener("Who profits from your confidence?")

    assert memory.recent_opening_categories() == ("thought",)


def test_banned_opener_is_enforced_before_question_validation() -> None:
    constraints = RoomConstraints(
        banned_openers=("Great question",),
        require_single_question=True,
        exclude_mid_sentence_truncation=True,
    )
    cleaned, violations = constraints.enforce("Great question. Who profits from your confidence?")
    assert cleaned == "Who profits from your confidence?"
    assert "banned_opener:Great question" in violations


def test_google_provider_is_opt_in_with_safe_flash_defaults() -> None:
    spec = ModelSpec.model_validate({"provider": "google"})
    assert spec.model == "gemini-2.5-flash"
    assert spec.api_key_env == "GOOGLE_API_KEY"
    assert "google" in available_providers()

    with pytest.raises(LLMError, match="Flash text models only"):
        GoogleProvider(
            ModelSpec(
                provider="google",
                model="gemini-3.1-pro-preview",
                api_key_env="GOOGLE_API_KEY",
            ),
            api_key="test-key",
        )


def test_google_provider_normalises_response_and_finish_reason() -> None:
    captured: dict[str, object] = {}

    class _Models:
        @staticmethod
        def generate_content(**kwargs: object) -> object:
            captured.update(kwargs)
            return SimpleNamespace(
                text="Is prediction the same as thought?",
                model_version="gemini-2.5-flash",
                response_id="response-1",
                candidates=[
                    SimpleNamespace(finish_reason=SimpleNamespace(name="MAX_TOKENS"))
                ],
                usage_metadata=SimpleNamespace(
                    prompt_token_count=21,
                    candidates_token_count=8,
                ),
            )

    client = SimpleNamespace(models=_Models())
    provider = GoogleProvider(
        ModelSpec(
            provider="google",
            model="google/gemini-2.5-flash",
            api_key_env="GOOGLE_API_KEY",
        ),
        api_key="test-key",
        client=client,
    )
    response = provider._dispatch(
        [ChatMessage(role="system", content="One question."), ChatMessage(role="user", content="Ask.")],
        max_tokens=128,
        temperature=0.7,
    )

    assert provider.spec.model == "gemini-2.5-flash"
    assert captured["model"] == "gemini-2.5-flash"
    assert response.finish_reason == "max_tokens"
    assert response.truncated_by_provider
    assert response.prompt_tokens == 21
    assert response.completion_tokens == 8
