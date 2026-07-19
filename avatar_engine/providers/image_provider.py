# -*- coding: utf-8 -*-
"""
Image generation abstraction plus Gemini adapter.

Avatar modes
------------
ON  (default) — sends the reference portrait image alongside the text prompt
                 to lock facial identity across the frame.
OFF           — sends text-only prompt regardless of whether a reference image
                 is configured. Bypasses all likeness anchoring.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from google import genai
from google.genai import errors as genai_errors
from PIL import Image

import config as app_config
from avatar_engine.providers.gemini_utils import (
    build_model_chain,
    chain_with_preferred_first,
    is_model_not_found_error,
)

logger = logging.getLogger(__name__)


def _iterate_response_parts(response: Any) -> Iterable[Any]:
    parts = getattr(response, "parts", None)
    if parts:
        yield from parts
        return
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return
    content = getattr(candidates[0], "content", None)
    if content is None:
        return
    yield from getattr(content, "parts", []) or []


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
        output_stem: str = "avatar_post",
        output_directory: Path | None = None,
        avatar_mode: str = "ON",
    ) -> Path:
        """Return filesystem path of the rendered asset."""


class GeminiImageAdapter(ImageProvider):
    def __init__(self, api_key: str | None = None, model_id: str | None = None) -> None:
        key = api_key or app_config.GEMINI_API_KEY
        if not key:
            raise ValueError("Gemini API key missing. Set GEMINI_API_KEY.")
        self._client = genai.Client(api_key=key)
        preferred = model_id or app_config.GEMINI_IMAGE_MODEL
        self._image_chain = build_model_chain(
            self._client,
            capability_type="image",
            preferred=preferred,
        )
        self._model_id = self._image_chain[0] if self._image_chain else preferred
        self.last_gemini_image_model_used: str | None = None
        self.last_gemini_image_failure_model_id: str | None = None
        logger.debug(
            "GeminiImageAdapter primary: %s (image chain length %d)",
            self._model_id,
            len(self._image_chain),
        )

    def generate(
        self,
        prompt: str,
        *,
        reference_image_path: Path | None = None,
        style_reference_path: Path | None = None,
        output_stem: str = "avatar_post",
        output_directory: Path | None = None,
        aspect_ratio: str | None = None,
        avatar_mode: str = "ON",
    ) -> Path:
        """
        Generate an image from the prompt.

        Parameters
        ----------
        prompt:
            The image generation prompt (already built by VisualArchitect).
        reference_image_path:
            Portrait reference used for likeness-locking.  Ignored when
            avatar_mode='OFF' regardless of whether the file exists.
        style_reference_path:
            Optional fine-art / sketch reference image.  When provided and the
            file exists on disk, it is prepended to the Gemini contents list so
            the model can match its artistic texture and tonal palette.
            Applied regardless of avatar_mode.
        output_stem:
            Filename prefix for the saved PNG.
        output_directory:
            Directory to write the output file.
        aspect_ratio:
            Override the configured global aspect ratio.
        avatar_mode:
            'ON'  — include reference image if available (likeness-locked generation).
            'OFF' — text-only prompt; reference image never sent to the model.
        """
        contents: list[Any] = []

        ratio = aspect_ratio or app_config.GEMINI_IMAGE_ASPECT_RATIO
        ratio_note = (
            f"Compose for a strict {ratio} portrait frame (vertical social feed), "
            "full-bleed subject with tasteful headroom and foot room."
        )
        prompt_with_ratio = f"{ratio_note}\n\n{prompt}"

        # ------------------------------------------------------------------
        # Reference image: only injected when avatar_mode == 'ON'.
        # ------------------------------------------------------------------
        use_reference = avatar_mode == "ON"

        ref_path: Path | None = None
        if use_reference:
            # Prefer explicitly passed path, then fall back to global config.
            if reference_image_path is not None:
                ref_path = Path(reference_image_path)
            else:
                ref_path = Path(app_config.REFERENCE_IMAGE_PATH)

        if use_reference and ref_path is not None and ref_path.exists():
            reference_prompt = (
                "Use the uploaded reference portrait ONLY to preserve facial identity across the frame: "
                "bone structure, age cues, complexion, hairstyle. Recreate wardrobe and staging from prompt. "
                "Do not caricature.\n\n"
            )
            contents.extend([reference_prompt + prompt_with_ratio, Image.open(ref_path)])
        else:
            if use_reference and ref_path is not None:
                logger.warning(
                    "Reference likeness path configured but missing on disk (%s); "
                    "sending text-only image prompt.",
                    ref_path.resolve(),
                )
            elif not use_reference:
                logger.debug(
                    "Avatar mode OFF: skipping reference image; sending text-only prompt."
                )
            contents.append(prompt_with_ratio)

        # Style reference image — injected into the Gemini contents list so the
        # model matches the artistic texture, tonal palette, and cross-hatching
        # style of the reference PNG rather than inferring style from text alone.
        _sref = Path(style_reference_path) if style_reference_path else None
        if _sref is not None and _sref.exists():
            _style_instruction = (
                "STYLE REFERENCE IMAGE (high priority): Match the exact visual aesthetic "
                "of the uploaded reference — charcoal pencil sketch illustration, "
                "dense monochrome cross-hatching, moody atmospheric chiaroscuro, "
                "dark azure-blue and deep charcoal tones, sharp white highlights. "
                "Replicate the texture, line weight, and tonal contrast of that reference "
                "for the new scene. Do NOT copy the subject matter — only the style.\n\n"
            )
            try:
                contents.append(_style_instruction)
                contents.append(Image.open(_sref))
                logger.info(
                    "Style reference injected into Gemini payload: %s", _sref.name
                )
            except Exception as _sref_exc:
                logger.warning(
                    "Style reference load failed (%s) — continuing without it.", _sref_exc
                )
        else:
            if style_reference_path:
                logger.warning(
                    "Style reference path configured but file not found on disk (%s).",
                    style_reference_path,
                )

        gen_cfg = _generation_config_for_image(ratio)
        chain = chain_with_preferred_first(self._image_chain, self._model_id)

        # ── Imagen fast-path ──────────────────────────────────────────────
        # Imagen models use client.models.generate_images(), not generate_content().
        # Detect by model slug and route separately so the standard Gemini
        # generate_content loop never receives an Imagen model ID (which would
        # return an API error rather than a graceful skip).
        _primary_slug = (self._model_id or "").lower().lstrip("models/")
        if _primary_slug.startswith("imagen"):
            return self._generate_via_imagen(
                prompt_with_ratio,
                ratio=ratio,
                output_stem=output_stem,
                output_directory=output_directory,
            )

        self.last_gemini_image_model_used = None
        self.last_gemini_image_failure_model_id = None

        out_dir = output_directory or app_config.OUTPUTS_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = "".join(ch if ch.isalnum() else "_" for ch in output_stem).strip("_") or "generated"
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        out_path = out_dir / f"{slug}_{ts}.png"

        # ------------------------------------------------------------------
        # Premium safe-harbour model used as absolute last resort when every
        # model in the chain either fails the API call or returns a None
        # image payload.
        # ------------------------------------------------------------------
        _PREMIUM_FALLBACK = app_config.SAFE_GEMINI_IMAGE_MODEL

        # Build extended chain: configured chain + premium fallback (deduplicated)
        seen: set[str] = set()
        full_chain: list[str] = []
        for m in list(chain) + [_PREMIUM_FALLBACK]:
            if m not in seen:
                seen.add(m)
                full_chain.append(m)

        last_err: BaseException | None = None
        saved = False

        for candidate in full_chain:
            self.last_gemini_image_failure_model_id = candidate
            response = None

            # --- Attempt API call with ImageConfig, then without ---
            if gen_cfg is not None:
                try:
                    response = self._client.models.generate_content(
                        model=candidate,
                        contents=contents,
                        config=gen_cfg,
                    )
                    logger.info("Gemini OK | generate_content (image+cfg) | model=%s", candidate)
                except genai_errors.APIError as exc:
                    last_err = exc
                    if is_model_not_found_error(exc):
                        logger.warning(
                            "GEMINI_ALERT (image): `%s` unavailable (%s); trying next SKU.",
                            candidate, exc,
                        )
                        continue
                    logger.warning(
                        "Image ImageConfig rejected (%s); retry without ImageConfig on %s.",
                        exc, candidate,
                    )
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    if is_model_not_found_error(exc):
                        logger.warning(
                            "GEMINI_ALERT (image): `%s` unavailable (%s); trying next SKU.",
                            candidate, exc,
                        )
                        continue
                    logger.warning(
                        "Image generation with ImageConfig failed (%s); retry without config on %s.",
                        exc, candidate,
                    )

            if response is None:
                # Retry the same candidate without config.
                # Prepend the hardcoded sketch enforcement directive to guarantee
                # Gemini cannot drift into photorealism on the retry attempt.
                _SKETCH_FALLBACK_PREFIX = (
                    "in the style of a detailed, emotional charcoal pencil sketch illustration, "
                    "monochrome cross-hatching, moody atmosphere, soft horror style couple "
                    "with theatrical expressionless masks. "
                )
                _fallback_contents: list[Any] = list(contents)
                if _fallback_contents and isinstance(_fallback_contents[0], str):
                    _fallback_contents[0] = _SKETCH_FALLBACK_PREFIX + _fallback_contents[0]
                else:
                    _fallback_contents.insert(0, _SKETCH_FALLBACK_PREFIX)
                logger.debug("Image fallback retry | sketch prefix prepended to contents.")
                try:
                    response = self._client.models.generate_content(
                        model=candidate, contents=_fallback_contents
                    )
                    logger.info("Gemini OK | generate_content (image/no-cfg) | model=%s", candidate)
                except genai_errors.APIError as exc:
                    last_err = exc
                    if is_model_not_found_error(exc):
                        logger.warning(
                            "GEMINI_ALERT (image): `%s` unavailable (%s); trying next SKU.",
                            candidate, exc,
                        )
                        continue
                    raise
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    if is_model_not_found_error(exc):
                        logger.warning(
                            "GEMINI_ALERT (image): `%s` unavailable (%s); trying next SKU.",
                            candidate, exc,
                        )
                        continue
                    raise

            if response is None:
                logger.warning("GEMINI_ALERT (image): no response object from `%s`; skipping.", candidate)
                continue

            # --- Extract image payload from response ---
            # Both inline_data bytes and as_image() PIL objects are attempted.
            # If neither yields a real image, we log and try the next chain model
            # rather than crashing with AttributeError.
            for part in _iterate_response_parts(response):
                inline = getattr(part, "inline_data", None)
                data = getattr(inline, "data", None) if inline else None
                if data:
                    with out_path.open("wb") as handle:
                        handle.write(data if isinstance(data, (bytes, bytearray)) else bytes(data))
                    self.last_gemini_image_model_used = candidate
                    saved = True
                    logger.info(
                        "Image payload saved via inline_data | model=%s | path=%s",
                        candidate, out_path,
                    )
                    break
                if hasattr(part, "as_image"):
                    pil_image = part.as_image()
                    if pil_image is None:
                        # Model accepted the request but returned a null PIL object.
                        # Log clearly and fall through to the next chain model.
                        logger.warning(
                            "GEMINI_ALERT (image): `%s` returned a None PIL image payload. "
                            "Falling back to next chain model.",
                            candidate,
                        )
                        break  # break inner parts loop — outer loop continues to next model
                    pil_image.save(out_path)
                    self.last_gemini_image_model_used = candidate
                    saved = True
                    logger.info(
                        "Image payload saved via as_image() | model=%s | path=%s",
                        candidate, out_path,
                    )
                    break

            if saved:
                break  # successfully wrote image — exit the model chain loop

            logger.warning(
                "GEMINI_ALERT (image): `%s` response contained no downloadable image payload; "
                "trying next SKU.",
                candidate,
            )

        if not saved:
            if last_err:
                raise last_err
            raise RuntimeError(
                "All Gemini image models in chain (including premium fallback) returned no valid image payload."
            )

        return out_path.resolve()

    # ------------------------------------------------------------------
    # Imagen generate_images() fast path
    # ------------------------------------------------------------------

    def _generate_via_imagen(
        self,
        prompt: str,
        *,
        ratio: str,
        output_stem: str,
        output_directory: "Path | None",
    ) -> "Path":
        """
        Generate an image using the Imagen API (generate_images, not generate_content).

        Uses ``self._model_id`` as the Imagen model slug.  Falls back to the
        standard Gemini generate_content chain when Imagen raises any error,
        so a mis-configured or quota-exceeded Imagen key does not hard-crash.
        """
        from google.genai import types as _genai_types  # type: ignore[import]

        out_dir = output_directory or (
            Path(app_config.ENGINE_ROOT) / "outputs" / "images"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        out_path = out_dir / f"{output_stem}_{ts}.png"

        # Allowed Imagen aspect ratios (exact strings the API accepts)
        _VALID_IMAGEN_RATIOS = {"1:1", "4:3", "3:4", "16:9", "9:16"}
        imagen_ratio = ratio if ratio in _VALID_IMAGEN_RATIOS else "3:4"

        try:
            logger.info(
                "Imagen fast-path | model=%s | ratio=%s", self._model_id, imagen_ratio
            )
            response = self._client.models.generate_images(
                model=self._model_id,
                prompt=prompt,
                config=_genai_types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio=imagen_ratio,
                    person_generation="ALLOW_ADULT",
                ),
            )
            if response and response.generated_images:
                pil_image = response.generated_images[0].image
                if pil_image is not None:
                    pil_image.save(str(out_path))
                    self.last_gemini_image_model_used = self._model_id
                    logger.info(
                        "Image payload saved via Imagen | model=%s | path=%s",
                        self._model_id, out_path,
                    )
                    return out_path.resolve()
            logger.warning(
                "Imagen model '%s' returned no image payload — falling back to Gemini chain.",
                self._model_id,
            )
        except Exception as _img_exc:  # noqa: BLE001
            logger.warning(
                "Imagen call failed (%s) — falling back to Gemini generate_content chain.",
                _img_exc,
            )

        # Fallback: run the standard generate_content chain
        gen_cfg = _generation_config_for_image(imagen_ratio)
        chain = chain_with_preferred_first(self._image_chain, None)
        # Re-enter the generate_content loop (reuse same logic block via a
        # minimal recursive call pattern — avoids code duplication).
        self._model_id = app_config.SAFE_GEMINI_IMAGE_MODEL
        self._image_chain = [app_config.SAFE_GEMINI_IMAGE_MODEL]
        return self._run_generate_content_chain(
            prompt, gen_cfg=gen_cfg, out_path=out_path
        )

    def _run_generate_content_chain(
        self,
        prompt: str,
        *,
        gen_cfg: "Any",
        out_path: "Path",
    ) -> "Path":
        """Minimal generate_content chain runner used as Imagen fallback."""
        from avatar_engine.providers.gemini_utils import chain_with_preferred_first  # noqa: F811

        chain = chain_with_preferred_first(self._image_chain, self._model_id)
        _PREMIUM_FALLBACK = app_config.SAFE_GEMINI_IMAGE_MODEL
        full_chain = list(chain)
        if _PREMIUM_FALLBACK not in full_chain:
            full_chain.append(_PREMIUM_FALLBACK)

        saved = False
        last_err: "BaseException | None" = None

        for candidate in full_chain:
            try:
                response = self._client.models.generate_content(
                    model=candidate,
                    contents=[prompt],
                    config=gen_cfg,
                )
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                continue

            if not response:
                continue

            for part in _iterate_response_parts(response):
                inline = getattr(part, "inline_data", None)
                data = getattr(inline, "data", None) if inline else None
                if data:
                    with out_path.open("wb") as h:
                        h.write(data if isinstance(data, (bytes, bytearray)) else bytes(data))
                    self.last_gemini_image_model_used = candidate
                    saved = True
                    break
                if hasattr(part, "as_image"):
                    pil_image = part.as_image()
                    if pil_image is not None:
                        pil_image.save(out_path)
                        self.last_gemini_image_model_used = candidate
                        saved = True
                        break
            if saved:
                break

        if not saved:
            if last_err:
                raise last_err
            raise RuntimeError("Imagen fallback chain: all models returned no image payload.")

        return out_path.resolve()
