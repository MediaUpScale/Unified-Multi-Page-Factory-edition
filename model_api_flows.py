# -*- coding: utf-8 -*-
"""
Runtime-selectable **model API flow presets** (provider / infra per media type).

Terminology (keep distinct — never interchangeable):
  - *flow preset* / ``model_api_flows`` — which provider handles image / audio / video
    (Together vs remote GPU vs ElevenLabs vs F5-TTS, etc.).
  - *comfy_workflow* / workflow JSON — ``.json`` graphs under ``remote_GPU_workflows/``,
    used only when the ``remote_gpu`` provider is selected for that media type.

Architecture rule (keep it this way):
  Presets reference workflow files **by name** via ``WORKFLOW_*`` constants in
  ``remote_gpu_manager`` — they do **not** duplicate or auto-list the contents of
  ``remote_GPU_workflows/``. Adding a new ``.json`` under that folder does **not**
  require touching this module. Update a preset only when exposing a new named,
  selectable production combination. Do not build auto-sync / auto-listing of
  every workflow file as a preset.

Precedence (highest → lowest):
  1. Per-media CLI flags (``--img-production``, ``--audio-production``, ``--video-production``)
  2. ``--model-api-flow <preset>``
  3. ``.env`` default (``ENABLE_REMOTE_GPU_WORKFLOWS``) — unchanged when no new flags are passed

LoRA resolution (image only):
  flow preset ``image.lora`` (if present) > channel ``REMOTE_GPU_LORA_*`` > no LoRA
  Sentinel ``"lora": "off"`` disables LoRA even when the channel has one configured.
  Omitting the ``lora`` key inherits the channel default.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

# Per-process log-once tracker for LoRA payload advisories. Without this the
# per-image call fires the same "Together LoRA skipped — Schnell doesn't
# support image_loras" warning ~160 times in a 10-reel batch. The warning is
# useful information (someone enabled LoRA on a Schnell-tier model) but only
# needs to be surfaced ONCE per (model_id, reason) combination per process.
_LORA_ADVISORY_LOCK = threading.Lock()
_LORA_ADVISORY_SEEN: set[tuple[str, str]] = set()


def _lora_advisory_once(reason: str, model_id: str | None, *args: Any) -> None:
    key = (reason, (model_id or "").lower())
    with _LORA_ADVISORY_LOCK:
        if key in _LORA_ADVISORY_SEEN:
            return
        _LORA_ADVISORY_SEEN.add(key)
    logger.warning(reason, *args)

# Sentinel: key omitted vs explicit off must remain distinguishable.
_LORA_OMITTED: object = object()
LORA_OFF: str = "off"

RemoteMode = Literal["comfyui", "runpod"]

# Import Comfy workflow JSON *names* (not path strings) from the remote GPU layer.
from core_engine.remote_gpu_manager import (  # noqa: E402
    WORKFLOW_F5TTS_TXT2AUDIO,
    WORKFLOW_FLUX_LORA_TXT2IMG,
    WORKFLOW_FLUX_TXT2IMG,
    WORKFLOW_WAN22_IMG2VID,
    workflows_dir,
)

TOGETHER_FLUX_SCHNELL = "black-forest-labs/FLUX.1-schnell"
TOGETHER_FLUX_DEV = "black-forest-labs/FLUX.1-dev"
TOGETHER_FLUX_DEV_LORA = "black-forest-labs/FLUX.1-dev-lora"

# ---------------------------------------------------------------------------
# Preset registry
# ---------------------------------------------------------------------------

FLOWS: dict[str, dict[str, Any]] = {
    # Legacy cloud stack — must ignore remote GPU even if .env has it on.
    "lowtier_together_elevenlabs": {
        "image": {
            "provider": "together",
            "model": TOGETHER_FLUX_SCHNELL,
            # No lora — Together Schnell has no LoRA support on our account/docs.
        },
        "audio": {"provider": "elevenlabs"},
        "video": {"provider": "moviepy"},
    },
    "hightier_together_elevenlabs": {
        "image": {
            "provider": "together",
            "model": TOGETHER_FLUX_DEV,
            # Omit lora → inherit channel default when a Together URL is available;
            # Schnell-style LoRA is never applied (see resolve warnings).
        },
        "audio": {"provider": "elevenlabs"},
        "video": {"provider": "moviepy"},
    },
    "remote_gpu_pod": {
        "mode": "comfyui",
        "image": {
            "provider": "remote_gpu",
            "workflow": WORKFLOW_FLUX_LORA_TXT2IMG,
            # lora omitted → channel REMOTE_GPU_LORA_* default
        },
        "audio": {
            "provider": "remote_gpu",
            "workflow": WORKFLOW_F5TTS_TXT2AUDIO,
        },
        "video": {
            "provider": "remote_gpu",
            "workflow": WORKFLOW_WAN22_IMG2VID,
        },
    },
    "remote_gpu_serverless": {
        "mode": "runpod",
        "image": {
            "provider": "remote_gpu",
            "workflow": WORKFLOW_FLUX_LORA_TXT2IMG,
        },
        "audio": {
            "provider": "remote_gpu",
            "workflow": WORKFLOW_F5TTS_TXT2AUDIO,
        },
        "video": {
            "provider": "remote_gpu",
            "workflow": WORKFLOW_WAN22_IMG2VID,
        },
    },
}


@dataclass
class MediaResolved:
    provider: str
    model: str | None = None
    """Together (or other) model id when applicable."""
    workflow: str | None = None
    """Comfy workflow JSON filename when provider=remote_gpu."""
    lora: Any = field(default=_LORA_OMITTED)
    """``_LORA_OMITTED`` | ``\"off\"`` | ``dict`` with name/strength/trigger[/url]."""

    def lora_is_omitted(self) -> bool:
        return self.lora is _LORA_OMITTED

    def lora_is_off(self) -> bool:
        if self.lora is _LORA_OMITTED:
            return False
        if self.lora is False or self.lora is None:
            return True
        if isinstance(self.lora, str) and self.lora.strip().lower() in {
            LORA_OFF, "none", "false", "0", "disabled",
        }:
            return True
        return False

    def lora_dict(self) -> dict[str, Any] | None:
        if self.lora_is_omitted() or self.lora_is_off():
            return None
        if isinstance(self.lora, dict):
            return self.lora
        return None


