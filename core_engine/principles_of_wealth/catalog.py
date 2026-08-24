# -*- coding: utf-8 -*-
"""Episode catalog, playlist map, SEO builders, and source-file matching.

Source files stay on the external production drive. Matching rules:

* ``Ray Dalio epN.mp4``          → long-form episode N
* ``Short N`` / ``ShortN``       → Short for episode N
* ``ThumbN`` / ``thumbN``        → thumbnail for episode N
* ``EpX.Y`` chapter clips        → unmatched library assets (not auto-assigned)
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

CHANNEL_ID = "principles_of_wealth_finance_economics"
_ENGINE_ROOT = Path(__file__).resolve().parents[2]
_PAGE_CFG_DIR = (
    _ENGINE_ROOT / "channels_config" / "principles_of_wealth_finance_economics"
)

_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".webm"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_SKIP_DIR_NAMES = {"processed", "tmp", "temp", ".tmp"}

_LONG_EP_RE = re.compile(
    r"ray\s*dalio\s+ep(?:isode)?\s*0*(?P<n>\d+)\b",
    re.IGNORECASE,
)
_SHORT_EP_RE = re.compile(
    r"\bshort\s*-?\s*0*(?P<n>\d+)\b",
    re.IGNORECASE,
)
_THUMB_EP_RE = re.compile(
    r"\bthumb(?:nail)?\s*-?\s*0*(?P<n>\d+)\b",
    re.IGNORECASE,
)
# Reject Ep2.3 / Ep4.10 chapter clips from the long-form matcher.
_CHAPTER_RE = re.compile(r"\bep\s*\d+\.\d+", re.IGNORECASE)

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

PLAYLISTS: tuple[PlaylistSpec, ...] = (
    PlaylistSpec(
        act=1,
        title="ACT I - The Foundations of Wealth Strategy",
        description=(
            "ACT I — The Foundations of Wealth Strategy. Independent educational "
            "analysis of economic frameworks and principles of wealth creation. "
            "Not affiliated with Ray Dalio or Bridgewater Associates."
        ),
        first_episode=1,
        last_episode=10,
    ),
    PlaylistSpec(
        act=2,
        title="ACT II - The Mechanics of the Financial Machine",
        description=(
            "ACT II — The Mechanics of the Financial Machine. Cash flow, leverage, "
            "debt, diversification, and the systems that actually protect wealth. "
            "Independent educational curation. Not affiliated with Ray Dalio or "
            "Bridgewater Associates."
        ),
        first_episode=11,
        last_episode=20,
    ),
    PlaylistSpec(
        act=3,
        title="ACT III - The Psychology of Sustaining Wealth",
        description=(
            "ACT III — The Psychology of Sustaining Wealth. Cycles, drawdowns, "
            "humility, and what wealth is actually for. Independent educational "
            "curation. Not affiliated with Ray Dalio or Bridgewater Associates."
        ),
        first_episode=21,
        last_episode=30,
    ),
)


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
    cfg = _load_page_config()
    suffix = str(getattr(cfg, "YOUTUBE_TITLE_SUFFIX", "") or "")
    full = f"{spec.title_core}{suffix}"
    if len(full) <= _YT_TITLE_MAX:
        return full
    short_suffix = " | Ray Dalio Framework Analyzed (Financial Education)"
    mid = f"{spec.title_core}{short_suffix}"
    if len(mid) <= _YT_TITLE_MAX:
        return mid
    budget = _YT_TITLE_MAX - len(short_suffix)
    return _fit_title(spec.title_core, max(20, budget)) + short_suffix


def build_short_title(spec: EpisodeSpec) -> str:
    cfg = _load_page_config()
    suffix = str(getattr(cfg, "YOUTUBE_SHORT_TITLE_SUFFIX", "") or "")
    full = f"{spec.short_title}{suffix}"
    if len(full) <= _YT_TITLE_MAX:
        return full
    return _fit_title(f"{spec.short_title} - Ray Dalio Analysis #shorts")


def build_long_description(spec: EpisodeSpec, *, related_url: str = "") -> str:
    cfg = _load_page_config()
    body = str(getattr(cfg, "YOUTUBE_DESCRIPTION_TEMPLATE", "") or "").strip()
    header = (
        f"{spec.act_label}, Ep. {spec.episode}: {spec.title_core}\n\n"
    )
    extra = f"\n\nFull series playlist: {spec.act_label}."
    if related_url:
        extra = f"\n\nRelated Short: {related_url}" + extra
    return f"{header}{body}{extra}".strip()


def build_short_description(spec: EpisodeSpec, *, long_video_id: str = "") -> str:
    cfg = _load_page_config()
    body = str(getattr(cfg, "YOUTUBE_SHORT_DESCRIPTION_TEMPLATE", "") or "").strip()
    link = ""
    if long_video_id:
        link = (
            f"Full educational episode: https://youtu.be/{long_video_id}\n\n"
        )
    return f"{link}{body}".strip()


def default_tags() -> list[str]:
    cfg = _load_page_config()
    tags = getattr(cfg, "YOUTUBE_DEFAULT_TAGS", None)
    if isinstance(tags, list) and tags:
        return [str(t) for t in tags]
    return [
        "financial education",
        "wealth building",
        "macroeconomics",
        "investing",
        "ray dalio analysis",
    ]


@dataclass
class AssetMatch:
    episode: int
    long_path: str = ""
    short_path: str = ""
    thumbnail_path: str = ""
    long_bytes: int = 0
    short_bytes: int = 0
    thumbnail_bytes: int = 0

    @property
    def has_long(self) -> bool:
        return bool(self.long_path) and self.long_bytes > 0

    @property
    def has_short(self) -> bool:
        return bool(self.short_path) and self.short_bytes > 0

    @property
    def has_thumbnail(self) -> bool:
        return bool(self.thumbnail_path) and self.thumbnail_bytes > 0


@dataclass
class ScanResult:
    source_dir: str
    matches: dict[int, AssetMatch] = field(default_factory=dict)
    unmatched: list[str] = field(default_factory=list)
    skipped_empty: list[str] = field(default_factory=list)
    missing_episodes: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_dir": self.source_dir,
            "matches": {
                str(k): asdict(v) for k, v in sorted(self.matches.items())
            },
            "unmatched": self.unmatched,
            "skipped_empty": self.skipped_empty,
            "missing_episodes": self.missing_episodes,
        }


def _iter_source_files(source_dir: Path) -> Iterable[Path]:
    if not source_dir.is_dir():
        return []
    for path in source_dir.rglob("*"):
        if not path.is_file():
            continue
        if any(part.lower() in _SKIP_DIR_NAMES for part in path.parts):
            continue
        yield path


def _prefer_better(existing: str, existing_bytes: int, candidate: Path) -> bool:
    if not existing:
        return True
    size = candidate.stat().st_size
    if size <= 0:
        return False
    # Prefer a non-variant stem (Thumb6.jpg over Thumb6-A.jpg).
    if re.search(r"-\s*[A-Za-z]\b", candidate.stem) and not re.search(
        r"-\s*[A-Za-z]\b", Path(existing).stem
    ):
        return False
    return size > existing_bytes


def scan_source_directory(source_dir: Optional[str | Path] = None) -> ScanResult:
    root = resolve_source_directory(source_dir)
    result = ScanResult(source_dir=str(root))
    if not root.is_dir():
        result.unmatched.append(f"SOURCE_DIR_MISSING: {root}")
        result.missing_episodes = [spec.episode for spec in EPISODES]
        return result

    matches: dict[int, AssetMatch] = {
        spec.episode: AssetMatch(episode=spec.episode) for spec in EPISODES
    }

    for path in _iter_source_files(root):
        suffix = path.suffix.lower()
        name = path.name
        size = path.stat().st_size
        if size <= 0:
            result.skipped_empty.append(str(path))
            continue

        assigned = False
        if suffix in _VIDEO_EXTS:
            if _CHAPTER_RE.search(name) and not _SHORT_EP_RE.search(name):
                result.unmatched.append(str(path))
                continue
            short_m = _SHORT_EP_RE.search(name)
            # Never treat a file whose name contains "short" as a long-form episode.
            long_m = None
            if not short_m and "short" not in name.lower():
                long_m = _LONG_EP_RE.search(name)
            if short_m:
                n = int(short_m.group("n"))
                if n in matches and _prefer_better(
                    matches[n].short_path, matches[n].short_bytes, path
                ):
                    matches[n].short_path = str(path)
                    matches[n].short_bytes = size
                    assigned = True
            elif long_m:
                n = int(long_m.group("n"))
                if n in matches and _prefer_better(
                    matches[n].long_path, matches[n].long_bytes, path
                ):
                    matches[n].long_path = str(path)
                    matches[n].long_bytes = size
                    assigned = True
        elif suffix in _IMAGE_EXTS:
            thumb_m = _THUMB_EP_RE.search(name)
            if thumb_m:
                n = int(thumb_m.group("n"))
                if n in matches and _prefer_better(
                    matches[n].thumbnail_path, matches[n].thumbnail_bytes, path
                ):
                    matches[n].thumbnail_path = str(path)
                    matches[n].thumbnail_bytes = size
                    assigned = True

        if not assigned:
            result.unmatched.append(str(path))

    result.matches = {
        n: m for n, m in matches.items() if m.has_long or m.has_short or m.has_thumbnail
    }
    result.missing_episodes = [
        spec.episode
        for spec in EPISODES
        if spec.episode not in result.matches or not result.matches[spec.episode].has_long
    ]
    return result


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


def write_scan_snapshot(scan: ScanResult, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(scan.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return dest
