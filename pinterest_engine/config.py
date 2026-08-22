# -*- coding: utf-8 -*-
"""
pinterest_engine.config
-----------------------
Agnostic multi-channel configuration for the Pinterest engine.

Runtime root:
    Path(__file__).resolve().parents[1]

Channel context (``--channel <channel_id>``) drives:
  - Environment file selection
  - Inventory / outputs directory
  - Isolated scheduled_history.txt
  - Brand CTAs / URLs / AI prompts from the channel content pack

Content packs live exclusively under:
  ``channels_config/<channel_id>/config.json``
  (also accepts ``channel_config.json`` / legacy ``page_config.json`` filenames)

Legacy compatibility (record fields / filenames only):
  - Record field ``channel_id`` preferred; falls back to ``page_id``
  - Filename ``channel_config.json`` preferred; falls back to ``page_config.json``
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Workspace root (parent of pinterest_engine/)
ENGINE_ROOT: Path = Path(__file__).resolve().parents[1]

# Sole channel configuration root (no channels/ or pages_config/ probes).
CHANNELS_CONFIG_DIR: Path = ENGINE_ROOT / "channels_config"

# Content-pack filenames inside channels_config/<channel_id>/
_CHANNEL_CONFIG_FILENAMES = (
    "config.json",
    "channel_config.json",
    "page_config.json",  # legacy filename inside channels_config
)

DOTENV_PATH: Path = ENGINE_ROOT / ".env"
_DOTENV_RESOLVED_PATH: Path = DOTENV_PATH
DOTENV_LOADED_FROM_FILE: bool = False

CHANNEL_ID: str | None = None
CHANNEL_CONTENT_PATH: Path | None = None


def _load_dotenv_file(env_path: Path) -> tuple[Path, bool]:
    """Load a .env file with utf-8-sig so Drive BOM ghosts are stripped."""
    resolved = env_path.expanduser().resolve()
    if not resolved.is_file():
        return resolved, False

    raw = resolved.read_bytes()
    utf8_bom = bytes([0xEF, 0xBB, 0xBF])
    if raw[:3] == utf8_bom:
        try:
            resolved.write_bytes(raw[3:])
            logger.debug(".env BOM stripped automatically.")
        except OSError:
            pass

    loaded = bool(load_dotenv(dotenv_path=resolved, override=True, encoding="utf-8-sig"))
    _honour_blank_env_values(resolved)
    return resolved, loaded


def _honour_blank_env_values(env_path: Path) -> None:
    """Force KEY= (empty) assignments so a channel env can clear inherited tokens."""
    try:
        raw = env_path.read_text(encoding="utf-8-sig")
    except OSError:
        return
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, val = stripped.split("=", 1)
        if val.strip() == "":
            os.environ[key.strip()] = ""


def _resolve_path(value: str | None, default: Path) -> Path:
    return Path((value or str(default))).expanduser()


def _parse_interval_hours(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _safe_channel_id(channel_id: str) -> str:
    """Normalize channel id to a filesystem-safe slug."""
    slug = channel_id.strip().replace("\\", "/").split("/")[-1]
    slug = re.sub(r"[^\w.\-]+", "_", slug, flags=re.UNICODE)
    return slug or "default"


def get_record_channel_id(record: dict | None) -> str | None:
    """
    Read channel identity from a record with legacy fallback.

    Prefers ``channel_id``, then ``page_id`` (legacy).
    """
    if not isinstance(record, dict):
        return None
    for key in ("channel_id", "page_id"):
        val = record.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def resolve_channel_home_dir(channel_id: str | None = None) -> Path | None:
    """Return ``channels_config/<channel_id>/`` if that directory exists."""
    cid = channel_id or CHANNEL_ID
    if not cid:
        return None
    candidate = CHANNELS_CONFIG_DIR / cid
    return candidate if candidate.is_dir() else None


# Back-compat alias used by older call sites
resolve_channel_config_dir = resolve_channel_home_dir


def resolve_channel_env_path(channel_id: str) -> Path:
    """
    Pick the best env file for a channel.

    Order:
      1. ``.env.<channel_id>`` (workspace root)
      2. ``channels_config/<channel_id>/.env``
      3. workspace ``.env``
    """
    cid = _safe_channel_id(channel_id)
    candidates = [
        ENGINE_ROOT / f".env.{cid}",
        CHANNELS_CONFIG_DIR / cid / ".env",
        ENGINE_ROOT / ".env",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return ENGINE_ROOT / ".env"


def resolve_channel_outputs_dir(channel_id: str) -> Path:
    """
    Inventory/history root for a channel.

    Precedence
    ----------
    1. ``outputs_dir`` / ``inventory_dir`` from the channel content pack
    2. ``outputs/<channel_id>/`` when that tree already has inventory,
       history, a library, or content_library.json
    3. ``data/<channel_id>/`` when that tree has inventory/history
    4. Legacy shared ``outputs/`` only for channels that do **not** have
       their own library tree (preserves anna_protocol root ledger)
    5. Fresh isolated ``outputs/<channel_id>/``
    """
    cid = _safe_channel_id(channel_id)
    pack, _ = load_channel_content_pack(cid)
    pack_dir = _pack_get(pack, "outputs_dir", "inventory_dir")
    if pack_dir:
        resolved = Path(pack_dir)
        if not resolved.is_absolute():
            resolved = ENGINE_ROOT / resolved
        logger.info("Channel '%s': outputs from content pack: %s", cid, resolved)
        return resolved

    outputs_chan = ENGINE_ROOT / "outputs" / cid
    root_outputs = ENGINE_ROOT / "outputs"
    # Preserve the original anna_protocol ledger at outputs/ until that
    # channel gets its own isolated inventory/history files.
    if cid == "anna_protocol":
        chan_has_inventory = (
            (outputs_chan / "master_inventory.json").is_file()
            or (outputs_chan / "scheduled_history.txt").is_file()
        )
        root_has_state = (
            (root_outputs / "master_inventory.json").is_file()
            or (root_outputs / "scheduled_history.txt").is_file()
        )
        if root_has_state and not chan_has_inventory:
            logger.info(
                "Channel 'anna_protocol': using legacy shared outputs/ ledger."
            )
            return root_outputs
    data_chan = ENGINE_ROOT / "data" / cid
    root_outputs = ENGINE_ROOT / "outputs"
    library_dir = outputs_chan / "library"

    chan_has_library = library_dir.is_dir() and any(library_dir.glob("post_*.json"))
    chan_has_content = (outputs_chan / "content_library.json").is_file()
    chan_has_state = (
        (outputs_chan / "master_inventory.json").is_file()
        or (outputs_chan / "scheduled_history.txt").is_file()
        or chan_has_library
        or chan_has_content
    )
    if chan_has_state:
        return outputs_chan

    if data_chan.is_dir() and (
        (data_chan / "master_inventory.json").is_file()
        or (data_chan / "scheduled_history.txt").is_file()
    ):
        return data_chan

    root_has_state = (
        (root_outputs / "master_inventory.json").is_file()
        or (root_outputs / "scheduled_history.txt").is_file()
    )
    # Only bridge known channels that have no isolated library of their own.
    if root_has_state and resolve_channel_home_dir(cid) is not None:
        logger.info(
            "Channel '%s': using legacy shared outputs/ "
            "(inventory/history not yet under outputs/%s/).",
            cid, cid,
        )
        return root_outputs

    return outputs_chan


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read channel config %s: %s", path, exc)
        return {}


def load_channel_content_pack(channel_id: str | None = None) -> tuple[dict[str, Any], Path | None]:
    """
    Load channel-scoped CTA/URL/prompt pack from config JSON.

    Returns (pack_dict, source_path_or_None).
    """
    home = resolve_channel_home_dir(channel_id)
    if home is None:
        return {}, None
    for name in _CHANNEL_CONFIG_FILENAMES:
        path = home / name
        if path.is_file():
            pack = _read_json_file(path)
            if pack:
                logger.info("Channel content pack loaded: %s", path)
                return pack, path
    return {}, None


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        # Support JSON array string or newline / pipe delimited
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [str(x).strip() for x in parsed if str(x).strip()]
            except json.JSONDecodeError:
                pass
        if "\n" in text:
            return [ln.strip() for ln in text.splitlines() if ln.strip()]
        if "|" in text:
            return [p.strip() for p in text.split("|") if p.strip()]
        return [text]
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    return []


def _pack_get(pack: dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        val = pack.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return default


def _refresh_channel_content() -> None:
    """
    Populate brand/content fields from channel pack + env.

    Core engine defaults are always empty/neutral. No hardcoded brand URLs,
    persona prompts, hashtags, or CTA templates live in code.
    """
    global TARGET_URL, HASHTAGS, CTA_BUTTON_LABEL, CTA_VARIANTS
    global DEFAULT_TOPIC, AI_SYSTEM_PROMPT, CHANNEL_CONTENT_PATH, DISPLAY_NAME
    global ANTHROPIC_API_KEY, MIN_INTERVAL_HOURS, MAX_INTERVAL_HOURS
    global PINTEREST_PINS_PER_DAY, BOARD_NAME, PIN_THEME

    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    MIN_INTERVAL_HOURS = _parse_interval_hours("MIN_INTERVAL_HOURS", 3.0)
    MAX_INTERVAL_HOURS = _parse_interval_hours("MAX_INTERVAL_HOURS", 6.0)
    PINTEREST_PINS_PER_DAY = int(os.getenv("PINTEREST_PINS_PER_DAY", "4"))

    pack, CHANNEL_CONTENT_PATH = load_channel_content_pack(CHANNEL_ID)

    # Env overrides pack; both override empty core defaults.
    TARGET_URL = (
        os.getenv("PINTEREST_TARGET_URL")
        or os.getenv("TARGET_URL")
        or _pack_get(pack, "target_url", "sales_url", "url")
    ).strip()

    HASHTAGS = (
        os.getenv("PINTEREST_HASHTAGS")
        or _pack_get(pack, "hashtags")
    ).strip()

    CTA_BUTTON_LABEL = (
        os.getenv("PINTEREST_CTA_LABEL")
        or _pack_get(pack, "cta_button_label", "cta_label", "button_label")
    ).strip()

    env_variants = _as_str_list(os.getenv("PINTEREST_CTA_VARIANTS"))
    pack_variants = _as_str_list(
        pack.get("cta_variants")
        or pack.get("sales_cta_variants")
        or pack.get("cta_templates")
    )
    CTA_VARIANTS = env_variants or pack_variants

    DEFAULT_TOPIC = (
        os.getenv("PINTEREST_DEFAULT_TOPIC")
        or _pack_get(pack, "default_topic")
        or "Untitled"
    ).strip()

    AI_SYSTEM_PROMPT = (
        os.getenv("PINTEREST_AI_SYSTEM_PROMPT")
        or _pack_get(pack, "ai_system_prompt", "persona_prompt", "system_prompt")
    ).strip()

    DISPLAY_NAME = _pack_get(
        pack, "display_name", "channel_name", "name",
        default=CHANNEL_ID or "",
    )

    BOARD_NAME = (
        os.getenv("PINTEREST_BOARD_NAME")
        or _pack_get(pack, "board_name", "display_name", default=DISPLAY_NAME)
    ).strip()

    raw_theme = pack.get("pin_theme") if isinstance(pack.get("pin_theme"), dict) else {}
    PIN_THEME = {str(k): v for k, v in raw_theme.items()}


def _apply_paths(outputs_dir: Path | None = None) -> None:
    """Recompute path constants after env / CLI / channel overrides."""
    global OUTPUTS_DIR, ASSETS_DIR, LIBRARY_DIR, CONTENT_LIBRARY_PATH
    global MASTER_INVENTORY_PATH, PINTEREST_HISTORY_PATH, SCHEDULED_HISTORY_PATH
    global CHANNEL_CONFIG_DIR, CHANNEL_HOME_DIR

    if outputs_dir is not None:
        OUTPUTS_DIR = Path(outputs_dir)
    elif CHANNEL_ID:
        OUTPUTS_DIR = resolve_channel_outputs_dir(CHANNEL_ID)
    else:
        default_outputs = ENGINE_ROOT / "outputs"
        OUTPUTS_DIR = _resolve_path(os.getenv("OUTPUTS_DIR"), default_outputs)

    ASSETS_DIR = OUTPUTS_DIR / "assets"
    LIBRARY_DIR = OUTPUTS_DIR / "library"
    CONTENT_LIBRARY_PATH = OUTPUTS_DIR / "content_library.json"
    MASTER_INVENTORY_PATH = OUTPUTS_DIR / "master_inventory.json"
    PINTEREST_HISTORY_PATH = OUTPUTS_DIR / "pinterest_history.json"
    SCHEDULED_HISTORY_PATH = OUTPUTS_DIR / "scheduled_history.txt"
    CHANNEL_HOME_DIR = resolve_channel_home_dir(CHANNEL_ID)
    CHANNEL_CONFIG_DIR = CHANNEL_HOME_DIR  # alias

    _refresh_channel_content()

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)


def configure(
    channel_id: str | None = None,
    env_path: str | Path | None = None,
    inventory_dir: str | Path | None = None,
) -> None:
    """
    Apply CLI / channel context for a run.

    Precedence
    ----------
    env file:
        explicit ``--env`` > channel-resolved env > workspace ``.env``
    inventory/outputs:
        explicit ``--inventory-dir`` > channel outputs resolution >
        workspace ``outputs/`` (or OUTPUTS_DIR env)
    content (CTAs/URLs/prompts):
        channel ``.env`` keys > channel ``config.json`` > empty
    """
    global CHANNEL_ID, DOTENV_PATH, _DOTENV_RESOLVED_PATH, DOTENV_LOADED_FROM_FILE

    CHANNEL_ID = _safe_channel_id(channel_id) if channel_id else None

    root_env = ENGINE_ROOT / ".env"
    # Shared keys (Claude, etc.) always load from workspace .env first.
    if root_env.is_file():
        _load_dotenv_file(root_env)

    if env_path is not None:
        DOTENV_PATH = Path(env_path).expanduser()
    elif CHANNEL_ID:
        DOTENV_PATH = resolve_channel_env_path(CHANNEL_ID)
    else:
        DOTENV_PATH = root_env

    if DOTENV_PATH.resolve() != root_env.resolve():
        _DOTENV_RESOLVED_PATH, DOTENV_LOADED_FROM_FILE = _load_dotenv_file(DOTENV_PATH)
    else:
        _DOTENV_RESOLVED_PATH, DOTENV_LOADED_FROM_FILE = _load_dotenv_file(DOTENV_PATH)

    out = Path(inventory_dir).expanduser() if inventory_dir is not None else None
    _apply_paths(out)


def format_cta_variant(template: str, url: str | None = None) -> str:
    """Expand ``{url}`` / ``{target_url}`` placeholders in a CTA template."""
    link = (url if url is not None else TARGET_URL) or ""
    return (
        template
        .replace("{url}", link)
        .replace("{target_url}", link)
        .replace("{URL}", link)
    )


def print_dotenv_bootstrap() -> None:
    if CHANNEL_ID:
        print(f"[bootstrap] channel       : {CHANNEL_ID}")
        if DISPLAY_NAME and DISPLAY_NAME != CHANNEL_ID:
            print(f"[bootstrap] display name  : {DISPLAY_NAME}")
    if DOTENV_LOADED_FROM_FILE:
        print(f"[bootstrap] .env loaded   : {_DOTENV_RESOLVED_PATH}")
    else:
        print(f"[bootstrap] .env not loaded from {_DOTENV_RESOLVED_PATH}")
    print(f"[bootstrap] outputs dir   : {OUTPUTS_DIR}")
    print(f"[bootstrap] history file  : {SCHEDULED_HISTORY_PATH}")
    if CHANNEL_HOME_DIR is not None:
        print(f"[bootstrap] channel home  : {CHANNEL_HOME_DIR}")
    if CHANNEL_CONTENT_PATH is not None:
        print(f"[bootstrap] content pack  : {CHANNEL_CONTENT_PATH}")
    print(f"[bootstrap] target_url    : {TARGET_URL or '(none — inventory/metadata only)'}")
    print(f"[bootstrap] board name    : {BOARD_NAME or '(none)'}")
    print(f"[bootstrap] cta variants  : {len(CTA_VARIANTS)}")
    print(f"[bootstrap] ai prompt     : {'set' if AI_SYSTEM_PROMPT else '(none)'}")


def get_best_claude_model(anthropic_client: object | None = None) -> str:
    """Resolve a Claude model id for optional AI metadata generation."""
    fallback = (os.getenv("CLAUDE_MODEL") or "claude-3-5-sonnet-latest").strip()
    if anthropic_client is None:
        return fallback
    try:
        listing = anthropic_client.models.list()  # type: ignore[union-attr]
        models = list(getattr(listing, "data", None) or listing)
        for priority in ("sonnet", "haiku"):
            for m in models:
                mid = str(getattr(m, "id", "") or "").lower()
                if priority in mid and "claude" in mid:
                    return mid
        for m in models:
            mid = str(getattr(m, "id", "") or "")
            if "claude" in mid.lower():
                return mid
    except Exception as exc:  # noqa: BLE001
        logger.debug("Claude model discovery failed (%s); using fallback.", exc)
    return fallback


# ---------------------------------------------------------------------------
# Bootstrap defaults on import (workspace .env, root outputs/, empty content)
# ---------------------------------------------------------------------------
_DOTENV_RESOLVED_PATH, DOTENV_LOADED_FROM_FILE = _load_dotenv_file(DOTENV_PATH)

OUTPUTS_DIR: Path
ASSETS_DIR: Path
LIBRARY_DIR: Path
CONTENT_LIBRARY_PATH: Path
MASTER_INVENTORY_PATH: Path
PINTEREST_HISTORY_PATH: Path
SCHEDULED_HISTORY_PATH: Path
CHANNEL_HOME_DIR: Path | None
CHANNEL_CONFIG_DIR: Path | None
ANTHROPIC_API_KEY: str | None
MIN_INTERVAL_HOURS: float
MAX_INTERVAL_HOURS: float
PINTEREST_PINS_PER_DAY: int
TARGET_URL: str
HASHTAGS: str
CTA_BUTTON_LABEL: str
CTA_VARIANTS: list[str]
DEFAULT_TOPIC: str
AI_SYSTEM_PROMPT: str
DISPLAY_NAME: str
BOARD_NAME: str
PIN_THEME: dict[str, Any]

_apply_paths()
