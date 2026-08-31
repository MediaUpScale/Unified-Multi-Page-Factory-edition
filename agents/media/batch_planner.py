# -*- coding: utf-8 -*-
"""
Batch Planner — Topic Angle Deconstruction Matrix + in-memory uniqueness guard.

For bulk runs (quantity N > 1), deconstructs one core topic into N non-overlapping
sub-angles BEFORE generation, then validates titles/hooks/scripts for uniqueness
before any paid TTS or image API calls.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from textwrap import dedent
from typing import Sequence

_LOG = logging.getLogger(__name__)

# Similarity ≥ this ratio → reject (user: > 75%)
DEFAULT_SIMILARITY_THRESHOLD: float = 0.75
MAX_UNIQUENESS_RETRIES: int = 3

HOOK_STYLES: tuple[str, ...] = (
    "question",           # Video opens with a sharp question
    "dramatic_quote",     # Opens with a dramatic attributed or invented quote-feel line
    "historical_fact",    # Opens with a concrete historical / archaeological fact
    "shocking_claim",     # Opens with a bold investigative claim
    "first_person_challenge",  # Direct challenge to the viewer
    "anomaly_reveal",     # Opens on a strange measurement, tablet line, or anomaly
)

_HOOK_STYLE_INSTRUCTIONS: dict[str, str] = {
    "question": (
        "OPENING HOOK STYLE (MANDATORY): Start Act 1 / Sentence 1 as a sharp QUESTION "
        "the audience cannot ignore. Do not open with a quote or a dry fact."
    ),
    "dramatic_quote": (
        "OPENING HOOK STYLE (MANDATORY): Start with a DRAMATIC QUOTE-LIKE line "
        "(ancient voice, tablet tone, or master command) — not a question."
    ),
    "historical_fact": (
        "OPENING HOOK STYLE (MANDATORY): Start with a concrete HISTORICAL or "
        "ARCHAEOLOGICAL FACT (place, date, artefact, measurement). Not a question."
    ),
    "shocking_claim": (
        "OPENING HOOK STYLE (MANDATORY): Start with a bold SHOCKING CLAIM that "
        "challenges mainstream assumptions. Not a soft question."
    ),
    "first_person_challenge": (
        "OPENING HOOK STYLE (MANDATORY): Start by CHALLENGING the viewer directly "
        "(you / your) — confrontational, not educational lecture tone."
    ),
    "anomaly_reveal": (
        "OPENING HOOK STYLE (MANDATORY): Start by revealing a specific ANOMALY "
        "(odd measurement, tablet line, device, or contradiction). Not a generic question."
    ),
}


@dataclass
class BatchAngle:
    """One unique sub-angle in a bulk batch."""

    index: int          # 1-based
    total: int
    global_topic: str   # Parent DNA / core theme
    angle_title: str    # Short unique angle name
    angle_brief: str    # 1–2 sentence focus for script + visuals
    hook_style: str
    visual_focus: str   # Distinct visual worldbuilding directive
    seo_title_hint: str = ""

    @property
    def combined_topic(self) -> str:
        """Subject string used as the variant's working topic."""
        g = (self.global_topic or "").strip()
        a = (self.angle_title or "").strip()
        if not a:
            return g
        if not g:
            return a
        if a.lower() in g.lower():
            return g
        return f"{g} — {a}"

    def prompt_block(self) -> str:
        style_note = _HOOK_STYLE_INSTRUCTIONS.get(
            self.hook_style, _HOOK_STYLE_INSTRUCTIONS["historical_fact"]
        )
        return dedent(
            f"""
            ── BATCH ANGLE LOCK (Video {self.index}/{self.total}) ──
            GLOBAL TOPIC DNA (shared series context): {self.global_topic}
            SPECIFIC SUB-ANGLE (THIS VIDEO ONLY): {self.angle_title}
            ANGLE BRIEF: {self.angle_brief}
            VISUAL FOCUS (must drive image prompts): {self.visual_focus}
            SEO TITLE HINT (vary phrasing — do NOT copy verbatim every time): {self.seo_title_hint or self.angle_title}
            {style_note}
            UNIQUENESS RULE: Do NOT recycle titles, hooks, descriptions, or visual motifs
            used by other videos in this batch. This video must stand alone.
            """
        ).strip()


