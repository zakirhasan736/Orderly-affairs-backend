"""Unit tests for hybrid local extract + quality gate."""

from pathlib import Path

from app.ai.local_document_extract import (
    _score_text_quality,
    extract_document_text,
    extraction_result_is_empty,
    local_text_min_chars,
)


def test_score_rejects_short_text():
    score, needs_vision, quality = _score_text_quality("hi", min_chars=80)
    assert needs_vision is True
    assert quality == "bad"
    assert score < 0.5


def test_score_accepts_readable_text():
    text = (
        "Auto insurance card for Jane Doe. Policy number ABC-12345. "
        "Vehicle: 2020 Honda Civic. Effective dates January to December."
    )
    score, needs_vision, quality = _score_text_quality(text, min_chars=40)
    assert needs_vision is False
    assert quality == "good"
    assert score >= 0.5


def test_extract_txt(tmp_path: Path):
    path = tmp_path / "note.txt"
    body = "Policy holder: Alex Casey\nPolicy number: POL-999\n" * 4
    path.write_text(body, encoding="utf-8")
    meta = extract_document_text(path, "text/plain")
    assert meta["method"] == "txt"
    assert "Alex Casey" in meta["text"]
    assert meta["needs_vision"] is False


def test_extraction_result_is_empty():
    assert extraction_result_is_empty(None) is True
    assert extraction_result_is_empty({"patch": {}}) is True
    assert extraction_result_is_empty({"patch": {"7A": [{"policy_number": ""}]}}) is True
    assert (
        extraction_result_is_empty(
            {"patch": {"7A": [{"policy_number": "ABC-1"}]}}
        )
        is False
    )


def test_min_chars_default():
    assert local_text_min_chars() >= 1