@dataclass
class ResolvedFlow:
    """Concrete provider/model selection after applying precedence rules."""

    preset_name: str | None
    """Named preset, ``\"env_default\"``, or ``\"custom\"`` when only per-media flags."""
    image: MediaResolved
    audio: MediaResolved
    video: MediaResolved
    mode: RemoteMode | None = None
    source: str = "env_default"
    """Human-readable resolution source for logs."""

    def summary_line(self) -> str:
        def _media(m: MediaResolved) -> str:
            bits = [m.provider]
            if m.model:
                bits.append(m.model)
            if m.workflow:
                bits.append(f"comfy_workflow={m.workflow}")
            if m.lora_is_off():
                bits.append("lora=off")
            elif m.lora_dict():
                ld = m.lora_dict() or {}
                bits.append(f"lora={ld.get('name') or ld.get('url') or 'on'}")
            elif not m.lora_is_omitted():
                bits.append("lora=?")
            else:
                bits.append("lora=inherit")
            return "/".join(str(b) for b in bits)

        return (
            f"preset={self.preset_name or '-'} | source={self.source} | "
            f"mode={self.mode or '-'} | "
            f"image={_media(self.image)} | "
            f"audio={_media(self.audio)} | "
            f"video={_media(self.video)}"
        )


# Process-wide active flow. ``None`` → pure .env behaviour (legacy).
_ACTIVE_FLOW: ResolvedFlow | None = None
_FLOW_EXPLICITLY_SET: bool = False