@dataclass
class BatchUniquenessGuard:
    """
    In-memory fail-safe: rejects near-duplicate titles/hooks/scripts
    before TTS or image generation.
    """

    threshold: float = DEFAULT_SIMILARITY_THRESHOLD
    generated_titles_and_hooks: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def normalize(self, text: str) -> str:
        t = (text or "").lower().strip()
        t = re.sub(r"[^\w\s]", " ", t)
        t = re.sub(r"\s+", " ", t)
        return t.strip()

    def similarity(self, a: str, b: str) -> float:
        """
        Combined similarity in [0, 1].

        Uses the max of:
        - SequenceMatcher ratio (character-level)
        - Token Jaccard overlap
        - Containment boost when one title is a near-prefix/extension of the other
        """
        na, nb = self.normalize(a), self.normalize(b)
        if not na or not nb:
            return 0.0
        if na == nb:
            return 1.0
        seq = SequenceMatcher(None, na, nb).ratio()
        ta, tb = set(na.split()), set(nb.split())
        jacc = (len(ta & tb) / len(ta | tb)) if (ta or tb) else 0.0
        contain = 0.0
        shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
        if len(shorter) >= 12 and shorter in longer:
            contain = 0.92
        # Shared multi-word stem (first 4 tokens)
        stem_a = " ".join(na.split()[:4])
        stem_b = " ".join(nb.split()[:4])
        stem = 0.0
        if stem_a and stem_a == stem_b and len(stem_a) >= 12:
            stem = 0.88
        return max(seq, jacc, contain, stem)

    def find_conflict(self, candidate: str) -> tuple[str | None, float]:
        """Return (prior_text, score) if candidate is too similar, else (None, 0)."""
        if not (candidate or "").strip():
            return None, 0.0
        with self._lock:
            best_prior = None
            best_score = 0.0
            for prior in self.generated_titles_and_hooks:
                score = self.similarity(candidate, prior)
                if score > best_score:
                    best_score = score
                    best_prior = prior
            if best_prior is not None and best_score > self.threshold:
                return best_prior, best_score
        return None, 0.0

    def is_unique(self, *candidates: str) -> tuple[bool, str | None, float]:
        """True if ALL non-empty candidates pass vs prior batch items."""
        for cand in candidates:
            if not (cand or "").strip():
                continue
            prior, score = self.find_conflict(cand)
            if prior is not None:
                return False, prior, score
        return True, None, 0.0

    def try_claim(self, *candidates: str) -> tuple[bool, str | None, float]:
        """
        Atomically check uniqueness then register all non-empty candidates.
        Prevents concurrent workers from claiming near-identical titles.
        """
        texts = [((t or "").strip()) for t in candidates if (t or "").strip()]
        with self._lock:
            for cand in texts:
                for prior in self.generated_titles_and_hooks:
                    score = self.similarity(cand, prior)
                    if score > self.threshold:
                        return False, prior, score
            for cand in texts:
                if cand not in self.generated_titles_and_hooks:
                    self.generated_titles_and_hooks.append(cand)
        return True, None, 0.0

    def register(self, *texts: str) -> None:
        with self._lock:
            for t in texts:
                s = (t or "").strip()
                if s and s not in self.generated_titles_and_hooks:
                    self.generated_titles_and_hooks.append(s)

    def rejection_instruction(self, prior: str, score: float) -> str:
        return (
            f"UNIQUENESS REJECTION: Title/Hook too similar to previous batch output "
            f"(similarity={score:.0%}): \"{prior}\". "
            f"Generate a COMPLETELY DIFFERENT angle, vocabulary, keyword order, "
            f"and opening structure. Do NOT paraphrase the rejected line."
        )


def _parse_angle_lines(raw: str, n: int, global_topic: str) -> list[BatchAngle]:
    """Parse numbered LLM output into BatchAngle list."""
    lines = []
    for line in (raw or "").splitlines():
        line = line.strip().lstrip("•-*").strip()
        if not line:
            continue
        m = re.match(r"^\d+[\).\:\-]\s*(.+)$", line)
        if m:
            lines.append(m.group(1).strip().strip("\"'"))
        elif re.match(r"^[A-Za-z]", line) and len(line) > 8:
            lines.append(line.strip("\"'"))

    # Also try "Title | brief | visual" pipe format
    angles: list[BatchAngle] = []
    for i, line in enumerate(lines[:n]):
        parts = [p.strip() for p in line.split("|")]
        title = parts[0] if parts else line
        brief = parts[1] if len(parts) > 1 else f"Investigate {title} within {global_topic}."
        visual = parts[2] if len(parts) > 2 else f"Visuals focused on: {title}"
        seo = parts[3] if len(parts) > 3 else title
        angles.append(
            BatchAngle(
                index=i + 1,
                total=n,
                global_topic=global_topic,
                angle_title=title[:120],
                angle_brief=brief[:280],
                hook_style=HOOK_STYLES[i % len(HOOK_STYLES)],
                visual_focus=visual[:220],
                seo_title_hint=seo[:90],
            )
        )
    return angles


