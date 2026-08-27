# -*- coding: utf-8 -*-
"""
Master Mei narrative helpers — V4.0 Cinematic Story Arc.

Episode theme lock, CTA dedupe, high-RPM SEO metadata, philosopher roster.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

try:
    from channels_config.master_mei.system_config import (
        is_didactic_philosophy_enabled,
        is_explicit_philosopher_citations_enabled,
        is_siren_enabled,
        is_v3_active,
        is_v4_active,
        is_western_majority_masses_enabled,
    )
except Exception:  # noqa: BLE001 — allow import outside package context
    def is_v3_active() -> bool:  # type: ignore[misc]
        return True

    def is_v4_active() -> bool:  # type: ignore[misc]
        return True

    def is_siren_enabled() -> bool:  # type: ignore[misc]
        return True

    def is_didactic_philosophy_enabled() -> bool:  # type: ignore[misc]
        return True

    def is_explicit_philosopher_citations_enabled() -> bool:  # type: ignore[misc]
        return True

    def is_western_majority_masses_enabled() -> bool:  # type: ignore[misc]
        return True

# ---------------------------------------------------------------------------
# Approved philosophical roster — ONE thinker/concept per episode (High-RPM)
# Autonomous rotation across Western + Eastern authorities.
# ---------------------------------------------------------------------------
PHILOSOPHER_ROSTER: dict[str, dict[str, Any]] = {
    "plato": {
        "key": "plato",
        "philosopher": "Plato",
        "concept": "The Allegory of the Cave",
        "label": "Plato's Allegory of the Cave",
        "core": (
            "Didactically explain Plato's Cave: glowing screens are the shadow-wall; "
            "digital propaganda keeps minds chained facing illusions while true sovereignty "
            "requires turning toward the light of disciplined focus and independence."
        ),
        "seduction": "shadow-feeds, viral illusions, and comfortable darkness",
        "practice": "turn from the feed — daily attention austerity and truth-seeking",
        "seo_hook": "Plato's Allegory of the Cave",
        "hashtag": "Plato",
        "principles": ["Escape illusion", "Mental sovereignty", "Seek truth over feed"],
        "keywords": (
            "plato", "cave", "allegory", "shadow", "illusion", "chains", "prison",
            "screen", "propaganda", "matrix", "awakening", "truth",
        ),
        "siren": False,
    },
    "nietzsche": {
        "key": "nietzsche",
        "philosopher": "Friedrich Nietzsche",
        "concept": "The Will to Power",
        "label": "Nietzsche's Will to Power",
        "core": (
            "Didactically explain Nietzsche's will to power: the Machine breeds herd morality "
            "and soft comfort; reclaiming focus and financial independence is self-overcoming "
            "against digital nihilism."
        ),
        "seduction": "herd outrage, comfort addiction, and slave morality of the feed",
        "practice": "self-overcoming drills — create, don't scroll; build capital, don't consume",
        "seo_hook": "Nietzsche's Will to Power",
        "hashtag": "Nietzsche",
        "principles": ["Self-overcoming", "Anti-herd mindset", "Creative power"],
        "keywords": (
            "nietzsche", "will to power", "ubermensch", "herd", "nihilism", "overcome",
            "slave morality", "strength", "create", "power",
        ),
        "siren": False,
    },
    "schopenhauer": {
        "key": "schopenhauer",
        "philosopher": "Arthur Schopenhauer",
        "concept": "The World as Will and Representation",
        "label": "Schopenhauer's Will and Representation",
        "core": (
            "Didactically explain Schopenhauer: endless craving is the will; digital apps "
            "weaponize representation — images that never satisfy — trapping attention and wealth."
        ),
        "seduction": "infinite craving loops, lust of the eyes, never-enough dopamine",
        "practice": "deny the will's next hit — scheduled silence and capital restraint",
        "seo_hook": "Schopenhauer's Will and Representation",
        "hashtag": "Schopenhauer",
        "principles": ["Deny craving", "See through representation", "Quiet the will"],
        "keywords": (
            "schopenhauer", "will", "representation", "craving", "pessimism", "desire",
            "suffering", "appetite", "lust", "never enough",
        ),
        "siren": False,
    },
    "epictetus": {
        "key": "epictetus",
        "philosopher": "Epictetus",
        "concept": "The Dichotomy of Control",
        "label": "Epictetus' Dichotomy of Control",
        "core": (
            "Didactically explain Epictetus: only judgment and action are yours — not likes, "
            "not algorithms. Mental sovereignty begins by releasing what the Machine controls."
        ),
        "seduction": "rage-bait, notification anxiety, and outcomes you cannot command",
        "practice": "morning dichotomy review — act only on what is yours",
        "seo_hook": "Epictetus' Dichotomy of Control",
        "hashtag": "Epictetus",
        "principles": ["Dichotomy of control", "Inner freedom", "Action over reaction"],
        "keywords": (
            "epictetus", "dichotomy", "control", "judgment", "stoic", "freedom",
            "anxiety", "notification", "reaction", "handbook",
        ),
        "siren": False,
    },
    "bentham": {
        "key": "bentham",
        "philosopher": "Jeremy Bentham",
        "concept": "The Panopticon",
        "label": "Bentham's Panopticon",
        "core": (
            "Didactically explain Bentham's Panopticon: you behave as if always watched — "
            "social media and corporate dashboards are the modern inspection tower draining focus."
        ),
        "seduction": "performative posting, metric anxiety, and self-surveillance",
        "practice": "leave the tower — private work blocks without an audience",
        "seo_hook": "Bentham's Panopticon",
        "hashtag": "Panopticon",
        "principles": ["Anti-surveillance", "Private mastery", "Quit performing"],
        "keywords": (
            "bentham", "panopticon", "watch", "inspection", "tower", "prison",
            "metrics", "perform", "surveillance", "visibility",
        ),
        "siren": False,
    },
    "foucault": {
        "key": "foucault",
        "philosopher": "Michel Foucault",
        "concept": "The Digital Panopticon",
        "label": "Michel Foucault's Digital Panopticon",
        "core": (
            "Didactically explain Foucault's Panopticon as algorithmic surveillance and "
            "self-policing social media addiction — the Machine watches until you watch yourself."
        ),
        "seduction": "algorithmic surveillance, likes, and the fear of being seen",
        "practice": "attention fasting and refusal to self-police for the crowd",
        "seo_hook": "Michel Foucault's The Digital Panopticon",
        "hashtag": "Foucault",
        "principles": ["Digital sovereignty", "Anti-surveillance mindset", "Attention warfare"],
        "keywords": (
            "foucault", "panopticon", "surveillance", "algorithm", "self-policing",
            "social media", "rat race", "slavery", "matrix", "wired", "crowd", "mass",
        ),
        "siren": False,
    },
    "sun_tzu": {
        "key": "sun_tzu",
        "philosopher": "Sun Tzu",
        "concept": "The Art of Digital War",
        "label": "Sun Tzu's Art of Digital War",
        "core": (
            "Didactically explain Sun Tzu: know yourself and the terrain of the market/matrix — "
            "tactical focus defeats noise; scattered attention loses the war before it starts."
        ),
        "seduction": "scattered notifications and fake urgency dressed as opportunity",
        "practice": "single-point focus drills and capital allocation of attention",
        "seo_hook": "Sun Tzu's The Art of Digital War",
        "hashtag": "SunTzu",
        "principles": ["Tactical focus", "Know yourself", "Financial sovereignty"],
        "keywords": (
            "sun tzu", "art of war", "strategy", "tactical", "terrain", "enemy",
            "wealth", "money", "financial", "capital", "empire", "market",
        ),
        "siren": False,
    },
    "stoic": {
        "key": "stoic",
        "philosopher": "Marcus Aurelius",
        "concept": "Stoic Sovereignty",
        "label": "Marcus Aurelius' Stoic Sovereignty",
        "core": (
            "Didactically explain Stoic sovereignty (Aurelius): control what is "
            "yours — judgment and action — and total resistance to external digital distractions."
        ),
        "seduction": "external outrage, comfort feeds, and moods the Machine hands you",
        "practice": "dawn judgment audit and cold refusal of impulse",
        "seo_hook": "Marcus Aurelius' Stoic Sovereignty",
        "hashtag": "Stoicism",
        "principles": ["Inner judgment", "Impulse control", "Resistance to externals"],
        "keywords": (
            "marcus", "aurelius", "stoic", "citadel", "sovereignty",
            "discipline", "impulse", "resilience", "meditations", "hardship",
        ),
        "siren": False,
    },
    "buddha": {
        "key": "buddha",
        "philosopher": "Siddhartha Gautama",
        "concept": "The Illusion of Desire (Maya)",
        "label": "Buddha's Illusion of Desire (Maya)",
        "core": (
            "Didactically explain Maya — the illusion of desire: instant gratification "
            "clouds the mind; detachment restores clarity and sovereign will."
        ),
        "seduction": "instant hits — scrolls, snacks, and phantom pleasures",
        "practice": "watch craving rise and refuse to obey it",
        "seo_hook": "Buddha's The Illusion of Desire (Maya)",
        "hashtag": "Mindfulness",
        "principles": ["Detachment", "Mental clarity", "Craving refusal"],
        "keywords": (
            "buddha", "buddhist", "maya", "desire", "illusion", "detachment",
            "craving", "gratification", "clarity", "gautama", "siddhartha",
        ),
        "siren": False,
    },
    "siren": {
        "key": "siren",
        "philosopher": "Master Mei",
        "concept": "The Siren Trap",
        "label": "The Siren Trap (Fleeting Beauty)",
        "core": (
            "Special episode: short-term lust and cybernetic femme-fatale avatars are "
            "manufactured by the Matrix to drain a warrior's wealth and shatter focus."
        ),
        "seduction": "hyper-targeted beauty, lust loops, and engineered femme-fatale avatars",
        "practice": "walk past the siren without negotiation — guard capital and attention",
        "seo_hook": "Master Mei's The Siren Trap",
        "hashtag": "SirenTrap",
        "principles": ["Impulse intercept", "Capital protection", "Focus fortress"],
        "keywords": (
            "siren", "seduction", "allure", "lust", "beauty", "femme", "tempt",
            "narciss", "fleeting", "trap beauty", "desire", "porn", "distraction",
        ),
        "siren": True,
    },
}

# Backward-compatible aliases → roster keys
EPISODE_THEMES: dict[str, dict[str, Any]] = {
    "panopticon": PHILOSOPHER_ROSTER["foucault"],
    "strategy_wealth": PHILOSOPHER_ROSTER["sun_tzu"],
    "discipline": PHILOSOPHER_ROSTER["stoic"],
    "seduction": PHILOSOPHER_ROSTER["siren"],
    "foucault": PHILOSOPHER_ROSTER["foucault"],
    "sun_tzu": PHILOSOPHER_ROSTER["sun_tzu"],
    "stoic": PHILOSOPHER_ROSTER["stoic"],
    "buddha": PHILOSOPHER_ROSTER["buddha"],
    "siren": PHILOSOPHER_ROSTER["siren"],
    "maya": PHILOSOPHER_ROSTER["buddha"],
    "plato": PHILOSOPHER_ROSTER["plato"],
    "nietzsche": PHILOSOPHER_ROSTER["nietzsche"],
    "schopenhauer": PHILOSOPHER_ROSTER["schopenhauer"],
    "epictetus": PHILOSOPHER_ROSTER["epictetus"],
    "bentham": PHILOSOPHER_ROSTER["bentham"],
    "cave": PHILOSOPHER_ROSTER["plato"],
}

_ROTATION_STATE_PATH = (
    Path(__file__).resolve().parents[2] / "outputs" / "master_mei" / ".philosopher_rotation.json"
)
_RECENT_THEME_LIMIT = 4

_APPROVED_CTA = (
    "Master your mind. Follow Master Mei to reclaim your sovereignty."
)

# Narration word budget defaults (overridden by resolve_mei_duration_profile)
# Primary band = 90–120 s reels → 8–10 scenes
_NARRATION_WORDS_MIN: int = 120
_NARRATION_WORDS_TARGET: int = 135
_NARRATION_WORDS_MAX: int = 150
_PHILOSOPHICAL_BREAK_TAG: str = '<break time="1.5s"/>'
_MAX_PHILOSOPHICAL_BREAKS: int = 4

_RAG_DIRECTIVE_PATH: Path = (
    Path(__file__).resolve().parents[2]
    / "channels_config"
    / "master_mei"
    / "rag_directive.txt"
)

# Dynamic duration → words / frames / consecutive training (0.86× + pauses)
# Primary: 90–120 s | Scene1 ≤8s | body scenes 10–12s | 8–10 frames
MEI_DURATION_PROFILES: dict[int, dict[str, int]] = {
    90: {
        "target_s": 95,
        "words_min": 115,
        "words_max": 135,
        "words_target": 125,
        "frames": 8,
        "max_consec_training": 1,
        "hook_max_s": 8,
        "body_min_s": 10,
        "body_max_s": 12,
    },
    105: {
        "target_s": 105,
        "words_min": 130,
        "words_max": 155,
        "words_target": 142,
        "frames": 9,
        "max_consec_training": 2,
        "hook_max_s": 8,
        "body_min_s": 10,
        "body_max_s": 12,
    },
    120: {
        "target_s": 120,
        "words_min": 150,
        "words_max": 175,
        "words_target": 162,
        "frames": 10,
        "max_consec_training": 2,
        "hook_max_s": 8,
        "body_min_s": 10,
        "body_max_s": 12,
    },
}

_FALLBACK_SCRIPTWRITER_DIRECTIVE: str = """
SYSTEM DIRECTIVE: MASTER MEI NARRATIVE ENGINE

