# -*- coding: utf-8 -*-
"""Gemini 2.5 Flash OCR engine with incremental vault caching.

Standalone, production-oriented module. Running this file as a script OCRs every
image under ``TARGET_FOLDER`` in one Gemini call (max 100 images) and upserts
results into ``ocr_vault.json`` under the ``ocr_momma_deploy`` dataset key.

Typical usage::

    from utils.ocr_engine import OCREngine

    engine = OCREngine()
    summary = engine.process_directory(TARGET_FOLDER)

    # or:  python utils/ocr_engine.py
"""
from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import random
import re
import sys
import time
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, TypedDict

from dotenv import load_dotenv
from google import genai
from google.genai import types

_PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.gemini_usage import (
    GeminiUsage,
    extract_usage,
    hush_gemini_sdk_logs,
    log_usage,
    unwrap_json_object,
)
from utils.ocr_text import strip_wrapping_quotes

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path / model constants
# ---------------------------------------------------------------------------

TARGET_FOLDER: Path = Path(
    r"G:\My Drive\Z sosFiles\Z_act\@ NETWORK"
    r"\@MEDIAUPSCALE_FACTORY_DYNAMIC_CONTENT"
    r"\Unified Multi-Page Factory\assets\OCR SOURCE\momma"
)
VAULT_PATH: Path = Path(
    r"G:\My Drive\Z sosFiles\Z_act\@ NETWORK"
    r"\@MEDIAUPSCALE_FACTORY_DYNAMIC_CONTENT"
    r"\Unified Multi-Page Factory\assets\ocr_vault.json"
)
DATASET_KEY: str = "ocr_momma_deploy"
GEMINI_OCR_MODEL: str = "gemini-2.5-flash"

IMAGE_EXTENSIONS: frozenset[str] = frozenset({
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
})

OCR_SYSTEM_PROMPT: str = (
    "Extract ONLY the exact text shown in the image, preserving line breaks "
    "and original punctuation. If handwritten or notebook-style, return only "
    "the raw transcribed text without markdown code blocks (e.g., no ```text), "
    "commentary, or greetings."
)
OCR_USER_PROMPT: str = (
    "OCR every attached image. Return ONLY a JSON object that maps each "
    "IMAGE_KEY to the exact transcribed text. Preserve line breaks. Use an "
    "empty string when an image has no readable text."
)

_MAX_RETRIES: int = 6
_BACKOFF_BASE_S: float = 1.0
_BACKOFF_CAP_S: float = 60.0
_MAX_IMAGES_PER_CALL: int = 100
_MAX_IMAGE_BYTES: int = 15 * 1024 * 1024
_FENCE_RE = re.compile(
    r"^```(?:[a-zA-Z0-9_-]+)?\r?\n(.*)\r?\n```\s*$",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Typed vault records
# ---------------------------------------------------------------------------

class OCRItem(TypedDict, total=False):
    file_path: str
    extracted_text: str
    processed_at: str
    status: str
    error: str


class OCRDataset(TypedDict):
    last_updated: str
    total_items: int
    items: dict[str, OCRItem]


class ProcessSummary(TypedDict):
    dataset_key: str
    vault_path: str
    last_updated: str
    total_items: int
    discovered: int
    processed: int
    skipped: int
    failed: int


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    """Return a seconds-precision local ISO-8601 timestamp (matches vault spec)."""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _load_env() -> None:
    """Load ``GEMINI_API_KEY`` from the project ``.env``, then the process cwd."""
    project_env = _PROJECT_ROOT / ".env"
    if project_env.is_file():
        load_dotenv(dotenv_path=project_env, override=False, encoding="utf-8-sig")
    load_dotenv(override=False)


def _resolve_api_key(explicit: str | None = None) -> str:
    _load_env()
    key = (
        (explicit or "").strip()
        or (os.getenv("GEMINI_API_KEY") or "").strip()
        or (os.getenv("GOOGLE_API_KEY") or "").strip()
    )
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to the project .env or the environment."
        )
    return key


def _mime_for(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    if guessed and guessed.startswith("image/"):
        return guessed
    suffix = path.suffix.lower()
    mapping = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".heic": "image/heic",
        ".heif": "image/heif",
    }
    return mapping.get(suffix, "image/jpeg")


def _clean_ocr_text(raw: str) -> str:
    """Strip accidental markdown fences while keeping original line breaks."""
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        return fenced.group(1).strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return strip_wrapping_quotes(text)


def _response_text(response: Any) -> str:
    direct = (getattr(response, "text", None) or "").strip()
    if direct:
        return direct
    chunks: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            piece = getattr(part, "text", None)
            if piece and not getattr(part, "thought", False):
                chunks.append(str(piece))
    return "\n".join(chunks).strip()


