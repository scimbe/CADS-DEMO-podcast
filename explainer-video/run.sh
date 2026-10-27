#!/usr/bin/env bash
# explainer-video: Erklaervideo aus dem eigenen Text (LLM-Szenen -> Thomas-Stimme -> Slides -> MP4).
#   ./run.sh config.json [out_dir]      (config.json oder "-" fuer stdin; out_dir Standard: ./out)
# stdout: Zeilen "PROGRESS {json}", am Ende "RESULT {json}" oder "ERROR {json}". Diagnose auf stderr.
# Exit: 0 ok, 2 Eingabe, 3 Skript, 4 TTS, 5 Render/Encode, 6 Zeitlimit, 1 intern. Vertrag: API.md
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PODCAST_PYTHON_BIN:-python3}"
command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null && command -v magick >/dev/null || command -v convert >/dev/null \
  || { echo 'ERROR {"code":"tools_missing","message":"ffmpeg/ffprobe/ImageMagick fehlen"}'; exit 5; }
CFG="${1:-}"; [ -n "$CFG" ] || { echo "usage: $0 config.json [out_dir]" >&2; exit 2; }
exec env PYTHONPATH="$HERE" "$PY" -X utf8 -m evgen "$CFG" "${2:-out}"