STRICT LENGTH & DURATION CONSTRAINTS:
- TARGET REEL DURATION: 90 to 120 seconds.
- DELIVERY STYLE: Slow, humble, measured, ancestral tranquility.

PERSONA: Never address the audience as disciples/students/followers.
Never aggressive commands. Never "I studied X". Reflective phrasing only.

FOCUSED NARRATIVE — ONE CENTRAL IDEA PER EPISODE + INVARIANT CORE FLOW:
1. PHILOSOPHICAL HOOK (Ancestral Grounding)
2. THE FOCUSED UNCONSCIOUS TRAP
3. SPIRITUAL & FINANCIAL LIBERATION
4. HUMBLE PRACTICAL DISCIPLINE
5. SEAMLESS REFLECTIVE CLOSE (CTA stitched separately)
""".strip()


def _load_rag_directive_file() -> str:
    try:
        if _RAG_DIRECTIVE_PATH.is_file():
            text = _RAG_DIRECTIVE_PATH.read_text(encoding="utf-8").strip()
            if text:
                return text
    except OSError:
        pass
    return ""


MASTER_SCRIPTWRITER_DIRECTIVE: str = (
    _load_rag_directive_file() or _FALLBACK_SCRIPTWRITER_DIRECTIVE
)


def resolve_mei_duration_profile(duration_s: float | None = None) -> dict[str, int]:
    """Map target duration to words/frames/timing profile (90 / 105 / 120 s)."""
    d = float(duration_s if duration_s is not None else 105.0)
    if d <= 100.0:
        key = 90
    elif d <= 112.0:
        key = 105
    else:
        key = 120
    return dict(MEI_DURATION_PROFILES[key])


def apply_mei_word_budget_from_duration(duration_s: float | None = None) -> dict[str, int]:
    """Update module word-budget globals from the duration profile; return the profile."""
    global _NARRATION_WORDS_MIN, _NARRATION_WORDS_TARGET, _NARRATION_WORDS_MAX
    profile = resolve_mei_duration_profile(duration_s)
    _NARRATION_WORDS_MIN = int(profile["words_min"])
    _NARRATION_WORDS_TARGET = int(profile["words_target"])
    _NARRATION_WORDS_MAX = int(profile["words_max"])
    return profile


def master_scriptwriter_directive() -> str:
    """Canonical RAG / system directive (reload file when present)."""
    global MASTER_SCRIPTWRITER_DIRECTIVE
    loaded = _load_rag_directive_file()
    if loaded:
        MASTER_SCRIPTWRITER_DIRECTIVE = loaded
    return MASTER_SCRIPTWRITER_DIRECTIVE

# Cross-episode cliché blacklist — never recycle these fixed templates
_BANNED_NARRATION_PHRASES: tuple[str, ...] = (
    "citadel",
    "biomechanical cords",
    "sirens",
    "master mei offers no comfort",
    "relentless practice",
    "silent war for your essence",
    "techno-slave",
    "techno slave",
    "silent war for the mind",  # near-duplicate of banned essence line
)

_BANNED_PHRASE_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(re.escape(p), re.IGNORECASE) for p in _BANNED_NARRATION_PHRASES
)

# Third-person Master Mei → first-person rewrites (narrator IS Mei)
_THIRD_PERSON_MEI_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bMaster\s+Mei\s+offers\s+no\s+comfort\b", re.IGNORECASE), "Comfort is not the path"),
    (re.compile(r"\bMaster\s+Mei\s+does\s+not\s+offer\s+comfort\b", re.IGNORECASE), "Comfort is not the path"),
    (re.compile(r"\bMaster\s+Mei\s+demands?\b", re.IGNORECASE), "Consider"),
    (re.compile(r"\bMaster\s+Mei\s+teaches?\b", re.IGNORECASE), "The path teaches"),
    (re.compile(r"\bMaster\s+Mei\s+guides?\b", re.IGNORECASE), "The path guides"),
    (re.compile(r"\bMaster\s+Mei\s+trains?\b", re.IGNORECASE), "Discipline trains"),
    (re.compile(r"\bMaster\s+Mei\s+offers?\b", re.IGNORECASE), "There is offered"),
    (re.compile(r"\bMaster\s+Mei\s+says?\b", re.IGNORECASE), "The teaching says"),
    (re.compile(r"\bMaster\s+Mei\s+warns?\b", re.IGNORECASE), "The ancients warn"),
    (re.compile(r"\bFollow\s+Master\s+Mei\b", re.IGNORECASE), "Cultivate inner mastery"),
    (re.compile(r"\bMaster\s+Mei\b", re.IGNORECASE), "I"),
)

# Humble persona scrub — audience address + aggressive / boastful phrasing
_HUMBLE_VOICE_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bmy\s+disciples\b", re.IGNORECASE), "fellow seekers"),
    (re.compile(r"\bmeus\s+disc[ií]pulos\b", re.IGNORECASE), "fellow seekers"),
    (re.compile(r"\b(?:dear\s+)?(?:students|followers)\b", re.IGNORECASE), "fellow seekers"),
    (re.compile(r"\bI\s+demand\s+(?:that\s+)?you\b", re.IGNORECASE), "Consider how one might"),
    (re.compile(r"\bI\s+demand\b", re.IGNORECASE), "Consider"),
    (re.compile(r"\bDon't\s+do\s+that\b", re.IGNORECASE), "When the mind ceases to chase that"),
    (re.compile(r"\bDo\s+this\b", re.IGNORECASE), "Consider this quieter path"),
    (re.compile(r"\bI\s+studied\b", re.IGNORECASE), "The ancients observed"),
    (re.compile(r"\bI\s+have\s+studied\b", re.IGNORECASE), "The ancients observed"),
)

# Common TTS / LLM misspellings → correct form (CTA must never say "sovereianty")
_CTA_TYPO_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsovereianty\b", re.IGNORECASE), "sovereignty"),
    (re.compile(r"\bsoverignty\b", re.IGNORECASE), "sovereignty"),
    (re.compile(r"\bsoveriegnty\b", re.IGNORECASE), "sovereignty"),
    (re.compile(r"\bsovereignity\b", re.IGNORECASE), "sovereignty"),
)


def fix_cta_typos(text: str) -> str:
    """Correct known sovereignty / CTA misspellings before TTS or burn-in."""
    out = text or ""
    for pat, repl in _CTA_TYPO_FIXES:
        out = pat.sub(repl, out)
    return out


def narration_word_budget() -> tuple[int, int, int]:
    """Return ``(min, target, max)`` narration word counts for Master Mei VO."""
    return _NARRATION_WORDS_MIN, _NARRATION_WORDS_TARGET, _NARRATION_WORDS_MAX


def banned_narration_phrases() -> tuple[str, ...]:
    """Frozen cliché blacklist enforced in prompts and post-process scrubbing."""
    return _BANNED_NARRATION_PHRASES


def scrub_banned_narration_phrases(text: str) -> str:
    """Remove blacklisted cliché phrases from a narration body."""
    out = text or ""
    for pat in _BANNED_PHRASE_RES:
        out = pat.sub(" ", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    return out.strip()


def enforce_first_person_mei(text: str) -> str:
    """Rewrite third-person 'Master Mei' references into first-person POV."""
    out = text or ""
    for pat, repl in _THIRD_PERSON_MEI_FIXES:
        out = pat.sub(repl, out)
    # Clean awkward artifacts like "I 's path" after naive replacements
    out = re.sub(r"\bI\s+'s\b", "my", out, flags=re.IGNORECASE)
    out = re.sub(r"\bI\s+is\b", "I am", out, flags=re.IGNORECASE)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def enforce_humble_mei_voice(text: str) -> str:
    """Strip disciple-address, aggressive commands, and 'I studied' boasting."""
    out = text or ""
    for pat, repl in _HUMBLE_VOICE_FIXES:
        out = pat.sub(repl, out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def sanitize_mei_narration_body(text: str) -> str:
    """Apply POV lock + humble voice + phrase blacklist to narration (not CTA)."""
    out = scrub_banned_narration_phrases(text or "")
    out = enforce_first_person_mei(out)
    out = enforce_humble_mei_voice(out)
    return out


# Phrases that must NOT appear in narration body (CTA is a separate stitched block)
_FOLLOW_CTA_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?is)\s*master\s+your\s+mind\.?\s*follow\s+master\s+mei[^.?!]*[.?!]?",
    ),
    re.compile(r"(?is)\s*follow\s+master\s+mei[^.?!]*[.?!]?"),
    re.compile(r"(?is)\s*subscribe\s+to\s+master\s+mei[^.?!]*[.?!]?"),
    re.compile(r"(?is)\s*follow\s+for\s+(?:more|stoic|daily)[^.?!]*[.?!]?"),
    re.compile(
        r"(?is)\s*follow\s+master\s+mei\s*\|\s*mind\s+control[^.?!]*[.?!]?",
    ),
)


def _autonomous_pool() -> list[str]:
    """Thinker keys available for free rotation (excludes siren unless enabled)."""
    keys = [k for k, m in PHILOSOPHER_ROSTER.items() if not m.get("siren")]
    if is_siren_enabled() and "siren" in PHILOSOPHER_ROSTER:
        keys.append("siren")
    return keys or ["stoic"]


def _load_recent_theme_keys() -> list[str]:
    try:
        if _ROTATION_STATE_PATH.is_file():
            data = json.loads(_ROTATION_STATE_PATH.read_text(encoding="utf-8"))
            recent = data.get("recent") or []
            return [str(x) for x in recent if str(x) in PHILOSOPHER_ROSTER]
    except Exception:  # noqa: BLE001
        pass
    return []


def _remember_theme_key(key: str) -> None:
    try:
        recent = _load_recent_theme_keys()
        recent = [key] + [k for k in recent if k != key]
        recent = recent[:_RECENT_THEME_LIMIT]
        _ROTATION_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ROTATION_STATE_PATH.write_text(
            json.dumps({"recent": recent}, indent=2),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass


_TOPIC_THEME_CACHE: dict[str, str] = {}


def classify_episode_theme(topic: str) -> str:
    """Return ONE philosopher roster key (Single-Concept Episodic Rule).

    Keyword match when strong; otherwise autonomous hash rotation across the
    full Western/Eastern roster, avoiding the last few used thinkers.
    Cached per topic so title/script/description stay locked in one run.
    """
    cache_key = (topic or "").strip().lower()
    if cache_key in _TOPIC_THEME_CACHE:
        return _TOPIC_THEME_CACHE[cache_key]

    lo = cache_key
    scores = {
        key: sum(1 for kw in meta["keywords"] if kw in lo)
        for key, meta in PHILOSOPHER_ROSTER.items()
    }
    if not is_siren_enabled():
        scores["siren"] = -10**9
    recent = _load_recent_theme_keys()
    for rk in recent:
        if rk in scores:
            scores[rk] -= 3  # soft penalty — keep variety across episodes
    best = max(scores, key=scores.get)
    if scores[best] <= 0:
        # Autonomous rotation: topic hash into non-recent pool
        pool = [k for k in _autonomous_pool() if k not in recent] or _autonomous_pool()
        digest = hashlib.md5((topic or "mei").encode("utf-8")).hexdigest()
        best = pool[int(digest, 16) % len(pool)]
    _TOPIC_THEME_CACHE[cache_key] = best
    return best


def episode_theme_meta(topic: str) -> dict[str, Any]:
    key = classify_episode_theme(topic)
    meta = dict(PHILOSOPHER_ROSTER.get(key) or PHILOSOPHER_ROSTER["stoic"])
    meta["key"] = meta.get("key") or key
    _remember_theme_key(meta["key"])
    return meta


def episode_number_for_topic(topic: str, theme_key: str | None = None) -> int:
    """Stable episode index for SEO titles (hash-based, 100–999)."""
    key = theme_key or classify_episode_theme(topic)
    digest = hashlib.md5(f"{key}:{topic or ''}".encode("utf-8")).hexdigest()
    return 100 + (int(digest[:6], 16) % 900)


def strip_inline_follow_cta(text: str) -> str:
    """
    Remove follow/subscribe CTA sentences from narration body.

    The approved follow CTA is generated as a separate audio block and stitched
    after narration — never duplicated inside the script.
    """
    if not text:
        return ""
    clean = text
    for pat in _FOLLOW_CTA_PATTERNS:
        clean = pat.sub(" ", clean)
    clean = re.sub(r"\s{2,}", " ", clean).strip()
    clean = re.sub(r"\s+([,.;:])", r"\1", clean)
    return clean


def approved_cta_text() -> str:
    return fix_cta_typos(_APPROVED_CTA)


# ---------------------------------------------------------------------------
# Voice / TTS prep — pure spoken text only (no emotion tags, no SSML)
# ElevenLabs must never receive bracketed expressions or break markup.
# ---------------------------------------------------------------------------
_ANY_BRACKET_TAG = re.compile(r"\[[^\]]*\]")
_SSML_OR_XML = re.compile(r"<[^>]+>")
_EMOTION_WORDS = re.compile(
    r"\b(?:cold\s+chuckle|arrogant\s+scoff|deep\s+subtle\s+laugh|"
    r"cackles?|chuckles?|scoffs?|giggles?|whispers?|sighs?)\b",
    re.IGNORECASE,
)


def normalize_philosophical_breaks(text: str) -> str:
    """Normalize allowed SSML pauses to the canonical 1.5s break tag."""
    if not text:
        return ""
    out = re.sub(
        r"<\s*break\s+[^>]*/?\s*>",
        f" {_PHILOSOPHICAL_BREAK_TAG} ",
        text,
        flags=re.IGNORECASE,
    )
    # Cap consecutive / total breaks
    parts = re.split(re.escape(_PHILOSOPHICAL_BREAK_TAG), out, flags=re.IGNORECASE)
    if len(parts) - 1 <= _MAX_PHILOSOPHICAL_BREAKS:
        return re.sub(r"[ \t]{2,}", " ", out).strip()
    kept = parts[0]
    for i, chunk in enumerate(parts[1:], start=1):
        if i <= _MAX_PHILOSOPHICAL_BREAKS:
            kept += f" {_PHILOSOPHICAL_BREAK_TAG} "
        kept += chunk
    return re.sub(r"[ \t]{2,}", " ", kept).strip()


def inject_philosophical_breaks(text: str, *, max_breaks: int | None = None) -> str:
    """
    Ensure 2–4 strategic ``<break time="1.5s"/>`` pauses after key impacts.

    If the LLM already inserted breaks, normalize/cap them. Otherwise inject after
    sentences that land a philosophical or financial sovereignty beat.
    """
    cap = int(max_breaks if max_breaks is not None else _MAX_PHILOSOPHICAL_BREAKS)
    raw = text or ""
    if re.search(r"<\s*break\s+", raw, flags=re.IGNORECASE):
        return normalize_philosophical_breaks(raw)

    # Split on sentence endings while keeping delimiters
    pieces = re.split(r"(?<=[.!?])\s+", raw.strip())
    if len(pieces) < 3:
        return raw.strip()

    hit_re = re.compile(
        r"(?i)\b(?:"
        r"panopticon|mechanism|algorithm|impulse|gratification|propaganda|"
        r"sovereign|capital|wealth|focus|perception|discipline|antidote|"
        r"enslav|hijack|bleed|autonomy|money|energy"
        r")\b"
    )
    out: list[str] = []
    breaks = 0
    # Prefer mid/late hits — skip the very first sentence
    for i, sent in enumerate(pieces):
        out.append(sent)
        if (
            breaks < cap
            and i >= 1
            and i < len(pieces) - 1
            and hit_re.search(sent)
            and (breaks < 2 or i >= len(pieces) // 3)
        ):
            out.append(_PHILOSOPHICAL_BREAK_TAG)
            breaks += 1
    # Guarantee at least 2 pauses when script is long enough
    if breaks < 2 and len(pieces) >= 5:
        rebuilt: list[str] = []
        breaks = 0
        targets = {max(1, len(pieces) // 3), max(2, (2 * len(pieces)) // 3)}
        for i, sent in enumerate(pieces):
            rebuilt.append(sent)
            if i in targets and breaks < 2:
                rebuilt.append(_PHILOSOPHICAL_BREAK_TAG)
                breaks += 1
        out = rebuilt
    return normalize_philosophical_breaks(" ".join(out))


def prepare_mei_tts_text(text: str) -> str:
    """
    Normalize Master Mei narration for ElevenLabs.

    - Purges emotion/expression tags (``[cold chuckle]``, etc.)
    - PRESERVES strategic ``<break time="1.5s"/>`` pauses (SSML) for 100–120 s pacing
    - Strips ``[ACT N]`` / meta tokens so the model never reads brackets aloud
    - Enforces first-person POV + phrase blacklist
    """
    if not text:
        return ""
    clean = text

    # Protect legal break tags, then strip other XML/SSML
    _break_token = "<<<MEI_BREAK>>>"
    clean = re.sub(
        r"<\s*break\s+[^>]*/?\s*>",
        f" {_break_token} ",
        clean,
        flags=re.IGNORECASE,
    )
    clean = _SSML_OR_XML.sub(" ", clean)
    clean = clean.replace(_break_token, _PHILOSOPHICAL_BREAK_TAG)

    # Mandatory: strip ALL bracketed tags so TTS never reads "[stoic]" aloud
    clean = re.sub(r"\[.*?\]", "", clean, flags=re.DOTALL)
    # Curly / code-fence markers (never read aloud) — keep break tags already restored
    clean = re.sub(r"\{[^}]*\}", " ", clean)
    clean = re.sub(r"`{1,3}[^`]*`{1,3}", " ", clean)
    clean = _EMOTION_WORDS.sub(" ", clean)

    clean = re.sub(r"[ \t]{2,}", " ", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    clean = re.sub(r"\s*\.\.\.\s*", " ... ", clean)
    clean = re.sub(r"(?:\s*\.\.\.\s*){2,}", " ... ", clean)
    # Drop LLM artifacts that would be spoken aloud
    clean = re.sub(r">{2,}", " ", clean)
    clean = re.sub(r"[-_]{3,}", " ", clean)
    # POV lock + cliché blacklist (narrator IS Mei; CTA stays separate)
    # Temporarily shield breaks from third-person rewrites
    clean = clean.replace(_PHILOSOPHICAL_BREAK_TAG, f" {_break_token} ")
    clean = sanitize_mei_narration_body(clean)
    clean = clean.replace(_break_token, _PHILOSOPHICAL_BREAK_TAG)
    clean = inject_philosophical_breaks(clean)
    return clean.strip()


def mei_voice_prompt_block(*, duration_s: float | None = None) -> str:
    """Inject MASTER SCRIPTWRITER directive + POV/depth/pacing into LLM prompts."""
    profile = apply_mei_word_budget_from_duration(duration_s)
    blacklist = ", ".join(f'"{p}"' for p in _BANNED_NARRATION_PHRASES[:7])
    pov = (
        "POV LOCK (IMMUTABLE): You ARE Master Mei. Write in FIRST PERSON with humble presence "
        "('I', 'we as seekers', contemplative observation). "
        "NEVER address the audience as 'my disciples', 'students', or 'followers'. "
        "NEVER aggressive commands ('do this', 'don't do that', 'I demand…'). "
        "NEVER say 'I studied X' — weave philosophers organically. "
        "TONE: deep humility, respect, ancestral tranquility. "
        "Prefer: 'Consider how…', 'The ancients observed that…', 'When the mind ceases to fight…'. "
        "ANTI-INSTRUCTIONAL: no checklists, no 'Step 1', no how-to manuals."
    )
    depth = (
        "FOCUSED NARRATIVE ENGINE — ONE central idea per episode. Follow the invariant core flow: "
        "(1) PHILOSOPHICAL HOOK — ancestral grounding on how attention is captured by noise; "
        "(2) FOCUSED UNCONSCIOUS TRAP — deepen the single chosen theme of modern seduction; "
        "(3) SPIRITUAL & FINANCIAL LIBERATION — resistance frees spirit and builds sovereignty; "
        "(4) HUMBLE PRACTICAL DISCIPLINE — one quiet mind-body practice; "
        "(5) SEAMLESS REFLECTIVE CLOSE — calm invitation to inner mastery (CTA stitched separately). "
        "CINEMATIC ALLEGORY + metaphor only — never instructional self-help."
    )
    diversity = (
        f"PHRASE BLACKLIST (never use): {blacklist}. "
        "Every script must invent a UNIQUE allegory for ONE focal idea — "
        "never cram multiple philosophies into one episode."
    )
    words = (
        f"VOICE PACING: Humble, cinematic, deliberate (0.86× TTS). "
        f"After key philosophical impacts insert exactly: {_PHILOSOPHICAL_BREAK_TAG} "
        f"(no other SSML / emotion tags). "
        f"STRICT WORD COUNT for {profile['target_s']}s target: "
        f"{profile['words_min']}–{profile['words_max']} words MAX "
        f"(target ~{profile['words_target']}). Frames={profile['frames']}."
    )
    directive = MASTER_SCRIPTWRITER_DIRECTIVE + "\n\n"
    if not is_v3_active():
        return (
            directive +
            "CONTENT PHILOSOPHY (V2 ACTION LAYOUT):\n"
            f"1. {pov}\n"
            f"2. {depth}\n"
            "3. THREAT — dopamine, scrolling, algorithms, trap beauty, soft comfort.\n"
            "4. SOLUTION — quiet self-mastery, patient discipline, strategic stillness.\n"
            f"5. {diversity}\n"
            "6. TONE — humble, solemn, ancestral; NEVER wellness fluff or hustle-bro clichés.\n"
            f"{words}\n"
            "FORBIDDEN: emotion tags / tip lists / third-person Master Mei / "
            "'my disciples' / aggressive commands "
            "(allowed pacing only: break time 1.5s after key impacts)."
        )
    if is_v4_active():
        return (
            directive +
            "ENGINE: V4.0_FOCUSED_NARRATIVE_ARC (IMMUTABLE WHILE ACTIVE):\n"
            f"1. {pov}\n"
            "2. ONE FOCAL IDEA — a coherent story around a single thinker/concept. "
            "NEVER loose tip lists or multi-topic manuals.\n"
            "3. MANDATORY PHILOSOPHICAL AUTHORITY — credit ONE major Western or Eastern thinker "
            "by name in the hook (Stoicism / Taoism / Zen / Seneca / Marcus Aurelius / Lao Tzu / "
            "Miyamoto Musashi / Epictetus / Plato / Sun Tzu / Foucault).\n"
            f"4. {depth}\n"
            "5. THE MACHINE — sensory numbness + instant gratification + propaganda "
            "hijack perception; spirit and capital are the spoils.\n"
            "6. RESOLUTION — a humble invitation for fellow seekers toward focus, spiritual "
            "freedom, and financial sovereignty.\n"
            f"7. {diversity}\n"
            "TONE: humble, accessible, ancestral tranquility — spoken as ME among seekers.\n"
            f"{words}\n"
            "FORBIDDEN: emotion tags, Follow/Subscribe lines, tip-list cadence, "
            "third-person Master Mei, 'my disciples', aggressive commands, 'I studied X', "
            "generic pep-talks with zero named philosopher "
            "(allowed pacing only: break time 1.5s after key impacts)."
        )
    return (
        directive +
        "ENGINE: V3.0_FOCUSED_NARRATIVE_ARC (IMMUTABLE WHILE ACTIVE):\n"
        f"1. {pov}\n"
        "2. SINGLE-CONCEPT RULE — ONE thinker and ONE concept per video.\n"
        f"3. {depth}\n"
        "4. RESOLUTION — humble discipline → spiritual freedom AND financial sovereignty.\n"
        f"5. {diversity}\n"
        f"{words}\n"
        "FORBIDDEN: emotion tags / tip lists / third-person Master Mei "
        "(allowed pacing only: break time 1.5s after key impacts)."
    )


def build_seo_title(topic: str, *, theme_key: str | None = None) -> str:
    """
    High-RPM Shorts title.

    V4/V3: [Philosopher]'s [Concept]: How To Escape The Digital Matrix | Master Mei Ep. [X]
    """
    key = theme_key or classify_episode_theme(topic)
    meta = PHILOSOPHER_ROSTER.get(key) or EPISODE_THEMES.get(key) or PHILOSOPHER_ROSTER["stoic"]
    if not is_v3_active():
        hook = meta.get("seo_hook") or meta.get("label", "Stoic Sovereignty")
        tag = meta.get("hashtag", "Stoicism")
        return f"{hook} | Master Mei #{tag}"[:100]

    philosopher = meta.get("philosopher") or "Master Mei"
    concept = meta.get("concept") or meta.get("label") or "Stoic Sovereignty"
    ep = episode_number_for_topic(topic, theme_key=meta.get("key") or key)
    title = (
        f"{philosopher}'s {concept}: How To Escape The Digital Matrix "
        f"| Master Mei Ep. {ep}"
    )
    return title[:100]


def build_seo_keywords(topic: str, *, theme_key: str | None = None) -> str:
    """Hashtag line for Shorts / Reels / TikTok."""
    del topic, theme_key
    if is_v4_active():
        return (
            "#Stoicism #SunTzu #FinancialFreedom #MasterMei #Mindset "
            "#Cyberpunk #MartialArts #DigitalDetox"
        )
    return (
        "#Stoicism #FinancialFreedom #MasterMei #Mindset #Cyberpunk "
        "#MartialArts #DigitalDetox"
    )


def build_seo_description(
    topic: str,
    *,
    caption: str = "",
    theme_key: str | None = None,
) -> str:
    """High-RPM description naming the philosopher + concept."""
    key = theme_key or classify_episode_theme(topic)
    meta = PHILOSOPHER_ROSTER.get(key) or EPISODE_THEMES.get(key) or PHILOSOPHER_ROSTER["stoic"]
    philosopher = meta.get("philosopher") or "Master Mei"
    concept = meta.get("concept") or meta.get("label") or "Stoic Sovereignty"
    body = (caption or "").strip()
    if len(body) > 400:
        body = body[:397].rstrip() + "…"

    if is_v4_active():
        parts = [
            (
                f"Master Mei breaks down {philosopher}'s strategy on {concept}. "
                "Discover how the Machine seduces your focus and how martial discipline "
                "builds true financial sovereignty."
            ),
            f"Topic: {topic.strip()}",
        ]
        if body:
            parts.extend(["", body])
        parts.extend(["", build_seo_keywords(topic, theme_key=meta.get("key") or key)])
        return "\n".join(parts)

    if is_v3_active():
        parts = [
            (
                f"Master Mei exposes {philosopher}'s theory on {concept}. "
                "Discover how the Machine seduces your mind through cheap gratification "
                "and how martial discipline builds true financial and spiritual freedom."
            ),
            f"Topic: {topic.strip()}",
        ]
        if body:
            parts.extend(["", body])
        parts.extend(["", build_seo_keywords(topic, theme_key=meta.get("key") or key)])
        return "\n".join(parts)

    principles = ", ".join(meta.get("principles") or [])
    parts = [
        f"Master Mei | {meta.get('label', concept)}",
        f"Lesson focus: {meta.get('core', '')}",
        f"Principles: {principles} — Stoicism, Bushido, Financial Sovereignty.",
        f"Topic: {topic.strip()}",
    ]
    if body:
        parts.extend(["", body])
    parts.extend([
        "",
        "Subscribe to Master Mei | Mind Control for cold discipline, "
        "classical strategy, and true wealth.",
        build_seo_keywords(topic, theme_key=meta.get("key") or key),
    ])
    return "\n".join(parts)


# 3-Act cinematic arc — "The Art of War Against the Neural Matrix"
# Act I The Trap ~30% | Act II The Forge ~40% | Act III Liberation ~30%
_STORY_ACT1_FRAC: float = 0.30
_STORY_ACT2_FRAC: float = 0.70  # cumulative end of Act II


def three_act_bounds(n_acts: int) -> tuple[int, int, int]:
    """
    Map N image beats onto the 3-Act Neural Matrix war story.

    Returns ``(end_act1, end_act2, n)`` as exclusive upper bounds:
      Act I   The Trap        indices ``[0, end_act1)``
      Act II  The Forge       indices ``[end_act1, end_act2)``  — Master Mei active
      Act III Liberation      indices ``[end_act2, n)``
    """
    n = max(3, int(n_acts))
    end1 = max(1, round(n * _STORY_ACT1_FRAC))
    end2 = max(end1 + 1, round(n * _STORY_ACT2_FRAC))
    if end2 >= n:
        end2 = n - 1
    if end1 >= end2:
        end1 = max(1, end2 - 1)
    return end1, end2, n


def four_act_bounds(n_acts: int) -> tuple[int, int, int, int]:
    """
    Compatibility shim — maps legacy 4-slot callers onto the 3-Act arc.

    Returns ``(a1, a2, a3, n)`` where a1=end Trap, a2=mid Forge, a3=end Forge.
    """
    end1, end2, n = three_act_bounds(n_acts)
    mid_forge = max(end1 + 1, (end1 + end2) // 2)
    return end1, mid_forge, end2, n


def story_phase_for_act(act_index: int, n_acts: int) -> str:
    """Return ``trap`` | ``forge`` | ``liberation`` for a zero-based act index."""
    end1, end2, n = three_act_bounds(n_acts)
    i = max(0, min(int(act_index), n - 1))
    if i < end1:
        return "trap"
    if i < end2:
        return "forge"
    return "liberation"


def _five_beat_bounds(n_acts: int) -> tuple[int, int, int, int, int]:
    """Return 1-based inclusive end act for beats 1..5 (~15/30/25/20/10%)."""
    n = max(1, int(n_acts))
    ends = [
        max(1, int(round(n * 0.15))),
        max(1, int(round(n * 0.45))),
        max(1, int(round(n * 0.70))),
        max(1, int(round(n * 0.90))),
        n,
    ]
    # Enforce strictly increasing ends when n allows
    for i in range(1, 5):
        ends[i] = max(ends[i], min(n, ends[i - 1] + (1 if n >= 5 else 0)))
    ends[4] = n
    return ends[0], ends[1], ends[2], ends[3], ends[4]


def _build_v2_three_act_script_instructions(n_acts: int, episode: dict[str, Any]) -> str:
    """V2 fallback — focused single-idea narrative with humble voice."""
    b1, b2, b3, b4, b5 = _five_beat_bounds(n_acts)
    label = episode.get("label", "The Art of War Against the Neural Matrix")
    core = episode.get("core", "")
    blacklist = ", ".join(f'"{p}"' for p in _BANNED_NARRATION_PHRASES[:7])
    return f"""