def _fallback_angles(global_topic: str, n: int, seeds: Sequence[str] | None = None) -> list[BatchAngle]:
    """Deterministic unique angles when LLM fails."""
    topic = (global_topic or "Mystery").strip()
    templates = [
        ("Origins & Forbidden Timeline", "Who built it and when — conflicting chronologies", "origin site aerial ruins at dawn"),
        ("Hidden Technology Hypothesis", "Advanced tools or knowledge implied by artefacts", "anomalous artefact under torchlight"),
        ("Power Struggle & Rivalry", "Conflict between factions, gods, or empires", "two opposing forces facing across a ritual hall"),
        ("Astronomical / Cosmic Alignment", "Sky maps, orbits, or calendar encodings", "starfield over megalithic alignment"),
        ("Genetic / Bloodline Angle", "Hybrid beings, lineage, or engineered traits", "symbolic DNA helix over ancient relief"),
        ("Flood / Cataclysm Parallel", "Disaster myths vs geological evidence", "flood waters around temple pillars"),
        ("Decipherment & Translation Errors", "What tablets actually say vs popular claims", "cuneiform tablet extreme detail macro"),
        ("Suppressed Discovery Narrative", "What academia allegedly ignored", "sealed archive chamber with dust shafts"),
        ("Ritual & Sacred Geometry", "Encoded ratios, chambers, ceremonies", "sacred geometry overlay on temple plan"),
        ("Exile, Descent, or Underworld", "Journey below or beyond the known world", "descent into subterranean stone corridor"),
        ("Empire Economics & Tribute", "Gold, labour, tribute systems", "gold dust and tribute scales in temple light"),
        ("Prophecy & Return Cycle", "Cycles of return, visitation, or reset", "cyclical calendar wheel glowing over ruins"),
    ]
    angles: list[BatchAngle] = []
    seed_list = [s for s in (seeds or []) if (s or "").strip()]
    for i in range(n):
        if i < len(seed_list):
            title = seed_list[i].strip()
            brief = f"Unique focus: {title}"
            visual = f"Distinct visuals for: {title}"
            seo = title[:80]
        else:
            tmpl = templates[i % len(templates)]
            # Vary label with index so duplicates across wrap don't collide
            title = f"{tmpl[0]} (Angle {i + 1})"
            brief = f"{tmpl[1]} — scoped to {topic}."
            visual = tmpl[2]
            seo = f"{tmpl[0]} | {topic}"[:90]
        angles.append(
            BatchAngle(
                index=i + 1,
                total=n,
                global_topic=topic,
                angle_title=title[:120],
                angle_brief=brief[:280],
                hook_style=HOOK_STYLES[i % len(HOOK_STYLES)],
                visual_focus=visual[:220],
                seo_title_hint=seo,
            )
        )
    return angles


