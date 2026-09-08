from __future__ import annotations

from utils.ocr_paraphrase import strip_keywords, word_change_ratio


def test_strip_keywords_removes_mom_notes_footer() -> None:
    raw = (
        "I want them to carry this.\n"
        "was ever given.\n\n"
        "the mom notes"
    )
    cleaned = strip_keywords(raw)
    assert "mom notes" not in cleaned.lower()
    assert "I want them to carry this." in cleaned


def test_word_change_ratio_stays_low_for_light_edits() -> None:
    original = "you are exactly the mom that your child needs"
    rewritten = "you are truly the mom that your child needs"
    assert word_change_ratio(original, rewritten) <= 0.25
