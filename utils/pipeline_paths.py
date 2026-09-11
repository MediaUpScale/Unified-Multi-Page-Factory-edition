# -*- coding: utf-8 -*-
"""Factory output / asset / scratch / channel-store paths.

Honors ``OUTPUT_PATH`` and ``ASSETS_PATH`` from the process environment
(or the project ``.env``). Pipeline artifacts (renders, logs, MoviePy
temps) belong under that outputs root — never the process CWD and
never a hardcoded ``<repo>/outputs`` path when the env root is set.

Permanent channel state (libraries, transcripts, publish logs) lives on
C: at ``channels_config/<channel>/store/`` and is mirrored to G:.
``safe_rmtree`` refuses to delete that store.

This module is the single source of truth for output location logic.
Callers must not construct ``<repo>/outputs/...`` themselves.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

_FACTORY_ROOT: Path = Path(__file__).resolve().parents[1]
_LOG = logging.getLogger(__name__)
_warned_local_fallback = False


def _parse_dotenv_file(name: str) -> str | None:
    """Read a single key from the factory ``.env`` without requiring python-dotenv."""
    env_path = _FACTORY_ROOT / ".env"
    if not env_path.is_file():
        return None
    try:
        text = env_path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, val = stripped.partition("=")
        if key.strip() == name:
            parsed = val.strip().strip('"').strip("'")
            return parsed or None
    return None


def _ensure_dotenv() -> None:
    """Load ``.env`` without clobbering keys already present in the process."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_path = _FACTORY_ROOT / ".env"
    if env_path.is_file():
        load_dotenv(dotenv_path=env_path, override=False, encoding="utf-8-sig")


_ensure_dotenv()


def factory_root() -> Path:
    return _FACTORY_ROOT


def repo_outputs_fallback() -> Path:
    """Local ``<repo>/outputs`` — last-resort only when factory env is unset."""
    return _FACTORY_ROOT / "outputs"


def _env_path(*names: str) -> Path | None:
    for name in names:
        raw = (os.getenv(name) or "").strip().strip('"').strip("'")
        if raw:
            return Path(raw).expanduser()
        parsed = _parse_dotenv_file(name)
        if parsed:
            os.environ.setdefault(name, parsed)
            return Path(parsed).expanduser()
    return None


def outputs_root() -> Path:
    """Factory outputs root: ``OUTPUT_PATH`` or ``OUTPUTS_DIR``.

    Falls back to ``<repo>/outputs`` only when neither key is set in the
    process environment or the factory ``.env``. That fallback is a last
    resort for tests — production runs must set ``OUTPUT_PATH``.
    """
    global _warned_local_fallback
    resolved = _env_path("OUTPUT_PATH", "OUTPUTS_DIR")
    if resolved is not None:
        return resolved
    fallback = repo_outputs_fallback()
    if not _warned_local_fallback:
        _LOG.warning(
            "OUTPUT_PATH/OUTPUTS_DIR unset; falling back to %s. "
            "Set OUTPUT_PATH in the factory .env so no channel writes to the repo tree.",
            fallback,
        )
        _warned_local_fallback = True
    return fallback


def assets_root() -> Path:
    """Factory assets root: ``ASSETS_PATH``, else ``outputs_root()``."""
    return _env_path("ASSETS_PATH") or outputs_root()


def outputs_dir() -> Path:
    """Backward-compatible alias for :func:`outputs_root`."""
    return outputs_root()


def coerce_outputs_path(raw: str | Path | None) -> Path:
    """Resolve a pack-relative ``outputs/...`` path against :func:`outputs_root`.

    Absolute paths are returned as-is. A leading ``outputs`` / ``output``
    segment is stripped so ``outputs/ancient_knowledge`` lands under the
    env root instead of ``<repo>/outputs/ancient_knowledge``.
    """
    if raw is None or str(raw).strip() == "":
        return outputs_root()
    path = Path(str(raw).strip())
    if path.is_absolute():
        return path
    parts = list(path.parts)
    if parts and parts[0].lower() in {"outputs", "output"}:
        parts = parts[1:]
    return outputs_root().joinpath(*parts) if parts else outputs_root()


def page_outputs_dir(page_id: str, *, create: bool = False) -> Path:
    slug = (page_id or "unknown").strip().lower() or "unknown"
    path = outputs_root() / slug
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def page_assets_dir(page_id: str, *, create: bool = False) -> Path:
    """Generated stills for a page: ``{OUTPUT_PATH}/{page}/assets``.

    ``ASSETS_PATH`` is the factory-level media root (shared/source assets).
    Per-page generated stills stay namespaced under the page outputs tree so
    libraries, planners, and image files remain one folder.
    """
    path = page_outputs_dir(page_id) / "assets"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def page_clips_dir(page_id: str, *, create: bool = True) -> Path:
    """Final MP4s for a page: ``{OUTPUT_PATH}/{page}/clips``."""
    path = page_outputs_dir(page_id) / "clips"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def page_metadata_dir(page_id: str, *, create: bool = True) -> Path:
    """Diagnostics / script JSON for a page: ``{OUTPUT_PATH}/{page}/metadata``."""
    path = page_outputs_dir(page_id) / "metadata"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def page_library_dir(page_id: str, *, create: bool = False) -> Path:
    """Telemetry / post JSON for a page: ``{OUTPUT_PATH}/{page}/library``."""
    path = page_outputs_dir(page_id) / "library"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def default_page_dir(page_id: str | None = None, *, create: bool = False) -> Path:
    """Resolve the active page outputs tree, defaulting to ``ACTIVE_PAGE``."""
    slug = (page_id or os.getenv("ACTIVE_PAGE") or "unknown").strip().lower()
    return page_outputs_dir(slug or "unknown", create=create)


