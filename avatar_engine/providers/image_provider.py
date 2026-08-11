# -*- coding: utf-8 -*-
"""
Image generation abstraction — Together AI FLUX.1-schnell backend.

``GeminiImageAdapter`` retains its historical name for call-site compatibility
but delegates all rendering to Together AI (``black-forest-labs/FLUX.1-schnell``).
"""

from __future__ import annotations

import base64
import io
import logging
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from google import genai
from google.genai import errors as genai_errors
from PIL import Image

import config as app_config
from avatar_engine.providers.gemini_utils import (
    _is_retryable_503,
    build_model_chain,
    chain_with_preferred_first,
    is_model_not_found_error,
)
from avatar_engine.text_utils import safe_output_stem

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-model retry budget for transient 503 / 429 / UNAVAILABLE errors.
# 429 RESOURCE_EXHAUSTED: fixed 3.0 s delay, up to 3 attempts before rotating.
# ---------------------------------------------------------------------------
_IMG_MAX_503_RETRIES: int = 2
_IMG_MAX_429_RETRIES: int = 3
_IMG_429_DELAY_S: float = 3.0
_IMG_RETRY_BASE_S: int = 1
_IMAGE_API_TIMEOUT_S: float = float(getattr(app_config, "IMAGE_API_TIMEOUT_S", 25.0) or 25.0)


def _is_imagen_model(model_id: str | None) -> bool:
    low = (model_id or "").lower().removeprefix("models/")
    return low.startswith("imagen-") or low.startswith("imagen")


def _is_retryable_429(exc: BaseException) -> bool:
    """True for quota / rate-limit / RESOURCE_EXHAUSTED errors."""
    code = getattr(exc, "code", None)
    if code in (429, "429"):
        return True
    status = str(getattr(exc, "status", "") or "").upper()
    if "429" in status or "RESOURCE_EXHAUSTED" in status or "RATE_LIMIT" in status:
        return True
    msg = str(getattr(exc, "message", None) or exc).upper()
    return (
        "429" in msg
        or "RESOURCE_EXHAUSTED" in msg
        or "RATE LIMIT" in msg
        or "RATE_LIMIT" in msg
        or "QUOTA" in msg
        or "TOO MANY REQUESTS" in msg
    )


def _run_with_timeout(fn, *, timeout_s: float = _IMAGE_API_TIMEOUT_S, label: str = "image API"):
    """
    Execute ``fn`` in a worker thread with a hard timeout.
    Raises TimeoutError on expiry so callers can rotate to the next SKU cleanly.
    """
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(fn)
        try:
            return fut.result(timeout=max(1.0, float(timeout_s)))
        except FuturesTimeoutError as exc:
            raise TimeoutError(
                f"{label} exceeded {timeout_s:.0f}s hard timeout — rotating SKU"
            ) from exc


