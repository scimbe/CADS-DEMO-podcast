"""Unit tests for the bounded-concurrency per-item TTS loops.

No Piper and no ffmpeg: the synth call, the ffmpeg wrappers and the duration
probe are all monkeypatched. These tests pin the two properties that matter
when the serial loops became thread-pooled:

  * output order is preserved (results/concat stay item-ordered), and
  * the items actually run concurrently (bounded, but overlapping).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from podcast_producer import announce, cut_mix, wiki_podcast, wiki_script
from podcast_producer import ffmpeg_util as ff


class _ConcurrencyProbe:
    """Records how many synth calls are in flight at the same time."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def __call__(self, text, out_wav, *, piper_bin, model_path):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.05)  # hold the "subprocess" so overlap is observable
        Path(out_wav).write_bytes(b"RIFF0000WAVE")  # a real (tiny) file on disk
        with self.lock:
            self.active -= 1


def _patch_ffmpeg(monkeypatch):
    monkeypatch.setattr(ff, "ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(ff, "ffprobe_bin", lambda: "ffprobe")
    monkeypatch.setattr(ff, "probe_duration_seconds",
                        lambda p: 1.0, raising=True)
    monkeypatch.setattr(ff, "normalize_wav",
                        lambda src, dst, **k: Path(dst).write_bytes(b"n"), raising=True)
    monkeypatch.setattr(ff, "concat_wavs",
                        lambda parts, dst: Path(dst).write_bytes(b"c"), raising=True)
    monkeypatch.setattr(ff, "to_mp3",
                        lambda src, dst, **k: Path(dst).write_bytes(b"m"), raising=True)


# --------------------------------------------------------------------------
# announce.generate_announcements
# --------------------------------------------------------------------------

def test_announcements_preserve_chapter_order(tmp_path, monkeypatch):
    _patch_ffmpeg(monkeypatch)
    monkeypatch.setattr(announce, "_default_piper_bin", lambda: "piper")
    monkeypatch.setattr(announce, "_default_voice_model", lambda: __file__)
    monkeypatch.setattr(announce, "synth_announcement",
                        lambda text, out, **k: Path(out).write_bytes(b"w"))

    chapters = [{"index": i, "title": f"Kapitel {i}"} for i in range(1, 8)]
    results = announce.generate_announcements(chapters, tmp_path / "ann")
    assert [r["index"] for r in results] == [1, 2, 3, 4, 5, 6, 7]
    assert [r["text"] for r in results] == [f"Chapter {i}: Kapitel {i}" for i in range(1, 8)]


def test_announcements_run_concurrently(tmp_path, monkeypatch):
    _patch_ffmpeg(monkeypatch)
    probe = _ConcurrencyProbe()
    monkeypatch.setattr(announce, "_default_piper_bin", lambda: "piper")
    monkeypatch.setattr(announce, "_default_voice_model", lambda: __file__)
    monkeypatch.setattr(announce, "synth_announcement", probe)
    monkeypatch.setattr(announce, "TTS_MAX_WORKERS", 4)

    chapters = [{"index": i, "title": f"K{i}"} for i in range(1, 7)]
    announce.generate_announcements(chapters, tmp_path / "ann")
    assert probe.max_active >= 2, "expected overlapping synth calls"
    assert probe.max_active <= 4, "must respect the worker cap"


def test_announcements_empty_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(announce, "_default_piper_bin", lambda: "piper")
    monkeypatch.setattr(announce, "_default_voice_model", lambda: __file__)
    assert announce.generate_announcements([], tmp_path / "ann") == []


# --------------------------------------------------------------------------
# wiki_podcast._synthesize_audio
# --------------------------------------------------------------------------

def _script(n: int) -> wiki_script.PodcastScript:
    turns = [{"speaker": "Host" if i % 2 else "Gast", "text": f"Beitrag {i}"}
             for i in range(1, n + 1)]
    return wiki_script.PodcastScript(title="T", turns=turns, generation="llm")


def test_synthesize_audio_preserves_turn_order(tmp_path, monkeypatch):
    _patch_ffmpeg(monkeypatch)
    monkeypatch.setattr(announce, "_default_piper_bin", lambda: "piper")
    monkeypatch.setattr(announce, "_default_voice_model", lambda: __file__)
    monkeypatch.setattr(announce, "synth_announcement",
                        lambda text, out, **k: Path(out).write_bytes(b"w"))

    audio = wiki_podcast._synthesize_audio(_script(6), tmp_path)
    assert audio["produced"] is True
    assert [t["index"] for t in audio["turns"]] == [1, 2, 3, 4, 5, 6]
    assert [t["speaker"] for t in audio["turns"]] == \
        ["Host", "Gast", "Host", "Gast", "Host", "Gast"]


def test_synthesize_audio_concat_receives_ordered_parts(tmp_path, monkeypatch):
    _patch_ffmpeg(monkeypatch)
    seen: list[list[str]] = []
    monkeypatch.setattr(ff, "concat_wavs",
                        lambda parts, dst: (seen.append([Path(p).name for p in parts]),
                                            Path(dst).write_bytes(b"c")))
    monkeypatch.setattr(announce, "_default_piper_bin", lambda: "piper")
    monkeypatch.setattr(announce, "_default_voice_model", lambda: __file__)
    monkeypatch.setattr(announce, "synth_announcement", _ConcurrencyProbe())
    monkeypatch.setattr(announce, "TTS_MAX_WORKERS", 4)

    wiki_podcast._synthesize_audio(_script(5), tmp_path)
    # Despite concurrent synthesis, concat got the normalized parts in order.
    assert seen and seen[0] == [f"norm_{i:03d}.wav" for i in range(1, 6)]


def test_synthesize_audio_degrades_when_piper_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(announce, "_default_piper_bin", lambda: None)
    monkeypatch.setattr(announce, "_default_voice_model", lambda: None)
    audio = wiki_podcast._synthesize_audio(_script(3), tmp_path)
    assert audio["produced"] is False and "unavailable" in audio["reason"]
