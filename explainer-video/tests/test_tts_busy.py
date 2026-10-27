"""TTS-Auslastung (503): Backoff, Concurrency-Grenze, zweiter Anlauf, freundliche Fehler ohne Rohmeldung."""
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from evgen import pipeline, tts
from evgen.errors import TtsError

RAW = "ERROR: synthesis failed (voice=primary): ERROR stream-start HTTP Error 503"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(pipeline, "TTS_PACE_S", 0.0)
    monkeypatch.setattr(pipeline, "BACKOFF_SCALE", 0.01)


class Fake:
    """Fake-TTS: die ersten `fail_first` Aufrufe liefern 503; zaehlt gleichzeitige Aufrufe."""
    name = "fake"

    def __init__(self, fail_first=0, delay=0.0):
        self.fail_first, self.delay, self.calls, self.active, self.max_active = fail_first, delay, 0, 0, 0
        self.lock = threading.Lock()

    def synth(self, text, voice, timeout):
        with self.lock:
            self.calls += 1
            n = self.calls
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay)
            if n <= self.fail_first:
                raise tts.classify(RAW)
            return b"RIFF" + b"\0" * 2000
        finally:
            with self.lock:
                self.active -= 1


def test_classify_busy_is_friendly_and_keeps_raw_in_detail():
    e = tts.classify(RAW)
    assert e.code == "tts_busy" and str(e) == tts.FRIENDLY_BUSY
    assert "503" not in str(e) and "ERROR" not in str(e) and "stream-start" in e.detail
    assert tts.classify("irgendwas anderes kaputt").code == "tts_failed"


@pytest.mark.parametrize("n", [1, 3, 6])
def test_retries_503_with_backoff_then_succeeds(n):
    f = Fake(fail_first=n)
    assert pipeline.synth_retry(f, "Hallo Welt.", time.monotonic() + 30).startswith(b"RIFF")
    assert f.calls == n + 1


def test_budget_exhausted_raises_busy():
    f = Fake(fail_first=10**6)
    t0 = time.monotonic()
    with pytest.raises(TtsError) as e:
        pipeline.synth_retry(f, "Hallo.", time.monotonic() + 0.05)
    assert e.value.code == "tts_busy" and f.calls >= 1 and time.monotonic() - t0 < 2


def test_backoff_grows_with_jitter(monkeypatch):
    monkeypatch.setattr(pipeline, "BACKOFF_SCALE", 1.0)
    d = [[pipeline.backoff_delay(a) for _ in range(50)] for a in range(5)]
    for a, base in enumerate((0.5, 1.0, 2.0, 4.0, 4.0)):
        assert all(0.74 * base <= x <= 1.26 * base for x in d[a])
    assert len(set(d[0])) > 1                                      # Jitter


def test_not_configured_is_not_retried():
    class NC(Fake):
        def synth(self, *a):
            self.calls += 1
            raise TtsError("x", "tts_not_configured")
    f = NC()
    with pytest.raises(TtsError):
        pipeline.synth_retry(f, "x", time.monotonic() + 30)
    assert f.calls == 1


def test_one_run_never_uses_more_than_two_tts_slots(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "TTS_SLOTS", threading.Semaphore(2))
    monkeypatch.setattr(pipeline.video, "duration", lambda p: 1.0)
    f = Fake(delay=0.05)
    jobs = lambda i: pipeline.synth_scene(f, f"Szene {i} spricht.", tmp_path, i)
    pipeline.run_scene_jobs(8, jobs, workers=6, deadline=time.monotonic() + 30)
    assert f.calls == 8 and f.max_active <= 2


def test_failed_scene_gets_a_second_attempt_at_the_end():
    calls = {"n": 0}

    def job(i):
        if i == 2:
            calls["n"] += 1
            if calls["n"] == 1:
                raise tts.classify(RAW)          # erster Anlauf scheitert (Budget aufgebraucht)
        return {"ok": i}
    res = pipeline.run_scene_jobs(4, job, workers=2, deadline=time.monotonic() + 30)
    assert sorted(res) == [0, 1, 2, 3] and calls["n"] == 2


def test_still_failing_ends_with_friendly_error_and_details():
    def job(i):
        if i == 1:
            raise tts.classify(RAW)
        return {"ok": i}
    with pytest.raises(TtsError) as e:
        pipeline.run_scene_jobs(3, job, workers=2, deadline=time.monotonic() + 30)
    assert str(e.value) == "Die Stimme ist gerade ausgelastet – bitte in einer Minute erneut versuchen."
    assert e.value.code == "tts_busy" and "503" in e.value.detail and "503" not in str(e.value)


tools = shutil.which("ffmpeg") and shutil.which("magick")


def _run(tmp_path, fail_first, scale="0.01", budget="30"):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"text": "Das ist ein Selbsttest-Text, der lang genug ist fuer die Eingabepruefung.", "length": 30}))
    env = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin", "EVGEN_TTS_BACKEND": "stub", "EVGEN_STUB_SCRIPT": "1",
           "EVGEN_STUB_FAIL_FIRST": str(fail_first), "EVGEN_TTS_BACKOFF_SCALE": scale, "EVGEN_TTS_SCENE_BUDGET": budget}
    return subprocess.run([str(ROOT / "run.sh"), str(cfg), str(tmp_path / "out")], capture_output=True, text=True, env=env, timeout=300)


@pytest.mark.skipif(not tools, reason="ffmpeg/ImageMagick fehlen")
def test_end_to_end_survives_early_503s(tmp_path):
    r = _run(tmp_path, fail_first=5)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "out" / "video.mp4").stat().st_size > 1000


@pytest.mark.skipif(not tools, reason="ffmpeg/ImageMagick fehlen")
def test_end_to_end_permanent_busy_gives_friendly_error_without_raw_text(tmp_path):
    r = _run(tmp_path, fail_first=10**6, budget="0.05")
    assert r.returncode == 4
    err = json.loads(r.stdout.strip().splitlines()[-1].split(" ", 1)[1])
    assert err["code"] == "tts_busy" and err["message"] == tts.FRIENDLY_BUSY
    assert "503" in err["details"] and "503" not in err["message"] and "ERROR" not in err["message"]
