# -*- coding: utf-8 -*-
"""Light paraphrase pass over an OCR vault.

Reads ``ocr_vault.json``, strips configured footer/source keywords, rewrites
each quote while changing at most ~20% of the words, and writes
``ocr_vault_original.json`` next to the source file.

Typical usage::

    python utils/ocr_paraphrase.py
    python utils/ocr_paraphrase.py --vault path/to/ocr_vault.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Keywords removed from every quote before / after paraphrase
# ---------------------------------------------------------------------------

REMOVE_KEYWORDS: tuple[str, ...] = (
    "the mom notes",
    "mom notes",
    "themomnotes",
    "@themomnotes",
    "#momnotes",
    "#themomnotes",
)

MAX_WORD_CHANGE_RATIO: float = 0.20
GEMINI_MODEL: str = "gemini-2.5-flash"

FACTORY_ROOT: Path = Path(
    r"G:\My Drive\Z sosFiles\Z_act\@ NETWORK"
    r"\@MEDIAUPSCALE_FACTORY_DYNAMIC_CONTENT"
    r"\Unified Multi-Page Factory"
)
DEFAULT_VAULT: Path = FACTORY_ROOT / "assets" / "ocr_vault.json"
DEFAULT_OUTPUT_NAME: str = "ocr_vault_original.json"

_ENGINE_ROOT: Path = Path(__file__).resolve().parents[1]
_FENCE_RE = re.compile(
    r"^```(?:[a-zA-Z0-9_-]+)?\r?\n(.*)\r?\n```\s*$",
    re.DOTALL,
)

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _load_env() -> None:
    env_path = _ENGINE_ROOT / ".env"
    if env_path.is_file():
        load_dotenv(dotenv_path=env_path, override=False, encoding="utf-8-sig")
    load_dotenv(override=False)


def _api_key() -> str:
    _load_env()
    key = (
        (os.getenv("GEMINI_API_KEY") or "").strip()
        or (os.getenv("GOOGLE_API_KEY") or "").strip()
    )
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set in the environment or .env")
    return key


def strip_keywords(text: str, keywords: tuple[str, ...] = REMOVE_KEYWORDS) -> str:
    """Remove configured source labels without disturbing the quote body."""
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    ordered = sorted((kw for kw in keywords if kw.strip()), key=len, reverse=True)
    for keyword in ordered:
        cleaned = re.sub(re.escape(keyword), "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _word_list(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", text.lower())


def word_change_ratio(original: str, rewritten: str) -> float:
    """Fraction of original words that do not appear in the rewrite (bag-of-words)."""
    source = _word_list(original)
    if not source:
        return 0.0
    dest = _word_list(rewritten)
    leftover = list(dest)
    missing = 0
    for word in source:
        if word in leftover:
            leftover.remove(word)
        else:
            missing += 1
    return missing / len(source)


def _extract_json_object(raw: str) -> str:
    text = (raw or "").strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    raise json.JSONDecodeError("No JSON object in model response", text, 0)


def _unwrap_json(raw: str) -> Any:
    return json.loads(_extract_json_object(raw))


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


def _paraphrase_chunk(client: Any, types: Any, items: Mapping[str, str]) -> dict[str, str]:
    instruction = (
        "You lightly paraphrase short notebook quotes for a parenting social channel.\n"
        f"For each value, change at most {int(MAX_WORD_CHANGE_RATIO * 100)}% of the words.\n"
        "Keep the same meaning, emotional tone, and line breaks.\n"
        "Do not add hashtags, titles, or source credits.\n"
        "Return ONLY a JSON object with the same keys and rewritten string values."
    )
    config_kwargs: dict[str, Any] = {
        "system_instruction": instruction,
        "temperature": 0.4,
        "max_output_tokens": 4096,
        "response_mime_type": "application/json",
    }
    thinking = getattr(types, "ThinkingConfig", None)
    if thinking is not None:
        config_kwargs["thinking_config"] = thinking(thinking_budget=0)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=json.dumps(items, ensure_ascii=False),
        config=types.GenerateContentConfig(**config_kwargs),
    )
    text = _response_text(response)
    if not text:
        raise RuntimeError("Gemini returned an empty paraphrase payload")
    parsed = _unwrap_json(text)
    if not isinstance(parsed, dict):
        raise RuntimeError("Gemini paraphrase payload must be a JSON object")
    return {str(key): str(value) for key, value in parsed.items()}


def _paraphrase_batch(items: Mapping[str, str], chunk_size: int = 6) -> dict[str, str]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=_api_key())
    keys = list(items.keys())
    merged: dict[str, str] = {}
    for start in range(0, len(keys), chunk_size):
        chunk_keys = keys[start : start + chunk_size]
        chunk = {key: items[key] for key in chunk_keys}
        merged.update(_paraphrase_chunk(client, types, chunk))
    return merged


def paraphrase_vault(
    vault_path: str | Path,
    output_path: str | Path | None = None,
    dataset_key: str | None = None,
) -> Path:
    """Rewrite every extracted_text in *vault_path* and write ``ocr_vault_original.json``."""
    source = Path(vault_path)
    if not source.is_file():
        raise FileNotFoundError(f"OCR vault not found: {source}")
    dest = Path(output_path) if output_path else source.with_name(DEFAULT_OUTPUT_NAME)

    vault = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(vault, dict):
        raise RuntimeError(f"Vault root must be a JSON object: {source}")

    keys = [dataset_key] if dataset_key else [key for key, value in vault.items() if isinstance(value, dict)]
    quotes: dict[str, str] = {}
    locations: list[tuple[str, str]] = []
    for key in keys:
        dataset = vault.get(key)
        if not isinstance(dataset, dict):
            continue
        items = dataset.get("items")
        if not isinstance(items, dict):
            continue
        for item_key, item in items.items():
            if not isinstance(item, Mapping):
                continue
            raw = str(item.get("extracted_text") or "").strip()
            if not raw:
                continue
            quotes[f"{key}::{item_key}"] = strip_keywords(raw)
            locations.append((key, str(item_key)))

    rewritten = dict(quotes)
    if quotes:
        try:
            batch = _paraphrase_batch(quotes)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OCR paraphrase | Gemini batch failed (%s); keeping keyword-stripped text", exc)
            batch = {}
        for composite, cleaned in quotes.items():
            candidate = strip_keywords(str(batch.get(composite) or cleaned))
            if not candidate:
                rewritten[composite] = cleaned
                continue
            ratio = word_change_ratio(cleaned, candidate)
            if ratio > MAX_WORD_CHANGE_RATIO + 0.05:
                logger.info(
                    "OCR paraphrase | revert %s (changed %.0f%% > 20%%)",
                    composite,
                    ratio * 100,
                )
                rewritten[composite] = cleaned
            else:
                rewritten[composite] = candidate

    for dataset_name, item_key in locations:
        dataset = vault[dataset_name]
        items = dataset["items"]
        record = dict(items[item_key])
        composite = f"{dataset_name}::{item_key}"
        record["extracted_text"] = rewritten.get(composite, strip_keywords(str(record.get("extracted_text") or "")))
        record["paraphrased_at"] = _utc_now_iso()
        items[item_key] = record
        dataset["last_updated"] = _utc_now_iso()
        vault[dataset_name] = dataset

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(vault, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.info("OCR paraphrase | wrote %s (%d quote(s))", dest, len(locations))
    return dest


def _configure_logging() -> None:
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(description="Paraphrase OCR vault quotes (≤20% word change)")
    parser.add_argument("--vault", default=str(DEFAULT_VAULT), help="Source ocr_vault.json")
    parser.add_argument("--output", default=None, help="Destination JSON (default: ocr_vault_original.json)")
    parser.add_argument("--dataset", default=None, help="Optional single dataset key")
    args = parser.parse_args(argv)
    dest = paraphrase_vault(args.vault, output_path=args.output, dataset_key=args.dataset)
    print(f"OCR paraphrase wrote {dest}")
    return 0


if __name__ == "__main__":
    if str(_ENGINE_ROOT) not in sys.path:
        sys.path.insert(0, str(_ENGINE_ROOT))
    sys.exit(main())
