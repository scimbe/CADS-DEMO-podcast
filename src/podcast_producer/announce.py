"""Stage 4 (optional/stretch): generate a spoken chapter-title announcement
per chapter with Piper (local TTS), and splice them before each chapter's
audio into episode_with_announcements.mp3.

Never required for the acceptance bar; only runs with --with-announcements.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import ffmpeg_util as ff

# Per-item TTS is I/O-/subprocess-bound (each call shells out to Piper), and
# each item is independent, so a small bounded thread pool overlaps the waits.
# Kept modest on purpose; override with PODCAST_TTS_WORKERS if needed.
TTS_MAX_WORKERS = max(1, int(os.environ.get("PODCAST_TTS_WORKERS", "4")))


class AnnounceError(RuntimeError):
    pass


def _default_piper_bin() -> str | None:
    return os.environ.get("PIPER_BIN") or shutil.which("piper")


def _default_voice_model() -> str | None:
    return os.environ.get("PIPER_MODEL_PATH")


def synth_announcement(text: str, out_wav: Path, *, piper_bin: str, model_path: str) -> None:
    args = [piper_bin, "--model", model_path, "--output_file", str(out_wav)]
    print(f"+ echo {text!r} | {' '.join(args)}", file=sys.stderr)
    result = subprocess.run(args, input=text, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise AnnounceError(f"piper exited {result.returncode}\n{result.stderr}")


def generate_announcements(
    chapters: list[dict],
    out_dir: Path,
    *,
    piper_bin: str | None = None,
    model_path: str | None = None,
) -> list[dict]:
    piper_bin = piper_bin or _default_piper_bin()
    model_path = model_path or _default_voice_model()
    missing = []
    if not piper_bin:
        missing.append("piper binary not found (pip install piper-tts, or set PIPER_BIN)")
    if not model_path or not Path(model_path).exists():
        missing.append(f"piper voice model not found ({model_path!r}); "
                        f"run scripts/setup_piper_voice.sh or set PIPER_MODEL_PATH")
    if missing:
        raise AnnounceError("Piper TTS unavailable:\n  - " + "\n  - ".join(missing))

    out_dir.mkdir(parents=True, exist_ok=True)

    def _synth_one(c: dict) -> dict:
        wav_path = out_dir / f"chapter{c['index']}.wav"
        text = f"Chapter {c['index']}: {c['title']}"
        synth_announcement(text, wav_path, piper_bin=piper_bin, model_path=model_path)
        return {
            "index": c["index"],
            "text": text,
            "wav": str(wav_path),
            "duration_s": round(ff.probe_duration_seconds(wav_path), 3),
        }

    if not chapters:
        return []
    # Warm the ffprobe lookup once so concurrent workers don't race on it.
    ff.ffprobe_bin()
    workers = min(TTS_MAX_WORKERS, len(chapters))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # executor.map preserves input order, so results stay chapter-ordered.
        return list(pool.map(_synth_one, chapters))


def _cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chapters-json", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--piper-bin", default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    data = json.loads(args.chapters_json.read_text())
    chapters = data["chapters"] if isinstance(data, dict) else data
    results = generate_announcements(chapters, args.out_dir,
                                      piper_bin=args.piper_bin, model_path=args.model)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    _cli()