POV LOCK: You ARE Master Mei — FIRST PERSON with humble ancestral presence.
NEVER address the audience as "my disciples", "students", or "followers".
NEVER aggressive commands ("I demand", "do this", "don't do that").
NEVER "I studied X". NEVER third-person "Master Mei" in the script body.

SINGLE FOCAL IDEA (IMMUTABLE):
This entire video explores ONE idea only: "{label}".
Stay on THIS theme: {core}
Do NOT cram multiple philosophies into one episode.

TONE: deep humility, respect, ancestral tranquility.
Prefer: "Consider how…", "The ancients observed that…", "When the mind ceases to fight…".
AUDIENCE: Western / US men seeking self-mastery — addressed as fellow human seekers.
NEVER wellness fluff, hustle-bro clichés, or therapy-speak.
PHRASE BLACKLIST: {blacklist}. Invent a UNIQUE allegory for this ONE idea.

INVARIANT CORE FLOW (map onto acts):

1. PHILOSOPHICAL HOOK ([ACT 1]–[ACT {b1}]):
   Ancestral grounding — how attention and consciousness are captured by external noise.
2. FOCUSED UNCONSCIOUS TRAP ([ACT {b1 + 1}]–[ACT {b2}]):
   Deepen the single theme; modern seductions enslave before physical chains form.
   VISUAL SYNC: enslaved masses — gaunt, passive, plugged into glowing neural feeds.
