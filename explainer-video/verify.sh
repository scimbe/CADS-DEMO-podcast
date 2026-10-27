#!/usr/bin/env bash
# Offline-Selbsttest ohne LLM/TTS (Stub): prueft Folien, Zusammenbau, Streams, Untertitel.
set -uo pipefail
D="$(mktemp -d)"; trap 'rm -rf "$D"' EXIT
echo '{"text":"Das ist ein Selbsttest-Text, der lang genug ist fuer die Eingabepruefung.","length":30}' > "$D/c.json"
EVGEN_TTS_BACKEND=stub EVGEN_STUB_SCRIPT=1 "$(dirname "$0")/run.sh" "$D/c.json" "$D/out" >/dev/null || { echo "FAIL: run.sh"; exit 1; }
for f in video.mp4 subtitles.vtt script.json poster.jpg meta.json; do [ -s "$D/out/$f" ] || { echo "FAIL: $f fehlt"; exit 1; }; done
ffprobe -v error -show_entries stream=codec_type -of csv=p=0 "$D/out/video.mp4" | sort | tr '\n' ' ' | grep -q "audio video" || { echo "FAIL: Streams"; exit 1; }
head -1 "$D/out/subtitles.vtt" | grep -q WEBVTT || { echo "FAIL: VTT"; exit 1; }
echo OK
