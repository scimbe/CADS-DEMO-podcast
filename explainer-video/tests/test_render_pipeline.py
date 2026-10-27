"""Offline-Ende-zu-Ende (Stub-TTS, ImageMagick + ffmpeg) und Escape-Test."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from evgen import render

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("magick") and shutil.which("ffprobe")), reason="ffmpeg/ImageMagick fehlen")


def test_percent_and_at_are_neutralised():
    assert render._safe("100% @datei") == "100%% @datei"
    assert render._safe("@/etc/passwd").startswith("/etc")


def test_stub_pipeline_end_to_end(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"text": "Das ist ein Selbsttest-Text, der lang genug ist fuer die Eingabepruefung.", "length": 30}))
    env = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin", "EVGEN_TTS_BACKEND": "stub", "EVGEN_STUB_SCRIPT": "1"}
    r = subprocess.run([str(ROOT / "run.sh"), str(cfg), str(tmp_path / "out")], capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    lines = r.stdout.strip().splitlines()
    assert all(l.split(" ", 1)[0] in ("PROGRESS", "RESULT") for l in lines) and lines[-1].startswith("RESULT ")
    pcts = [json.loads(l[9:])["pct"] for l in lines if l.startswith("PROGRESS ")]
    assert pcts == sorted(pcts) and pcts[-1] == 100
    out = tmp_path / "out"
    for f in ("video.mp4", "subtitles.vtt", "script.json", "poster.jpg", "meta.json"):
        assert (out / f).stat().st_size > 0, f
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,codec_type,width,height", "-of", "json", str(out / "video.mp4")],
                       capture_output=True, text=True)
    st = {s["codec_type"]: s for s in json.loads(p.stdout)["streams"]}
    assert st["video"]["codec_name"] == "h264" and st["video"]["width"] == 1920 and st["audio"]["codec_name"] == "aac"
    assert json.loads((out / "meta.json").read_text())["stub"] is True


def test_invalid_config_exit_2_and_error_line(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"text": "zu kurz"}))
    r = subprocess.run([str(ROOT / "run.sh"), str(cfg), str(tmp_path / "o")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and r.stdout.strip().splitlines()[-1].startswith('ERROR {"code": "text_too_short"')
