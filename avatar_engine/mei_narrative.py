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
    from pages_config.master_mei.system_config import (
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
    Path(__file__).resolve().parents[1] / "outputs" / "master_mei" / ".philosopher_rotation.json"
)
_RECENT_THEME_LIMIT = 4

_APPROVED_CTA = (
    "Master your mind. Follow Master Mei to reclaim your sovereignty."
)

# Narration word budget @ 0.92× TTS → ~80–100 s voiceover body (CTA separate)
_NARRATION_WORDS_MIN: int = 190
_NARRATION_WORDS_TARGET: int = 210
_NARRATION_WORDS_MAX: int = 230

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
    (re.compile(r"\bMaster\s+Mei\s+offers\s+no\s+comfort\b", re.IGNORECASE), "I offer no comfort"),
    (re.compile(r"\bMaster\s+Mei\s+does\s+not\s+offer\s+comfort\b", re.IGNORECASE), "I do not offer comfort"),
    (re.compile(r"\bMaster\s+Mei\s+demands?\b", re.IGNORECASE), "I demand"),
    (re.compile(r"\bMaster\s+Mei\s+teaches?\b", re.IGNORECASE), "I teach"),
    (re.compile(r"\bMaster\s+Mei\s+guides?\b", re.IGNORECASE), "I guide"),
    (re.compile(r"\bMaster\s+Mei\s+trains?\b", re.IGNORECASE), "I train"),
    (re.compile(r"\bMaster\s+Mei\s+offers?\b", re.IGNORECASE), "I offer"),
    (re.compile(r"\bMaster\s+Mei\s+says?\b", re.IGNORECASE), "I say"),
    (re.compile(r"\bMaster\s+Mei\s+warns?\b", re.IGNORECASE), "I warn"),
    (re.compile(r"\bFollow\s+Master\s+Mei\b", re.IGNORECASE), "Walk my path"),
    (re.compile(r"\bMaster\s+Mei\b", re.IGNORECASE), "I"),
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


def sanitize_mei_narration_body(text: str) -> str:
    """Apply POV lock + phrase blacklist to narration (not the separate CTA)."""
    out = scrub_banned_narration_phrases(text or "")
    out = enforce_first_person_mei(out)
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


def prepare_mei_tts_text(text: str) -> str:
    """
    Normalize Master Mei narration for ElevenLabs — pure spoken text only.

    - Purges ALL emotion/expression tags (``[cold chuckle]``, ``[arrogant scoff]``, etc.)
    - Converts SSML/XML ``<break …>`` into spoken ellipsis pauses ``...``
    - Strips ``[ACT N]`` / meta tokens so the model never reads brackets aloud
    - Preserves deliberate ``...`` and short-sentence punctuation for stoic pacing
    """
    if not text:
        return ""
    clean = text

    # SSML / XML breaks → dramatic ellipsis (punctuation-only pacing)
    clean = re.sub(
        r"<\s*break\s+[^>]*/?\s*>",
        " ... ",
        clean,
        flags=re.IGNORECASE,
    )
    clean = _SSML_OR_XML.sub(" ", clean)

    # Mandatory: strip ALL bracketed tags so TTS never reads "[stoic]" aloud
    clean = re.sub(r"\[.*?\]", "", clean, flags=re.DOTALL)
    # Curly / code-fence markers (never read aloud)
    clean = re.sub(r"\{[^}]*\}", " ", clean)
    clean = re.sub(r"`{1,3}[^`]*`{1,3}", " ", clean)
    # Safety: drop any leftover spoken emotion words the model wrote outside brackets
    clean = _EMOTION_WORDS.sub(" ", clean)

    # Collapse whitespace; keep ellipses intact for pacing
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    clean = re.sub(r"\s*\.\.\.\s*", " ... ", clean)
    clean = re.sub(r"(?:\s*\.\.\.\s*){2,}", " ... ", clean)
    # POV lock + cliché blacklist (narrator IS Mei; CTA stays separate)
    clean = sanitize_mei_narration_body(clean)
    return clean.strip()