3. SPIRITUAL & FINANCIAL LIBERATION ([ACT {b2 + 1}]–[ACT {b3}]):
   Resistance frees spirit AND builds long-term financial sovereignty.
4. HUMBLE PRACTICAL DISCIPLINE ([ACT {b3 + 1}]–[ACT {b4}]):
   One quiet mind-body practice (breath, stillness, or stance) — never an aggressive command.
   VISUAL SYNC: high-altitude nature / open-air shrine; martial motion if shown.
5. SEAMLESS REFLECTIVE CLOSE ([ACT {b4 + 1}]–[ACT {b5}]):
   Calm invitation to cultivate inner mastery. DO NOT speak Follow/Subscribe.
   VISUAL SYNC: seekers / trainees standing unbroken as illusion collapses.

STRICT BAN: emotion tags, "Follow Master Mei", "Subscribe", "my disciples", "I demand",
"I studied", third-person "Master Mei".
Allowed pacing only: <break time="1.5s"/> after key impacts.
Each [ACT N] ≈ one spoken beat. STRICT TOTAL: {_NARRATION_WORDS_MIN}–{_NARRATION_WORDS_MAX} words
(target ~{_NARRATION_WORDS_TARGET}) for 100–120 s at 0.86× TTS + break pauses.
""".strip()


def build_three_act_script_instructions(n_acts: int, episode: dict[str, Any]) -> str:
    """Prompt block — focused 5-beat narrative (V4 / V3 / V2 rollback)."""
    if not is_v3_active():
        return _build_v2_three_act_script_instructions(n_acts, episode)

    b1, b2, b3, b4, b5 = _five_beat_bounds(n_acts)
    philosopher = episode.get("philosopher") or "Marcus Aurelius"
    concept = episode.get("concept") or episode.get("label") or "Stoic Sovereignty"
    label = episode.get("label") or f"{philosopher}'s {concept}"
    core = episode.get("core", "")
    seduction = scrub_banned_narration_phrases(
        episode.get("seduction") or "cheap dopamine and soft digital comfort"
    )
    practice = scrub_banned_narration_phrases(
        episode.get("practice") or "a quiet breath rhythm and still martial stance"
    )
    is_siren = bool(episode.get("siren")) and is_siren_enabled()
    cite = is_explicit_philosopher_citations_enabled() or is_didactic_philosophy_enabled()
    western = is_western_majority_masses_enabled()

    mass_visual = (
        "VISUAL SYNC: ethnically diverse enslaved masses, PREDOMINANTLY Western/Occidental "
        "(Caucasian, Hispanic, Black, mixed) — attractive men and stylish Western/Asian "
        "women hooked into holographic feeds and glowing VR cords, seduced by cheap "
        "digital dopamine. Master Mei remains the sole Oriental/Asian ancestral wisdom figure. "
        "NO Master Mei in the trap beats. NO idle portraits."
        if western
        else (
            "VISUAL SYNC: diverse crowds — men, women, youth — trapped in glowing digital "
            "cages / holographic feeds. NO Master Mei. NO idle portraits."
        )
    )
    if is_siren:
        mass_visual = (
            "SPECIAL SIREN VISUALS: stunning cybernetic femme-fatale with glowing mechanical "
            "eye accents extending digital temptation; Western-majority crowds paralyzed nearby. "
            + mass_visual
        )

    blacklist = ", ".join(f'"{p}"' for p in _BANNED_NARRATION_PHRASES[:7])

    if is_v4_active():
        hook_formula = (
            f'FIRST-PERSON reflective directive (adapt — weave {philosopher} organically; '
            f'NEVER say "I studied X"): '
            f'"Consider how {philosopher} named what so many ignore — {concept}. '
            f"When attention is handed to the Machine through {seduction}, consciousness "
            f'itself becomes easy prey… a quiet tax on the future estate."'
            if cite
            else "In FIRST PERSON, open with ancestral/philosophical grounding — humble, reflective."
        )
        trap_formula = (
            f'FIRST-PERSON reflective directive (adapt — deepen ONLY {concept}): '
            f'"The ancients observed that {seduction} binds the mind long before any chain '
            f'touches the body. This is the unconscious trap of {concept}."'
            if cite
            else "In FIRST PERSON, deepen the single focal trap without tip lists."
        )
        liberation_formula = (
            "FIRST-PERSON reflective directive (adapt): "
            '"When the mind ceases to fight for every easy promise, spirit and capital '
            'begin to gather again — true wealth follows disciplined attention."'
        )
        discipline_formula = (
            f'FIRST-PERSON humble practice directive (adapt — ONE quiet tool only): '
            f'"There is a simpler forge — {practice}. Not a command… a rhythm the body '
            f'can remember when comfort calls."'
            if cite
            else "Offer ONE quiet mind-body practice humbly — never an aggressive command."
        )
        close_formula = (
            "FIRST-PERSON reflective close (adapt): "
            '"Fellow seekers — cultivate inner mastery in the quiet. The styled invitation '
            'awaits beyond these words." '
            "(Do NOT speak Follow/Subscribe — CTA is stitched separately.)"
        )
        engine_line = "ENGINE: V4.0_FOCUSED_NARRATIVE_ARC"
    else:
        hook_formula = (
            f'FIRST-PERSON reflective formula (adapt): '
            f'"Consider how we live inside {philosopher}\'s {concept} without noticing. '
            f'The Machine seduces through {seduction}."'
            if cite
            else "In FIRST PERSON, introduce the trap with humble ancestral calm."
        )
        trap_formula = (
            f'FIRST-PERSON reflective formula (adapt): '
            f'"The ancients observed that {concept} begins as comfort… then becomes a cage."'
            if cite
            else "In FIRST PERSON, deepen the single unconscious trap."
        )
        liberation_formula = (
            "FIRST-PERSON reflective formula (adapt): "
            '"When easy promises are refused, spirit and long-term sovereignty can grow."'
        )
        discipline_formula = (
            f'FIRST-PERSON humble formula (adapt): '
            f'"One quiet practice remains — {practice}."'
            if cite
            else "Offer ONE quiet mind-body practice humbly."
        )
        close_formula = (
            "FIRST-PERSON reflective close (adapt): "
            '"Cultivate inner mastery. Do not speak Follow/Subscribe."'
        )
        engine_line = "ENGINE: V3.0_FOCUSED_NARRATIVE_ARC"

    return f"""
{engine_line}