def _map_ocr_texts(payload: Mapping[str, Any], keys: list[str]) -> dict[str, str]:
    """Match model JSON keys to vault item keys (exact, then filename)."""
    raw = {str(key): "" if value is None else str(value) for key, value in payload.items()}
    mapped: dict[str, str] = {}
    by_name = {Path(key).name: text for key, text in raw.items()}
    for key in keys:
        if key in raw:
            mapped[key] = raw[key]
        elif Path(key).name in by_name:
            mapped[key] = by_name[Path(key).name]
    return mapped


def _exception_status(exc: BaseException) -> tuple[int | None, str]:
    code = getattr(exc, "code", None)
    if code is None:
        code = getattr(exc, "status_code", None)
    try:
        code_int = int(code) if code is not None else None
    except (TypeError, ValueError):
        code_int = None
    message = str(getattr(exc, "message", None) or exc)
    return code_int, message


def _is_retryable(exc: BaseException) -> bool:
    """True for HTTP 429 / RESOURCE_EXHAUSTED and transient transport failures."""
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    code, message = _exception_status(exc)
    if code in {408, 409, 425, 429, 500, 502, 503, 504}:
        return True
    blob = f"{getattr(exc, 'status', '')} {message}".upper()
    markers = (
        "429",
        "RESOURCE_EXHAUSTED",
        "RATE_LIMIT",
        "RATE LIMIT",
        "TOO MANY REQUESTS",
        "UNAVAILABLE",
        "503",
        "HIGH DEMAND",
        "OVERLOADED",
        "DEADLINE EXCEEDED",
        "CONNECTION RESET",
        "CONNECTION ABORTED",
        "TEMPORARILY",
        "TRY AGAIN",
        "TIMEOUT",
    )
    return any(marker in blob for marker in markers)


def _retry_after_seconds(exc: BaseException, attempt: int) -> float:
    """Honor Retry-After when present; otherwise exponential backoff + jitter."""
    header = None
    for attr in ("retry_after", "retry_delay"):
        header = getattr(exc, attr, None)
        if header is not None:
            break
    response = getattr(exc, "response", None)
    if header is None and response is not None:
        headers = getattr(response, "headers", None) or {}
        header = headers.get("Retry-After") or headers.get("retry-after")
    if header is not None:
        try:
            return min(float(header), _BACKOFF_CAP_S)
        except (TypeError, ValueError):
            delay_attr = getattr(header, "seconds", None)
            if delay_attr is not None:
                try:
                    return min(float(delay_attr), _BACKOFF_CAP_S)
                except (TypeError, ValueError):
                    pass
    expo = min(_BACKOFF_BASE_S * (2 ** attempt), _BACKOFF_CAP_S)
    jitter = random.uniform(0.0, 0.35 * expo)
    return expo + jitter


def _discover_images(folder: Path) -> list[Path]:
    if not folder.is_dir():
        raise FileNotFoundError(f"OCR source folder does not exist: {folder}")
    found: list[Path] = []
    for path in sorted(folder.rglob("*"), key=lambda p: p.as_posix().lower()):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            found.append(path)
    return found


def _item_key(path: Path, root: Path) -> str:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return path.name
    return rel.as_posix() if len(rel.parts) > 1 else path.name


def _empty_dataset() -> OCRDataset:
    return {"last_updated": "", "total_items": 0, "items": {}}


def _coerce_items(raw: Any) -> dict[str, OCRItem]:
    if isinstance(raw, Mapping):
        items: dict[str, OCRItem] = {}
        for key, value in raw.items():
            if isinstance(value, Mapping):
                items[str(key)] = dict(value)  # type: ignore[misc]
        return items
    if isinstance(raw, list):
        items = {}
        for value in raw:
            if not isinstance(value, Mapping):
                continue
            file_path = str(value.get("file_path") or "")
            key = Path(file_path).name if file_path else f"item_{len(items) + 1}"
            items[key] = dict(value)  # type: ignore[misc]
        return items
    return {}


def _normalize_dataset(raw: Any) -> OCRDataset:
    if not isinstance(raw, Mapping):
        return _empty_dataset()
    items = _coerce_items(raw.get("items"))
    return {
        "last_updated": str(raw.get("last_updated") or ""),
        "total_items": int(raw.get("total_items") or len(items)),
        "items": items,
    }