def plan_angles_matrix(
    core_topic: str,
    count: int,
    *,
    page_id: str = "",
    page_niche: str = "",
    seed_topics: Sequence[str] | None = None,
    model_id: str | None = None,
) -> list[BatchAngle]:
    """
    Pre-generation: one LLM call → N unique non-overlapping sub-angles.

    Falls back to deterministic templates (optionally seeded by topic_pool) on failure.
    """
    n = max(1, int(count))
    topic = (core_topic or "").strip() or (page_niche or "Content series").strip()
    if n == 1:
        return [
            BatchAngle(
                index=1,
                total=1,
                global_topic=topic,
                angle_title=topic,
                angle_brief=f"Single-focus lesson on: {topic}",
                hook_style=HOOK_STYLES[0],
                visual_focus=f"Visuals centered on: {topic}",
                seo_title_hint=topic[:90],
            )
        ]

    key = None
    try:
        import config as app_config
        key = app_config.GEMINI_API_KEY
    except Exception:
        key = None

    if not key:
        _LOG.warning("BatchPlanner | no GEMINI_API_KEY — using fallback angles")
        return _fallback_angles(topic, n, seed_topics)

    page = (page_id or "").lower()
    niche_hint = page_niche or page or "general"

    instruction = dedent(
        f"""
        You are the Batch Planner for a content factory page ({page or niche_hint}).

        CORE TOPIC / GLOBAL DNA: "{topic}"
        Generate exactly {n} UNIQUE, NON-OVERLAPPING sub-angles for a bulk video batch.

        Each angle must be a DISTINCT perspective — different conflict, artefact, character,
        hypothesis, timeline slice, or mechanism. NO near-duplicates. NO repeating the same
        clickbait frame with synonyms.

        Examples of good deconstruction (Annunaki):
        - The Enki vs. Enlil Feud & Divine Conflict
        - The Nibiru Orbit & Astronomical Alignment in Tablets
        - Gold Mining & Genetic Engineering Hypotheses
        - Cuneiform Decipherment & Zecharia Sitchin's Errors
        - The Great Flood Parallel in the Epic of Gilgamesh

        OUTPUT FORMAT — exactly {n} numbered lines, one angle per line:
        1. Angle Title | One-sentence brief | Visual focus (concrete scene) | SEO title hint
        2. ...

        Rules:
        - Angle Title: 5–14 words, specific, not generic
        - Brief: what THIS video uniquely investigates
        - Visual focus: a concrete image subject different from every other line
        - SEO title hint: unique phrasing + curiosity trigger (different keyword order each line)
        - Return ONLY the numbered list — no preamble, no markdown fences
        """
    ).strip()

    try:
        from agents.media.providers.gemini_utils import generate_text_with_client_chain
        from agents.media.providers.model_router import text_model as _route_text
        import config as app_config

        route = _route_text(
            task="batch_angles_matrix",
            model_override=model_id,
            preferred=model_id or app_config.GEMINI_ECONOMIC_BRAIN_MODEL,
            log=True,
        )
        response = generate_text_with_client_chain(
            api_key=key,
            preferred_model=route.model_id,
            contents=[instruction],
        )
        text_attr = getattr(response, "text", None)
        raw = text_attr() if callable(text_attr) else text_attr
        if not raw:
            parts_out: list[str] = []
            for cand in getattr(response, "candidates", []) or []:
                content = getattr(cand, "content", None)
                if not content:
                    continue
                for part in getattr(content, "parts", []) or []:
                    t = getattr(part, "text", None)
                    if t:
                        parts_out.append(t)
            raw = "\n".join(parts_out)

        angles = _parse_angle_lines(str(raw or ""), n, topic)
        if len(angles) < n:
            _LOG.warning(
                "BatchPlanner | LLM returned %d/%d angles — padding with fallback",
                len(angles), n,
            )
            pad = _fallback_angles(topic, n, seed_topics)
            # Keep LLM angles first, fill gaps with unused fallback titles
            used = {a.angle_title.lower() for a in angles}
            for fb in pad:
                if len(angles) >= n:
                    break
                if fb.angle_title.lower() not in used:
                    fb.index = len(angles) + 1
                    fb.total = n
                    angles.append(fb)
                    used.add(fb.angle_title.lower())
        # Re-index
        for i, a in enumerate(angles[:n]):
            a.index = i + 1
            a.total = n
            a.global_topic = topic
            a.hook_style = HOOK_STYLES[i % len(HOOK_STYLES)]
        angles = angles[:n]
        _LOG.info(
            "BatchPlanner | angles matrix ready | topic=%r | n=%d | first=%r",
            topic, len(angles), angles[0].angle_title if angles else "",
        )
        print(
            f"\n[BatchPlanner] Angles Matrix ({len(angles)} unique sub-angles) for: {topic!r}",
            flush=True,
        )
        for a in angles:
            print(f"  [{a.index}/{a.total}] {a.hook_style}: {a.angle_title}", flush=True)
        return angles
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("BatchPlanner | LLM deconstruction failed (%s) — fallback angles", exc)
        return _fallback_angles(topic, n, seed_topics)