{MASTER_SCRIPTWRITER_DIRECTIVE}

POV LOCK (IMMUTABLE): The narrator IS Master Mei — humble ancestral presence.
Write in FIRST PERSON with reflective phrasing ("Consider how…", "The ancients observed…",
"When the mind ceases to fight…").
PROHIBITED audience address: "my disciples", "meus discípulos", "students", "followers".
PROHIBITED: aggressive commands ("do this", "don't do that", "I demand…"), "I studied X",
third-person "Master Mei …", "Follow Master Mei".
(CTA audio/text is stitched separately AFTER narration — never inside the script.)
Weave {philosopher} / {concept} organically without academic boasting.

SINGLE FOCAL IDEA (IMMUTABLE):
This entire video is ONE coherent cinematic story about "{label}".
ONE thinker only: {philosopher}. ONE concept only: {concept}.
Never mix philosophies. Never stuff every topic into one episode.
Philosophical spine: {core}
NAME {philosopher} / {concept} aloud in the hook without academic framing.

INVARIANT CORE FLOW (maps to MASTER MEI NARRATIVE ENGINE — every episode):
1) PHILOSOPHICAL HOOK — ancestral grounding: attention/consciousness captured by external noise.
2) FOCUSED UNCONSCIOUS TRAP — deepen ONLY {philosopher}'s {concept}; modern seductions
   ({seduction}) enslave before physical chains form.
