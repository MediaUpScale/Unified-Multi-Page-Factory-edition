from __future__ import annotations

from utils.ocr_paraphrase import strip_keywords, word_change_ratio
from utils.ocr_text import strip_wrapping_quotes


def test_strip_keywords_removes_mom_notes_footer() -> None:
    raw = (
        "I want them to carry this.\n"
        "was ever given.\n\n"
        "the mom notes"
    )
    cleaned = strip_keywords(raw)
    assert "mom notes" not in cleaned.lower()
    assert "I want them to carry this." in cleaned


def test_strip_wrapping_quotes_only_outer_marks() -> None:
    assert strip_wrapping_quotes("\u201cDon\u2019t spoil other people\u2019s joy.\u201e") == (
        "Don\u2019t spoil other people\u2019s joy."
    )
    assert strip_wrapping_quotes('"Be gentle with yourself."') == "Be gentle with yourself."
    assert strip_wrapping_quotes('She said "hello" today') == 'She said "hello" today'


def test_word_change_ratio_stays_low_for_light_edits() -> None:
    original = "you are exactly the mom that your child needs"
    rewritten = "you are truly the mom that your child needs"
    assert word_change_ratio(original, rewritten) <= 0.25
