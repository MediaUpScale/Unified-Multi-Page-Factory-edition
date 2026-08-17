# -*- coding: utf-8 -*-
"""
Remote GPU workflow manager — ComfyUI / RunPod adapter layer.

Loads API-format JSON templates from ``remote_GPU_workflows/`` and exposes
simple ``generate_image`` / ``generate_audio`` / ``generate_video`` wrappers.

Activation is gated by ``ENABLE_REMOTE_GPU_WORKFLOWS`` (see ``config.py``).
When the flag is false, callers must continue using the legacy Together /
ElevenLabs / MoviePy paths — this module is never required on that path.
"""
from __future__ import annotations

import copy
import json
import logging
import mimetypes
import os
import random
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

# Per-thread pending uploads so concurrent serverless jobs do not race.
_TLS = threading.local()

from core_engine.comfy_workflow_convert import convert_ui_to_api, is_api_format

logger = logging.getLogger(__name__)


class RemoteGPUError(RuntimeError):
    """Raised for ComfyUI / RunPod transport or job failures."""


# ---------------------------------------------------------------------------
# Workflow template filenames (relative to REMOTE_GPU_WORKFLOWS_DIR)
# ---------------------------------------------------------------------------
WORKFLOW_FLUX_TXT2IMG = "flux_dev_txt_to_img.json"
WORKFLOW_FLUX_LORA_TXT2IMG = "flux_dev_lora_txt_to_img.json"
WORKFLOW_F5TTS_TXT2AUDIO = "F5TTS_txt_to_audio.json"
WORKFLOW_WAN22_IMG2VID = "wan2_2_img_to_video.json"

# Legacy hardcoded IDs (fallbacks). Prefer class_type resolution at inject time.
_FLUX_PROMPT_NODE = "56:51"
_FLUX_LATENT_NODE = "56:50"
_FLUX_SAMPLER_NODE = "56:52"
_FLUX_SAVE_NODE = "9"

_F5_LOAD_AUDIO_NODE = "10"
_F5_TTS_NODE = "13"

_WAN_LOAD_IMAGE_NODE = "97"
_WAN_POS_PROMPT_NODE = "129:93"
_WAN_NEG_PROMPT_NODE = "129:89"
_WAN_I2V_NODE = "129:98"
_WAN_SEED_NODE = "129:86"
_WAN_STEPS_4_NODE = "129:118"
_WAN_STEPS_20_NODE = "129:128"
_WAN_DURATION_NODE = "129:161"
_WAN_FPS_NODE = "129:162"
_WAN_SAVE_NODE = "108"

_DEFAULT_POLL_S = 2.0
_DEFAULT_TIMEOUT_S = 600.0
_HTTP_TIMEOUT_S = 120.0

# Custom serverless worker (hearmeman) mounts ComfyUI input here — NOT
# runpod-slim/ComfyUI/input (pod interactive path). Confirmed in start logs:
#   Setting input directory to: /runpod-volume/serverless_data/input
_DEFAULT_SERVERLESS_INPUT_PREFIX = "serverless_data/input"
_DEFAULT_S3_ENDPOINT_EU_RO_1 = "https://s3api-eu-ro-1.runpod.io"

# Cached RunPod network-volume S3 client (process-wide).
_S3_LOCK = threading.Lock()
_S3_CACHE: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Config helpers (lazy — avoid hard import failures when unused)
# ---------------------------------------------------------------------------

def _cfg(name: str, default: Any = None) -> Any:
    """
    Runtime config lookup.

    Prefer ``os.environ`` so ``model_api_flows.apply_production_flow`` (and other
    CLI overrides) win over values frozen on ``config`` at import time
    (e.g. ``REMOTE_GPU_MODE=runpod`` after a ``comfyui`` .env default).
    """
    if name in os.environ and str(os.environ.get(name, "")).strip() != "":
        return os.environ.get(name)
    try:
        import config as app_config  # noqa: PLC0415

        return getattr(app_config, name, default)
    except Exception:  # noqa: BLE001
        return os.getenv(name, default)


def _safe_upload_name(path: Path) -> str:
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in path.name)
    if not safe:
        safe = "input.bin"
    if not Path(safe).suffix:
        safe = f"{safe}{path.suffix or '.png'}"
    return safe


def _resolve_runpod_s3_creds() -> dict[str, str] | None:
    """
    Resolve S3-compatible credentials for the endpoint's network volume.

    Access key is the RunPod ``user_…`` id (from endpoint metadata when unset).
    Secret is ``RUNPOD_S3_SECRET_ACCESS_KEY`` or ``RUNPOD_VOLUME_EU-RO-1_KEY``.
    """
    secret = (
        str(_cfg("RUNPOD_S3_SECRET_ACCESS_KEY", "") or "").strip()
        or str(_cfg("RUNPOD_VOLUME_EU-RO-1_KEY", "") or "").strip()
        or str(os.getenv("RUNPOD_VOLUME_EU-RO-1_KEY") or "").strip()
    )
    access_key = (
        str(_cfg("RUNPOD_S3_ACCESS_KEY_ID", "") or "").strip()
        or str(os.getenv("RUNPOD_S3_ACCESS_KEY_ID") or "").strip()
    )
    volume_id = (
        str(_cfg("RUNPOD_NETWORK_VOLUME_ID", "") or "").strip()
        or str(os.getenv("RUNPOD_NETWORK_VOLUME_ID") or "").strip()
    )
    endpoint = (
        str(_cfg("RUNPOD_S3_ENDPOINT", "") or "").strip()
        or str(os.getenv("RUNPOD_S3_ENDPOINT") or "").strip()
        or _DEFAULT_S3_ENDPOINT_EU_RO_1
    )
    input_prefix = (
        str(_cfg("RUNPOD_COMFY_INPUT_PREFIX", "") or "").strip()
        or str(os.getenv("RUNPOD_COMFY_INPUT_PREFIX") or "").strip()
        or _DEFAULT_SERVERLESS_INPUT_PREFIX
    ).strip("/")

    api_key = (
        str(_cfg("RUNPOD_API_KEY", "") or "").strip()
        or str(os.getenv("RUNPOD_API_KEY") or "").strip()
    )
    endpoint_id = (
        str(_cfg("RUNPOD_ENDPOINT_ID", "") or "").strip()
        or str(os.getenv("RUNPOD_ENDPOINT_ID") or "").strip()
    )

    if (not access_key or not volume_id) and api_key and endpoint_id:
        try:
            url = f"https://rest.runpod.io/v1/endpoints/{endpoint_id}"
            req = Request(url, headers={"Authorization": f"Bearer {api_key}"})
            with urlopen(req, timeout=30) as resp:
                meta = json.loads(resp.read().decode("utf-8"))
            if not access_key:
                access_key = str(meta.get("userId") or "").strip()
            if not volume_id:
                volume_id = str(
                    meta.get("networkVolumeId")
                    or (meta.get("networkVolumeIds") or [None])[0]
                    or ""
                ).strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not auto-resolve RunPod volume S3 metadata: %s", exc)

    if not secret or not access_key or not volume_id:
        return None
    return {
        "access_key": access_key,
        "secret": secret,
        "volume_id": volume_id,
        "endpoint": endpoint.rstrip("/"),
        "input_prefix": input_prefix,
    }


def _get_runpod_s3_client(creds: Mapping[str, str]) -> Any:
    cache_key = f"{creds['endpoint']}|{creds['access_key']}|{creds['volume_id']}"
    with _S3_LOCK:
        cached = _S3_CACHE.get(cache_key)
        if cached is not None:
            return cached
        try:
            import boto3  # noqa: PLC0415
            from botocore.client import Config as BotoConfig  # noqa: PLC0415
        except ImportError as exc:
            raise RemoteGPUError(
                "boto3 is required for RunPod network-volume uploads "
                "(pip install boto3)"
            ) from exc
        client = boto3.client(
            "s3",
            endpoint_url=creds["endpoint"],
            aws_access_key_id=creds["access_key"],
            aws_secret_access_key=creds["secret"],
            config=BotoConfig(signature_version="s3v4"),
            region_name="eu-ro-1",
        )
        _S3_CACHE[cache_key] = client
        return client


def stage_file_on_runpod_volume(
    local_path: Path | str,
    *,
    server_name: str | None = None,
) -> str:
    """
    Blocking upload into the serverless ComfyUI input dir on the network volume.

    Waits for S3 PutObject + HeadObject confirmation before returning so Wan's
    LoadImage can resolve the filename when /prompt is submitted afterward.
    """
    path = Path(local_path)
    if not path.is_file():
        raise FileNotFoundError(f"Volume stage source missing: {path}")
    creds = _resolve_runpod_s3_creds()
    if creds is None:
        raise RemoteGPUError(
            "RunPod network-volume S3 credentials incomplete. Set "
            "RUNPOD_VOLUME_EU-RO-1_KEY (secret) and optionally "
            "RUNPOD_S3_ACCESS_KEY_ID / RUNPOD_NETWORK_VOLUME_ID "
            "(auto-fetched from the endpoint when possible)."
        )
    name = server_name or _safe_upload_name(path)
    key = f"{creds['input_prefix']}/{name}"
    client = _get_runpod_s3_client(creds)
    body = path.read_bytes()
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    t0 = time.monotonic()
    client.put_object(
        Bucket=creds["volume_id"],
        Key=key,
        Body=body,
        ContentType=content_type,
    )
    # RunPod S3 often returns 403 on HeadObject; confirm via GetObject instead.
    try:
        got = client.get_object(Bucket=creds["volume_id"], Key=key)
        size = int(got.get("ContentLength") or 0)
        # Drain body so the connection can close cleanly.
        try:
            got["Body"].read(1)
            got["Body"].close()
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        listed = client.list_objects_v2(
            Bucket=creds["volume_id"], Prefix=key, MaxKeys=1,
        )
        matches = listed.get("Contents") or []
        if not matches or matches[0].get("Key") != key:
            raise RemoteGPUError(
                f"Volume upload not visible after PutObject: s3://{creds['volume_id']}/{key}"
            ) from exc
        size = int(matches[0].get("Size") or 0)
    if size <= 0:
        raise RemoteGPUError(
            f"Volume upload confirmed empty object: s3://{creds['volume_id']}/{key}"
        )
    # Brief settle for volume→worker visibility (NFS-style mounts).
    time.sleep(0.35)
    logger.info(
        "Volume upload confirmed | s3://%s/%s | %d bytes | %.2fs",
        creds["volume_id"], key, size, time.monotonic() - t0,
    )
    return name


