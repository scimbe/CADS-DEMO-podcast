#!/usr/bin/env bash
# Download the en_US-amy-low Piper voice (optional; only needed for
# --with-announcements or for regenerating speech fixtures).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VOICE_DIR="$REPO_ROOT/vendor/piper-voices"
BASE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/low"

mkdir -p "$VOICE_DIR"
cd "$VOICE_DIR"

for f in en_US-amy-low.onnx en_US-amy-low.onnx.json; do
  if [ -f "$f" ]; then
    echo "$f already present — skipping"
    continue
  fi
  echo "Downloading $f ..."
  if ! curl -sL --fail -o "$f" "$BASE_URL/$f"; then
    echo "FATAL: could not download $f from huggingface.co. Piper TTS" >&2
    echo "(optional stretch feature) will be unavailable; this does not" >&2
    echo "block the required pipeline (cut_mix -> transcribe -> chapters)." >&2
    rm -f "$f"
    exit 1
  fi
done

echo "Piper voice ready: $VOICE_DIR/en_US-amy-low.onnx"