def _call_generate_content_with_backoff(
    client: genai.Client,
    model: str,
    contents: list[Any],
    config: Any | None,
    *,
    max_retries: int = _IMG_MAX_503_RETRIES,
    base_delay: int = _IMG_RETRY_BASE_S,
    call_counter: "list[int] | None" = None,
    timeout_s: float = _IMAGE_API_TIMEOUT_S,
) -> tuple[Any, bool]:
    """
    Call ``client.models.generate_content`` with 503/429 backoff + hard timeout.

    Returns ``(response, advanced)`` where ``advanced=True`` means the caller
    should skip to the next model in the chain (404 / timeout / exhausted retries).
    """
    last_exc: BaseException | None = None
    attempts_429 = 0
    attempts_503 = 0
    # Combined loop: allow up to max(429, 503) budget while honouring each type
    for attempt in range(max(_IMG_MAX_429_RETRIES, max_retries) + 1):
        try:
            def _do_call() -> Any:
                kwargs: dict[str, Any] = {"model": model, "contents": contents}
                if config is not None:
                    kwargs["config"] = config
                return client.models.generate_content(**kwargs)

            if call_counter is not None:
                call_counter[0] += 1
            response = _run_with_timeout(
                _do_call, timeout_s=timeout_s, label=f"generate_content[{model}]"
            )
            return response, False

        except TimeoutError as exc:
            logger.warning("%s — advancing chain.", exc)
            return None, True

        except (genai_errors.APIError, Exception) as exc:  # noqa: BLE001
            last_exc = exc

            if is_model_not_found_error(exc):
                logger.warning(
                    "IMAGE 404 '%s' — model not found / removed; advancing chain.", model
                )
                return None, True

            if _is_retryable_429(exc):
                attempts_429 += 1
                if attempts_429 <= _IMG_MAX_429_RETRIES:
                    logger.warning(
                        "IMAGE 429/RESOURCE_EXHAUSTED '%s' attempt %d/%d — sleeping %.1fs.",
                        model, attempts_429, _IMG_MAX_429_RETRIES, _IMG_429_DELAY_S,
                    )
                    time.sleep(_IMG_429_DELAY_S)
                    continue
                logger.warning(
                    "IMAGE 429 '%s' — retries exhausted; advancing chain (blinded fallback).",
                    model,
                )
                return None, True

            if _is_retryable_503(exc):
                attempts_503 += 1
                if attempts_503 < max_retries:
                    wait = base_delay * (2 ** (attempts_503 - 1))
                    logger.warning(
                        "IMAGE 503 '%s' attempt %d/%d — backoff %ds.",
                        model, attempts_503, max_retries, wait,
                    )
                    time.sleep(wait)
                    continue
                logger.warning(
                    "IMAGE 503 '%s' — retries exhausted; advancing chain.", model
                )
                return None, True

            # Non-retryable: log and advance rather than hang the pipeline
            logger.warning(
                "IMAGE error on '%s' (%s); advancing chain without hang.", model, exc
            )
            return None, True

    if last_exc:
        logger.warning("IMAGE giving up on '%s' (%s); advancing.", model, last_exc)
    return None, True


def _iterate_response_parts(response: Any) -> Iterable[Any]:
    """Yield all content parts from a generate_content response (all candidates)."""
    seen: set[int] = set()

    def _yield_parts(parts: Any) -> Iterable[Any]:
        for part in parts or []:
            pid = id(part)
            if pid in seen:
                continue
            seen.add(pid)
            yield part

    top = getattr(response, "parts", None)
    if top:
        yield from _yield_parts(top)

    for cand in getattr(response, "candidates", None) or []:
        content = getattr(cand, "content", None)
        if content is None:
            continue
        yield from _yield_parts(getattr(content, "parts", None))


def _coerce_image_bytes(raw: Any) -> bytes | None:
    """Normalize inline image payloads (bytes, bytearray, memoryview, or base64 str)."""
    if raw is None:
        return None
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    if isinstance(raw, bytearray):
        raw = bytes(raw)
    if isinstance(raw, bytes):
        if not raw:
            return None
        # Some SDKs return base64 *as* bytes (ASCII) — detect & decode
        head = raw[:32].lstrip()
        if head.startswith(b"iVBOR") or head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8"):
            return raw
        try:
            text = raw.decode("ascii").strip()
            if re_is_b64_looking(text):
                return base64.b64decode(text, validate=False)
        except Exception:  # noqa: BLE001
            pass
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if text.startswith("data:") and "," in text:
            text = text.split(",", 1)[1]
        try:
            return base64.b64decode(text, validate=False)
        except Exception:  # noqa: BLE001
            return None
    return None


def re_is_b64_looking(text: str) -> bool:
    if len(text) < 64:
        return False
    sample = text[:120].replace("\n", "").replace("\r", "")
    return all(c.isalnum() or c in "+/=" for c in sample)