def remote_gpu_max_parallel() -> int:
    """
    Max concurrent remote-GPU jobs for this process (any modality).

    Reads ``REMOTE_GPU_MAX_PARALLEL`` / ``REMOTE_GPU_WORKERS`` (default 5).
    Must match (or stay ≤) the serverless endpoint worker count so extra
    workers are actually utilized — not a hardcoded fan-out.

    Used by ``run_parallel_jobs`` for image / audio / video batches alike —
    not an image-only knob.
    """
    for env_key in ("REMOTE_GPU_MAX_PARALLEL", "REMOTE_GPU_WORKERS"):
        raw = (os.getenv(env_key) or "").strip()
        if raw:
            try:
                return max(1, int(raw))
            except ValueError:
                pass
    cfg_val = _cfg("REMOTE_GPU_MAX_PARALLEL", 5)
    try:
        return max(1, int(cfg_val))
    except (TypeError, ValueError):
        return 5


def run_parallel_jobs(
    jobs: Mapping[Any, Any],
    *,
    max_workers: int | None = None,
) -> dict[Any, Any]:
    """
    Generic concurrent job runner for remote-GPU (and any) zero-arg callables.

    ``jobs`` maps an id → zero-arg callable. Fan-out defaults to
    ``remote_gpu_max_parallel()`` so image acts, future WAN video blocks, and
    batched audio share the same worker-scaled concurrency.

    Returns ``{id: result_or_exception}`` — never raises.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: dict[Any, Any] = {}
    if not jobs:
        return results
    n = int(max_workers) if max_workers is not None else remote_gpu_max_parallel()
    workers = max(1, min(n, len(jobs)))
    logger.info(
        "run_parallel_jobs | n_jobs=%d max_workers=%d (REMOTE_GPU_MAX_PARALLEL)",
        len(jobs), workers,
    )
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn): idx for idx, fn in jobs.items()}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results[idx] = fut.result()
            except Exception as exc:  # noqa: BLE001
                results[idx] = exc
    return results


def is_remote_gpu_enabled(media: str | None = None) -> bool:
    """
    True when remote GPU should handle generation.

    When a runtime *flow preset* is active (``model_api_flows``), *media*
    selects the answer for ``\"image\"`` / ``\"audio\"`` / ``\"video\"``.
    Without an active flow (or with ``media=None``), falls back to the
    ``ENABLE_REMOTE_GPU_WORKFLOWS`` env/config boolean — today's behaviour.

    Reads ``os.environ`` first so long-running processes
    (``--schedule-uploads``) pick up flag flips without relying solely on
    import-time ``config`` attributes.
    """
    if media:
        try:
            from model_api_flows import media_uses_remote_gpu  # noqa: PLC0415

            answered = media_uses_remote_gpu(media)
            if answered is not None:
                return bool(answered)
        except Exception:
            pass

    # If an explicit flow is active but caller omitted media, prefer "any remote".
    try:
        from model_api_flows import (  # noqa: PLC0415
            get_active_flow,
            is_flow_explicitly_set,
        )

        flow = get_active_flow()
        if flow is not None and is_flow_explicitly_set() and media is None:
            return any(
                m.provider == "remote_gpu"
                for m in (flow.image, flow.audio, flow.video)
            )
    except Exception:
        pass

    raw = os.getenv("ENABLE_REMOTE_GPU_WORKFLOWS")
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    cfg_val = _cfg("ENABLE_REMOTE_GPU_WORKFLOWS", False)
    if isinstance(cfg_val, bool):
        return cfg_val
    return str(cfg_val).strip().lower() in ("1", "true", "yes", "on")


def normalize_comfy_base_url(url: str) -> str:
    """
    Strip UI fragments / trailing junk from a ComfyUI base URL.

    Browser copies often look like ``https://…proxy.runpod.net/#<uuid>`` —
    the ``#…`` fragment is client-side only and must never be sent to the API.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    # Drop fragment explicitly (urlsplit also isolates it)
    raw = raw.split("#", 1)[0].strip()
    parts = urlsplit(raw)
    cleaned = urlunsplit((parts.scheme, parts.netloc, parts.path or "", "", ""))
    if not cleaned:
        return ""
    return cleaned if cleaned.endswith("/") else cleaned + "/"


def workflows_dir() -> Path:
    configured = _cfg("REMOTE_GPU_WORKFLOWS_DIR", None)
    if configured:
        p = Path(str(configured)).expanduser()
        if not p.is_absolute():
            root = Path(str(_cfg("ENGINE_ROOT", Path(__file__).resolve().parent.parent)))
            p = root / p
        return p.resolve()
    root = Path(str(_cfg("ENGINE_ROOT", Path(__file__).resolve().parent.parent)))
    return (root / "remote_GPU_workflows").resolve()


def default_output_dir(kind: str = "misc") -> Path:
    """Return ``outputs/<page>/remote_gpu/<kind>/`` (or project fallback)."""
    base = _cfg("PAGE_OUTPUTS_DIR", None) or _cfg("OUTPUTS_DIR", None)
    if base is None:
        base = Path(__file__).resolve().parent.parent / "outputs" / "remote_gpu_tests"
    out = Path(base) / "remote_gpu" / kind
    out.mkdir(parents=True, exist_ok=True)
    return out


def _engine_root() -> Path:
    return Path(str(_cfg("ENGINE_ROOT", Path(__file__).resolve().parent.parent)))


def _page_config_dir(page_id: str) -> Path:
    """Prefer ``channels_config/<page>/``, fall back to legacy ``pages_config/``."""
    root = _engine_root()
    channels = root / "channels_config" / page_id
    if channels.is_dir():
        return channels
    legacy = root / "pages_config" / page_id
    return legacy if legacy.is_dir() else channels


