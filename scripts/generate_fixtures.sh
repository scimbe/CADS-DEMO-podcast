#!/usr/bin/env bash
# Generate the test fixture audio: three short spoken tracks (via Piper local
# TTS) plus one ffmpeg-tone-generated stinger, used as a real, reproducible,
# no-download-at-test-time alternative to a public-domain clip.
#
# Why Piper here instead of espeak-ng (the original plan's fixture-speech
# tool): this build sandbox has no passwordless root, so `apt-get install
# espeak-ng` is unavailable. Piper is already a project dependency (optional
# local TTS for chapter announcements, pip-installable, no root needed), so
# it does double duty as the fixture-speech synthesizer. See docs/LIMITATIONS.md.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW_DIR="$REPO_ROOT/tests/fixtures/raw"
mkdir -p "$RAW_DIR"

PIPER_BIN="${PIPER_BIN:-$REPO_ROOT/.venv/bin/piper}"
PIPER_MODEL_PATH="${PIPER_MODEL_PATH:-$REPO_ROOT/vendor/piper-voices/en_US-amy-low.onnx}"

if [ ! -x "$PIPER_BIN" ]; then
  echo "FATAL: piper binary not found/executable at $PIPER_BIN" >&2
  echo "Run: pip install -r requirements.txt" >&2
  exit 1
fi
if [ ! -f "$PIPER_MODEL_PATH" ]; then
  echo "FATAL: piper voice model not found at $PIPER_MODEL_PATH" >&2
  echo "Run: bash scripts/setup_piper_voice.sh" >&2
  exit 1
fi

synth() {
  local out="$1" text="$2"
  echo "Synthesizing $out ..."
  echo "$text" | "$PIPER_BIN" --model "$PIPER_MODEL_PATH" --output_file "$out"
}

synth "$RAW_DIR/track1.wav" \
  "Our platform uses a zero trust tunnel, so that no server ever exposes an open port to the internet."

synth "$RAW_DIR/track2.wav" \
  "The agent marketplace lets developers publish signed manifests that install new tools automatically."

synth "$RAW_DIR/track3.wav" \
  "Sort arena is a small demo that visualizes sorting algorithms racing against each other in real time."

echo "Generating tone stinger (ffmpeg lavfi sine source) ..."
ffmpeg -y -hide_banner -loglevel error \
  -f lavfi -i "sine=frequency=880:duration=1:sample_rate=44100" \
  -ac 2 -c:a pcm_s16le \
  "$RAW_DIR/stinger.wav"

echo ""
echo "Fixtures written to $RAW_DIR:"
ls -la "$RAW_DIR"
