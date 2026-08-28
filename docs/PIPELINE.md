# Pipeline stage contracts

Four stages, each a real tool doing real work; Python only shells out and
moves JSON between them (`src/podcast_producer/pipeline.py` orchestrates).

```
tracks (WAV files)
   │
   ▼
[1] cut_mix.py  (ffmpeg: trim, concat, optional amix)
   │  episode_master.wav, episode.mp3, episode_16k.wav
   ▼
[2] transcribe.py  (whisper.cpp whisper-cli)
   │  transcript.json (segments), transcript.srt
   ▼
[3] chapters.py  (LLM call + code-level timestamp validator)
   │  chapters.json
   ▼
[4] announce.py  (optional: Piper TTS)
      announcements/chapterN.wav
```

## 1. `cut_mix.py`

**Input:** an ordered list of track specs, each `path.wav` or
`path.wav:trim_start_s:trim_end_s` (either bound optional), plus an optional
`--mix-bed path.wav` background track.

**What it does, concretely:**
- Normalizes every input to 44.1kHz stereo PCM16 WAV, applying `-ss/-to`
  trims during the same ffmpeg decode (exact for PCM WAV; this is the
  "cutting" step).
- Concatenates the normalized parts with ffmpeg's `concat` demuxer (lossless
  join of same-format WAVs — the tracks + stinger transitions become one
  episode).
- If `--mix-bed` is given: loops the bed track under the full episode
  duration, attenuates it (`volume=<bed_volume>`, default 0.15), and mixes
  it with the episode via `amix` (`duration=first`) — this is the actual
  "mixing" operation, distinct from concatenation.
- Encodes `episode.mp3` (libmp3lame) and `episode_16k.wav` (16kHz mono
  PCM16 — whisper.cpp's required ASR input format, converted explicitly
  every time, never assumed).

**Output:** `episode_master.wav`, `episode.mp3`, `episode_16k.wav`,
`cut_mix_manifest.json` (per-track durations, whether a bed was mixed).

## 2. `transcribe.py`

**Input:** `episode_16k.wav`, `WHISPER_CLI_PATH`, `WHISPER_MODEL_PATH`.

Runs `whisper-cli -m $MODEL -f episode_16k.wav -l en -oj -osrt -of
out/transcript`. Parses `transcript.json`'s `transcription` array — each
entry's `offsets.from`/`offsets.to` (milliseconds) and `text` — into a plain
list of `{start_ms, end_ms, text}` dicts for `chapters.py`.

**Output:** `transcript.json` (whisper.cpp's native format), `transcript.srt`
(human-readable), and the parsed segment list (in-process, also embedded in
`pipeline_summary.json`).

**Failure mode:** raises `TranscribeError` (non-zero exit) if the binary or
model is missing, unless `--allow-mock-transcript` was explicitly passed —
see `docs/LIMITATIONS.md` §2.

## 3. `chapters.py`

**Input:** the segment list from stage 2.

Sends the full segment list (with real `start_ms`/`end_ms`/`text`) to the
LLM (`LLM_BASE_URL`/`LLM_MODEL`/`LLM_API_KEY`) with a system prompt that
hard-constrains it to: only group/summarize existing segment text, and only
use `start_ms` values copied from the given segments. Requests
`response_format: {"type": "json_object"}`; if the endpoint rejects that
parameter, retries once without it. If the response doesn't parse as the
expected `{"chapters": [...]}` shape, retries once more with the parse error
echoed back to the model.

**Code-level validator (not just prompt instruction):** every returned
chapter's `start_ms` is snapped to the nearest real segment boundary if
within `snap_tolerance_ms` (default 2000ms); anything further off raises
`ChapterValidationError` — the pipeline fails loudly rather than keeping a
hallucinated timestamp. Titles are trimmed to ≤60 chars; non-increasing or
duplicate-boundary chapters are dropped; the first chapter is required to
start at the episode's actual first segment boundary.

**Output:** `chapters.json` = `{"chapters": [{"index", "start_ms",
"start_time", "title"}, ...], "generated_from_mock_transcript": bool}`.

(Note: this is an object wrapping the chapter array, not a bare array, so the
mock-transcript flag can travel with it — see `docs/LIMITATIONS.md` §2.)

## 4. `announce.py` (optional, `--with-announcements`)

**Input:** the chapter list, `PIPER_BIN`, `PIPER_MODEL_PATH`.

For each chapter, synthesizes `"Chapter N: <title>"` with Piper into
`announcements/chapterN.wav`. Never required for the acceptance bar; raises
`AnnounceError` (not silently skipped) if invoked without Piper available.

## 5. `pipeline.py`

Orchestrates 1→2→3(→4), writes every stage's output under `--out-dir`, and
additionally writes `pipeline_summary.json` (everything in one file) and
`chapters.json` at the top level of `--out-dir`.