def mei_voice_prompt_block() -> str:
    """Inject into script LLM prompts — POV, depth, pacing, zero sound tags."""
    blacklist = ", ".join(f'"{p}"' for p in _BANNED_NARRATION_PHRASES[:7])
    pov = (
        "POV LOCK (IMMUTABLE): You ARE Master Mei. Write ONLY in FIRST PERSON "
        "('I', 'My disciples', 'I demand', 'My path'). "
        "NEVER refer to 'Master Mei' in the third person inside the script body."
    )
    depth = (
        "PHILOSOPHICAL DEPTH: Introduce an original concept from Stoicism, Taoism, Zen, "
        "Seneca, Marcus Aurelius, Lao Tzu, Miyamoto Musashi, and/or Epictetus. "
        "Explicitly break down how sensory numbness, instant gratification, and propaganda "
        "hijack human perception to enslave the spirit and destroy financial/spiritual sovereignty."
    )
    diversity = (
        f"PHRASE BLACKLIST (never use): {blacklist}. "
        "Every script must invent a UNIQUE allegory or lesson on discipline and financial freedom — "
        "no recycled template lines across episodes."
    )
    words = (
        f"VOICE PACING: Stoic, cinematic. Ellipses between heavy truths. "
        f"STRICT WORD COUNT: {_NARRATION_WORDS_MIN}–{_NARRATION_WORDS_MAX} words "
        f"(target ~{_NARRATION_WORDS_TARGET}) for 80–100 s at 0.92× TTS."
    )
    if not is_v3_active():
        return (
            "CONTENT PHILOSOPHY (V2 ACTION LAYOUT):\n"
            f"1. {pov}\n"
            f"2. {depth}\n"
            "3. THREAT — dopamine, scrolling, algorithms, trap beauty, soft comfort.\n"
            "4. SOLUTION — self-mastery, brutal physical discipline, strategic patience.\n"
            f"5. {diversity}\n"
            "6. TONE — authoritative, solemn; NEVER wellness fluff or hustle-bro clichés.\n"
            f"{words}\n"
            "FORBIDDEN: emotion tags, SSML, bracketed sound directions, third-person 'Master Mei'."
        )
    if is_v4_active():
        return (
            "ENGINE: V4.0_CINEMATIC_STORY_ARC (IMMUTABLE WHILE ACTIVE):\n"
            f"1. {pov}\n"
            "2. COHERENT STORY — Beginning, Middle, End. NEVER loose tip lists. "
            "Narrative must flow as one original allegory.\n"
            "3. MANDATORY PHILOSOPHICAL AUTHORITY — credit ONE major Western or Eastern thinker "
            "by name in Act I (Stoicism / Taoism / Zen / Seneca / Marcus Aurelius / Lao Tzu / "
            "Miyamoto Musashi / Epictetus / Plato / Sun Tzu).\n"
            f"4. {depth}\n"
            "5. THE MACHINE — sensory numbness + instant gratification + propaganda "
            "hijack perception; spirit and capital are the spoils.\n"
            "6. RESOLUTION — I lead disciples from digital bondage to focus, spiritual freedom, "
            "and financial sovereignty.\n"
            f"7. {diversity}\n"
            "TONE: highly stern, accessible, cinematic authority — spoken as ME.\n"
            f"{words}\n"
            "FORBIDDEN: emotion tags, SSML, Follow/Subscribe lines, tip-list cadence, "
            "third-person 'Master Mei', generic pep-talks with zero named philosopher."
        )
    return (
        "ENGINE: V3.0_DIDACTIC_STOIC_STORY_ARC (IMMUTABLE WHILE ACTIVE):\n"
        f"1. {pov}\n"
        "2. SINGLE-CONCEPT RULE — ONE thinker and ONE concept per video.\n"
        f"3. {depth}\n"
        "4. RESOLUTION — my discipline path → spiritual freedom AND financial wealth.\n"
        f"5. {diversity}\n"
        f"{words}\n"
        "FORBIDDEN: emotion tags, SSML, Follow/Subscribe lines, third-person 'Master Mei'."
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


def _build_v2_three_act_script_instructions(n_acts: int, episode: dict[str, Any]) -> str:
    """V2 fallback — action-focused Neural Matrix war layout."""
    end1, end2, n = three_act_bounds(n_acts)
    label = episode.get("label", "The Art of War Against the Neural Matrix")
    core = episode.get("core", "")
    blacklist = ", ".join(f'"{p}"' for p in _BANNED_NARRATION_PHRASES[:7])
    return f"""
POV LOCK: You ARE Master Mei — FIRST PERSON only ("I", "My disciples", "I demand").
NEVER third-person "Master Mei" in the script body.

SINGLE-TOPIC RULE (IMMUTABLE):
This entire video is ONE cohesive cinematic lesson: "{label}".
Philosophical spine: THE ART OF WAR AGAINST THE NEURAL MATRIX.
Stay on THIS theme: {core}

TONE: cinematic battle-cry — authoritative, solemn, uncompromising.
Ancient wisdom meeting dystopian tech critique.
AUDIENCE: Western / US men seeking self-mastery and tech-critique.
NEVER wellness fluff, hustle-bro clichés, or therapy-speak.
PHRASE BLACKLIST: {blacklist}. Invent a UNIQUE allegory every episode.

3-ACT STORY ARC (MANDATORY — write as ONE continuous war story, not a list of tips):

- ACT I — THE TRAP / DIGITAL SLAVERY ([ACT 1]–[ACT {end1}] | ~30%):
  I expose human minds consumed by the tech matrix — false comfort and glowing propaganda.
  VISUAL SYNC: enslaved masses — gaunt, passive, plugged into glowing neural feeds.
  NO idle monk portraits.

- ACT II — THE FORGE / MY TRAINING ([ACT {end1 + 1}]–[ACT {end2}] | ~40%):
  I supervise my disciples in unique high-intensity martial conditioning.
  VISUAL SYNC: 2–5 disciples in extreme motion; I supervise in the background.

- ACT III — LIBERATION / RECLAIMING SOVEREIGNTY ([ACT {end2 + 1}]–[ACT {n}] | ~30%):
  My disciples cut free of digital tethers; stand unbroken. DO NOT speak Follow/Subscribe.
  VISUAL SYNC: disciples ripping free of glowing feeds mid strike.

STRICT BAN: emotion tags, SSML, "Follow Master Mei", "Subscribe", third-person "Master Mei".
Each [ACT N] ≈ one spoken beat. STRICT TOTAL: {_NARRATION_WORDS_MIN}–{_NARRATION_WORDS_MAX} words
(target ~{_NARRATION_WORDS_TARGET}) for 80–100 s at 0.92× TTS.
""".strip()


def build_three_act_script_instructions(n_acts: int, episode: dict[str, Any]) -> str:
    """Prompt block — V4 cinematic (or V3 didactic / V2 rollback)."""
    if not is_v3_active():
        return _build_v2_three_act_script_instructions(n_acts, episode)

    end1, end2, n = three_act_bounds(n_acts)
    philosopher = episode.get("philosopher") or "Marcus Aurelius"
    concept = episode.get("concept") or episode.get("label") or "Stoic Sovereignty"
    label = episode.get("label") or f"{philosopher}'s {concept}"
    core = episode.get("core", "")
    seduction = scrub_banned_narration_phrases(
        episode.get("seduction") or "cheap dopamine and soft digital comfort"
    )
    practice = scrub_banned_narration_phrases(
        episode.get("practice") or "ruthless daily martial discipline"
    )
    is_siren = bool(episode.get("siren")) and is_siren_enabled()
    cite = is_explicit_philosopher_citations_enabled() or is_didactic_philosophy_enabled()
    western = is_western_majority_masses_enabled()

    mass_visual = (
        "VISUAL SYNC: ethnically diverse enslaved masses, PREDOMINANTLY Western/Occidental "
        "(Caucasian, Hispanic, Black, mixed) — attractive men and stylish Western/Asian "
        "women hooked into holographic feeds and glowing VR cords, seduced by cheap "
        "digital dopamine. Master Mei remains the sole Oriental/Asian ancestral wisdom figure. "
        "NO Master Mei in Act I. NO idle portraits."
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
        act1_formula = (
            f'FIRST-PERSON voiceover directive (adapt — cite {philosopher} explicitly): '
            f'"I studied {philosopher}. {philosopher} revealed {concept} — yet you hand your '
            f"perception to the Machine every dawn through {seduction}. Sensory numbness and "
            f'instant gratification are not entertainment… they are the leash on your spirit."'
            if cite
            else "In FIRST PERSON, expose the digital trap with stern philosophical diagnosis."
        )
        act2_formula = (
            f'FIRST-PERSON voiceover directive (adapt — invent a UNIQUE forge allegory): '
            f'"I do not comfort you. On my path, my disciples endure {practice}. '
            f'I demand you reclaim command of attention before you reclaim capital."'
            if cite
            else "In FIRST PERSON, invent a unique martial/mental counter-strategy (never reuse prior episode templates)."
        )
        act3_formula = (
            "FIRST-PERSON voiceover directive (adapt — unique liberation close): "
            '"I cut the feed. I guard my focus and my capital. '
            'Walk my path of daily discipline — reclaim your spirit and your freedom."'
        )
        engine_line = "ENGINE: V4.0_CINEMATIC_STORY_ARC"
        act1_title = "THE SEDUCTION & PHILOSOPHICAL DIAGNOSIS"
        act3_title = "LIBERATION & WEALTH SOVEREIGNTY"
    else:
        act1_formula = (
            f'FIRST-PERSON voiceover formula (adapt): '
            f'"I warn you — you live inside {philosopher}\'s {concept} without knowing it. '
            f'The Machine seduces you with {seduction}."'
            if cite
            else "In FIRST PERSON, introduce the trap with stern authority."
        )
        act2_formula = (
            f'FIRST-PERSON voiceover formula (adapt): '
            f'"To break this cage, I demand {practice} — as {philosopher} taught."'
            if cite
            else "In FIRST PERSON, explain the disciplined counter-strategy."
        )
        act3_formula = (
            "FIRST-PERSON voiceover formula (adapt): "
            '"I sever the feed. I protect my energy and my capital. '
            'Walk my path — reclaim your ultimate freedom."'
        )
        engine_line = "ENGINE: V3.0_DIDACTIC_STOIC_STORY_ARC"
        act1_title = "THE SEDUCTION & CONCEPT INTRO"
        act3_title = "LIBERATION & FINANCIAL SOVEREIGNTY"

    return f"""
{engine_line}
POV LOCK (IMMUTABLE): The narrator IS Master Mei.
Write ONLY in FIRST PERSON ("I", "My disciples", "I demand", "My path").
PROHIBITED in narration body: third-person "Master Mei …", "Follow Master Mei".
(CTA audio is stitched separately AFTER narration — never inside the script.)

SINGLE-CONCEPT RULE (IMMUTABLE):
This entire video is ONE coherent cinematic story about "{label}".
ONE thinker only: {philosopher}. ONE concept only: {concept}.
Never mix philosophies. Philosophical spine: {core}
EXPLICIT CITATIONS REQUIRED: name {philosopher} / {concept} aloud in Act I (still in first person).

PHILOSOPHICAL DEPTH (MANDATORY):
Explain how sensory numbness, instant gratification, and propaganda hijack perception
to enslave the spirit and destroy financial/spiritual sovereignty.
Invent a UNIQUE allegory this episode — do not recycle prior episode templates.

PHRASE BLACKLIST (NEVER USE): {blacklist}.

TONE: highly stern, accessible, cinematic authority — I teach warriors as their master.
AUDIENCE: Western / US men — self-mastery, tech-critique, financial sovereignty.
NEVER tip lists, wellness fluff, hustle-bro clichés, or therapy-speak.

3-ACT STORY ARC (MANDATORY — Beginning / Middle / End, organic narrative flow):

- ACT I — {act1_title} ([ACT 1]–[ACT {end1}] | ~30%):
  Expose the modern digital trap using explicit philosophical foundations.
  Explain where/how The Machine seduces humanity.
  {act1_formula}
  {mass_visual}

- ACT II — THE FORGE OF DISCIPLINE ([ACT {end1 + 1}]–[ACT {end2}] | ~40%):
  Transition from passive slavery to active resistance through martial and mental training.
  I dominate this act (50%+ screen presence) supervising my disciples — NOT a static portrait.
  Invent a UNIQUE high-intensity training environment this episode (never the same kata scene).
  {act2_formula}
  VISUAL SYNC: I supervise 2–5 disciples in extreme motion. HIGH MOTION. NO idle monk close-ups.

- ACT III — {act3_title} ([ACT {end2 + 1}]–[ACT {n}] | ~30%):
  Climax: breaking free → ultimate focus, spiritual freedom, financial sovereignty.
  {act3_formula}
  VISUAL SYNC: disciples ripping free of glowing digital tethers / propaganda feeds;
  standing unbroken as the illusion collapses.
  DO NOT speak Follow/Subscribe (CTA is stitched separately after narration).

CHARACTER RULES (mental picture only — spoken prose, no stage directions):
- Enslaved masses = Western-majority diverse genders, holographic feeds, VR tethers.
- Disciples = active, sweating, martial motion, cutting free of digital bondage.
- I (Master Mei) = Act II Oriental/Asian ancestral instructor supervising in the background.

STRICT BAN in narration: emotion tags, SSML, "Follow Master Mei", "Subscribe", tip lists,
third-person "Master Mei", and all blacklisted clichés above.
Each [ACT N] ≈ one spoken beat. STRICT TOTAL: {_NARRATION_WORDS_MIN}–{_NARRATION_WORDS_MAX} words
(target ~{_NARRATION_WORDS_TARGET}) for 80–100 s at 0.92× TTS delivery.
""".strip()


def build_four_act_script_instructions(n_acts: int, episode: dict[str, Any]) -> str:
    """Legacy name — routes to active 3-Act engine (V4 / V3 / V2 rollback)."""
    return build_three_act_script_instructions(n_acts, episode)
