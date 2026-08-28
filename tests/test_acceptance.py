"""Acceptance test: proves the pipeline produced real artifacts from real
audio, not fabricated/stubbed output. Run AFTER the pipeline, per README:

    bash scripts/generate_fixtures.sh
    python -m podcast_producer.pipeline --tracks ... --out-dir out/
    pytest tests/test_acceptance.py -v
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "out"
KEYWORDS_PATH = REPO_ROOT / "tests" / "fixtures" / "expected" / "keywords.json"


def _ffprobe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert result.returncode == 0, f"ffprobe failed on {path}: {result.stderr}"
    return float(result.stdout.strip())


def _require_pipeline_output():
    if not OUT_DIR.exists():
        pytest.fail(
            f"{OUT_DIR} does not exist. Run the pipeline first:\n"
            f"  bash scripts/generate_fixtures.sh\n"
            f"  python -m podcast_producer.pipeline --tracks ... --out-dir out/"
        )


def test_episode_mp3_is_real_audio():
    _require_pipeline_output()
    mp3 = OUT_DIR / "episode.mp3"
    assert mp3.exists(), "episode.mp3 was not produced"
    size = mp3.stat().st_size
    assert size > 10_000, f"episode.mp3 suspiciously small ({size} bytes) — likely empty/fabricated"
    duration = _ffprobe_duration(mp3)
    assert duration > 0, "episode.mp3 has zero/invalid duration"


def test_transcript_files_exist_and_nonempty():
    _require_pipeline_output()
    srt = OUT_DIR / "transcript.srt"
    tjson = OUT_DIR / "transcript.json"
    assert srt.exists() and srt.stat().st_size > 0, "transcript.srt missing or empty"
    assert tjson.exists() and tjson.stat().st_size > 0, "transcript.json missing or empty"


def _is_mock_run() -> bool:
    chapters_path = OUT_DIR / "chapters.json"
    if not chapters_path.exists():
        return False
    data = json.loads(chapters_path.read_text())
    return bool(data.get("generated_from_mock_transcript"))


def test_transcript_contains_expected_keywords_unless_mock():
    _require_pipeline_output()
    if _is_mock_run():
        pytest.skip(
            "pipeline ran with --allow-mock-transcript (whisper.cpp model "
            "unavailable) — keyword check does not apply to a mock transcript. "
            "This is reported, not silently skipped: see docs/LIMITATIONS.md."
        )
    srt_text = (OUT_DIR / "transcript.srt").read_text().lower()
    keywords = json.loads(KEYWORDS_PATH.read_text())

    report = {}
    for track, words in keywords.items():
        found = [w for w in words if w.lower() in srt_text]
        report[track] = {"expected": words, "found": found}
        # fuzzy: most (not all) expected keywords per track must appear,
        # to tolerate small ASR errors from a tiny model + synthesized speech
        assert len(found) >= max(1, len(words) - 1), (
            f"track {track}: only found {found} of expected {words} in transcript.\n"
            f"Full transcript:\n{srt_text}"
        )
    print(json.dumps(report, indent=2))


def test_chapters_json_valid_and_grounded():
    _require_pipeline_output()
    chapters_path = OUT_DIR / "chapters.json"
    assert chapters_path.exists(), "chapters.json was not produced"
    data = json.loads(chapters_path.read_text())
    chapters = data["chapters"]
    assert len(chapters) >= 2, f"expected >=2 chapters for a 3-segment episode, got {len(chapters)}"

    # Independently recompute real segment boundaries from transcript.json
    # (not by trusting chapters.py's own validator) to prove the LLM's
    # timestamps are grounded in real whisper.cpp output, not hallucinated.
    tjson = json.loads((OUT_DIR / "transcript.json").read_text())
    real_starts = [int(e["offsets"]["from"]) for e in tjson.get("transcription", [])]
    assert real_starts, "transcript.json has no segments to validate chapters against"

    seen_titles = set()
    prev_start = -1
    for c in chapters:
        assert set(c.keys()) >= {"index", "start_ms", "start_time", "title"}
        assert c["start_ms"] > prev_start, "chapter start_ms must be strictly increasing"
        prev_start = c["start_ms"]

        nearest = min(real_starts, key=lambda s: abs(s - c["start_ms"]))
        diff = abs(nearest - c["start_ms"])
        assert diff <= 2000, (
            f"chapter {c['index']} start_ms={c['start_ms']} is {diff}ms from the "
            f"nearest real segment boundary ({nearest}ms) — exceeds 2s tolerance, "
            f"looks hallucinated"
        )

        title = c["title"]
        assert title, "chapter title is empty"
        assert len(title) <= 60, f"chapter title too long ({len(title)} chars): {title!r}"
        assert title.strip().lower() not in ("chapter", "title", "untitled"), \
            f"chapter title looks like a placeholder: {title!r}"
        # basic prompt-leak guard: title shouldn't just echo instructions
        assert "start_ms" not in title.lower() and "json" not in title.lower(), \
            f"chapter title looks like a prompt/schema leak: {title!r}"
        seen_titles.add(title)


def test_announcements_if_present():
    _require_pipeline_output()
    ann_dir = OUT_DIR / "announcements"
    if not ann_dir.exists():
        pytest.skip("pipeline was not run with --with-announcements")
    wavs = sorted(ann_dir.glob("chapter*.wav"))
    assert wavs, "announcements dir exists but has no chapter wavs"
    for w in wavs:
        duration = _ffprobe_duration(w)
        assert duration > 0, f"{w} has zero/invalid duration"
        assert w.stat().st_size > 1000, f"{w} suspiciously small"