def _save_image_bytes(out_path: Path, data: bytes) -> bool:
    """Write image bytes; validate via PIL when possible."""
    if not data:
        return False
    try:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.open(io.BytesIO(data))
        img.load()
        # Normalize to PNG for pipeline consistency
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
        img.save(out_path, format="PNG")
        return out_path.is_file() and out_path.stat().st_size > 0
    except Exception:  # noqa: BLE001
        # Fall through: raw write (may already be PNG/JPEG bytes)
        try:
            out_path = Path(out_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(data)
            return out_path.is_file() and out_path.stat().st_size > 0
        except Exception:  # noqa: BLE001
            return False


def _extract_and_save_image(response: Any, out_path: Path) -> bool:
    """
    Extract image byte stream / PIL from a Gemini flash-image response.

    Iterates ALL parts (text parts often expose ``as_image() → None``; that must
    NOT abort the loop — image bytes live on a later part).
    """
    out_path = Path(out_path)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(
            "Cannot create image output directory '%s' (%s).", out_path.parent, exc
        )
        return False

    for part in _iterate_response_parts(response):
        # 1) inline_data / inlineData blob
        inline = getattr(part, "inline_data", None) or getattr(part, "inlineData", None)
        if inline is not None:
            raw = getattr(inline, "data", None)
            if raw is None and isinstance(inline, dict):
                raw = inline.get("data")
            blob = _coerce_image_bytes(raw)
            if blob and _save_image_bytes(out_path, blob):
                logger.info("Image payload saved via inline_data | path=%s", out_path)
                return True

        # 2) SDK Part.as_image() → PIL (skip None — keep scanning parts)
        if hasattr(part, "as_image"):
            try:
                pil_image = part.as_image()
            except Exception as exc:  # noqa: BLE001
                logger.debug("as_image() raised on part (%s); continuing.", exc)
                pil_image = None
            if pil_image is not None:
                try:
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    pil_image.save(out_path)
                    if out_path.is_file() and out_path.stat().st_size > 0:
                        logger.info("Image payload saved via as_image() | path=%s", out_path)
                        return True
                except Exception as exc:  # noqa: BLE001
                    logger.warning("as_image() save failed (%s); trying next part.", exc)

        # 3) Rare: part.blob / media bytes
        for attr in ("blob", "data", "image_bytes"):
            blob = _coerce_image_bytes(getattr(part, attr, None))
            if blob and _save_image_bytes(out_path, blob):
                logger.info("Image payload saved via part.%s | path=%s", attr, out_path)
                return True

    return False


def _generation_config_for_image(aspect_ratio: str):
    """Build SDK config when ``ImageConfig`` is available."""
    try:
        from google.genai import types  # type: ignore
        return types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
        )
    except Exception:  # noqa: BLE001
        return None


class ImageProvider(ABC):
    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        reference_image_path: Path | None = None,
        style_reference_path: Path | None = None,
        style_reference_paths: "list[Path] | None" = None,
        style_reference_weight: "float | None" = None,
        output_stem: str = "avatar_post",
        output_directory: Path | None = None,
        avatar_mode: str = "ON",
    ) -> Path:
        """Return filesystem path of the rendered asset."""