def pipeline_logs_dir() -> Path:
    path = outputs_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def pipeline_tmp_dir(*parts: str) -> Path:
    path = outputs_dir() / "tmp"
    for part in parts:
        path = path / part
    path.mkdir(parents=True, exist_ok=True)
    return path


def moviepy_temp_audio_dir() -> str:
    """Directory MoviePy uses for ``*TEMP_MPY_wvf_snd*`` temp audio."""
    return str(pipeline_tmp_dir("moviepy"))


# ---------------------------------------------------------------------------
# Channel state (C: primary) and cleanup protection
# ---------------------------------------------------------------------------
STORE_DIRNAME = "store"
EPHEMERAL_DIR_NAMES = frozenset({
    "tmp",
    "temp",
    "scratch",
    "raw_frames",
    "renders",
    "__pycache__",
})
EPHEMERAL_DIR_PREFIXES = ("seq_run_", "lofi_run_")
_CHANNEL_DIR_SKIP = frozenset({
    "__pycache__",
    "tests",
    "shared",
    ".git",
})


class ProtectedStateError(RuntimeError):
    """Raised when a cleanup path would delete permanent channel state."""


def channels_config_root() -> Path:
    """``channels_config/`` (or ``CHANNEL_STORE_ROOT`` in tests)."""
    override = (os.getenv("CHANNEL_STORE_ROOT") or os.getenv("CHANNELS_CONFIG_ROOT") or "").strip()
    if override:
        return Path(override).expanduser()
    return _FACTORY_ROOT / "channels_config"


def channel_slug(channel: str | None) -> str:
    return (channel or "unknown").strip().lower() or "unknown"


def channel_config_dir(channel: str) -> Path:
    return channels_config_root() / channel_slug(channel)


def channel_store_dir(channel: str, *, create: bool = False) -> Path:
    """Primary permanent state: ``channels_config/<channel>/store/``."""
    path = channel_config_dir(channel) / STORE_DIRNAME
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def channel_mirror_dir(channel: str, *, create: bool = False) -> Path:
    """G: / factory outputs root for a channel: ``{OUTPUT_PATH}/<channel>/``."""
    return page_outputs_dir(channel, create=create)


def channel_mirror_store_dir(channel: str, *, create: bool = False) -> Path:
    """Cloud mirror of the local store: ``{OUTPUT_PATH}/<channel>/store/``."""
    path = channel_mirror_dir(channel) / STORE_DIRNAME
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def discover_channel_ids() -> list[str]:
    """Channel slugs that have a ``channels_config/<slug>/`` directory."""
    root = channels_config_root()
    if not root.is_dir():
        return []
    found: list[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if name in _CHANNEL_DIR_SKIP or name.startswith("."):
            continue
        found.append(name)
    return found


def is_ephemeral_artifact_dir(path: Path | str) -> bool:
    """True for build-only folders (tmp, raw_frames, seq_run_*, …)."""
    name = Path(path).name.lower()
    if name in EPHEMERAL_DIR_NAMES:
        return True
    return any(name.startswith(prefix) for prefix in EPHEMERAL_DIR_PREFIXES)


def _resolved(path: Path | str) -> Path:
    raw = Path(path)
    try:
        return raw.resolve()
    except OSError:
        return raw


def is_protected_state_path(path: Path | str) -> bool:
    """True when *path* is (or lives under) ``channels_config/<channel>/store/``."""
    resolved = _resolved(path)
    parts = [part.lower() for part in resolved.parts]
    for index, part in enumerate(parts):
        if part != "channels_config":
            continue
        if index + 2 < len(parts) and parts[index + 2] == STORE_DIRNAME:
            return True
    try:
        rel = resolved.relative_to(channels_config_root().resolve())
    except (ValueError, OSError):
        return False
    return len(rel.parts) >= 2 and rel.parts[1].lower() == STORE_DIRNAME


def contains_protected_store(path: Path | str) -> bool:
    """True if deleting *path* would remove a channel ``store/`` tree."""
    resolved = _resolved(path)
    if is_protected_state_path(resolved):
        return True
    try:
        root = channels_config_root().resolve()
        rel = resolved.relative_to(root)
    except (ValueError, OSError):
        return False
    return rel == Path(".") or len(rel.parts) == 1


def safe_rmtree(path: Path | str, *, missing_ok: bool = True) -> None:
    """``shutil.rmtree`` that refuses to touch ``channels_config/*/store/``."""
    import shutil

    target = Path(path)
    if contains_protected_store(target):
        raise ProtectedStateError(
            f"Refusing to delete protected channel state: {target}"
        )
    if not target.exists():
        if missing_ok:
            return
        raise FileNotFoundError(str(target))
    shutil.rmtree(target)
