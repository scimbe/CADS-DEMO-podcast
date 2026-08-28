"""Stage 2: transcribe episode_16k.wav with whisper.cpp's whisper-cli.

Runs the real whisper-cli binary against the real ggml model. The only
sanctioned exception is `--allow-mock-transcript`, which is off by default,
never used in CI, and produces an unmistakably-labelled fallback transcript
(see docs/LIMITATIONS.md) rather than pretending ASR ran.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MOCK_PREFIX = "[MOCK TRANSCRIPT — whisper.cpp model unavailable, see docs/LIMITATIONS.md]"


class TranscribeError(RuntimeError):
    pass


def _default_cli_path() -> str | None:
    env = os.environ.get("WHISPER_CLI_PATH")
    if env:
        return env
    return shutil.which("whisper-cli")


def _default_model_path() -> str | None:
    return os.environ.get("WHISPER_MODEL_PATH")


def transcribe(
    wav_path: Path,
    out_dir: Path,
    *,
    cli_path: str | None = None,
    model_path: str | None = None,
    language: str = "en",
    allow_mock: bool = False,
) -> dict:
    """Run whisper-cli, parse its JSON output into a segment list.

    Returns {"segments": [{"start_ms", "end_ms", "text"}], "mock": bool,
    "srt_path": str, "json_path": str}.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cli_path = cli_path or _default_cli_path()
    model_path = model_path or _default_model_path()

    missing = []
    if not cli_path or not Path(cli_path).exists():
        missing.append(f"whisper-cli binary not found (looked at {cli_path!r}; "
                        f"run scripts/setup_whisper_cpp.sh or set WHISPER_CLI_PATH)")
    if not model_path or not Path(model_path).exists():
        missing.append(f"whisper.cpp ggml model not found (looked at {model_path!r}; "
                        f"run scripts/setup_whisper_cpp.sh or set WHISPER_MODEL_PATH)")

    if missing:
        if not allow_mock:
            raise TranscribeError(
                "Real transcription is unavailable:\n  - " + "\n  - ".join(missing) +
                "\nRefusing to silently fabricate a transcript. Pass "
                "--allow-mock-transcript to explicitly opt into a clearly-labelled "
                "mock transcript instead (never used in CI). See docs/LIMITATIONS.md."
            )
        return _mock_transcript(out_dir)

    prefix = out_dir / "transcript"
    args = [
        cli_path,
        "-m", model_path,
        "-f", str(wav_path),
        "-l", language,
        "-oj", "-osrt",
        "-of", str(prefix),
        "--no-prints",
    ]
    print(f"+ {' '.join(args)}", file=sys.stderr)
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise TranscribeError(
            f"whisper-cli exited {result.returncode}\n--- stderr ---\n{result.stderr}"
        )
    print(result.stderr, file=sys.stderr)

    json_path = prefix.with_suffix(".json")
    srt_path = prefix.with_suffix(".srt")
    data = json.loads(json_path.read_text())

    segments = []
    for entry in data.get("transcription", []):
        offsets = entry.get("offsets", {})
        segments.append(
            {
                "start_ms": int(offsets.get("from", 0)),
                "end_ms": int(offsets.get("to", 0)),
                "text": entry.get("text", "").strip(),
            }
        )

    return {
        "segments": segments,
        "mock": False,
        "srt_path": str(srt_path),
        "json_path": str(json_path),
    }


def _mock_transcript(out_dir: Path) -> dict:
    srt_path = out_dir / "transcript.srt"
    json_path = out_dir / "transcript.json"
    text = f"{MOCK_PREFIX} (no real speech content was transcribed)"
    segments = [{"start_ms": 0, "end_ms": 1000, "text": text}]
    srt_path.write_text(f"1\n00:00:00,000 --> 00:00:01,000\n{text}\n")
    json_path.write_text(json.dumps({
        "mock": True,
        "transcription": [
            {
                "timestamps": {"from": "00:00:00,000", "to": "00:00:01,000"},
                "offsets": {"from": 0, "to": 1000},
                "text": text,
            }
        ],
    }, indent=2))
    return {
        "segments": segments,
        "mock": True,
        "srt_path": str(srt_path),
        "json_path": str(json_path),
    }


def _cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wav", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--whisper-cli", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--allow-mock-transcript", action="store_true")
    args = parser.parse_args()

    result = transcribe(
        args.wav, args.out_dir,
        cli_path=args.whisper_cli, model_path=args.model,
        allow_mock=args.allow_mock_transcript,
    )
    print(json.dumps({"mock": result["mock"], "n_segments": len(result["segments"])}, indent=2))


if __name__ == "__main__":
    _cli()
