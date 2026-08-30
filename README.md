# CADS-DEMO-podcast — Podcast/Audio Producer

A local pipeline that orchestrates **real, existing open-source audio
tools** to go from raw audio tracks to a produced episode, a real
transcript, and LLM-generated chapter markers:

- **[ffmpeg](https://ffmpeg.org/)** — cuts (trims), concatenates, and mixes
  multiple audio tracks into an episode.
- **[whisper.cpp](https://github.com/ggml-org/whisper.cpp)** — transcribes
  the episode locally (no cloud ASR).
- An LLM (shared demo-portfolio endpoint) — reads the **real transcript
  text and timing** and proposes chapter markers/titles. It never invents
  audio content, and every timestamp it returns is validated in code
  against the real transcript before being trusted (see
  [`docs/PIPELINE.md`](docs/PIPELINE.md)).
- **[Piper](https://github.com/rhasspy/piper)** (optional) — local TTS for
  spoken chapter-title announcements.

Tracking issue: [CADS-agent-marketplace#26](https://github.com/scimbe/CADS-agent-marketplace/issues/26).

Scope note: this is a **local CLI pipeline**, not a `*.bunsenbrenner.org`
web/tunnel service — that wrapper is explicitly out of scope for this pass
(see `docs/LIMITATIONS.md` §5).

## What's real vs. what's a documented limitation

Everything in the pipeline is real: real ffmpeg audio processing, a real
whisper.cpp build transcribing real synthesized speech (word-for-word
correct on the committed fixtures), and a real call to the project's LLM
endpoint producing chapter markers that are validated against the actual
transcript timeline. The one deviation from the original plan — using
**Piper** instead of `espeak-ng` to synthesize the three spoken test
fixtures, because this build sandbox has no passwordless root for `apt-get`
— is documented plainly in [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) §1.
`docs/LIMITATIONS.md` also documents the (currently untriggered, but real
and tested) fallback behavior if whisper.cpp's model download is ever
unavailable in a more locked-down environment — it fails loudly by default,
and only produces a clearly-labelled mock transcript with an explicit
opt-in flag, never silently.

## Proof artifacts (committed, no need to re-run anything to inspect)

A short (~22s) real episode produced by this exact pipeline is committed at
[`tests/fixtures/expected/sample-output/`](tests/fixtures/expected/sample-output/):

- [`episode.mp3`](tests/fixtures/expected/sample-output/episode.mp3) — the
  real, ffmpeg-produced, cut-and-concatenated episode audio.
- [`transcript.srt`](tests/fixtures/expected/sample-output/transcript.srt) —
  the real whisper.cpp transcript.
- [`chapters.json`](tests/fixtures/expected/sample-output/chapters.json) —
  the real, validator-checked LLM chapter output.

The raw input fixtures that produced them are at
[`tests/fixtures/raw/`](tests/fixtures/raw/) (three short Piper-synthesized
spoken tracks + one ffmpeg tone-generated stinger).

## How to run it

### 1. Install dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # openai, pytest, piper-tts

bash scripts/setup_whisper_cpp.sh   # clone+build whisper.cpp (pinned tag b4938),
                                     # download ggml-tiny.en.bin (~75MB)
bash scripts/setup_piper_voice.sh   # download the en_US-amy-low Piper voice
```

`ffmpeg`/`ffprobe` must already be on `PATH` (this repo assumes they are —
they're a near-universal system package, unlike `espeak-ng` which turned
out to need root in this sandbox; see `docs/LIMITATIONS.md`).

### 2. Configure the LLM endpoint

```bash
cp config/pipeline.env.example .env
# edit .env: fill in LLM_API_KEY (never commit this file — it's gitignored)
```

### 3. Generate the test fixtures (or use the committed ones)

```bash
bash scripts/generate_fixtures.sh
# -> tests/fixtures/raw/{track1,track2,track3,stinger}.wav
```

### 4. Run the pipeline

```bash
set -a; source .env; set +a
export PYTHONPATH=src

python -m podcast_producer.pipeline \
  --tracks tests/fixtures/raw/track1.wav tests/fixtures/raw/stinger.wav \
           tests/fixtures/raw/track2.wav tests/fixtures/raw/stinger.wav \
           tests/fixtures/raw/track3.wav \
  --out-dir out/
```

Add `--with-announcements` to also synthesize a spoken chapter-title
announcement per chapter (optional/stretch, requires Piper — step 1).
Add `--mix-bed some.wav --bed-volume 0.15` to mix a background bed under
the episode. Add `:start:end` to any `--tracks` entry to trim it, e.g.
`track1.wav:0.5:6.0`.

`out/` will contain: `episode_master.wav`, `episode.mp3`, `episode_16k.wav`,
`cut_mix_manifest.json`, `transcript.json`, `transcript.srt`,
`chapters.json`, `pipeline_summary.json`, and (with `--with-announcements`)
`announcements/chapterN.wav`.

### 5. Run the tests

```bash
.venv/bin/pytest tests/ -v
```

`tests/test_chapters_validator.py` needs no external tools (pure unit
tests of the timestamp-snapping validator). `tests/test_acceptance.py`
checks the real `out/` produced in step 4 — see
[`docs/PIPELINE.md`](docs/PIPELINE.md) for exactly what each check proves.

## Wikipedia-topic podcast (grounded to de.wikipedia.org)

Besides producing an episode from raw audio tracks, this repo can generate a
**short podcast script for a Wikipedia topic**, grounded strictly on the
German Wikipedia so nothing is invented:

```bash
set -a; source .env; set +a        # or the wrapper's LITELLM_* vars
export PYTHONPATH=src

python -m podcast_producer.wiki_podcast "Kiel" --out-dir out-wiki/
```

What it does, stage by stage:

1. **Fetch** `GET https://de.wikipedia.org/api/rest_v1/page/summary/<Titel>`
   (`wiki_source.py`, stdlib only) and use its `extract` as the *only*
   factual source. If the page is a **disambiguation** page, is missing
   (404), or has no extract, it **aborts loudly** — it never guesses.
2. **Script** — form a short Host/Gast dialogue from the extract
   (`wiki_script.py`). By default an LLM rewrites it into natural speech,
   but its output is only kept if it passes a **number guard**: every
   numeric token in the source (years, populations, ordinals, …) must still
   appear in the script, otherwise the LLM output is discarded and a
   deterministic, close-to-verbatim fallback script is used. Pass
   `--no-llm` to force the verbatim fallback.
3. **Audio** (optional, `--with-audio`) — synthesize speech per turn with
   Piper and concatenate into `episode.mp3`, exactly like the main
   producer's audio path. Best-effort: if Piper/voice is unavailable it is
   skipped with a clear note (the script still stands). For German audio,
   point `PIPER_MODEL_PATH` at a German voice (see `docs/LIMITATIONS.md`).

Output in `--out-dir`: `wiki_source.json` (the raw, verbatim extract +
provenance), `script.json` / `script.txt` (the dialogue), `provenance.json`
(source URL, `CC BY-SA 4.0` license, generation mode, number-guard result),
`wiki_podcast_summary.json`, and — with `--with-audio` — `episode.mp3` plus
`turns/turnNN.wav`.

The LLM endpoint is read from `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY`,
or the OpenAI-compatible `LITELLM_BASE_URL` / `LITELLM_DEFAULT_MODEL` /
`LITELLM_API_KEY` as a fallback (the wrapper exports the latter).

## Repo layout

```
src/podcast_producer/
  cut_mix.py       ffmpeg wrapper: trim, concat, mix
  transcribe.py     whisper-cli wrapper + JSON parsing (+ documented mock fallback)
  chapters.py         LLM call + code-level timestamp-snap validator
  announce.py           optional: Piper chapter-announcement synthesis
  llm_client.py            thin OpenAI-SDK-compatible client (env-configured)
  pipeline.py                CLI entrypoint, orchestrates all stages
  wiki_source.py               Wikipedia REST fetch + validation (grounding)
  wiki_script.py                extract -> Host/Gast script, number-guarded
  wiki_podcast.py                 CLI entrypoint for the Wikipedia-topic podcast
scripts/            setup_whisper_cpp.sh, setup_piper_voice.sh, generate_fixtures.sh
config/pipeline.env.example
tests/              test_acceptance.py, test_chapters_validator.py, fixtures/
docs/               LIMITATIONS.md, PIPELINE.md
```

See [`docs/PIPELINE.md`](docs/PIPELINE.md) for the exact stage-by-stage I/O
contract and [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) for every known
gap or deviation from the original plan, stated plainly.