3) SPIRITUAL & FINANCIAL LIBERATION — resistance frees spirit AND builds financial sovereignty.
4) HUMBLE PRACTICAL DISCIPLINE — ONE quiet mind-body practice ({practice}).
5) SEAMLESS REFLECTIVE CLOSE — calm invitation to inner mastery (CTA displayed separately).
ANTI-INSTRUCTIONAL: no checklists, no "Step 1", no tip lists, no multi-topic manuals.
CINEMATIC ALLEGORY + metaphor only. Earn the wealth conclusion — never slap "get rich".
Invent a UNIQUE allegory this episode — do not recycle prior episode templates.

PACING: After 2–4 key philosophical impacts insert exactly: {_PHILOSOPHICAL_BREAK_TAG}
(no other SSML / emotion tags).

PHRASE BLACKLIST (NEVER USE): {blacklist}.

TONE: Deep humility, respect, ancestral tranquility — grounded and contemplative.
AUDIENCE: Western / US men — fellow human seekers of self-mastery and financial sovereignty.
NEVER tip lists, wellness fluff, hustle-bro clichés, or therapy-speak.

5-BEAT STORY ARC (MANDATORY — organic narrative flow around ONE focal idea):

1. PHILOSOPHICAL HOOK ([ACT 1]–[ACT {b1}]):
   Ancestral / philosophical grounding on how attention is captured by noise.
   {hook_formula}