class GeminiImageAdapter(ImageProvider):
    """
    Image adapter used by the reel/video pipeline.

    **Backend:** Together AI `black-forest-labs/FLUX.1-schnell` (steps=4).
    Class name retained for backwards compatibility with `main.py` imports.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model_id: str | None = None,
        *,
        tier: str | None = None,
        use_premium: bool | None = None,
        page_cost_tier: str | None = None,
    ) -> None:
        from avatar_engine.providers.together_image import TogetherImageAdapter

        # Prefer Together key; ignore Gemini key for image generation.
        together_key = api_key
        if together_key and together_key == getattr(app_config, "GEMINI_API_KEY", None):
            together_key = None
        self._impl = TogetherImageAdapter(
            api_key=together_key or getattr(app_config, "TOGETHER_API_KEY", None),
            model_id=model_id,
            tier=tier,
            use_premium=use_premium,
            page_cost_tier=page_cost_tier,
        )
        self._model_id = self._impl._model_id
        self._image_chain = list(self._impl._image_chain)
        self._router_tier = getattr(self._impl, "_router_tier", "cheap")
        self.last_gemini_image_model_used = self._impl.last_gemini_image_model_used
        self.last_gemini_image_failure_model_id = None
        self.last_api_call_count = 0
        self.last_estimated_cost_usd = float(
            getattr(self._impl, "last_estimated_cost_usd", 0.003) or 0.003
        )
        self.last_cost_key = getattr(self._impl, "last_cost_key", "image_flux_schnell")
        self._gemini_client: genai.Client | None = None
        logger.info(
            "GeminiImageAdapter → Together default | model=%s | est_cost=$%.3f/img "
            "(Gemini reference path enabled when avatar_mode=ON + reference image)",
            self._model_id, self.last_estimated_cost_usd,
        )

    def _gemini_client_lazy(self) -> genai.Client:
        if self._gemini_client is None:
            key = app_config.GEMINI_API_KEY
            if not key:
                raise ValueError(
                    "GEMINI_API_KEY required for avatar reference likeness generation."
                )
            self._gemini_client = genai.Client(api_key=key)
        return self._gemini_client

    def _generate_with_gemini_reference(
        self,
        prompt: str,
        *,
        reference_image_path: Path,
        style_reference_path: Path | None,
        output_stem: str,
        output_directory: Path | None,
        aspect_ratio: str | None,
        reference_image_weight: float | None,
        style_reference_paths: "list[Path] | None" = None,
        style_reference_weight: "float | None" = None,
    ) -> Path:
        """Legacy Gemini flash-image path — attaches reference portrait for likeness."""
        client = self._gemini_client_lazy()
        ratio = aspect_ratio or app_config.GEMINI_IMAGE_ASPECT_RATIO or "3:4"
        out_dir = Path(output_directory or app_config.OUTPUTS_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = safe_output_stem(output_stem)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        out_path = out_dir / f"{slug}_{ts}.png"

        contents: list[Any] = []
        # Reference portrait first (likeness lock)
        ref = Path(reference_image_path)
        if ref.is_file():
            try:
                contents.append(Image.open(ref))
                w = reference_image_weight if reference_image_weight is not None else 0.75
                contents.append(
                    f"REFERENCE LIKENESS (weight≈{w:.2f}): Match this person's face, "
                    "age, and identity exactly in the generated scene."
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not load avatar reference (%s)", exc)

        # MODULE 3 — dynamic multi-image style reference (2-3 images, IP-Adapter-style
        # weight). Genuine image-conditioned style guidance: Gemini flash-image accepts
        # multiple images in a single request.
        sref_candidates = list(style_reference_paths or [])
        if not sref_candidates and style_reference_path is not None:
            sref_candidates = [style_reference_path]
        sref_valid = [Path(p) for p in sref_candidates if p and Path(p).is_file()][:3]
        sw = style_reference_weight if style_reference_weight is not None else 0.72
        for idx, sref in enumerate(sref_valid):
            try:
                contents.append(Image.open(sref))
                contents.append(
                    f"STYLE REFERENCE {idx + 1}/{len(sref_valid)} (weight≈{sw:.2f}): "
                    "Match this aesthetic's lighting, texture, and mood."
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not load style reference %s (%s)", sref, exc)

        contents.append(
            f"Compose for a strict {ratio} portrait frame.\n\n{prompt}"
        )
        gen_cfg = _generation_config_for_image(ratio)
        model = app_config.normalize_image_model_id(
            getattr(app_config, "SAFE_GEMINI_IMAGE_FALLBACK_2", None)
            or "models/gemini-2.5-flash-image"
        )
        # Prefer live Gemini image SKUs (not Together FLUX ids)
        if "flux" in model.lower() or "black-forest" in model.lower():
            model = "models/gemini-2.5-flash-image"
        chain = [model, "models/gemini-3.1-flash-image", "models/gemini-2.5-flash-image"]
        hits = [0]
        last_err: BaseException | None = None
        for candidate in chain:
            self.last_gemini_image_failure_model_id = candidate
            try:
                response, advance = _call_generate_content_with_backoff(
                    client, candidate, contents, gen_cfg, call_counter=hits,
                )
                if advance or response is None:
                    continue
                if _extract_and_save_image(response, out_path):
                    self.last_api_call_count = max(1, hits[0])
                    self.last_gemini_image_model_used = candidate
                    self.last_cost_key = "image_gemini_flash"
                    self.last_estimated_cost_usd = 0.003
                    logger.info(
                        "Avatar likeness via Gemini | model=%s | %s",
                        candidate, out_path.name,
                    )
                    return out_path.resolve()
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.warning("Gemini reference generate failed on %s (%s)", candidate, exc)
        if last_err:
            raise last_err
        raise RuntimeError("Gemini reference image generation returned no payload.")

    def generate(
        self,
        prompt: str,
        *,
        reference_image_path: Path | None = None,
        style_reference_path: Path | None = None,
        style_reference_paths: "list[Path] | None" = None,
        style_reference_weight: "float | None" = None,
        output_stem: str = "avatar_post",
        output_directory: Path | None = None,
        aspect_ratio: str | None = None,
        avatar_mode: str = "ON",
        reference_image_weight: float | None = None,
        model_name: str | None = None,
        steps: int | None = None,
        draft: bool = False,
        width: int | None = None,
        height: int | None = None,
        visual_role: str | None = None,
        negative_prompt: str | None = None,
    ) -> Path:
        # Master Mei / likeness lock: route to Gemini when reference is attached
        # ONLY for Role A (caller must pass avatar_mode=ON + ref for master slots).
        use_ref = (
            (avatar_mode or "").upper() == "ON"
            and reference_image_path is not None
            and Path(reference_image_path).is_file()
        )
        if use_ref:
            try:
                return self._generate_with_gemini_reference(
                    prompt,
                    reference_image_path=Path(reference_image_path),
                    style_reference_path=style_reference_path,
                    style_reference_paths=style_reference_paths,
                    style_reference_weight=style_reference_weight,
                    output_stem=output_stem,
                    output_directory=output_directory,
                    aspect_ratio=aspect_ratio,
                    reference_image_weight=reference_image_weight,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Gemini likeness path failed (%s) — falling back to Together txt2img.",
                    exc,
                )

        path = self._impl.generate(
            prompt,
            reference_image_path=reference_image_path,
            style_reference_path=style_reference_path,
            style_reference_paths=style_reference_paths,
            style_reference_weight=style_reference_weight,
            output_stem=output_stem,
            output_directory=output_directory,
            aspect_ratio=aspect_ratio,
            avatar_mode=avatar_mode,
            reference_image_weight=reference_image_weight,
            model_name=model_name,
            steps=steps,
            draft=draft,
            width=width,
            height=height,
            visual_role=visual_role,
            negative_prompt=negative_prompt,
        )
        self.last_api_call_count = int(getattr(self._impl, "last_api_call_count", 1) or 1)
        self.last_gemini_image_model_used = getattr(
            self._impl, "last_gemini_image_model_used", self._model_id
        )
        self.last_gemini_image_failure_model_id = getattr(
            self._impl, "last_gemini_image_failure_model_id", None
        )
        self.last_estimated_cost_usd = float(
            getattr(self._impl, "last_estimated_cost_usd", 0.003) or 0.003
        )
        self.last_cost_key = getattr(self._impl, "last_cost_key", "image_flux_schnell")
        return path


# Back-compat / explicit export
def get_image_adapter(*args, **kwargs):
    """
    Factory: returns the active image adapter.

    When ``ENABLE_REMOTE_GPU_WORKFLOWS=true``, routes to the ComfyUI/RunPod
    Flux adapter. Otherwise returns the legacy Together-backed
    ``GeminiImageAdapter`` unchanged.
    """
    from core_engine.remote_gpu_manager import (  # noqa: PLC0415
        RemoteGPUImageAdapter,
        is_remote_gpu_enabled,
    )

    # Runtime / flow-preset check (media-aware) for long-running schedulers
    if is_remote_gpu_enabled("image"):
        logger.info("Image adapter -> RemoteGPU (flow/env image provider=remote_gpu)")
        return RemoteGPUImageAdapter(*args, **kwargs)
    return GeminiImageAdapter(*args, **kwargs)