def _read_text_if_exists(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return None
    return text or None


def _companion_txt(audio_path: Path) -> Path:
    return audio_path.with_suffix(".txt")


# Audio extensions accepted as F5-TTS reference clips (LoadAudio on ComfyUI).
_VOICE_REF_EXTS: tuple[str, ...] = (".wav", ".mp3", ".flac", ".ogg", ".m4a")


def _first_existing(paths: list[Path]) -> Path | None:
    for p in paths:
        if p.is_file():
            return p.resolve()
    return None


def _list_voice_ref_media(voice_dir: Path, *, pattern: str) -> list[Path]:
    """Glob *pattern* for every accepted voice-ref extension, sorted."""
    found: list[Path] = []
    for ext in _VOICE_REF_EXTS:
        # pattern is stem-ish, e.g. "*_voice_ref*" or "*"
        found.extend(voice_dir.glob(f"{pattern}{ext}"))
    return sorted({p.resolve() for p in found if p.is_file()})


def resolve_page_voice_reference(
    page_id: str | None = None,
    *,
    page_dir: Path | str | None = None,
    allow_global_fallback: bool = True,
) -> tuple[Path | None, str | None]:
    """
    Resolve F5-TTS ``(reference_audio, sample_text)`` for a page/channel.

    Per-page lookup (``channels_config/<page>/voice_reference/``):
      1. Optional ``VOICE_REFERENCE_AUDIO`` / ``VOICE_REFERENCE_TEXT`` in page_config
      2. ``{page_id}_voice_ref_10s.{wav|mp3|…}`` + matching ``.txt``
      3. First ``*_voice_ref*.{wav|mp3|…}`` (or sole media file) + matching ``.txt``

    Global fallback (only when *allow_global_fallback* and no page media):
      ``REMOTE_GPU_DEFAULT_REF_AUDIO`` must be a **local file that exists**.
      Bare server filenames (e.g. ``sample_10s.wav`` with no local copy) are
      **not** accepted — they cause ComfyUI LoadAudio 400s after wasting GPU time.

    Returns ``(None, None)`` when nothing usable is configured yet.
    """
    pid = (page_id or os.getenv("ACTIVE_PAGE") or _cfg("ACTIVE_PAGE", None) or "").strip()
    cfg_dir = Path(page_dir) if page_dir else (_page_config_dir(pid) if pid else None)
    voice_dir = (cfg_dir / "voice_reference") if cfg_dir is not None else None

    audio: Path | None = None
    sample: str | None = None

    if voice_dir is not None:
        voice_dir.mkdir(parents=True, exist_ok=True)

        # Optional explicit overrides from page_config.py (no page_loader import)
        page_cfg_audio, page_cfg_text = _read_page_voice_overrides(cfg_dir)

        if page_cfg_audio:
            cand = Path(page_cfg_audio).expanduser()
            if not cand.is_file():
                cand = voice_dir / page_cfg_audio
            # Allow override stem without extension → try accepted exts
            if not cand.is_file() and cand.suffix.lower() not in _VOICE_REF_EXTS:
                cand = _first_existing(
                    [voice_dir / f"{cand.name}{ext}" for ext in _VOICE_REF_EXTS]
                ) or cand
            if cand.is_file():
                audio = cand.resolve()
        if page_cfg_text:
            text_path = Path(page_cfg_text).expanduser()
            if not text_path.is_file():
                text_path = voice_dir / page_cfg_text
            sample = _read_text_if_exists(text_path) or page_cfg_text

        if audio is None and pid:
            audio = _first_existing(
                [voice_dir / f"{pid}_voice_ref_10s{ext}" for ext in _VOICE_REF_EXTS]
            )

        if audio is None:
            named = _list_voice_ref_media(voice_dir, pattern="*_voice_ref*")
            if named:
                audio = named[0]
            else:
                any_media = _list_voice_ref_media(voice_dir, pattern="*")
                # Ignore non-audio junk (README.txt etc. already excluded by ext)
                if len(any_media) == 1:
                    audio = any_media[0]

        if audio is not None and sample is None:
            sample = _read_text_if_exists(_companion_txt(audio))

        if audio is not None:
            logger.info(
                "Voice reference (page=%s) | audio=%s | sample_text=%s",
                pid or "?",
                audio.name,
                "yes" if sample else "missing",
            )
            return audio, sample

    if not allow_global_fallback:
        return None, None

    env_audio = (
        os.getenv("REMOTE_GPU_DEFAULT_REF_AUDIO")
        or _cfg("REMOTE_GPU_DEFAULT_REF_AUDIO", None)
        or ""
    ).strip()
    env_text = (
        os.getenv("REMOTE_GPU_DEFAULT_REF_TEXT")
        or _cfg("REMOTE_GPU_DEFAULT_REF_TEXT", None)
        or ""
    ).strip()

    if not env_audio:
        return None, None

    global_audio = Path(env_audio).expanduser()
    if not global_audio.is_file():
        # Relative to engine root / cwd
        for base in (
            Path(str(_cfg("ENGINE_ROOT", Path(__file__).resolve().parent.parent))),
            Path.cwd(),
        ):
            cand = (base / env_audio).resolve()
            if cand.is_file():
                global_audio = cand
                break

    if global_audio.is_file():
        audio = global_audio.resolve()
        sample = env_text or _read_text_if_exists(_companion_txt(audio))
        if not sample and env_text:
            sample = env_text
        logger.info(
            "Voice reference (global) | audio=%s | sample_text=%s",
            audio.name,
            "yes" if sample else "missing",
        )
        return audio, sample

    # Do NOT treat missing names like sample_10s.wav as "already on server".
    # That path burned ~315s of serverless GPU on LoadAudio validation failures.
    logger.warning(
        "REMOTE_GPU_DEFAULT_REF_AUDIO=%r is not a local file — ignoring bare "
        "server filename (add channels_config/<page>/voice_reference/"
        "<page>_voice_ref_10s.wav|.mp3 instead).",
        env_audio,
    )
    return None, None


def _load_page_config_module(cfg_dir: Path | None):
    """Load page_config.py from *cfg_dir* (or None on failure)."""
    if cfg_dir is None:
        return None
    cfg_path = cfg_dir / "page_config.py"
    if not cfg_path.is_file():
        return None
    try:
        import importlib.util  # noqa: PLC0415

        spec = importlib.util.spec_from_file_location(
            f"_page_cfg_{cfg_dir.name}", cfg_path
        )
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # noqa: BLE001
        return None


def _read_page_voice_overrides(cfg_dir: Path | None) -> tuple[str, str]:
    """Read optional VOICE_REFERENCE_* symbols from page_config.py if present."""
    mod = _load_page_config_module(cfg_dir)
    if mod is None:
        return "", ""
    audio = str(getattr(mod, "VOICE_REFERENCE_AUDIO", "") or "").strip()
    text = str(getattr(mod, "VOICE_REFERENCE_TEXT", "") or "").strip()
    return audio, text


def _normalize_lora_filename(name: str) -> str:
    """Ensure ComfyUI lora_name has a ``.safetensors`` suffix when omitted."""
    raw = (name or "").strip()
    if not raw:
        return ""
    low = raw.lower()
    if low.endswith((".safetensors", ".ckpt", ".pt", ".bin")):
        return raw
    return f"{raw}.safetensors"


def apply_lora_trigger(prompt: str, trigger: str | None) -> str:
    """
    Prepend the channel LoRA trigger word once (case-insensitive whole-token check).

    Used at the same remote image entry point ECONOMIC_REEL already hits
    (``RemoteGPUImageAdapter.generate`` → ``generate_image``), so there is no
    separate prompt path for LoRA channels.
    """
    word = (trigger or "").strip()
    body = (prompt or "").strip()
    if not word or not body:
        return body or prompt
    # Already present as a standalone token near the start / anywhere
    tokens = {t.strip(".,;:!?\"'()[]{}").lower() for t in body.split()}
    if word.lower() in tokens:
        return body
    return f"{word}, {body}"


def resolve_page_lora_config(
    page_id: str | None = None,
    *,
    page_dir: Path | str | None = None,
    apply_flow_preset: bool = True,
) -> dict[str, Any]:
    """
    Resolve per-channel Flux LoRA settings from ``page_config.py``.

    Returns dict::
        {
          "enabled": bool,
          "lora_name": str | None,   # ComfyUI filename (with .safetensors)
          "trigger": str,
          "strength": float,
          "workflow_name": str,      # LoRA graph or base Flux
        }

    ``REMOTE_GPU_LORA_NAME`` empty / None / false / "off" / "none" → no LoRA
    (base ``flux_dev_txt_to_img.json``).

    When a model_api_flows preset sets ``image.lora`` (including ``\"off\"``),
    that layer wins over the channel default unless ``apply_flow_preset=False``.
    """
    # Flow-preset LoRA layer (model_api_flows) wins when explicitly set.
    # Imported lazily to avoid circular imports at module load.
    if apply_flow_preset:
        try:
            from model_api_flows import (  # noqa: PLC0415
                get_active_flow,
                resolve_effective_lora,
            )

            flow = get_active_flow()
            if flow is not None and not flow.image.lora_is_omitted():
                return resolve_effective_lora(page_id, page_dir=page_dir)
        except Exception:
            pass

    pid = (page_id or os.getenv("ACTIVE_PAGE") or _cfg("ACTIVE_PAGE", None) or "").strip()
    cfg_dir = Path(page_dir) if page_dir else (_page_config_dir(pid) if pid else None)
    mod = _load_page_config_module(cfg_dir)

    raw_name = getattr(mod, "REMOTE_GPU_LORA_NAME", None) if mod is not None else None
    # Env override (optional debugging)
    env_name = (os.getenv("REMOTE_GPU_LORA_NAME") or "").strip()
    if env_name:
        raw_name = env_name

    disabled_tokens = {"", "none", "off", "false", "0", "disabled"}
    if raw_name is None or raw_name is False:
        enabled = False
        lora_name = None
    else:
        name_str = str(raw_name).strip()
        if name_str.lower() in disabled_tokens:
            enabled = False
            lora_name = None
        else:
            enabled = True
            lora_name = _normalize_lora_filename(name_str)

    trigger = ""
    strength = 0.8
    if mod is not None:
        trigger = str(getattr(mod, "REMOTE_GPU_LORA_TRIGGER", "") or "").strip()
        try:
            strength = float(getattr(mod, "REMOTE_GPU_LORA_STRENGTH", 0.8) or 0.8)
        except (TypeError, ValueError):
            strength = 0.8
    env_trigger = (os.getenv("REMOTE_GPU_LORA_TRIGGER") or "").strip()
    if env_trigger:
        trigger = env_trigger
    env_strength = (os.getenv("REMOTE_GPU_LORA_STRENGTH") or "").strip()
    if env_strength:
        try:
            strength = float(env_strength)
        except ValueError:
            pass

    # Explicit workflow override wins; else LoRA graph when enabled, else base
    env_wf = (
        os.getenv("REMOTE_GPU_FLUX_WORKFLOW")
        or _cfg("REMOTE_GPU_FLUX_WORKFLOW", None)
        or ""
    ).strip()
    if env_wf:
        workflow_name = env_wf
    elif enabled:
        workflow_name = WORKFLOW_FLUX_LORA_TXT2IMG
    else:
        workflow_name = WORKFLOW_FLUX_TXT2IMG

    url = None
    try:
        from model_api_flows import _channel_lora_url  # noqa: PLC0415

        url = _channel_lora_url(pid, page_dir=page_dir)
    except Exception:
        url = None

    return {
        "enabled": enabled,
        "lora_name": lora_name,
        "trigger": trigger if enabled else "",
        "strength": float(strength),
        "workflow_name": workflow_name,
        "page_id": pid or None,
        "url": url,
        "source": "channel_default",
    }


# ---------------------------------------------------------------------------
# Workflow JSON load + injection
# ---------------------------------------------------------------------------

def load_workflow(name: str, *, directory: Path | None = None) -> dict[str, Any]:
    """
    Load a ComfyUI workflow JSON and return API-format prompt dict.

    Accepts both API-format exports and UI-format saves (auto-converted,
    including subgraph expansion).
    """
    root = directory or workflows_dir()
    path = root / name
    if not path.is_file():
        raise FileNotFoundError(
            f"Remote GPU workflow not found: {path} "
            f"(expected under {root})"
        )
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Workflow {path.name} must be a JSON object.")
    if is_api_format(data):
        return data
    logger.info("Workflow %s is UI-format — converting to API prompt dict", path.name)
    return convert_ui_to_api(data)


def deep_copy_workflow(workflow: Mapping[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(dict(workflow))


def set_node_input(
    workflow: MutableMapping[str, Any],
    node_id: str,
    key: str,
    value: Any,
) -> None:
    """Set ``workflow[node_id]['inputs'][key] = value`` (creates inputs if needed)."""
    node = workflow.get(node_id)
    if not isinstance(node, dict):
        raise KeyError(f"Workflow node '{node_id}' not found")
    inputs = node.setdefault("inputs", {})
    if not isinstance(inputs, dict):
        raise TypeError(f"Node '{node_id}' inputs must be a dict")
    inputs[key] = value


def _log_wan_sampler_inputs(workflow: Mapping[str, Any], *, four_step: bool) -> None:
    """Log the steps/cfg/model values KSampler will actually receive in this prompt."""
    for nid in find_nodes_by_class(workflow, "KSamplerAdvanced"):
        inp = ((workflow.get(nid) or {}).get("inputs") or {})
        logger.info(
            "WAN KSampler payload | node=%s four_step_flag=%s steps=%r cfg=%r "
            "start_at_step=%r end_at_step=%r model=%r",
            nid,
            four_step,
            inp.get("steps"),
            inp.get("cfg"),
            inp.get("start_at_step"),
            inp.get("end_at_step"),
            inp.get("model"),
        )
    for nid in find_nodes_by_class(workflow, "ComfySwitchNode"):
        inp = ((workflow.get(nid) or {}).get("inputs") or {})
        title = str(((workflow.get(nid) or {}).get("_meta") or {}).get("title") or "")
        logger.info(
            "WAN Switch payload | node=%s title=%r switch=%r on_true=%r on_false=%r",
            nid, title, inp.get("switch"), inp.get("on_true"), inp.get("on_false"),
        )
    for nid in find_nodes_by_class(workflow, "PrimitiveBoolean"):
        inp = ((workflow.get(nid) or {}).get("inputs") or {})
        title = str(((workflow.get(nid) or {}).get("_meta") or {}).get("title") or "")
        logger.info(
            "WAN PrimitiveBoolean | node=%s title=%r value=%r",
            nid, title, inp.get("value"),
        )
    for nid in find_nodes_by_class(workflow, "LoraLoaderModelOnly"):
        inp = ((workflow.get(nid) or {}).get("inputs") or {})
        logger.info(
            "WAN LoRA loader in graph | node=%s lora_name=%r model=%r",
            nid, inp.get("lora_name"), inp.get("model"),
        )


def patch_workflow(
    workflow: MutableMapping[str, Any],
    patches: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """
    Apply ``{node_id: {input_key: value, ...}, ...}`` patches.

    Returns the same workflow mapping for chaining.
    """
    for node_id, fields in patches.items():
        for key, value in fields.items():
            set_node_input(workflow, node_id, key, value)
    return dict(workflow)


def find_nodes_by_class(
    workflow: Mapping[str, Any],
    class_type: str,
) -> list[str]:
    return [
        nid
        for nid, node in workflow.items()
        if isinstance(node, dict) and node.get("class_type") == class_type
    ]


def resolve_node(
    workflow: Mapping[str, Any],
    *,
    class_type: str | None = None,
    fallback_id: str | None = None,
    title_contains: str | None = None,
    prefer_last: bool = False,
) -> str:
    """
    Resolve a node id by ``class_type`` (preferred) with hardcoded fallback.

    When multiple matches exist, returns the first (or last if ``prefer_last``).
    Optional ``title_contains`` filters on ``_meta.title`` when present.
    """
    matches = find_nodes_by_class(workflow, class_type) if class_type else []
    if title_contains and matches:
        titled = []
        needle = title_contains.lower()
        for nid in matches:
            meta = (workflow[nid] or {}).get("_meta") or {}
            title = str(meta.get("title") or "")
            if needle in title.lower():
                titled.append(nid)
        if titled:
            matches = titled
    if matches:
        return matches[-1] if prefer_last else matches[0]
    if fallback_id and fallback_id in workflow:
        return fallback_id
    raise KeyError(
        f"Cannot resolve node class_type={class_type!r} fallback={fallback_id!r} "
        f"in workflow ({len(workflow)} nodes)"
    )


_VRAM_STAGING_RE = re.compile(
    r"(dynamic\s+vram|vram\s+loading|partially\s+loaded|unloading\s+(?:model|unet|weight)"
    r"|offload|distorch|model_management|loading\s+WAN|loading\s+diffusion"
    r"|unloaded|memory\s+low|oom)",
    re.IGNORECASE,
)


def _summarize_runpod_status(data: Mapping[str, Any], *, job_id: str) -> dict[str, Any]:
    """Keep timing/log fields from a RunPod status payload; drop base64 blobs."""
    output = data.get("output") if isinstance(data, dict) else None
    logs = ""
    if isinstance(output, dict):
        raw_logs = output.get("logs") or output.get("log") or output.get("worker_logs")
        if isinstance(raw_logs, str):
            logs = raw_logs
        elif isinstance(raw_logs, list):
            logs = "\n".join(str(x) for x in raw_logs)
    elif isinstance(data, dict):
        raw_logs = data.get("logs") or data.get("log")
        if isinstance(raw_logs, str):
            logs = raw_logs
    hits = [m.group(0) for m in _VRAM_STAGING_RE.finditer(logs)] if logs else []
    return {
        "job_id": job_id,
        "status": data.get("status") if isinstance(data, dict) else None,
        "delayTime": data.get("delayTime") if isinstance(data, dict) else None,
        "executionTime": data.get("executionTime") if isinstance(data, dict) else None,
        "workerId": data.get("workerId") if isinstance(data, dict) else None,
        "gpu": data.get("gpu") if isinstance(data, dict) else None,
        "vram_staging_hits": sorted(set(hits), key=str.lower),
        "vram_staging_present": bool(hits),
        "log_excerpt": (logs[-4000:] if logs else ""),
        "output_keys": (
            list(output.keys()) if isinstance(output, dict) else []
        ),
    }


# ---------------------------------------------------------------------------
# HTTP client (stdlib urllib — no new hard dependency)
# ---------------------------------------------------------------------------

class ComfyUIClient:
    """
    Talk to a ComfyUI HTTP server (``http://<pod>:8188``) or a RunPod
    Serverless ComfyUI endpoint.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        mode: str | None = None,
        api_key: str | None = None,
        endpoint_id: str | None = None,
        poll_interval_s: float | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self.mode = (mode or str(_cfg("REMOTE_GPU_MODE", "runpod") or "runpod")).strip().lower()
        self.api_key = (
            api_key
            or _cfg("RUNPOD_API_KEY", None)
            or os.getenv("RUNPOD_API_KEY")
            or ""
        ).strip()
        self.endpoint_id = (
            endpoint_id
            or _cfg("RUNPOD_ENDPOINT_ID", None)
            or os.getenv("RUNPOD_ENDPOINT_ID")
            or ""
        ).strip()
        self.poll_interval_s = float(
            poll_interval_s
            if poll_interval_s is not None
            else (_cfg("REMOTE_GPU_POLL_INTERVAL_S", _DEFAULT_POLL_S) or _DEFAULT_POLL_S)
        )
        self.timeout_s = float(
            timeout_s
            if timeout_s is not None
            else (_cfg("REMOTE_GPU_TIMEOUT_S", _DEFAULT_TIMEOUT_S) or _DEFAULT_TIMEOUT_S)
        )
        self.client_id = str(uuid.uuid4())
        self.last_job_seconds: float = 0.0
        self.last_job_meta: dict[str, Any] = {}
        self._gpu_seconds_lock = threading.Lock()
        self.total_gpu_seconds: float = 0.0

        raw_url = (
            base_url
            or os.getenv("REMOTE_GPU_BASE_URL")
            or os.getenv("COMFYUI_BASE_URL")
            or _cfg("REMOTE_GPU_BASE_URL", None)
            or ""
        )
        if self.mode == "runpod":
            ep_url = (
                os.getenv("RUNPOD_ENDPOINT_URL")
                or _cfg("RUNPOD_ENDPOINT_URL", None)
                or ""
            )
            ep_url = normalize_comfy_base_url(str(ep_url or ""))
            if ep_url:
                self.base_url = ep_url
            elif self.endpoint_id:
                self.base_url = f"https://api.runpod.ai/v2/{self.endpoint_id}/"
            else:
                raise RemoteGPUError(
                    "RunPod mode requires RUNPOD_ENDPOINT_URL or RUNPOD_ENDPOINT_ID"
                )
        else:
            self.base_url = normalize_comfy_base_url(str(raw_url or ""))
            if not self.base_url:
                raise RemoteGPUError(
                    "REMOTE_GPU_BASE_URL (or COMFYUI_BASE_URL) is required when "
                    "ENABLE_REMOTE_GPU_WORKFLOWS=true (e.g. http://<POD_IP>:8188). "
                    "Do not include the browser '#workflow-uuid' fragment."
                )

    # -- low-level HTTP -----------------------------------------------------

    def _headers(self, *, json_body: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {"User-Agent": "UnifiedMultiPageFactory-RemoteGPU/1.0"}
        if json_body:
            headers["Content-Type"] = "application/json"
        # RunPod serverless always needs the key. ComfyUI proxy URLs
        # (*.proxy.runpod.net) also reject unauthenticated calls with HTTP 403.
        if self.api_key and (
            self.mode == "runpod"
            or "proxy.runpod.net" in (self.base_url or "").lower()
        ):
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        url = urljoin(self.base_url, path.lstrip("/"))
        hdrs = dict(headers or self._headers(json_body=data is not None))
        req = Request(url, data=data, headers=hdrs, method=method.upper())
        try:
            with urlopen(req, timeout=timeout or _HTTP_TIMEOUT_S) as resp:
                body = resp.read()
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                return int(resp.status), body, resp_headers
        except HTTPError as exc:
            err_body = exc.read() if hasattr(exc, "read") else b""
            raise RemoteGPUError(
                f"HTTP {exc.code} {method} {url}: {err_body[:500]!r}"
            ) from exc
        except URLError as exc:
            raise RemoteGPUError(f"Connection failed {method} {url}: {exc}") from exc

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        _status, body, _hdrs = self._request(
            method, path, data=data, timeout=timeout
        )
        if not body:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RemoteGPUError(
                f"Non-JSON response from {path}: {body[:300]!r}"
            ) from exc

    # -- uploads ------------------------------------------------------------

    def upload_file(
        self,
        local_path: Path | str,
        *,
        media_kind: str = "auto",
        overwrite: bool = True,
        # Back-compat: older callers passed image=True/False (dead ternary).
        image: bool | None = None,
    ) -> str:
        """
        Upload a local file into the ComfyUI ``input/`` folder.

        Stock ComfyUI exposes a single multipart endpoint ``POST /upload/image``
        for images *and* audio (LoadAudio reads from the same input directory;
        the frontend's audio uploader also posts to ``/upload/image``). There is
        no separate ``/upload/audio`` on the pod API.

        Returns the server-side filename to reference in LoadImage / LoadAudio.
        """
        path = Path(local_path)
        if not path.is_file():
            raise FileNotFoundError(f"Upload source missing: {path}")

        kind = (media_kind or "auto").strip().lower()
        if image is not None and kind == "auto":
            kind = "image" if image else "audio"
        if kind == "auto":
            mime_guess = (mimetypes.guess_type(path.name)[0] or "").lower()
            if mime_guess.startswith("audio/") or path.suffix.lower() in {
                ".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac",
            }:
                kind = "audio"
            else:
                kind = "image"

        if self.mode == "runpod":
            # Official worker-comfyui: upload to ComfyUI input, confirm, THEN
            # /prompt. Our custom serverless worker ignores input.images[] and
            # validates LoadImage immediately — so stage onto the network
            # volume path that ComfyUI actually uses (serverless_data/input)
            # and wait for S3 confirmation before returning.
            safe_name = _safe_upload_name(path)
            staged = False
            try:
                safe_name = stage_file_on_runpod_volume(path, server_name=safe_name)
                staged = True
            except RemoteGPUError as exc:
                logger.warning(
                    "Volume stage unavailable (%s); falling back to input.images[] "
                    "(may fail on custom workers that skip upload-before-/prompt)",
                    exc,
                )

            # Keep images[] as a secondary path for stock worker-comfyui.
            import base64  # noqa: PLC0415

            raw = path.read_bytes()
            b64 = base64.b64encode(raw).decode("ascii")
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            entry = {
                "name": safe_name,
                "image": f"data:{mime};base64,{b64}",
            }
            pending = getattr(_TLS, "pending_uploads", None)
            if pending is None:
                pending = []
                _TLS.pending_uploads = pending
            pending.append(entry)
            logger.info(
                "RunPod upload ready | kind=%s | %s (%d bytes) | volume_staged=%s",
                kind, safe_name, path.stat().st_size, staged,
            )
            return safe_name

        # ComfyUI multipart upload — ALWAYS /upload/image (official audio path too)
        boundary = f"----RemoteGPU{uuid.uuid4().hex}"
        field_name = "image"
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        file_bytes = path.read_bytes()
        parts: list[bytes] = []
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field_name}"; filename="{path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n".encode("utf-8")
            + file_bytes
            + b"\r\n"
        )
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="overwrite"\r\n\r\n'
            f"{'true' if overwrite else 'false'}\r\n".encode("utf-8")
        )
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)
        headers = self._headers(json_body=False)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        endpoint = "upload/image"
        _status, resp_body, _ = self._request(
            "POST", endpoint, data=body, headers=headers, timeout=_HTTP_TIMEOUT_S
        )
        try:
            meta = json.loads(resp_body.decode("utf-8"))
        except json.JSONDecodeError:
            meta = {}
        name = meta.get("name") or path.name
        logger.info(
            "Uploaded to ComfyUI | kind=%s | local=%s → server=%s",
            kind, path.name, name,
        )
        return str(name)

    # -- queue / poll / download --------------------------------------------

    def queue_prompt(self, workflow: Mapping[str, Any]) -> str:
        """Submit a workflow; return ``prompt_id``."""
        if self.mode == "runpod":
            return self._queue_runpod(workflow)

        payload: dict[str, Any] = {
            "prompt": dict(workflow),
            "client_id": self.client_id,
        }
        data = self._request_json("POST", "prompt", payload=payload)
        if not isinstance(data, dict) or not data.get("prompt_id"):
            raise RemoteGPUError(f"Unexpected /prompt response: {data!r}")
        if data.get("node_errors"):
            raise RemoteGPUError(f"ComfyUI node_errors: {data['node_errors']}")
        prompt_id = str(data["prompt_id"])
        logger.info("ComfyUI queued | prompt_id=%s", prompt_id)
        return prompt_id

    def _queue_runpod(self, workflow: Mapping[str, Any]) -> str:
        # Upload must already be confirmed (volume HeadObject and/or pending
        # images[] built in upload_file) before this /run → worker /prompt.
        job_input: dict[str, Any] = {"workflow": dict(workflow)}
        pending = getattr(_TLS, "pending_uploads", None)
        n_images = 0
        if pending:
            job_input["images"] = list(pending)
            n_images = len(pending)
            _TLS.pending_uploads = []
        # Custom worker previously hard-failed at 600s ("não terminou em 600s")
        # even when the endpoint allows 1800s — pass timeout hints if supported.
        try:
            timeout_s = int(float(self.timeout_s or _DEFAULT_TIMEOUT_S))
        except (TypeError, ValueError):
            timeout_s = int(_DEFAULT_TIMEOUT_S)
        timeout_s = max(timeout_s, 1800)
        job_input["timeout"] = timeout_s
        job_input["workflow_timeout"] = timeout_s
        payload: dict[str, Any] = {
            "input": job_input,
            "policy": {"executionTimeout": timeout_s * 1000},
        }
        logger.info(
            "Submitting RunPod /run after upload confirm | images[]=%d | timeout_s=%d",
            n_images, timeout_s,
        )
        data = self._request_json("POST", "run", payload=payload)
        if not isinstance(data, dict):
            raise RemoteGPUError(f"Unexpected RunPod /run response: {data!r}")
        job_id = data.get("id") or data.get("job_id")
        if not job_id:
            raise RemoteGPUError(f"RunPod response missing job id: {data!r}")
        logger.info("RunPod queued | job_id=%s", job_id)
        return str(job_id)

    def wait_for_completion(self, prompt_id: str) -> dict[str, Any]:
        """Poll until the job finishes; return history/output payload."""
        deadline = time.monotonic() + self.timeout_s
        if self.mode == "runpod":
            return self._wait_runpod(prompt_id, deadline)

        while time.monotonic() < deadline:
            hist = self._request_json("GET", f"history/{prompt_id}", payload=None)
            if isinstance(hist, dict) and prompt_id in hist:
                entry = hist[prompt_id]
                status = (entry.get("status") or {}) if isinstance(entry, dict) else {}
                if status.get("status_str") == "error" or status.get("completed") is False:
                    msgs = status.get("messages") or entry.get("messages")
                    raise RemoteGPUError(f"ComfyUI job failed: {msgs or entry}")
                outputs = entry.get("outputs") if isinstance(entry, dict) else None
                if outputs:
                    logger.info("ComfyUI completed | prompt_id=%s", prompt_id)
                    return entry
            time.sleep(self.poll_interval_s)

        raise RemoteGPUError(
            f"Timed out after {self.timeout_s:.0f}s waiting for prompt_id={prompt_id}"
        )

    def _wait_runpod(self, job_id: str, deadline: float) -> dict[str, Any]:
        while time.monotonic() < deadline:
            data = self._request_json("GET", f"status/{job_id}", payload=None)
            if not isinstance(data, dict):
                time.sleep(self.poll_interval_s)
                continue
            status = str(data.get("status") or "").upper()
            if status in ("COMPLETED", "SUCCESS"):
                meta = _summarize_runpod_status(data, job_id=job_id)
                self.last_job_meta = meta
                logger.info(
                    "RunPod completed | job_id=%s | delay_ms=%s | exec_ms=%s | worker=%s",
                    job_id,
                    meta.get("delayTime"),
                    meta.get("executionTime"),
                    meta.get("workerId"),
                )
                return data
            if status in ("FAILED", "CANCELLED", "TIMED_OUT", "ERROR"):
                self.last_job_meta = _summarize_runpod_status(data, job_id=job_id)
                raise RemoteGPUError(f"RunPod job {job_id} ended with status={status}: {data}")
            time.sleep(self.poll_interval_s)
        raise RemoteGPUError(
            f"Timed out after {self.timeout_s:.0f}s waiting for RunPod job_id={job_id}"
        )

    def collect_output_files(self, result: Mapping[str, Any]) -> list[dict[str, Any]]:
        """
        Normalize ComfyUI history / RunPod output into a list of file descriptors:
        ``{filename, subfolder, type, node_id?}``.
        """
        files: list[dict[str, Any]] = []

        if self.mode == "runpod":
            output = result.get("output") if isinstance(result, dict) else None
            # Shapes seen in the wild:
            #   {images:[{...}]} / {files:[...]}
            #   {outputs:[{filename, data_base64, output_type, node_id}, ...]}
            #   raw list of file dicts
            candidates: list[Any] = []
            if isinstance(output, dict):
                nested = output.get("outputs")
                if isinstance(nested, list):
                    candidates.extend(nested)
                elif isinstance(nested, dict):
                    # Comfy-style node map nested under output.outputs
                    for node_id, node_out in nested.items():
                        if not isinstance(node_out, dict):
                            continue
                        for key in ("images", "gifs", "videos", "audio", "files"):
                            items = node_out.get(key)
                            if isinstance(items, list):
                                for item in items:
                                    if isinstance(item, dict):
                                        entry = dict(item)
                                        entry.setdefault("node_id", node_id)
                                        entry.setdefault("media_key", key)
                                        candidates.append(entry)
                for key in ("images", "files", "video", "audio", "gifs"):
                    val = output.get(key)
                    if isinstance(val, list):
                        candidates.extend(val)
                    elif isinstance(val, dict):
                        candidates.append(val)
            elif isinstance(output, list):
                candidates.extend(output)
            for item in candidates:
                if isinstance(item, dict):
                    files.append(item)
                elif isinstance(item, str):
                    files.append({"filename": item, "subfolder": "", "type": "output"})
            return files

        outputs = result.get("outputs") if isinstance(result, dict) else None
        if not isinstance(outputs, dict):
            return files
        for node_id, node_out in outputs.items():
            if not isinstance(node_out, dict):
                continue
            for key in ("images", "gifs", "videos", "audio", "files"):
                items = node_out.get(key)
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, dict):
                        entry = dict(item)
                        entry.setdefault("node_id", node_id)
                        entry.setdefault("media_key", key)
                        files.append(entry)
        return files

    def download_file(
        self,
        file_meta: Mapping[str, Any],
        dest: Path,
    ) -> Path:
        """Download one ComfyUI /view asset (or decode RunPod base64) to *dest*."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)

        # RunPod often returns data URLs / base64 inline (key varies by worker).
        b64 = (
            file_meta.get("data_base64")
            or file_meta.get("data")
            or file_meta.get("image")
            or file_meta.get("base64")
        )
        if isinstance(b64, str) and len(b64) > 64 and self.mode == "runpod":
            import base64  # noqa: PLC0415

            raw = b64.split(",", 1)[-1] if b64.startswith("data:") else b64
            dest.write_bytes(base64.b64decode(raw))
            logger.info(
                "Decoded RunPod base64 | %s → %s (%d bytes)",
                file_meta.get("filename") or dest.name,
                dest,
                dest.stat().st_size,
            )
            return dest.resolve()

        filename = file_meta.get("filename") or file_meta.get("name")
        if not filename:
            raise RemoteGPUError(f"Output file meta missing filename: {file_meta!r}")

        if self.mode == "runpod":
            # Some endpoints expose a direct URL
            url = file_meta.get("url") or file_meta.get("file_url")
            if url:
                req = Request(str(url), headers={"User-Agent": "UnifiedMultiPageFactory-RemoteGPU/1.0"})
                with urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
                    dest.write_bytes(resp.read())
                return dest.resolve()
            raise RemoteGPUError(
                f"RunPod output has no downloadable payload: "
                f"keys={list(file_meta.keys())}"
            )

        params = {
            "filename": filename,
            "subfolder": file_meta.get("subfolder") or "",
            "type": file_meta.get("type") or "output",
        }
        path = "view?" + urlencode(params)
        _status, body, _ = self._request(
            "GET", path, data=None, headers=self._headers(json_body=False)
        )
        if not body:
            raise RemoteGPUError(f"Empty download for {filename}")
        dest.write_bytes(body)
        logger.info("Downloaded | %s → %s (%d bytes)", filename, dest, len(body))
        return dest.resolve()

    def run_workflow(
        self,
        workflow: Mapping[str, Any],
        *,
        preferred_exts: Sequence[str] | None = None,
        output_path: Path | str | None = None,
        output_dir: Path | str | None = None,
        stem: str = "remote_gpu_out",
    ) -> Path:
        """
        Queue → wait → download the first matching output media file.

        Returns the local path of the saved asset.
        Records ``last_job_seconds`` on the client for CostTracker GPU lines.
        Concurrent callers (ThreadPool) each block on their own job — fan-out
        is limited by ``remote_gpu_max_parallel()`` at the call site.
        """
        t0 = time.monotonic()
        prompt_id = self.queue_prompt(workflow)
        result = self.wait_for_completion(prompt_id)
        files = self.collect_output_files(result)
        if not files:
            elapsed = time.monotonic() - t0
            self._record_job_seconds(elapsed)
            raise RemoteGPUError(
                f"Job {prompt_id} finished with no output files. Raw keys: "
                f"{list(result.keys()) if isinstance(result, dict) else type(result)}"
            )

        chosen = self._pick_file(files, preferred_exts)
        src_name = str(chosen.get("filename") or chosen.get("name") or "output.bin")
        src_suffix = Path(src_name).suffix or (
            preferred_exts[0] if preferred_exts else ".bin"
        )

        if output_path is not None:
            dest = Path(output_path)
            # Prefer the real ComfyUI media suffix (e.g. F5-TTS → .flac) so
            # MoviePy/ffmpeg aren't handed mislabeled .mp3 paths.
            if not dest.suffix:
                dest = dest.with_suffix(src_suffix)
            elif src_suffix and dest.suffix.lower() != src_suffix.lower():
                dest = dest.with_suffix(src_suffix)
        else:
            directory = Path(output_dir) if output_dir else default_output_dir("misc")
            directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            dest = directory / f"{stem}_{stamp}{src_suffix}"

        out = self.download_file(chosen, dest)
        self._record_job_seconds(time.monotonic() - t0)
        return out

    def _record_job_seconds(self, elapsed: float) -> None:
        secs = max(0.0, float(elapsed))
        self.last_job_seconds = secs
        with self._gpu_seconds_lock:
            self.total_gpu_seconds += secs
        logger.info(
            "RemoteGPU job time | mode=%s | job=%.1fs | cumulative_gpu_s=%.1fs | "
            "max_parallel=%d",
            self.mode, secs, self.total_gpu_seconds, remote_gpu_max_parallel(),
        )

    @staticmethod
    def _pick_file(
        files: Sequence[Mapping[str, Any]],
        preferred_exts: Sequence[str] | None,
    ) -> Mapping[str, Any]:
        if not preferred_exts:
            return files[0]
        wanted = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in preferred_exts}
        for f in files:
            name = str(f.get("filename") or f.get("name") or "")
            if Path(name).suffix.lower() in wanted:
                return f
        # Prefer by media_key hints
        media_rank = {"images": 0, "videos": 1, "gifs": 2, "audio": 3, "files": 4}
        ranked = sorted(
            files,
            key=lambda x: media_rank.get(str(x.get("media_key") or ""), 99),
        )
        return ranked[0]


# ---------------------------------------------------------------------------
# Wan 2.2 LightX2V path diagnostics / hardcode bypass
# ---------------------------------------------------------------------------

def _resolve_link_or_value(
    workflow: Mapping[str, Any],
    value: Any,
    *,
    depth: int = 0,
) -> Any:
    """Follow Primitive / ComfySwitchNode links to a concrete value (client-side)."""
    if depth > 8:
        return value
    if not (isinstance(value, list) and len(value) >= 1):
        return value
    src_id = str(value[0])
    src = workflow.get(src_id)
    if not isinstance(src, dict):
        return value
    ct = str(src.get("class_type") or "")
    inputs = src.get("inputs") or {}
    if ct == "ComfySwitchNode":
        sw = _resolve_link_or_value(workflow, inputs.get("switch"), depth=depth + 1)
        branch = "on_true" if bool(sw) else "on_false"
        return _resolve_link_or_value(workflow, inputs.get(branch), depth=depth + 1)
    if ct.startswith("Primitive"):
        return inputs.get("value")
    if ct == "LoraLoaderModelOnly":
        return {
            "node": src_id,
            "lora_name": inputs.get("lora_name"),
            "strength_model": inputs.get("strength_model"),
        }
    if ct in {"UNETLoader", "CheckpointLoaderSimple"}:
        return {
            "node": src_id,
            "class_type": ct,
            "unet_name": inputs.get("unet_name") or inputs.get("ckpt_name"),
        }
    if ct == "ModelSamplingSD3":
        return _resolve_link_or_value(workflow, inputs.get("model"), depth=depth + 1)
    return {"node": src_id, "class_type": ct}


def diagnose_wan_sampler_path(workflow: Mapping[str, Any]) -> dict[str, Any]:
    """
    Client-side resolution of what each KSamplerAdvanced will receive.

    This is the closest we can get without injecting prints into the remote
    ComfyUI worker: it mirrors ComfySwitchNode boolean selection on the
    submitted prompt graph.
    """
    switch_bool = None
    if "129:131" in workflow:
        switch_bool = (workflow["129:131"].get("inputs") or {}).get("value")
    samplers: list[dict[str, Any]] = []
    for nid, node in workflow.items():
        if not isinstance(node, dict) or node.get("class_type") != "KSamplerAdvanced":
            continue
        inp = node.get("inputs") or {}
        steps_v = _resolve_link_or_value(workflow, inp.get("steps"))
        cfg_v = _resolve_link_or_value(workflow, inp.get("cfg"))
        start_v = _resolve_link_or_value(workflow, inp.get("start_at_step"))
        end_v = _resolve_link_or_value(workflow, inp.get("end_at_step"))
        model_v = _resolve_link_or_value(workflow, inp.get("model"))
        samplers.append(
            {
                "node": nid,
                "steps": steps_v,
                "cfg": cfg_v,
                "start_at_step": start_v,
                "end_at_step": end_v,
                "model": model_v,
                "steps_input_raw": inp.get("steps"),
                "model_input_raw": inp.get("model"),
            }
        )
    lora_nodes = []
    for nid, node in workflow.items():
        if not isinstance(node, dict) or node.get("class_type") != "LoraLoaderModelOnly":
            continue
        inp = node.get("inputs") or {}
        lora_nodes.append(
            {
                "node": nid,
                "lora_name": inp.get("lora_name"),
                "reachable_from_sampler": any(
                    isinstance(s.get("model"), dict)
                    and s["model"].get("node") == nid
                    for s in samplers
                ),
            }
        )
    switches: list[dict[str, Any]] = []
    for nid, node in workflow.items():
        if not isinstance(node, dict) or node.get("class_type") != "ComfySwitchNode":
            continue
        inp = node.get("inputs") or {}
        title = str((node.get("_meta") or {}).get("title") or "")
        sw_raw = inp.get("switch")
        sw_resolved = _resolve_link_or_value(workflow, sw_raw)
        switches.append(
            {
                "node": nid,
                "title": title,
                "switch_raw": sw_raw,
                "switch_resolved": sw_resolved,
                "on_true": inp.get("on_true"),
                "on_false": inp.get("on_false"),
                "selected_branch": (
                    "on_true" if bool(sw_resolved) else "on_false"
                ),
            }
        )
    path = "lightx2v_4step" if switch_bool else "default_20step"
    if samplers and all(
        isinstance(s.get("steps"), (int, float)) and int(s["steps"]) == 4
        and isinstance(s.get("steps_input_raw"), (int, float))
        for s in samplers
    ):
        path = "hardcoded_4step_bypass_switch"
    steps_are_links = any(
        isinstance(s.get("steps_input_raw"), list) for s in samplers
    )
    return {
        "enable_4step_switch_bool": switch_bool,
        "resolved_path": path,
        "ksampler_steps_are_graph_links": steps_are_links,
        "samplers": samplers,
        "switches": switches,
        "lora_loaders": lora_nodes,
        "env_WAN_ENABLE_4STEP_LORA": os.getenv("WAN_ENABLE_4STEP_LORA"),
        "note": (
            "KSampler.steps is a ComfySwitchNode link unless hardcoded. "
            "RunPod COMPLETED/FAILED payloads have empty Comfy logs, so this "
            "client-side resolution is the execution-time proxy. If the worker "
            "ComfySwitchNode is not lazy or prefers its widget default (false), "
            "runtime can diverge from this diag."
        ),
    }


def _hardcode_wan_lightx2v_4step(workflow: dict[str, Any]) -> dict[str, Any]:
    """
    Bypass ComfySwitchNode entirely: wire LoRA models + literal steps=4 into
    both KSamplerAdvanced nodes (high-noise then low-noise split at 2).
    """
    wf = workflow
    # Force boolean true for any leftover switch consumers / logging clarity
    if "129:131" in wf:
        set_node_input(wf, "129:131", "value", True)
    # ModelSamplingSD3 ← LoraLoaderModelOnly (skip Switch Model)
    if "129:104" in wf and "129:101" in wf:
        set_node_input(wf, "129:104", "model", ["129:101", 0])
    if "129:103" in wf and "129:102" in wf:
        set_node_input(wf, "129:103", "model", ["129:102", 0])
    # Samplers: literal ints (no Switch Steps / CFG / Split)
    # 129:86 = high-noise (0 → split), 129:85 = low-noise (split → steps)
    if "129:86" in wf:
        set_node_input(wf, "129:86", "steps", 4)
        set_node_input(wf, "129:86", "cfg", 1.0)
        set_node_input(wf, "129:86", "start_at_step", 0)
        set_node_input(wf, "129:86", "end_at_step", 2)
    if "129:85" in wf:
        set_node_input(wf, "129:85", "steps", 4)
        set_node_input(wf, "129:85", "cfg", 1.0)
        set_node_input(wf, "129:85", "start_at_step", 2)
        set_node_input(wf, "129:85", "end_at_step", 4)
    logger.warning(
        "WAN HARDCODE: bypassed ComfySwitchNode — KSampler steps=4 cfg=1 "
        "split=2, models wired to LightX2V LoRA loaders"
    )
    print(
        "[WAN HARDCODE] KSampler steps=4 cfg=1.0 split=2 | "
        "models <- lightx2v LoRA (switch bypassed)",
        flush=True,
    )
    return wf


# ---------------------------------------------------------------------------
# High-level manager + public wrappers
# ---------------------------------------------------------------------------

class RemoteGPUManager:
    """Unified pipeline handler for Flux / F5-TTS / Wan2.2 remote workflows."""

    def __init__(
        self,
        *,
        client: ComfyUIClient | None = None,
        workflows_path: Path | None = None,
    ) -> None:
        self.workflows_path = workflows_path or workflows_dir()
        self.client = client or ComfyUIClient()
        self._cache: dict[str, dict[str, Any]] = {}
        self.last_video_workflow_diag: dict[str, Any] = {}

    def _template(self, filename: str) -> dict[str, Any]:
        if filename not in self._cache:
            self._cache[filename] = load_workflow(filename, directory=self.workflows_path)
        return deep_copy_workflow(self._cache[filename])

    # -- image (Flux Dev txt2img) -------------------------------------------

    def prepare_image_workflow(
        self,
        prompt: str,
        *,
        workflow_name: str | None = None,
        page_id: str | None = None,
        lora_name: str | None = None,
        lora_strength: float | None = None,
        apply_trigger: bool = True,
        negative_prompt: str | None = None,  # noqa: ARG002 — Flux graph uses ConditioningZeroOut
        seed: int | None = None,
        steps: int | None = None,
        width: int | None = None,
        height: int | None = None,
        cfg: float | None = None,
        filename_prefix: str | None = None,
        extra_patches: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        lora_cfg = resolve_page_lora_config(page_id)
        template = (
            workflow_name
            or lora_cfg["workflow_name"]
            or WORKFLOW_FLUX_TXT2IMG
        )
        final_prompt = prompt
        if apply_trigger and lora_cfg.get("enabled"):
            final_prompt = apply_lora_trigger(prompt, lora_cfg.get("trigger"))

        wf = self._template(template)
        seed_val = int(seed if seed is not None else random.randint(0, 2**63 - 1))

        prompt_node = resolve_node(
            wf, class_type="CLIPTextEncode", fallback_id=_FLUX_PROMPT_NODE,
        )
        sampler_node = resolve_node(
            wf, class_type="KSampler", fallback_id=_FLUX_SAMPLER_NODE,
        )
        latent_node = resolve_node(
            wf, class_type="EmptySD3LatentImage", fallback_id=_FLUX_LATENT_NODE,
        )
        save_node = resolve_node(
            wf, class_type="SaveImage", fallback_id=_FLUX_SAVE_NODE,
        )

        patches: dict[str, dict[str, Any]] = {
            prompt_node: {"text": final_prompt},
            sampler_node: {"seed": seed_val},
        }
        if steps is not None:
            patches[sampler_node]["steps"] = int(steps)
        if cfg is not None:
            patches[sampler_node]["cfg"] = float(cfg)
        if width is not None:
            patches.setdefault(latent_node, {})["width"] = int(width)
        if height is not None:
            patches.setdefault(latent_node, {})["height"] = int(height)
        if filename_prefix:
            patches[save_node] = {"filename_prefix": filename_prefix}

        # Inject LoRA name + strength into LoraLoader node(s) when present
        active_lora = lora_name or lora_cfg.get("lora_name")
        active_strength = (
            float(lora_strength)
            if lora_strength is not None
            else float(lora_cfg.get("strength") or 0.8)
        )
        lora_nodes = find_nodes_by_class(wf, "LoraLoader")
        if active_lora and lora_nodes:
            for nid in lora_nodes:
                patches[nid] = {
                    "lora_name": _normalize_lora_filename(str(active_lora)),
                    "strength_model": active_strength,
                    "strength_clip": active_strength,
                }
            logger.info(
                "Flux LoRA inject | page=%s | lora=%s | strength=%.2f | trigger=%r | wf=%s",
                lora_cfg.get("page_id") or page_id or "?",
                active_lora,
                active_strength,
                lora_cfg.get("trigger") or "",
                template,
            )
        elif template == WORKFLOW_FLUX_LORA_TXT2IMG and not active_lora:
            logger.warning(
                "LoRA workflow selected but no REMOTE_GPU_LORA_NAME for page=%s — "
                "using template default lora_name",
                page_id or "?",
            )

        if extra_patches:
            for nid, fields in extra_patches.items():
                patches.setdefault(nid, {}).update(dict(fields))
        _ = negative_prompt
        return patch_workflow(wf, patches)

    def generate_image(
        self,
        prompt: str,
        *,
        output_path: Path | str | None = None,
        output_dir: Path | str | None = None,
        seed: int | None = None,
        steps: int | None = None,
        width: int | None = None,
        height: int | None = None,
        negative_prompt: str | None = None,
        stem: str = "flux_remote",
        workflow_name: str | None = None,
        page_id: str | None = None,
        **kwargs: Any,
    ) -> Path:
        wf = self.prepare_image_workflow(
            prompt,
            workflow_name=workflow_name or kwargs.get("workflow_name"),
            page_id=page_id if page_id is not None else kwargs.get("page_id"),
            lora_name=kwargs.get("lora_name"),
            lora_strength=kwargs.get("lora_strength"),
            apply_trigger=bool(kwargs.get("apply_trigger", True)),
            negative_prompt=negative_prompt,
            seed=seed,
            steps=steps,
            width=width,
            height=height,
            filename_prefix=kwargs.get("filename_prefix"),
            extra_patches=kwargs.get("extra_patches"),
            cfg=kwargs.get("cfg"),
        )
        dest_dir = output_dir or (None if output_path else default_output_dir("images"))
        return self.client.run_workflow(
            wf,
            preferred_exts=(".png", ".jpg", ".jpeg", ".webp"),
            output_path=output_path,
            output_dir=dest_dir,
            stem=stem,
        )

    # -- audio (F5-TTS) -----------------------------------------------------

    def prepare_audio_workflow(
        self,
        text: str,
        *,
        sample_text: str | None = None,
        reference_audio: Path | str | None = None,
        reference_audio_name: str | None = None,
        page_id: str | None = None,
        seed: int | None = None,
        speed: float | None = None,
        extra_patches: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        wf = self._template(WORKFLOW_F5TTS_TXT2AUDIO)
        seed_val = int(seed if seed is not None else random.randint(0, 2**31 - 1))

        # Resolve page voice_reference/ (then global env) when caller omitted refs
        resolved_audio: Path | None = None
        resolved_sample: str | None = sample_text
        if reference_audio is None and reference_audio_name is None:
            resolved_audio, page_sample = resolve_page_voice_reference(page_id)
            if resolved_sample is None:
                resolved_sample = page_sample
        elif reference_audio is not None:
            resolved_audio = Path(reference_audio).expanduser()
            if resolved_sample is None:
                resolved_sample = _read_text_if_exists(_companion_txt(resolved_audio))

        server_audio = reference_audio_name
        if resolved_audio is not None and Path(resolved_audio).expanduser().is_file():
            resolved_audio = Path(resolved_audio).expanduser().resolve()
            server_audio = self.client.upload_file(
                resolved_audio, media_kind="audio",
            )
            logger.info(
                "F5-TTS reference audio | page=%s | file=%s | sample_text=%s",
                page_id or os.getenv("ACTIVE_PAGE") or "?",
                resolved_audio.name,
                "yes" if resolved_sample else "missing",
            )
        elif server_audio is None:
            # Last chance: local global default only (never a phantom server name).
            env_ref = (
                os.getenv("REMOTE_GPU_DEFAULT_REF_AUDIO")
                or _cfg("REMOTE_GPU_DEFAULT_REF_AUDIO", None)
                or ""
            ).strip()
            env_path = Path(env_ref).expanduser() if env_ref else None
            if env_path is not None and env_path.is_file():
                server_audio = self.client.upload_file(
                    env_path, media_kind="audio",
                )
                logger.info(
                    "F5-TTS reference audio | global file=%s (uploaded)",
                    env_path.name,
                )

        if not server_audio:
            pid_label = page_id or os.getenv("ACTIVE_PAGE") or "<page>"
            raise RemoteGPUError(
                "F5-TTS reference audio missing — refusing to queue (avoids "
                "wasted GPU on LoadAudio 'Invalid audio file: sample_10s.wav'). "
                f"Drop a local clip at channels_config/{pid_label}/voice_reference/"
                f"{pid_label}_voice_ref_10s.wav (or .mp3) plus matching .txt, "
                "or set REMOTE_GPU_DEFAULT_REF_AUDIO to an existing local file."
            )

        tts_node = resolve_node(
            wf, class_type="F5TTSAudioInputs", fallback_id=_F5_TTS_NODE,
        )
        load_node = resolve_node(
            wf, class_type="LoadAudio", fallback_id=_F5_LOAD_AUDIO_NODE,
        )
        patches: dict[str, dict[str, Any]] = {
            tts_node: {
                "speech": text,
                "seed": seed_val,
            },
            load_node: {"audio": server_audio},
        }
        if resolved_sample is not None:
            patches[tts_node]["sample_text"] = resolved_sample
        if speed is not None:
            patches[tts_node]["speed"] = float(speed)
        if extra_patches:
            for nid, fields in extra_patches.items():
                patches.setdefault(nid, {}).update(dict(fields))
        return patch_workflow(wf, patches)

    def generate_audio(
        self,
        text: str,
        *,
        output_path: Path | str | None = None,
        output_dir: Path | str | None = None,
        sample_text: str | None = None,
        reference_audio: Path | str | None = None,
        page_id: str | None = None,
        seed: int | None = None,
        speed: float | None = None,
        stem: str = "f5tts_remote",
        **kwargs: Any,
    ) -> Path:
        wf = self.prepare_audio_workflow(
            text,
            sample_text=sample_text,
            reference_audio=reference_audio,
            reference_audio_name=kwargs.get("reference_audio_name"),
            page_id=page_id if page_id is not None else kwargs.get("page_id"),
            seed=seed,
            speed=speed,
            extra_patches=kwargs.get("extra_patches"),
        )
        dest_dir = output_dir or (None if output_path else default_output_dir("audio"))
        path = self.client.run_workflow(
            wf,
            preferred_exts=(".wav", ".mp3", ".flac", ".ogg"),
            output_path=output_path,
            output_dir=dest_dir,
            stem=stem,
        )
        return path

    # -- video (Wan 2.2 img2vid) --------------------------------------------

    def prepare_video_workflow(
        self,
        image_path: Path | str,
        *,
        prompt: str,
        negative_prompt: str | None = None,
        seed: int | None = None,
        steps: int | None = None,
        width: int | None = None,
        height: int | None = None,
        duration_s: float | None = None,
        fps: float | None = None,
        image_name: str | None = None,
        extra_patches: Mapping[str, Mapping[str, Any]] | None = None,
        four_step: bool | None = None,
        hardcode_4step_bypass_switch: bool = False,
    ) -> dict[str, Any]:
        wf = self._template(WORKFLOW_WAN22_IMG2VID)
        server_name = image_name or self.client.upload_file(
            image_path, media_kind="image",
        )
        seed_val = int(seed if seed is not None else random.randint(0, 2**63 - 1))

        load_node = resolve_node(
            wf, class_type="LoadImage", fallback_id=_WAN_LOAD_IMAGE_NODE,
        )
        # Positive prompt: titled CLIPTextEncode when present, else first
        try:
            pos_node = resolve_node(
                wf,
                class_type="CLIPTextEncode",
                fallback_id=_WAN_POS_PROMPT_NODE,
                title_contains="Positive",
            )
        except KeyError:
            pos_node = resolve_node(
                wf, class_type="CLIPTextEncode", fallback_id=_WAN_POS_PROMPT_NODE,
            )
        neg_nodes = find_nodes_by_class(wf, "CLIPTextEncode")
        neg_node = next(
            (
                n for n in neg_nodes
                if "negative" in str(((wf[n] or {}).get("_meta") or {}).get("title") or "").lower()
            ),
            neg_nodes[-1] if len(neg_nodes) > 1 else _WAN_NEG_PROMPT_NODE,
        )
        i2v_node = resolve_node(
            wf, class_type="WanImageToVideo", fallback_id=_WAN_I2V_NODE,
        )
        # High-noise sampler carries noise_seed
        seed_node = resolve_node(
            wf, class_type="KSamplerAdvanced", fallback_id=_WAN_SEED_NODE,
        )

        patches: dict[str, dict[str, Any]] = {
            load_node: {"image": server_name},
            pos_node: {"text": prompt},
            seed_node: {"noise_seed": seed_val},
        }
        if negative_prompt is not None and neg_node in wf:
            patches[neg_node] = {"text": negative_prompt}
        if width is not None:
            patches.setdefault(i2v_node, {})["width"] = int(width)
        if height is not None:
            patches.setdefault(i2v_node, {})["height"] = int(height)
        if duration_s is not None:
            # Prefer PrimitiveFloat titled Duration; else fallback id
            try:
                dur_node = resolve_node(
                    wf,
                    class_type="PrimitiveFloat",
                    fallback_id=_WAN_DURATION_NODE,
                    title_contains="Duration",
                )
            except KeyError:
                dur_node = _WAN_DURATION_NODE if _WAN_DURATION_NODE in wf else ""
            if dur_node:
                patches[dur_node] = {"value": float(duration_s)}
        if fps is not None:
            try:
                fps_node = resolve_node(
                    wf,
                    class_type="PrimitiveFloat",
                    fallback_id=_WAN_FPS_NODE,
                    title_contains="FPS",
                )
            except KeyError:
                fps_node = _WAN_FPS_NODE if _WAN_FPS_NODE in wf else ""
            if fps_node:
                patches[fps_node] = {"value": float(fps)}

        # Explicit kwarg wins. Env alone is unsafe: config.py load_dotenv(override=True)
        # re-imports during apply_production_flow and can clobber WAN_ENABLE_4STEP_LORA
        # back to .env false after the benchmark set it true.
        if four_step is None:
            four_step = str(
                _cfg("WAN_ENABLE_4STEP_LORA", os.getenv("WAN_ENABLE_4STEP_LORA", "false"))
                or "false"
            ).strip().lower() in ("1", "true", "yes", "on")
        else:
            four_step = bool(four_step)

        if steps is not None and not hardcode_4step_bypass_switch:
            # Only patch the active branch's steps primitive so the inactive
            # branch keeps its baked-in 4 vs 20 defaults.
            target = _WAN_STEPS_4_NODE if four_step else _WAN_STEPS_20_NODE
            if target in wf:
                patches[target] = {"value": int(steps)}
            else:
                for nid in find_nodes_by_class(wf, "PrimitiveInt"):
                    title = str(((wf[nid] or {}).get("_meta") or {}).get("title") or "")
                    if title.lower().strip() == "int (steps)":
                        patches[nid] = {"value": int(steps)}

        # LightX2V 4-step LoRA trades motion dynamics for speed (Wan 2.2 docs).
        # Default OFF → full 20-step / CFG 3.5 path.
        for nid in find_nodes_by_class(wf, "PrimitiveBoolean"):
            title = str(((wf[nid] or {}).get("_meta") or {}).get("title") or "")
            if "4step" in title.lower().replace(" ", "") or "4-step" in title.lower():
                patches[nid] = {"value": bool(four_step)}
                break
        else:
            if "129:131" in wf:
                patches["129:131"] = {"value": bool(four_step)}
        if extra_patches:
            for nid, fields in extra_patches.items():
                patches.setdefault(nid, {}).update(dict(fields))
        patched = patch_workflow(wf, patches)

        if hardcode_4step_bypass_switch:
            patched = _hardcode_wan_lightx2v_4step(patched)
            four_step = True

        diag = diagnose_wan_sampler_path(patched)
        self.last_video_workflow_diag = diag
        _log_wan_sampler_inputs(patched, four_step=bool(four_step))
        logger.info("WAN workflow path diag: %s", diag)
        print(f"[WAN prepare] {diag}", flush=True)
        return patched

    def generate_video(
        self,
        image_path: Path | str,
        *,
        prompt: str,
        output_path: Path | str | None = None,
        output_dir: Path | str | None = None,
        negative_prompt: str | None = None,
        seed: int | None = None,
        steps: int | None = None,
        width: int | None = None,
        height: int | None = None,
        duration_s: float | None = None,
        fps: float | None = None,
        stem: str = "wan22_remote",
        **kwargs: Any,
    ) -> Path:
        wf = self.prepare_video_workflow(
            image_path,
            prompt=prompt,
            negative_prompt=negative_prompt,
            seed=seed,
            steps=steps,
            width=width,
            height=height,
            duration_s=duration_s,
            fps=fps,
            image_name=kwargs.get("image_name"),
            extra_patches=kwargs.get("extra_patches"),
            four_step=kwargs.get("four_step"),
            hardcode_4step_bypass_switch=bool(
                kwargs.get("hardcode_4step_bypass_switch", False)
            ),
        )
        dest_dir = output_dir or (None if output_path else default_output_dir("video"))
        return self.client.run_workflow(
            wf,
            preferred_exts=(".mp4", ".webm", ".gif", ".mkv"),
            output_path=output_path,
            output_dir=dest_dir,
            stem=stem,
        )


# Module-level singleton (lazy)
_MANAGER: RemoteGPUManager | None = None


def get_manager() -> RemoteGPUManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = RemoteGPUManager()
    return _MANAGER


def reset_manager() -> None:
    """Clear cached singleton (useful in tests)."""
    global _MANAGER
    _MANAGER = None


def generate_image(prompt: str, **kwargs: Any) -> Path:
    """Public wrapper — Flux Dev txt2img via remote ComfyUI/RunPod."""
    return get_manager().generate_image(prompt, **kwargs)


def generate_audio(text: str, **kwargs: Any) -> Path:
    """Public wrapper — F5-TTS txt2audio via remote ComfyUI/RunPod."""
    return get_manager().generate_audio(text, **kwargs)


def generate_video(image_path: Path | str, *, prompt: str, **kwargs: Any) -> Path:
    """Public wrapper — Wan 2.2 img2video via remote ComfyUI/RunPod."""
    return get_manager().generate_video(image_path, prompt=prompt, **kwargs)


# ---------------------------------------------------------------------------
# Drop-in image adapter (same generate() contract as GeminiImageAdapter)
# ---------------------------------------------------------------------------

class RemoteGPUImageAdapter:
    """Pipeline-compatible image adapter backed by remote Flux Dev (+ optional LoRA)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        self._manager = get_manager()
        self.page_id: str | None = (
            kwargs.get("page_id")
            or os.getenv("ACTIVE_PAGE")
            or _cfg("ACTIVE_PAGE", None)
        )
        self._lora_cfg = resolve_page_lora_config(self.page_id)
        # Per-channel LoRA → flux_dev_lora; no LoRA → base flux_dev
        self.workflow_name: str = str(
            kwargs.get("workflow_name")
            or self._lora_cfg.get("workflow_name")
            or WORKFLOW_FLUX_TXT2IMG
        )
        self.last_api_call_count: int = 1
        self.last_gemini_image_model_used: str | None = f"remote_gpu/{self.workflow_name}"
        self.last_gemini_image_failure_model_id: str | None = None
        self.last_estimated_cost_usd: float = 0.0
        self.last_cost_key: str = "image_remote_gpu_flux"
        if self._lora_cfg.get("enabled"):
            logger.info(
                "RemoteGPUImageAdapter | page=%s | LoRA=%s | strength=%.2f | trigger=%r",
                self.page_id,
                self._lora_cfg.get("lora_name"),
                float(self._lora_cfg.get("strength") or 0.8),
                self._lora_cfg.get("trigger") or "",
            )

    def generate(
        self,
        prompt: str,
        *,
        reference_image_path: Path | None = None,  # noqa: ARG002
        style_reference_path: Path | None = None,  # noqa: ARG002
        style_reference_paths: list[Path] | None = None,  # noqa: ARG002
        style_reference_weight: float | None = None,  # noqa: ARG002
        output_stem: str = "avatar_post",
        output_directory: Path | None = None,
        aspect_ratio: str | None = None,  # noqa: ARG002
        avatar_mode: str = "ON",  # noqa: ARG002
        reference_image_weight: float | None = None,  # noqa: ARG002
        model_name: str | None = None,  # noqa: ARG002
        steps: int | None = None,
        draft: bool = False,  # noqa: ARG002
        width: int | None = None,
        height: int | None = None,
        visual_role: str | None = None,  # noqa: ARG002
        negative_prompt: str | None = None,
    ) -> Path:
        try:
            from avatar_engine.text_utils import safe_output_stem  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            def safe_output_stem(s: str) -> str:  # type: ignore[misc]
                return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)[:80]

        out_dir = Path(output_directory) if output_directory else default_output_dir("images")
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"{safe_output_stem(output_stem)}_{stamp}.png"

        # Prefer factory portrait defaults when size omitted
        if width is None or height is None:
            size = _cfg("PRODUCTION_IMAGE_SIZE", (768, 1344))
            try:
                w_default, h_default = int(size[0]), int(size[1])
            except Exception:  # noqa: BLE001
                w_default, h_default = 736, 1344
            width = width or w_default
            height = height or h_default

        # Trigger prepend + LoRA node inject happen inside prepare_image_workflow
        # (same generate() entry ECONOMIC_REEL already uses via get_image_adapter).
        path = self._manager.generate_image(
            prompt,
            output_path=out_path,
            steps=steps,
            width=width,
            height=height,
            negative_prompt=negative_prompt,
            stem=safe_output_stem(output_stem),
            workflow_name=self.workflow_name,
            page_id=self.page_id,
            lora_name=self._lora_cfg.get("lora_name"),
            lora_strength=self._lora_cfg.get("strength"),
            apply_trigger=True,
        )
        self.last_api_call_count = 1
        self.last_gemini_image_model_used = f"remote_gpu/{self.workflow_name}"
        self.last_cost_key = "image_remote_gpu_flux"
        self.last_gpu_seconds = float(
            getattr(self._manager.client, "last_job_seconds", 0) or 0
        )
        # Rough estimate for adapter metadata; authoritative bill is CostTracker
        # in main via _track_remote_gpu_batch / track_gpu_seconds.
        _mode = str(getattr(self._manager.client, "mode", "comfyui") or "comfyui")
        _rate = (
            0.00044 if _mode in ("runpod", "serverless") else (0.34 / 3600.0)
        )
        self.last_estimated_cost_usd = round(self.last_gpu_seconds * _rate, 6)
        return Path(path).resolve()


def get_image_adapter(*args: Any, **kwargs: Any):
    """
    Convenience re-export — canonical factory lives in
    ``avatar_engine.providers.image_provider.get_image_adapter``.

    Kept here so smoke tests / docs that import from ``remote_gpu_manager``
    resolve correctly. Lazy import avoids a circular dependency.
    """
    from avatar_engine.providers.image_provider import (  # noqa: PLC0415
        get_image_adapter as _factory_get_image_adapter,
    )

    return _factory_get_image_adapter(*args, **kwargs)


__all__ = [
    "ComfyUIClient",
    "RemoteGPUError",
    "RemoteGPUImageAdapter",
    "RemoteGPUManager",
    "WORKFLOW_F5TTS_TXT2AUDIO",
    "WORKFLOW_FLUX_LORA_TXT2IMG",
    "WORKFLOW_FLUX_TXT2IMG",
    "WORKFLOW_WAN22_IMG2VID",
    "deep_copy_workflow",
    "diagnose_wan_sampler_path",
    "generate_audio",
    "generate_image",
    "generate_video",
    "get_image_adapter",
    "get_manager",
    "is_remote_gpu_enabled",
    "remote_gpu_max_parallel",
    "run_parallel_jobs",
    "load_workflow",
    "apply_lora_trigger",
    "normalize_comfy_base_url",
    "patch_workflow",
    "reset_manager",
    "resolve_node",
    "resolve_page_lora_config",
    "resolve_page_voice_reference",
    "set_node_input",
    "workflows_dir",
]
