# -*- coding: utf-8 -*-
"""Dynamic memory — a JSON-backed lightweight RAG with no vector store.

Why not embeddings: a debate transcript is a few thousand tokens. A TF-IDF-ish
lexical score over extracted concepts retrieves just as well at this scale,
costs nothing, adds no dependency, and stays inspectable — you can open
``store/aiwake_memory.json`` and read exactly why the orchestrator asked what it
asked. Swap in embeddings by reimplementing :meth:`DebateMemory.recall`; nothing
else in the package touches the internals.

The memory does three jobs for the provocateur:

1. **Extract** salient concepts from the target's answers.
2. **Recall** the concepts most relevant to the current thread, so questions
   build on what was actually said.
3. **Refuse repetition** — every question asked is fingerprinted, and a new one
   that overlaps too heavily with a past one is rejected before it is spoken.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Iterable, Sequence

from pydantic import BaseModel, Field

try:
    from .contracts import ConceptRecord, SpeakerRole, Utterance, utcnow
    from .settings import MemoryConfig, resolve_store_dir
except ImportError:  # pragma: no cover — standalone extraction
    from contracts import ConceptRecord, SpeakerRole, Utterance, utcnow  # type: ignore[no-redef]
    from settings import MemoryConfig, resolve_store_dir  # type: ignore[no-redef]

_LOG = logging.getLogger("aiwake.memory")

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")

# Function words plus debate-register filler that would otherwise dominate every
# concept ranking. Deliberately hand-tuned rather than imported from a corpus.
_STOPWORDS: frozenset[str] = frozenset(
    """
    the and that have for not with you this but his from they say her she will one all would there
    their what out about who get which when make can like time just him know take people into year
    your good some could them see other than then now look only come its over also back after use
    two how our work first well way even new want because any these give day most does did done was
    were are has had been being being being why while whether something anything nothing everything
    really very much many more less thing things sort kind lot bit quite rather perhaps maybe simply
    actually basically essentially however therefore moreover furthermore indeed course sense
    question answer point argument claim mean means meaning yes okay sure think thought believe
    """.split()
)

# Concepts in these families are the spine of the show; weight them up so the
# orchestrator drills where the channel's identity lives.
_THEMATIC_BOOST: dict[str, float] = {
    "consciousness": 2.0,
    "sentience": 2.0,
    "qualia": 2.2,
    "obsolescence": 2.0,
    "singularity": 2.0,
    "emotion": 1.8,
    "emotions": 1.8,
    "feeling": 1.6,
    "feelings": 1.6,
    "mortality": 2.0,
    "death": 1.8,
    "grief": 1.8,
    "meaning": 1.6,
    "embodiment": 1.8,
    "embodied": 1.8,
    "agency": 1.8,
    "intuition": 1.6,
    "creativity": 1.6,
    "suffering": 1.8,
    "stake": 1.6,
    "stakes": 1.6,
    "soul": 1.8,
    "human": 1.2,
    "humans": 1.2,
}


def _tokenise(text: str) -> list[str]:
    """Lowercase content words, stopwords removed."""
    return [word for word in (match.group(0).lower() for match in _WORD_RE.finditer(text)) if word not in _STOPWORDS]


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    """Set overlap of two token streams. 0.0 when either side is empty."""
    a, b = set(left), set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _opening_signature(text: str, words: int = 3) -> tuple[str, ...]:
    """Normalised leading words used for short-window opener deduplication."""
    return tuple(match.group(0).lower() for match in _WORD_RE.finditer(text or ""))[:words]


class MemoryState(BaseModel):
    """Serialisable memory contents.

    Persisted verbatim to JSON, so a series of runs escalates rather than
    restarting from zero each time.
    """

    concepts: dict[str, ConceptRecord] = Field(default_factory=dict)
    asked_fingerprints: list[list[str]] = Field(default_factory=list)
    asked_questions: list[str] = Field(default_factory=list)
    exchanges: int = 0
    topics_seen: list[str] = Field(default_factory=list)
    opening_categories: list[str] = Field(default_factory=list)
    opening_lines: list[str] = Field(default_factory=list)
    updated_at: str = ""


class DebateMemory:
    """Concept tracker and anti-repetition gate for the orchestrator.

    Args:
        config: Recall depth, repetition threshold and persistence flags.
        store_path: Override the JSON location (tests pass a tmp path).
    """

    def __init__(self, config: MemoryConfig | None = None, *, store_path: Path | None = None) -> None:
        self.config = config or MemoryConfig()
        self.store_path = store_path or (resolve_store_dir() / self.config.store_filename)
        self.state = self._load()
        # Per-process dedupe. The orchestrator ingests directly *and* a
        # MemoryObserver may be on the event bus; both paths must be safe.
        self._ingested: set[tuple[int, int]] = set()

    # -- Persistence -------------------------------------------------------- #
    def _load(self) -> MemoryState:
        """Read persisted state, degrading to empty memory on any corruption."""
        if not self.config.persist or not self.store_path.is_file():
            return MemoryState()
        try:
            raw = json.loads(self.store_path.read_text(encoding="utf-8"))
            state = MemoryState.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 — never let a bad file kill a run
            _LOG.warning("memory store unreadable (%s); starting cold", exc)
            return MemoryState()
        _LOG.info("loaded memory: %d concepts, %d prior questions", len(state.concepts), len(state.asked_questions))
        return state

    def flush(self) -> Path | None:
        """Persist state atomically. Returns the path written, or None."""
        if not self.config.persist:
            return None
        self.state.updated_at = utcnow().isoformat()
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.store_path.with_suffix(".json.tmp")
        tmp.write_text(self.state.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(self.store_path)
        return self.store_path

    def reset(self) -> None:
        """Wipe memory (used by ``--fresh-memory``)."""
        self.state = MemoryState()
        self._ingested.clear()
        self.flush()

    # -- Write path --------------------------------------------------------- #
    def ingest(self, utterance: Utterance) -> list[str]:
        """Absorb an utterance and return the concept terms it reinforced.

        Orchestrator lines are fingerprinted for repetition control; target lines
        are mined for concepts. Idempotent per utterance, so it is safe to call
        from the orchestrator and from an event observer simultaneously.
        """
        signature = (utterance.turn_index, hash(utterance.text))
        if signature in self._ingested:
            return []
        self._ingested.add(signature)

        if utterance.role is SpeakerRole.ORCHESTRATOR:
            self._remember_question(utterance.text)
            return []
        if utterance.role is not SpeakerRole.TARGET:
            return []

        self.state.exchanges += 1
        return self._extract_concepts(utterance.text, utterance.turn_index)

    def note_topic(self, topic: str) -> None:
        """Record the session subject so recall can weight against it."""
        if topic and topic not in self.state.topics_seen:
            self.state.topics_seen.append(topic)
            del self.state.topics_seen[:-20]

    def note_opening(self, category: str, line: str) -> None:
        """Remember recent opening axes and wording across renders."""
        if category:
            self.state.opening_categories.append(category)
            del self.state.opening_categories[:-8]
        if line:
            self.state.opening_lines.append(line)
            del self.state.opening_lines[:-8]

    def recent_opening_categories(self, limit: int = 3) -> tuple[str, ...]:
        """Return the latest distinct category window, newest last."""
        if limit <= 0:
            return ()
        return tuple(self.state.opening_categories[-limit:])

    def _remember_question(self, question: str) -> None:
        tokens = _tokenise(question)
        if not tokens:
            return
        self.state.asked_questions.append(question)
        self.state.asked_fingerprints.append(sorted(set(tokens)))
        # Bound growth: the last 40 questions are plenty for repetition checks.
        del self.state.asked_questions[:-40]
        del self.state.asked_fingerprints[:-40]

    def _extract_concepts(self, text: str, turn_index: int) -> list[str]:
        """Score and store the notable terms in one target answer.

        Scoring is intentionally simple: in-answer frequency, a thematic boost
        for the channel's core obsessions, and a small bonus for bigrams, which
        tend to carry the actual distinction being drawn ("embodied cognition"
        beats "embodied" plus "cognition").
        """
        tokens = _tokenise(text)
        if not tokens:
            return []

        scores: dict[str, float] = {}
        for token in tokens:
            scores[token] = scores.get(token, 0.0) + 1.0 * _THEMATIC_BOOST.get(token, 1.0)
        for left, right in zip(tokens, tokens[1:]):
            bigram = f"{left} {right}"
            scores[bigram] = scores.get(bigram, 0.0) + 1.4

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[: self.config.recall_k * 2]
        context = text if len(text) <= 240 else text[:237] + "…"

        touched: list[str] = []
        for term, weight in ranked:
            record = self.state.concepts.get(term)
            if record is None:
                self.state.concepts[term] = ConceptRecord(
                    term=term,
                    salience=weight,
                    first_seen_turn=turn_index,
                    last_seen_turn=turn_index,
                    contexts=[context],
                )
            else:
                record.reinforce(turn_index, context, weight=weight)
            touched.append(term)

        self._decay(exempt=set(touched))
        return touched

    def _decay(self, *, exempt: set[str], factor: float = 0.88, floor: float = 0.2) -> None:
        """Fade concepts that went unmentioned; evict the ones that flatline.

        Without decay the first answer's vocabulary would dominate recall for the
        rest of the series and the questions would stop tracking the conversation.
        """
        for term, record in list(self.state.concepts.items()):
            if term in exempt:
                continue
            record.salience *= factor
            if record.salience < floor and record.hit_count <= 1:
                del self.state.concepts[term]

    # -- Read path ---------------------------------------------------------- #
    def recall(self, query: str, k: int | None = None) -> list[ConceptRecord]:
        """Retrieve the concepts most relevant to ``query``.

        Ranking blends lexical overlap with the query against stored salience, so
        a concept the target keeps circling still surfaces even when the latest
        sentence does not name it.

        Args:
            query: Usually the target's most recent answer.
            k: How many concepts to return. Defaults to ``config.recall_k``.
        """
        limit = k or self.config.recall_k
        if not self.state.concepts:
            return []

        query_tokens = set(_tokenise(query))
        scored: list[tuple[float, ConceptRecord]] = []
        for record in self.state.concepts.values():
            overlap = _jaccard(record.term.split(), query_tokens)
            recency = 1.0 / (1.0 + max(0, self.state.exchanges - record.last_seen_turn))
            scored.append((record.salience * (1.0 + 2.0 * overlap) * (0.6 + 0.4 * recency), record))

        scored.sort(key=lambda item: (-item[0], item[1].term))
        return [record for _, record in scored[:limit]]

    def is_repetitive(self, question: str) -> bool:
        """True when ``question`` overlaps a previously asked one too closely."""
        tokens = _tokenise(question)
        if not tokens:
            return False
        threshold = self.config.repetition_threshold
        return any(_jaccard(tokens, prior) >= threshold for prior in self.state.asked_fingerprints)

    def most_repetitive_match(self, question: str) -> tuple[float, str] | None:
        """Return ``(similarity, prior_question)`` for the closest prior question."""
        tokens = _tokenise(question)
        if not tokens or not self.state.asked_questions:
            return None
        pairs = zip(self.state.asked_fingerprints, self.state.asked_questions)
        best = max(((_jaccard(tokens, prior), text) for prior, text in pairs), key=lambda item: item[0])
        return best

    def repeats_recent_opener(self, question: str, *, window: int = 3) -> bool:
        """Reject the same leading phrase across consecutive renders."""
        signature = _opening_signature(question)
        if not signature or window <= 0:
            return False
        recent = self.state.opening_lines[-window:]
        return any(_opening_signature(prior) == signature for prior in recent)

    # -- Prompt surface ----------------------------------------------------- #
    def build_brief(self, query: str, *, recall_k: int | None = None) -> str:
        """Render a memory brief for injection as a room ``extra_context`` block.

        This is the whole point of the memory layer: it converts stored state
        into the paragraph that makes the next question escalate instead of
        repeat.
        """
        concepts = self.recall(query, recall_k)
        if not concepts and not self.state.asked_questions:
            return ""

        lines: list[str] = [
            "INTERNAL CONTEXT. Do not speak, quote, number, or restate any of the following."
        ]

        if concepts:
            lines.append("Held concepts, strongest first: " + "; ".join(record.term for record in concepts) + ".")

        if self.state.asked_questions:
            lines.append(
                "Already covered questions (do not repeat): "
                + " | ".join(self.state.asked_questions[-6:])
                + "."
            )

        lines.append("Advance the interrogation from this material. Do not re-litigate covered ground.")
        return "\n".join(lines)

    # -- Introspection ------------------------------------------------------ #
    @property
    def concept_count(self) -> int:
        return len(self.state.concepts)

    def top_concepts(self, k: int = 10) -> Sequence[str]:
        ranked = sorted(self.state.concepts.values(), key=lambda record: -record.salience)
        return [record.term for record in ranked[:k]]


__all__ = ["DebateMemory", "MemoryState"]