def _is_cached(
    items: Mapping[str, OCRItem],
    *,
    key: str,
    path: Path,
    force_reprocess: bool,
) -> bool:
    if force_reprocess:
        return False
    resolved = str(path.resolve())
    candidates: Iterable[OCRItem] = []
    if key in items:
        candidates = [items[key]]
    else:
        candidates = (
            item
            for item in items.values()
            if str(item.get("file_path") or "") == resolved
        )
    for item in candidates:
        if str(item.get("status") or "") == "success":
            return True
    return False


def _read_image_payload(path: Path) -> tuple[bytes, str]:
    """Read image bytes; downscale oversized files so Gemini accepts the payload."""
    data = path.read_bytes()
    mime = _mime_for(path)
    if len(data) <= _MAX_IMAGE_BYTES:
        return data, mime
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(data)) as img:
            rgb = img.convert("RGB")
            rgb.thumbnail((3072, 3072))
            buf = BytesIO()
            rgb.save(buf, format="JPEG", quality=88, optimize=True)
            return buf.getvalue(), "image/jpeg"
    except Exception as exc:  # noqa: BLE001
        logger.warning("OCR | could not downscale %s (%s); sending original bytes", path.name, exc)
        return data, mime


def _load_vault(vault_path: Path) -> dict[str, Any]:
    if not vault_path.is_file():
        return {}
    try:
        with vault_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Corrupt OCR vault JSON at {vault_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"OCR vault root must be a JSON object: {vault_path}")
    return payload