def get_active_flow() -> ResolvedFlow | None:
    return _ACTIVE_FLOW


def is_flow_explicitly_set() -> bool:
    """True when CLI selected a preset and/or per-media overrides."""
    return _FLOW_EXPLICITLY_SET


def set_active_flow(flow: ResolvedFlow | None, *, explicit: bool = True) -> None:
    global _ACTIVE_FLOW, _FLOW_EXPLICITLY_SET
    _ACTIVE_FLOW = flow
    _FLOW_EXPLICITLY_SET = bool(explicit and flow is not None)


def list_preset_names() -> list[str]:
    return sorted(FLOWS.keys())


def _env_remote_gpu_on() -> bool:
    raw = os.getenv("ENABLE_REMOTE_GPU_WORKFLOWS")
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    try:
        import config as app_config

        cfg_val = getattr(app_config, "ENABLE_REMOTE_GPU_WORKFLOWS", False)
    except Exception:
        cfg_val = False
    if isinstance(cfg_val, bool):
        return cfg_val
    return str(cfg_val).strip().lower() in ("1", "true", "yes", "on")


def _env_remote_mode() -> RemoteMode:
    raw = (os.getenv("REMOTE_GPU_MODE") or "").strip().lower()
    if raw in ("comfyui", "runpod"):
        return raw  # type: ignore[return-value]
    try:
        import config as app_config

        cfg = str(getattr(app_config, "REMOTE_GPU_MODE", "runpod") or "runpod").strip().lower()
    except Exception:
        cfg = "runpod"
    # Serverless is the default when remote GPU is on; pod only if explicitly set.
    return "comfyui" if cfg == "comfyui" else "runpod"


def env_default_flow() -> ResolvedFlow:
    """Today's behaviour when no new CLI flags are passed."""
    if _env_remote_gpu_on():
        mode = _env_remote_mode()
        return ResolvedFlow(
            preset_name="env_default",
            source="env_default(ENABLE_REMOTE_GPU_WORKFLOWS=true)",
            mode=mode,
            image=MediaResolved(
                provider="remote_gpu",
                workflow=WORKFLOW_FLUX_LORA_TXT2IMG,
            ),
            audio=MediaResolved(
                provider="remote_gpu",
                workflow=WORKFLOW_F5TTS_TXT2AUDIO,
            ),
            video=MediaResolved(
                provider="remote_gpu",
                workflow=WORKFLOW_WAN22_IMG2VID,
            ),
        )
    return ResolvedFlow(
        preset_name="env_default",
        source="env_default(ENABLE_REMOTE_GPU_WORKFLOWS=false)",
        mode=None,
        image=MediaResolved(provider="together", model=TOGETHER_FLUX_SCHNELL),
        audio=MediaResolved(provider="elevenlabs"),
        video=MediaResolved(provider="moviepy"),
    )