2. FOCUSED UNCONSCIOUS TRAP ([ACT {b1 + 1}]–[ACT {b2}]):
   Explore {concept} deeply — unconscious enslavement via modern seduction.
   {trap_formula}
   {mass_visual}

3. SPIRITUAL & FINANCIAL LIBERATION ([ACT {b2 + 1}]–[ACT {b3}]):
   Connect the theme to true wealth, adaptability, and sovereignty.
   {liberation_formula}

4. HUMBLE PRACTICAL DISCIPLINE ([ACT {b3 + 1}]–[ACT {b4}]):
   One quiet practice forging internal resistance — never an aggressive command.
   Prefer high-altitude epic nature (~70%) or open-air mountain shrines (~30%).
   NEVER default to generic indoor temple halls. Templates are REFERENCES only.
   {discipline_formula}
   VISUAL SYNC: Master Mei in dynamic outdoor setting; if training appears, 2–5
   trainees in motion (on-screen figures — NEVER address the audience as disciples).
   HIGH MOTION when martial. NO idle monk close-ups.

5. SEAMLESS REFLECTIVE CLOSE ([ACT {b4 + 1}]–[ACT {b5}]):
   Calm humble invitation to cultivate inner mastery.
   {close_formula}
   VISUAL SYNC: seekers / trainees standing unbroken as illusion collapses.
   DO NOT speak Follow/Subscribe (styled CTA is stitched/displayed separately).

CHARACTER RULES (mental picture only — spoken prose, no stage directions):
- Enslaved masses = Western-majority diverse genders, holographic feeds, VR tethers.
- On-screen trainees = active martial motion (never call the audience "disciples").
- I (Master Mei) = humble Oriental/Asian ancestral guide in dynamic outdoor settings.

STRICT BAN in narration: emotion tags, SSML, "Follow Master Mei", "Subscribe", tip lists,
"my disciples", "I demand", "I studied", third-person "Master Mei", blacklisted clichés.
Each [ACT N] ≈ one spoken beat. STRICT TOTAL: {_NARRATION_WORDS_MIN}–{_NARRATION_WORDS_MAX} words MAX
(target ~{_NARRATION_WORDS_TARGET}) at 0.86× TTS + break pauses (duration profile).
""".strip()


def build_four_act_script_instructions(n_acts: int, episode: dict[str, Any]) -> str:
    """Legacy name — routes to active 3-Act engine (V4 / V3 / V2 rollback)."""
    return build_three_act_script_instructions(n_acts, episode)