def _atomic_write_json(vault_path: Path, payload: Mapping[str, Any]) -> None:
    """Write JSON atomically so a crash never leaves a half-written vault."""
    vault_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{vault_path.name}.",
        suffix=".tmp",
        dir=str(vault_path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, vault_path)
    except OSError:
        # Google Drive File Stream sometimes rejects replace(); fall back in-place.
        try:
            vault_path.write_text(encoded, encoding="utf-8")
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _persist_dataset(vault_path: Path, vault: dict[str, Any], dataset_key: str, dataset: OCRDataset) -> None:
    dataset["total_items"] = len(dataset["items"])
    dataset["last_updated"] = _utc_now_iso()
    vault[dataset_key] = dataset
    _atomic_write_json(vault_path, vault)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class OCREngine:
    """Gemini multimodal OCR with incremental vault persistence.

    Parameters
    ----------
    api_key:
        Optional override. Defaults to ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY``.
    model:
        Gemini model id. Defaults to ``gemini-2.5-flash``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = GEMINI_OCR_MODEL,
    ) -> None:
        self.model: str = model
        self._client = genai.Client(api_key=_resolve_api_key(api_key))

    def process_directory(
        self,
        target_folder: str,
        dataset_key: str = DATASET_KEY,
        vault_path: str | None = None,
        chunk_size: int = _MAX_IMAGES_PER_CALL,
        force_reprocess: bool = False,
        batch_size: int | None = None,
    ) -> dict:
        """OCR every image in ``target_folder`` in one Gemini call per chunk.

        ``chunk_size`` is the max number of images per API call (default 100).
        Already-successful items are skipped unless ``force_reprocess`` is true.
        """
        folder = Path(target_folder)
        dest = Path(vault_path) if vault_path else VAULT_PATH
        per_call = int(batch_size if batch_size is not None else chunk_size)
        if per_call < 1:
            raise ValueError("chunk_size must be >= 1")
        per_call = min(per_call, _MAX_IMAGES_PER_CALL)

        images = _discover_images(folder)
        vault = _load_vault(dest)
        dataset = _normalize_dataset(vault.get(dataset_key))
        items = dataset["items"]

        pending: list[tuple[str, Path]] = []
        skipped = 0
        for image in images:
            key = _item_key(image, folder)
            if _is_cached(items, key=key, path=image, force_reprocess=force_reprocess):
                skipped += 1
                continue
            pending.append((key, image))

        calls = (len(pending) + per_call - 1) // per_call if pending else 0
        logger.info(
            "OCR | %d image(s) | %d Gemini call(s) | cached=%d",
            len(pending),
            calls,
            skipped,
        )

        processed = 0
        failed = 0
        usage = GeminiUsage()
        if pending:
            processed, failed, usage = self._process_pending(
                pending=pending,
                items=items,
                vault=vault,
                dataset=dataset,
                dataset_key=dataset_key,
                vault_path=dest,
                chunk_size=per_call,
            )
        else:
            dataset["total_items"] = len(items)
            if images:
                _persist_dataset(dest, vault, dataset_key, dataset)

        if usage.input_tokens or usage.output_tokens or processed or failed:
            log_usage(f"OCR | ok={processed} fail={failed}", usage)

        summary: ProcessSummary = {
            "dataset_key": dataset_key,
            "vault_path": str(dest),
            "last_updated": dataset.get("last_updated") or _utc_now_iso(),
            "total_items": len(items),
            "discovered": len(images),
            "processed": processed,
            "skipped": skipped,
            "failed": failed,
        }
        return dict(summary)

    def _process_pending(
        self,
        *,
        pending: list[tuple[str, Path]],
        items: dict[str, OCRItem],
        vault: dict[str, Any],
        dataset: OCRDataset,
        dataset_key: str,
        vault_path: Path,
        chunk_size: int,
    ) -> tuple[int, int, GeminiUsage]:
        processed = 0
        failed = 0
        usage = GeminiUsage()
        for start in range(0, len(pending), chunk_size):
            chunk = pending[start : start + chunk_size]
            texts, chunk_usage = self._ocr_chunk(chunk)
            usage = usage + chunk_usage
            stamp = _utc_now_iso()
            for key, path in chunk:
                if key in texts:
                    items[key] = {
                        "file_path": str(path.resolve()),
                        "extracted_text": _clean_ocr_text(texts[key]),
                        "processed_at": stamp,
                        "status": "success",
                    }
                    processed += 1
                else:
                    items[key] = {
                        "file_path": str(path.resolve()),
                        "extracted_text": "",
                        "processed_at": stamp,
                        "status": "error",
                        "error": "missing from Gemini OCR payload",
                    }
                    failed += 1
            _persist_dataset(vault_path, vault, dataset_key, dataset)
        return processed, failed, usage

    def _ocr_chunk(self, pending: list[tuple[str, Path]]) -> tuple[dict[str, str], GeminiUsage]:
        keys = [key for key, _path in pending]
        contents: list[Any] = [
            OCR_USER_PROMPT + "\nKeys:\n" + "\n".join(keys),
        ]
        for key, path in pending:
            data, mime = _read_image_payload(path)
            contents.append(f"IMAGE_KEY={key}")
            contents.append(types.Part.from_bytes(data=data, mime_type=mime))

        config_kwargs: dict[str, Any] = {
            "system_instruction": OCR_SYSTEM_PROMPT,
            "temperature": 0.0,
            "max_output_tokens": 32768,
            "response_mime_type": "application/json",
        }
        thinking = getattr(types, "ThinkingConfig", None)
        if thinking is not None:
            config_kwargs["thinking_config"] = thinking(thinking_budget=0)
        afc = getattr(types, "AutomaticFunctionCallingConfig", None)
        if afc is not None:
            config_kwargs["automatic_function_calling"] = afc(disable=True)
        config = types.GenerateContentConfig(**config_kwargs)

        last_exc: BaseException | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                response = self._client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
                raw = _response_text(response)
                if not raw:
                    raise RuntimeError("Gemini returned an empty OCR payload")
                mapped = _map_ocr_texts(unwrap_json_object(raw), keys)
                return mapped, extract_usage(response)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not _is_retryable(exc) or attempt >= _MAX_RETRIES - 1:
                    break
                wait = _retry_after_seconds(exc, attempt)
                logger.warning("OCR | retry %d/%d (%.0fs)", attempt + 1, _MAX_RETRIES, wait)
                time.sleep(wait)
        assert last_exc is not None
        logger.error("OCR | Gemini call failed: %s", last_exc)
        return {key: "" for key, _path in pending}, GeminiUsage()


def _configure_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(message)s",
        )
    hush_gemini_sdk_logs()


def main(argv: list[str] | None = None) -> int:
    """CLI entry — defaults to the momma OCR source folder and ``ocr_momma_deploy``."""
    _configure_logging()
    parser = argparse.ArgumentParser(description="Gemini 2.5 Flash OCR → ocr_vault.json")
    parser.add_argument("--folder", default=str(TARGET_FOLDER), help="Image source directory")
    parser.add_argument("--dataset", default=DATASET_KEY, help="Vault dataset key")
    parser.add_argument("--vault", default=str(VAULT_PATH), help="ocr_vault.json path")
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=_MAX_IMAGES_PER_CALL,
        help="Max images per Gemini call (default 100, one call per folder)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--force", action="store_true", help="Re-OCR images already in the vault")
    args = parser.parse_args(argv)

    engine = OCREngine()
    summary = engine.process_directory(
        target_folder=args.folder,
        dataset_key=args.dataset,
        vault_path=args.vault,
        chunk_size=args.chunk_size,
        batch_size=args.batch_size,
        force_reprocess=args.force,
    )
    print(
        f"OCR complete | ok={summary['processed']} fail={summary['failed']} "
        f"cached={summary['skipped']} vault={summary['vault_path']}"
    )
    return 1 if int(summary["failed"]) else 0


if __name__ == "__main__":
    sys.exit(main())