def _parse_media_override(raw: str | None, *, kind: str) -> MediaResolved | None:
    """
    Parse ``--img-production`` / ``--audio-production`` / ``--video-production``.

    Accepted forms:
      together
      together/black-forest-labs/FLUX.1-schnell
      remote_gpu
      remote_gpu/flux_dev_lora_txt_to_img.json
      elevenlabs
      f5_tts | f5-tts
      moviepy
      wan | wan22
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    parts = text.split("/", 1)
    provider = parts[0].strip().lower().replace("-", "_")
    rest = parts[1].strip() if len(parts) > 1 else ""

    aliases = {
        "f5": "remote_gpu",
        "f5_tts": "remote_gpu",
        "f5tts": "remote_gpu",
        "comfy": "remote_gpu",
        "comfyui": "remote_gpu",
        "runpod": "remote_gpu",
        "wan": "remote_gpu",
        "wan22": "remote_gpu",
        "wan2_2": "remote_gpu",
    }
    provider = aliases.get(provider, provider)

    if kind == "image":
        if provider in ("together", "together_ai", "flux"):
            model = rest or TOGETHER_FLUX_SCHNELL
            return MediaResolved(provider="together", model=model, lora=_LORA_OMITTED)
        if provider == "remote_gpu":
            wf = rest or WORKFLOW_FLUX_LORA_TXT2IMG
            return MediaResolved(provider="remote_gpu", workflow=wf, lora=_LORA_OMITTED)
        raise ValueError(
            f"Invalid --img-production '{raw}'. "
            "Use together[/model] or remote_gpu[/comfy_workflow.json]."
        )

    if kind == "audio":
        if provider in ("elevenlabs", "eleven", "el"):
            return MediaResolved(provider="elevenlabs")
        if provider == "remote_gpu":
            wf = rest or WORKFLOW_F5TTS_TXT2AUDIO
            return MediaResolved(provider="remote_gpu", workflow=wf)
        raise ValueError(
            f"Invalid --audio-production '{raw}'. "
            "Use elevenlabs or remote_gpu[/comfy_workflow.json]."
        )

    if kind == "video":
        if provider in ("moviepy", "local", "none"):
            return MediaResolved(provider="moviepy")
        if provider == "remote_gpu":
            wf = rest or WORKFLOW_WAN22_IMG2VID
            return MediaResolved(provider="remote_gpu", workflow=wf)
        raise ValueError(
            f"Invalid --video-production '{raw}'. "
            "Use moviepy or remote_gpu[/comfy_workflow.json]."
        )

    raise ValueError(f"Unknown production override kind={kind!r} raw={raw!r}")


def _media_from_preset_block(block: dict[str, Any] | None, *, kind: str) -> MediaResolved:
    block = block or {}
    provider = str(block.get("provider") or "").strip().lower()
    model = block.get("model")
    workflow = block.get("workflow")
    if "lora" in block:
        lora_val: Any = block["lora"]
    else:
        lora_val = _LORA_OMITTED
    if isinstance(workflow, str):
        workflow = workflow.strip() or None
    if isinstance(model, str):
        model = model.strip() or None
    if not provider:
        raise ValueError(f"Flow preset {kind} block missing provider: {block!r}")
    return MediaResolved(
        provider=provider,
        model=model,
        workflow=workflow,
        lora=lora_val,
    )


def resolve_production_flow(
    *,
    preset_name: str | None = None,
    img_production: str | None = None,
    audio_production: str | None = None,
    video_production: str | None = None,
) -> ResolvedFlow:
    """
    Resolve the active production flow from CLI inputs.

    When *all* of preset + per-media flags are empty/None, returns ``env_default_flow()``.
    """
    preset_name = (preset_name or "").strip() or None
    has_preset = preset_name is not None
    has_overrides = any(
        (x or "").strip() for x in (img_production, audio_production, video_production)
    )

    if not has_preset and not has_overrides:
        return env_default_flow()

    if has_preset:
        if preset_name not in FLOWS:
            known = ", ".join(list_preset_names())
            raise ValueError(
                f"Unknown --model-api-flow '{preset_name}'. Known presets: {known}"
            )
        base = deepcopy(FLOWS[preset_name])
        mode_raw = base.get("mode")
        mode: RemoteMode | None
        if mode_raw in ("comfyui", "runpod"):
            mode = mode_raw  # type: ignore[assignment]
        else:
            mode = None
        image = _media_from_preset_block(base.get("image"), kind="image")
        audio = _media_from_preset_block(base.get("audio"), kind="audio")
        video = _media_from_preset_block(base.get("video"), kind="video")
        source = f"model_api_flow:{preset_name}"
        resolved_preset = preset_name
    else:
        # Per-media only — start from env default, then overlay.
        env_flow = env_default_flow()
        image, audio, video = env_flow.image, env_flow.audio, env_flow.video
        mode = env_flow.mode
        source = "per_media_overrides"
        resolved_preset = "custom"

    img_ov = _parse_media_override(img_production, kind="image")
    aud_ov = _parse_media_override(audio_production, kind="audio")
    vid_ov = _parse_media_override(video_production, kind="video")
    if img_ov is not None:
        # Per-media wins entirely for that media (including lora omit → inherit channel).
        image = img_ov
        source = f"{source}+img_production"
    if aud_ov is not None:
        audio = aud_ov
        source = f"{source}+audio_production"
    if vid_ov is not None:
        video = vid_ov
        source = f"{source}+video_production"

    # Mode: from preset if remote anywhere; else None for pure together/elevenlabs.
    uses_remote = any(
        m.provider == "remote_gpu" for m in (image, audio, video)
    )
    if uses_remote and mode is None:
        mode = _env_remote_mode()
    if not uses_remote:
        mode = None

    flow = ResolvedFlow(
        preset_name=resolved_preset,
        source=source,
        mode=mode,
        image=image,
        audio=audio,
        video=video,
    )
    _warn_incompatible_lora(flow)
    return flow


def _warn_incompatible_lora(flow: ResolvedFlow) -> None:
    """lowtier / Schnell must not silently carry LoRA."""
    img = flow.image
    if img.provider != "together":
        return
    model = (img.model or "").lower()
    lora = img.lora_dict()
    if lora is None and img.lora_is_omitted():
        return
    if img.lora_is_off():
        return
    # Explicit lora on Schnell (or non-*-lora / non-dev models): reject/ignore.
    schnell = "schnell" in model
    dev_lora_ok = ("dev-lora" in model) or model.endswith("flux.2-dev") or (
        "flux.1-dev" in model and "schnell" not in model
    )
    if schnell or not dev_lora_ok:
        logger.warning(
            "model_api_flows | image LoRA ignored for Together model=%s "
            "(LoRA is Dev-tier only; lowtier/Schnell cannot carry lora). "
            "Remove image.lora from this preset or switch to a Dev LoRA model.",
            img.model,
        )
        img.lora = LORA_OFF


def collect_referenced_comfy_workflows(flow: ResolvedFlow | None = None) -> list[str]:
    """Comfy workflow JSON basenames referenced by *flow* or all presets."""
    names: set[str] = set()
    if flow is not None:
        for m in (flow.image, flow.audio, flow.video):
            if m.provider == "remote_gpu" and m.workflow:
                names.add(Path(str(m.workflow)).name)
        return sorted(names)

    for preset in FLOWS.values():
        for key in ("image", "audio", "video"):
            block = preset.get(key) or {}
            if str(block.get("provider") or "").lower() != "remote_gpu":
                continue
            wf = block.get("workflow")
            if wf:
                names.add(Path(str(wf)).name)
    return sorted(names)


def validate_comfy_workflows_exist(
    *,
    flow: ResolvedFlow | None = None,
    directory: Path | None = None,
) -> None:
    """
    Fail fast if any referenced comfy_workflow JSON is missing from
    ``remote_GPU_workflows/``.
    """
    root = Path(directory) if directory else workflows_dir()
    missing: list[str] = []
    for name in collect_referenced_comfy_workflows(flow):
        path = root / name
        if not path.is_file():
            missing.append(str(path))
    if missing:
        raise FileNotFoundError(
            "model_api_flows validation failed — missing comfy_workflow file(s):\n  - "
            + "\n  - ".join(missing)
            + f"\nExpected under: {root}"
        )


def apply_production_flow(flow: ResolvedFlow, *, explicit: bool = True) -> ResolvedFlow:
    """
    Activate *flow* for this process and sync env knobs used by legacy gates.

    When *explicit* is True (CLI preset / per-media flags), rewrite
    ``ENABLE_REMOTE_GPU_WORKFLOWS`` so Together/ElevenLabs presets fully ignore
    remote GPU even if ``.env`` had it on. Mixed per-media selections still
    rely on media-aware ``is_remote_gpu_enabled(media=...)``.
    """
    validate_comfy_workflows_exist(flow=flow)
    set_active_flow(flow, explicit=explicit)

    if explicit:
        uses_remote = any(
            m.provider == "remote_gpu" for m in (flow.image, flow.audio, flow.video)
        )
        os.environ["ENABLE_REMOTE_GPU_WORKFLOWS"] = "true" if uses_remote else "false"
        if flow.mode:
            os.environ["REMOTE_GPU_MODE"] = flow.mode
        if flow.image.provider == "remote_gpu" and flow.image.workflow:
            os.environ["REMOTE_GPU_FLUX_WORKFLOW"] = Path(flow.image.workflow).name
        # Keep imported config module in sync — some call sites read app_config
        # attributes that were frozen at process start from .env.
        try:
            import config as _app_cfg  # noqa: PLC0415

            _app_cfg.ENABLE_REMOTE_GPU_WORKFLOWS = bool(uses_remote)
            if flow.mode:
                _app_cfg.REMOTE_GPU_MODE = flow.mode
            if flow.image.provider == "remote_gpu" and flow.image.workflow:
                _app_cfg.REMOTE_GPU_FLUX_WORKFLOW = Path(flow.image.workflow).name
        except Exception:  # noqa: BLE001
            pass

    # Include post-apply env so logs prove Together/ElevenLabs presets forced
    # ENABLE_REMOTE_GPU_WORKFLOWS=false even when .env still has true on disk.
    _env_remote = (os.getenv("ENABLE_REMOTE_GPU_WORKFLOWS") or "").strip().lower() or "?"
    logger.info(
        "MODEL_API_FLOW | %s | ENABLE_REMOTE_GPU_WORKFLOWS=%s",
        flow.summary_line(),
        _env_remote,
    )
    return flow


def media_uses_remote_gpu(media: str) -> bool | None:
    """
    Return True/False when an explicit/active flow answers for *media*,
    or ``None`` to fall back to the .env boolean.
    """
    flow = get_active_flow()
    if flow is None:
        return None
    key = (media or "").strip().lower()
    if key == "image":
        return flow.image.provider == "remote_gpu"
    if key == "audio":
        return flow.audio.provider == "remote_gpu"
    if key == "video":
        return flow.video.provider == "remote_gpu"
    return None


def resolved_together_image_model() -> str | None:
    """Together model id from the active flow, if image provider is together."""
    flow = get_active_flow()
    if flow is None or flow.image.provider != "together":
        return None
    return flow.image.model


def resolve_effective_lora(
    page_id: str | None = None,
    *,
    page_dir: Path | str | None = None,
) -> dict[str, Any]:
    """
    LoRA resolution with flow-preset layering.

    Order: flow ``image.lora`` (if present) > channel default > disabled.
    ``\"off\"`` disables even when the channel has LoRA configured.
    """
    from core_engine.remote_gpu_manager import resolve_page_lora_config

    # Channel only — avoid recursion through apply_flow_preset.
    channel = resolve_page_lora_config(
        page_id, page_dir=page_dir, apply_flow_preset=False,
    )
    flow = get_active_flow()
    if flow is None:
        return channel

    img = flow.image
    if img.lora_is_off():
        return {
            "enabled": False,
            "lora_name": None,
            "trigger": "",
            "strength": float(channel.get("strength") or 0.8),
            "url": None,
            "workflow_name": WORKFLOW_FLUX_TXT2IMG,
            "page_id": channel.get("page_id"),
            "source": "flow_preset:off",
        }

    if img.lora_is_omitted():
        out = dict(channel)
        out["url"] = _channel_lora_url(page_id, page_dir=page_dir)
        out["source"] = "channel_default"
        return out

    ld = img.lora_dict() or {}
    name = ld.get("name")
    trigger = str(ld.get("trigger") or "").strip()
    try:
        strength = float(ld.get("strength", 0.8) or 0.8)
    except (TypeError, ValueError):
        strength = 0.8
    url = (ld.get("url") or ld.get("path") or "").strip() or None
    if not url:
        url = _channel_lora_url(page_id, page_dir=page_dir)

    disabled = {None, "", "none", "off", "false", "0", "disabled"}
    if name is None or str(name).strip().lower() in disabled:
        enabled = bool(url)
        lora_name = None
    else:
        enabled = True
        from core_engine.remote_gpu_manager import _normalize_lora_filename

        lora_name = _normalize_lora_filename(str(name))

    workflow_name = img.workflow or (
        WORKFLOW_FLUX_LORA_TXT2IMG if enabled else WORKFLOW_FLUX_TXT2IMG
    )
    return {
        "enabled": enabled,
        "lora_name": lora_name,
        "trigger": trigger if enabled else "",
        "strength": strength,
        "url": url,
        "workflow_name": Path(str(workflow_name)).name,
        "page_id": channel.get("page_id"),
        "source": "flow_preset",
    }


def _channel_lora_url(
    page_id: str | None,
    *,
    page_dir: Path | str | None = None,
) -> str | None:
    """Optional hosted URL for Together ``image_loras`` (HF / public)."""
    env_url = (os.getenv("REMOTE_GPU_LORA_URL") or os.getenv("TOGETHER_LORA_URL") or "").strip()
    if env_url:
        return env_url
    try:
        from core_engine.remote_gpu_manager import (  # noqa: PLC0415
            _load_page_config_module,
            _page_config_dir,
        )

        pid = (page_id or os.getenv("ACTIVE_PAGE") or "").strip()
        cfg_dir = Path(page_dir) if page_dir else (_page_config_dir(pid) if pid else None)
        mod = _load_page_config_module(cfg_dir)
        if mod is None:
            return None
        for attr in ("REMOTE_GPU_LORA_URL", "TOGETHER_LORA_URL", "LORA_HOSTED_URL"):
            val = getattr(mod, attr, None)
            if val and str(val).strip():
                return str(val).strip()
    except Exception:
        return None
    return None


def together_image_loras_payload(
    lora_cfg: dict[str, Any] | None,
    *,
    model_id: str | None,
) -> list[dict[str, Any]] | None:
    """
    Translate canonical LoRA identity → Together ``image_loras`` payload.

    Returns None when LoRA must not be sent (Schnell, missing URL, disabled).
    """
    if not lora_cfg or not lora_cfg.get("enabled"):
        return None
    model = (model_id or "").lower()
    if "schnell" in model:
        _lora_advisory_once(
            "Together LoRA skipped — model %s does not support image_loras "
            "(Dev-tier / FLUX.1-dev-lora only). This message is logged once "
            "per process; disable LoRA in page_config.py to silence it.",
            model_id,
            model_id,
        )
        return None
    url = (lora_cfg.get("url") or "").strip()
    if not url:
        _lora_advisory_once(
            "Together LoRA enabled but no hosted URL (set REMOTE_GPU_LORA_URL / "
            "image.lora.url). Comfy local name=%r cannot be used as Together "
            "path. Logged once per process.",
            model_id,
            lora_cfg.get("lora_name"),
        )
        return None
    if not re.match(r"^https?://", url, flags=re.I):
        _lora_advisory_once(
            "Together LoRA url must be http(s):// — got %r. Logged once per process.",
            model_id,
            url,
        )
        return None
    scale = float(lora_cfg.get("strength") or 0.8)
    return [{"path": url, "scale": scale}]


def bootstrap_validate_all_presets() -> None:
    """Startup check: every comfy_workflow referenced by FLOWS exists on disk."""
    validate_comfy_workflows_exist(flow=None)