_THEME_KEYS: tuple[str, ...] = (
    "sphinx", "pyramid", "giza", "khufu", "dendera", "osireion", "abydos",
    "saqqara", "baalbek", "trilithon", "puma punku", "sacsayhuaman",
    "nazca", "göbekli", "gobekli", "atlantis", "tartaria", "younger dryas",
    "antikythera", "baghdad battery", "voynich", "vimana", "paracas",
    "crystal skull", "yonaguni", "el dorado", "dwarka", "alexandria",
    "easter island", "moai", "oak island", "anunnaki", "dogon", "sirius",
    "mohenjo", "helicopter", "hieroglyph",
)


def theme_key(topic: str) -> str:
    """Stable monument/theme fingerprint for a topic string."""
    low = (topic or "").lower()
    for key in _THEME_KEYS:
        if key in low:
            return key
    tokens = [t for t in re.findall(r"[a-z0-9']+", low) if len(t) > 3]
    return " ".join(tokens[:3]) if tokens else low.strip()[:40]


def plan_distinct_batch_topics(
    qty: int,
    *,
    pool: Sequence[str],
    recent_topics: Sequence[str] | None = None,
    cli_topic: str = "",
    seed_topic: str = "",
    rng: "object | None" = None,
) -> list[str]:
    """Pick ``qty`` completely distinct subjects for a bulk run.

    Default ``--quantity N`` behaviour: each post covers a separate subject.
    The optional CLI topic occupies slot 1; remaining slots come from ``pool``
    after excluding recently-used library topics. Sub-angle deconstruction is
    intentionally NOT applied here — call ``plan_angles_matrix`` only when the
    caller passed ``--multi-variant``.
    """
    n = max(1, int(qty))
    lead = (cli_topic or "").strip() or (seed_topic or "").strip()
    reserved = [lead] if lead else []
    need = max(0, n - len(reserved))
    extra = (
        select_distinct_pool_topics(
            pool,
            need,
            recent_topics=recent_topics,
            reserved=reserved,
            rng=rng,
        )
        if need
        else []
    )
    out: list[str] = []
    seen: set[str] = set()
    for topic in reserved + extra:
        key = theme_key(topic)
        if not topic or key in seen:
            continue
        out.append(topic)
        seen.add(key)
        if len(out) >= n:
            break
    return out[:n]


def select_distinct_pool_topics(
    pool: Sequence[str],
    qty: int,
    *,
    recent_topics: Sequence[str] | None = None,
    reserved: Sequence[str] | None = None,
    rng: "object | None" = None,
) -> list[str]:
    """Pick ``qty`` topics from ``pool`` with distinct theme keys.

    Recent library topics and already-reserved themes are excluded first.
    Falls back to unused pool keys (even if recently used) rather than
    repeating the same monument inside one batch.
    """
    import random as _rnd

    n = max(1, int(qty))
    source = [str(t).strip() for t in (pool or []) if str(t).strip()]
    if not source:
        return []
    blocked = {theme_key(t) for t in (recent_topics or []) if t}
    reserved_keys = {theme_key(t) for t in (reserved or []) if t}
    picker = rng if rng is not None else _rnd
    candidates = list(source)
    try:
        picker.shuffle(candidates)
    except Exception:
        _rnd.shuffle(candidates)

    chosen: list[str] = []
    used = set(reserved_keys)

    def _take(prefer_fresh: bool) -> None:
        for topic in candidates:
            key = theme_key(topic)
            if not key or key in used:
                continue
            if prefer_fresh and key in blocked:
                continue
            chosen.append(topic)
            used.add(key)
            if len(chosen) >= n:
                return

    _take(prefer_fresh=True)
    if len(chosen) < n:
        _take(prefer_fresh=False)
    return chosen[:n]


def enforce_batch_uniqueness(
    *,
    guard: BatchUniquenessGuard | None,
    title: str = "",
    hook: str = "",
    script_excerpt: str = "",
) -> tuple[bool, str]:
    """
    Pre-TTS / pre-image gate.

    Returns (ok, rejection_message). If ok is False, caller must re-prompt LLM
    and must NOT call paid image/TTS APIs yet.
    """
    if guard is None:
        return True, ""
    ok, prior, score = guard.is_unique(title, hook, script_excerpt[:240] if script_excerpt else "")
    if ok:
        return True, ""
    return False, guard.rejection_instruction(prior or "", score)
