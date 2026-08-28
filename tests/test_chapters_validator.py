"""Unit tests for chapters.py's timestamp-snapping validator.

These tests need no external tools (no ffmpeg/whisper.cpp/LLM) — they call
the pure validation/parsing functions directly with hand-built inputs, and
they run in any environment, including CI without the whisper.cpp model.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from podcast_producer.chapters import (  # noqa: E402
    ChapterValidationError,
    _ms_to_hhmmss,
    _parse_llm_chapters,
    _snap_to_segment,
)


def test_ms_to_hhmmss():
    assert _ms_to_hhmmss(0) == "00:00:00"
    assert _ms_to_hhmmss(7880) == "00:00:07"
    assert _ms_to_hhmmss(3_661_000) == "01:01:01"


def test_snap_exact_match():
    segment_starts = [0, 7880, 15360]
    assert _snap_to_segment(7880, segment_starts, tolerance_ms=2000) == 7880


def test_snap_within_tolerance():
    segment_starts = [0, 7880, 15360]
    # off by 900ms, within the 2000ms tolerance -> snaps to the real boundary
    assert _snap_to_segment(8780, segment_starts, tolerance_ms=2000) == 7880


def test_snap_rejects_hallucinated_timestamp():
    segment_starts = [0, 7880, 15360]
    with pytest.raises(ChapterValidationError):
        _snap_to_segment(50000, segment_starts, tolerance_ms=2000)


def test_snap_rejects_just_outside_tolerance():
    segment_starts = [0, 7880, 15360]
    with pytest.raises(ChapterValidationError):
        _snap_to_segment(10200, segment_starts, tolerance_ms=2000)  # 2320ms away


def test_parse_llm_chapters_valid():
    raw = '{"chapters": [{"start_ms": 0, "title": "Intro"}]}'
    chapters = _parse_llm_chapters(raw)
    assert chapters == [{"start_ms": 0, "title": "Intro"}]


def test_parse_llm_chapters_missing_key():
    raw = '{"chapters": [{"start_ms": 0}]}'
    with pytest.raises(ValueError):
        _parse_llm_chapters(raw)


def test_parse_llm_chapters_empty_list():
    raw = '{"chapters": []}'
    with pytest.raises(ValueError):
        _parse_llm_chapters(raw)


def test_parse_llm_chapters_not_json():
    with pytest.raises(Exception):
        _parse_llm_chapters("not json at all")
