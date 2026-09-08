from __future__ import annotations

from types import SimpleNamespace

from utils.gemini_usage import GeminiUsage, extract_usage, flash_cost_usd, format_usage_line
from utils.ocr_engine import _map_ocr_texts


def test_flash_cost_matches_list_price() -> None:
    # 1M in + 1M out → $0.075 + $0.30
    assert abs(flash_cost_usd(1_000_000, 1_000_000) - 0.375) < 1e-9


def test_extract_usage_reads_metadata() -> None:
    response = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=1200,
            candidates_token_count=80,
            thoughts_token_count=20,
        )
    )
    usage = extract_usage(response)
    assert usage.input_tokens == 1200
    assert usage.output_tokens == 100
    line = format_usage_line("OCR", usage)
    assert "in=1200" in line
    assert "out=100" in line
    assert "$" in line


def test_usage_add() -> None:
    total = GeminiUsage(10, 5) + GeminiUsage(3, 2)
    assert total.input_tokens == 13
    assert total.output_tokens == 7


def test_map_ocr_texts_matches_filename_fallback() -> None:
    payload = {"note.jpg": "hello mom"}
    mapped = _map_ocr_texts(payload, ["sub/note.jpg", "missing.jpg"])
    assert mapped["sub/note.jpg"] == "hello mom"
    assert "missing.jpg" not in mapped
