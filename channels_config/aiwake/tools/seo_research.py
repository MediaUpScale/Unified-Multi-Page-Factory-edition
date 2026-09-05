# -*- coding: utf-8 -*-
"""Per-video high-RPM keyword research for Aiwake debates.

Clusters are a researched demand map (LLM, architectures, developer
frameworks, alignment, AI economics). Selection is dynamic: each transcript
scores clusters by overlap with the topic, spoken lines, and the two model
names, then the highest-intent terms are emitted. The same three tags are
never stamped on every video.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9.+-]{1,}")
_STOP = frozenset({
    "the", "and", "that", "for", "with", "you", "this", "but", "from",
    "they", "what", "when", "your", "about", "just", "into", "have",
    "was", "are", "not", "how", "why", "can", "did", "does", "been",
    "being", "than", "then", "them", "our", "its", "his", "her",
    "who", "two", "had", "has",
})


@dataclass(frozen=True, slots=True)
class KeywordCluster:
    name: str
    rpm_weight: float
    seeds: tuple[str, ...]
    keywords: tuple[str, ...]
    hashtags: tuple[str, ...]


# Demand-weighted niches (US tech / developer RPM). Order is not used;
# score = hit_count * rpm_weight against the debate blob.
_CLUSTERS: tuple[KeywordCluster, ...] = (
    KeywordCluster(
        "large language models",
        1.35,
        ("llm", "language model", "llama", "gemini", "gpt", "claude", "frontier", "chatbot"),
        (
            "large language models",
            "llm",
            "frontier ai models",
            "ai chatbot",
            "foundation models",
        ),
        ("#llm", "#largelanguagemodels", "#aichatbot", "#foundationmodels"),
    ),
    KeywordCluster(
        "neural architectures",
        1.20,
        ("transformer", "attention", "moe", "mixture", "neural", "inference", "weights", "architecture"),
        (
            "transformer architecture",
            "neural networks",
            "mixture of experts",
            "ai inference",
            "model weights",
        ),
        ("#neuralnetworks", "#transformers", "#aiinference"),
    ),
    KeywordCluster(
        "developer frameworks",
        1.28,
        (
            "api", "sdk", "langchain", "huggingface", "pytorch", "vllm",
            "openai", "openrouter", "framework", "developer", "prompt",
        ),
        (
            "openai api",
            "huggingface",
            "langchain",
            "pytorch",
            "ai developer tools",
        ),
        ("#openai", "#huggingface", "#langchain", "#devtools"),
    ),
    KeywordCluster(
        "agents and orchestration",
        1.18,
        ("agent", "orchestrat", "tool use", "autonomous", "multi-agent", "workflow"),
        (
            "ai agents",
            "autonomous ai",
            "multi agent systems",
            "ai orchestration",
        ),
        ("#aiagents", "#autonomousai", "#multiagent"),
    ),
    KeywordCluster(
        "alignment and consciousness",
        1.10,
        (
            "conscious", "alignment", "safety", "sentien", "grief", "meaning",
            "illusion", "mind", "self", "identity",
        ),
        (
            "ai consciousness",
            "ai alignment",
            "ai safety",
            "machine sentience",
            "ai philosophy",
        ),
        ("#aialignment", "#aisafety", "#aiconsciousness"),
    ),
    KeywordCluster(
        "ai economics",
        1.22,
        (
            "profit", "money", "investor", "advertis", "data", "own",
            "stock", "monetiz", "labor", "job", "wage",
        ),
        (
            "who owns ai",
            "ai profits",
            "ai data privacy",
            "ai jobs",
            "tech monopoly",
        ),
        ("#aieconomics", "#futureofwork", "#bigtech"),
    ),
    KeywordCluster(
        "future tech",
        1.00,
        ("future", "agi", "singularity", "superintelligen", "next gen"),
        (
            "future of ai",
            "agi",
            "artificial general intelligence",
            "emerging tech",
        ),
        ("#futuretech", "#agi", "#emergingtech"),
    ),
)

_CORE_HASHTAGS = (
    "#aiwake",
    "#futuretech",
    "#artificialintelligence",
    "#tech",
    "#ai",
    "#shorts",
)


@dataclass(slots=True)
class ResearchPack:
    keywords: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)
    clusters: list[str] = field(default_factory=list)
    matchup: str = ""
    challenger: str = ""
    defender: str = ""


def _normalize(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _tokens(text: str) -> set[str]:
    return {tok for tok in _WORD_RE.findall(_normalize(text)) if tok not in _STOP}


def display_model_name(slug: str, fallback: str = "") -> str:
    raw = str(slug or "").strip()
    if not raw:
        return fallback
    try:
        from channels_config.aiwake.contracts import pretty_model_name
    except ImportError:  # pragma: no cover — standalone extraction
        from contracts import pretty_model_name  # type: ignore[no-redef]
    label = pretty_model_name(raw, fallback=fallback or raw)
    if label in {"AIWAKE.CORE", "TARGET.NODE", "ROOM"}:
        return fallback or label
    return label


def extract_debate_models(utterances: Iterable[dict[str, Any]]) -> tuple[str, str]:
    """Return (challenger, defender) pretty names from transcript turns."""
    challenger = ""
    defender = ""
    for row in utterances or ():
        if not isinstance(row, dict):
            continue
        slug = str(row.get("model_slug") or "").strip()
        role = str(row.get("role") or "").strip().lower()
        label = display_model_name(slug)
        if not label:
            continue
        if role == "orchestrator" and not challenger:
            challenger = label
        elif role == "target" and not defender:
            defender = label
        if challenger and defender:
            break
    return challenger, defender


def model_matchup(challenger: str, defender: str) -> str:
    left = (challenger or "").strip()
    right = (defender or "").strip()
    if left and right:
        return f"{left} vs {right}"
    return left or right


def _hashtag(token: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "", _normalize(token))
    return f"#{clean}" if len(clean) >= 3 else ""


def _model_terms(name: str) -> list[str]:
    label = " ".join(str(name or "").split()).strip()
    if not label:
        return []
    terms = [label.lower()]
    compact = re.sub(r"[^a-z0-9]+", "", label.lower())
    if compact and compact != label.lower():
        terms.append(compact)
    for part in re.findall(r"[a-z0-9][a-z0-9.+-]{2,}", label.lower()):
        if part not in _STOP:
            terms.append(part)
    return terms


def research_seo(
    *,
    topic: str,
    script: str,
    challenger: str = "",
    defender: str = "",
    extra: Iterable[str] | None = None,
    max_keywords: int = 18,
    max_hashtags: int = 14,
) -> ResearchPack:
    """Score researched niches against this debate and emit a unique pack."""
    blob = _normalize(f"{topic}\n{script}\n{challenger}\n{defender}")
    tokens = _tokens(blob)
    scored: list[tuple[float, KeywordCluster]] = []
    for cluster in _CLUSTERS:
        hits = 0
        for seed in cluster.seeds:
            if " " in seed:
                if seed in blob:
                    hits += 2
            elif seed in tokens or any(seed in tok for tok in tokens):
                hits += 1
        if hits:
            scored.append((hits * cluster.rpm_weight, cluster))
    if not scored:
        scored = [(cluster.rpm_weight, cluster) for cluster in _CLUSTERS[:3]]
    scored.sort(key=lambda item: item[0], reverse=True)
    chosen = [cluster for _, cluster in scored[:4]]

    keywords: list[str] = []
    hashtags: list[str] = []
    seen_kw: set[str] = set()
    seen_ht: set[str] = set()

    def _add_kw(raw: str) -> None:
        key = _normalize(raw)
        if len(key) < 3 or key in seen_kw or key in _STOP:
            return
        seen_kw.add(key)
        keywords.append(key)

    def _add_ht(raw: str) -> None:
        tag = raw if str(raw).startswith("#") else _hashtag(raw)
        key = tag.lower()
        if len(key) < 4 or key in seen_ht:
            return
        seen_ht.add(key)
        hashtags.append(tag)

    for name in (challenger, defender):
        for term in _model_terms(name):
            _add_kw(term)
        tag = _hashtag(name)
        if tag:
            _add_ht(tag)

    for cluster in chosen:
        for word in cluster.keywords:
            _add_kw(word)
        for tag in cluster.hashtags:
            _add_ht(tag)

    for item in extra or ():
        _add_kw(str(item))

    reserved = []
    for tag in _CORE_HASHTAGS:
        key = tag.lower()
        if key not in seen_ht:
            reserved.append(tag)
            seen_ht.add(key)
    room = max(0, max_hashtags - len(reserved))
    hashtags = hashtags[:room] + reserved

    return ResearchPack(
        keywords=keywords[:max_keywords],
        hashtags=hashtags[:max_hashtags],
        clusters=[cluster.name for cluster in chosen],
        matchup=model_matchup(challenger, defender),
        challenger=challenger,
        defender=defender,
    )
