# -*- coding: utf-8 -*-
"""Episode catalog, playlist map, and SEO builders.

Source-file matching (10 Shorts per episode) lives in ``scanner.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

CHANNEL_ID = "principles_of_wealth_finance_economics"
_ENGINE_ROOT = Path(__file__).resolve().parents[2]
_PAGE_CFG_DIR = (
    _ENGINE_ROOT / "channels_config" / "principles_of_wealth_finance_economics"
)

_YT_TITLE_MAX = 100


@dataclass(frozen=True)
class EpisodeSpec:
    episode: int
    act: int
    act_label: str
    title_core: str
    short_title: str
    match_keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlaylistSpec:
    act: int
    title: str
    description: str
    first_episode: int
    last_episode: int

    def contains(self, episode: int) -> bool:
        return self.first_episode <= episode <= self.last_episode

    def position_for(self, episode: int) -> int:
        return episode - self.first_episode


EPISODES: tuple[EpisodeSpec, ...] = (
    EpisodeSpec(1, 1, "ACT I", "Ray Dalio's 5 Principles to Build Wealth", "Wealth Is Not Money", ("5 principles", "build wealth")),
    EpisodeSpec(2, 1, "ACT I", "Pain, Feedback, and Financial Growth", "Pain Is Data", ("pain", "feedback", "financial growth")),
    EpisodeSpec(3, 1, "ACT I", "The Radical Truth About Money Few Are Willing to Face", "The Radical Truth About Money", ("radical truth", "about money")),
    EpisodeSpec(4, 1, "ACT I", "How Economic Reality Really Works (Beyond Income and Effort)", "How Economic Reality Works", ("economic reality", "beyond income")),
    EpisodeSpec(5, 1, "ACT I", "Ego: The Silent Tax on Investment Returns", "Ego: The Silent Tax", ("ego", "silent tax")),
    EpisodeSpec(6, 1, "ACT I", "The Difference Between Risk and Ruin — Why Most Investors Never Recover", "Risk vs Ruin", ("risk and ruin", "never recover")),
    EpisodeSpec(7, 1, "ACT I", "Why Stability Beats High Returns — The Wealth Principle Most Investors Ignore", "Stability Beats High Returns", ("stability beats", "high returns")),
    EpisodeSpec(8, 1, "ACT I", "Compounding Is a Law, Not a Strategy — Why Wealth Follows Structure", "Compounding Is a Law", ("compounding", "not a strategy")),
    EpisodeSpec(9, 1, "ACT I", "Decision-Making Under Uncertainty — The Skill That Protects Wealth", "Decision-Making Under Uncertainty", ("uncertainty", "protects wealth")),
    EpisodeSpec(10, 1, "ACT I", "Designing Principles for Money — How Professionals Think About Wealth", "Designing Principles for Money", ("designing principles", "professionals think")),
    EpisodeSpec(11, 2, "ACT II", "Your Personal Financial Machine — How Wealth Is Actually Engineered", "Your Personal Financial Machine", ("financial machine", "engineered")),
    EpisodeSpec(12, 2, "ACT II", "Cash Flow — The Lifeblood of Wealth | Why Net Worth Alone Fails", "Cash Flow Is the Lifeblood", ("cash flow", "net worth")),
    EpisodeSpec(13, 2, "ACT II", "Leverage Explained — Why Speed Destroys Wealth", "Leverage Explained", ("leverage", "speed destroys")),
    EpisodeSpec(14, 2, "ACT II", "Debt Explained — When It Builds Wealth or Destroys It", "Debt Explained", ("debt explained",)),
    EpisodeSpec(15, 2, "ACT II", "Diversification Is Not What You Think (This Is What Actually Protects Wealth)", "Diversification Is Not What You Think", ("diversification",)),
    EpisodeSpec(16, 2, "ACT II", "Patience Beats Talent: The Waiting Game Behind Real Wealth", "Patience Beats Talent", ("patience beats", "waiting game")),
    EpisodeSpec(17, 2, "ACT II", "Automation, Discipline, and Emotional Distance", "Automation and Discipline", ("automation", "emotional distance")),
    EpisodeSpec(18, 2, "ACT II", "Why Simple Systems Outperform Complex Ones Over Time", "Simple Systems Win", ("simple systems", "outperform")),
    EpisodeSpec(19, 2, "ACT II", "Scaling Decisions as Capital Grows", "Scaling Decisions", ("scaling decisions", "capital grows")),
    EpisodeSpec(20, 2, "ACT II", "When to Change the Machine — and When Not To", "When to Change the Machine", ("change the machine",)),
    EpisodeSpec(21, 3, "ACT III", "Cycles: Why Everything That Goes Up Eventually Tests You", "Cycles Always Test You", ("cycles", "goes up")),
    EpisodeSpec(22, 3, "ACT III", "Surviving Drawdowns Without Breaking Your System", "Surviving Drawdowns", ("drawdowns",)),
    EpisodeSpec(23, 3, "ACT III", "The Psychology of Large Numbers", "The Psychology of Large Numbers", ("large numbers",)),
    EpisodeSpec(24, 3, "ACT III", "Why Rich People Fear Different Things", "Why Rich People Fear Different Things", ("rich people fear",)),
    EpisodeSpec(25, 3, "ACT III", "Transferring Principles Across Life and Business", "Transferring Principles", ("transferring principles",)),
    EpisodeSpec(26, 3, "ACT III", "Mistakes That Only Appear at Higher Net Worths", "High-Net-Worth Mistakes", ("higher net worth",)),
    EpisodeSpec(27, 3, "ACT III", "The Role of Humility in Long-Term Wealth", "Humility and Long-Term Wealth", ("humility",)),
    EpisodeSpec(28, 3, "ACT III", "Teaching the Machine to the Next Generation", "Teaching the Next Generation", ("next generation", "teaching the machine")),
    EpisodeSpec(29, 3, "ACT III", "Freedom, Time, and What Wealth Is Actually For", "What Wealth Is Actually For", ("what wealth is", "freedom, time")),
    EpisodeSpec(30, 3, "ACT III", "Living Inside Reality — A Lifetime Practice", "Living Inside Reality", ("living inside reality", "lifetime practice")),
)

def _build_playlists() -> tuple[PlaylistSpec, ...]:
    from core.principles_of_wealth.seo_catalog import long_pack, playlist_pack

    ranges = ((1, 1, 10), (2, 11, 20), (3, 21, 30))
    out: list[PlaylistSpec] = []
    for act, first, last in ranges:
        titles = [(n, long_pack(n)["title"]) for n in range(first, last + 1)]
        pack = playlist_pack(act, titles)
        out.append(
            PlaylistSpec(
                act=act,
                title=pack["title"],
                description=pack["description"],
                first_episode=first,
                last_episode=last,
            )
        )
    return tuple(out)


PLAYLISTS: tuple[PlaylistSpec, ...] = _build_playlists()


def episode_by_number(n: int) -> EpisodeSpec:
    for spec in EPISODES:
        if spec.episode == n:
            return spec
    raise KeyError(f"No episode {n} in the Principles of Wealth catalog.")


def playlist_for_episode(n: int) -> PlaylistSpec:
    for pl in PLAYLISTS:
        if pl.contains(n):
            return pl
    raise KeyError(f"Episode {n} is outside ACT I–III (1–30).")


def _load_page_config() -> Any:
    import importlib.util

    path = _PAGE_CFG_DIR / "page_config.py"
    spec = importlib.util.spec_from_file_location(
        "channels_config.principles_of_wealth_finance_economics.page_config",
        path,
    )
    if spec is None or spec.loader is None:
        raise FileNotFoundError(path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def resolve_source_directory(override: Optional[str | Path] = None) -> Path:
    if override:
        return Path(override).expanduser()
    cfg = _load_page_config()
    return Path(str(getattr(cfg, "SOURCE_DIRECTORY", ""))).expanduser()


def resolve_processed_directory(
    source_dir: Path,
    override: Optional[str | Path] = None,
) -> Path:
    if override:
        return Path(override).expanduser()
    cfg = _load_page_config()
    sub = str(getattr(cfg, "PROCESSED_SUBFOLDER", "Processed") or "Processed")
    return source_dir / sub


def _fit_title(text: str, limit: int = _YT_TITLE_MAX) -> str:
    clean = re.sub(r"\s{2,}", " ", (text or "").strip())
    if len(clean) <= limit:
        return clean
    stripped = re.sub(r"\s*\([^)]*\)\s*", " ", clean).strip()
    if stripped != clean and 16 <= len(stripped) <= limit:
        return stripped
    for sep in (" — ", " – ", " - ", ": ", " | "):
        if sep in clean:
            head = clean.split(sep, 1)[0].strip()
            if 16 <= len(head) <= limit:
                return head
    cut = clean[: max(1, limit - 1)].rstrip(" |—-")
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0].rstrip(" |—-,;:")
    return (cut or clean[: limit - 1]) + "..."


def build_long_title(spec: EpisodeSpec) -> str:
    from core.principles_of_wealth.seo_catalog import long_pack

    return long_pack(spec.episode)["title"]


def build_short_title(
    spec: EpisodeSpec,
    *,
    hook: str = "",
    clip_index: int = 0,
) -> str:
    from core.principles_of_wealth.seo_catalog import short_pack

    return short_pack(spec.episode, clip_index or 0, hook=hook)["title"]


def build_long_description(spec: EpisodeSpec, *, related_url: str = "") -> str:
    from core.principles_of_wealth.seo_catalog import DISCLAIMER, long_pack

    desc = str(long_pack(spec.episode)["description"])
    if related_url:
        desc = desc.replace(DISCLAIMER, f"Related Short: {related_url}\n\n{DISCLAIMER}")
    return desc.strip()


def build_short_description(
    spec: EpisodeSpec,
    *,
    long_video_id: str = "",
    clip_index: int = 0,
    hook: str = "",
) -> str:
    from core.principles_of_wealth.seo_catalog import short_pack

    return short_pack(
        spec.episode,
        clip_index or 0,
        hook=hook,
        long_video_id=long_video_id,
    )["description"]


def default_tags(
    episode: Optional[int] = None,
    clip: Optional[int] = None,
    hook: str = "",
) -> list[str]:
    from core.principles_of_wealth.seo_catalog import BASE_TAGS, long_pack, short_pack

    if episode and clip:
        return list(short_pack(int(episode), int(clip), hook=hook)["tags"])
    if episode:
        return list(long_pack(int(episode))["tags"])
    cfg = _load_page_config()
    tags = getattr(cfg, "YOUTUBE_DEFAULT_TAGS", None)
    if isinstance(tags, list) and tags:
        return [str(t) for t in tags]
    return list(BASE_TAGS)


def parse_episode_list(raw: Optional[str]) -> Optional[list[int]]:
    """Parse ``1,2,5-8`` into a sorted unique episode list. None = all."""
    if raw is None or not str(raw).strip() or str(raw).strip().lower() in {"all", "*"}:
        return None
    out: list[int] = []
    for part in str(raw).split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            a, b = token.split("-", 1)
            lo, hi = int(a), int(b)
            out.extend(range(min(lo, hi), max(lo, hi) + 1))
        else:
            out.append(int(token))
    return sorted({n for n in out if 1 <= n <= 30})
