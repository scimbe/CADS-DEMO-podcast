# Known limitations

Stated plainly, per the project's honesty requirement — this is not an
exhaustive list of "todos", it is what is genuinely real vs. approximated
right now.

## 1. Fixture speech uses Piper TTS, not espeak-ng, and not a public-domain clip

The original plan called for `espeak-ng` (apt) to generate the three spoken
test fixtures. **This build sandbox has no passwordless `sudo`**, so
`apt-get install espeak-ng` fails with `dpkg: Permission denied` — this is a
real environment constraint, not a design choice.

Downloading a public-domain audio clip was rejected per the brief's own
guidance (licensing/attribution complexity, network flakiness at test time).

**What was actually done:** the three fixture tracks
(`tests/fixtures/raw/track{1,2,3}.wav`) are synthesized with **Piper**
(`en_US-amy-low` voice) — a real, local, open-source neural TTS engine that
was already a project dependency for the optional chapter-announcement
feature. This is not a mock: it is real synthesized speech, and
whisper.cpp's tiny.en model transcribes it **word-for-word correctly** (see
`tests/fixtures/expected/sample-output/transcript.srt`). `stinger.wav` is
still generated with ffmpeg's own `sine` (lavfi) tone source, exactly as the
brief calls out. Regenerate both with `scripts/generate_fixtures.sh`.

This is a substitution of *which installable open-source tool* produces the
fixture speech, not a substitution of real audio for fake — the acceptance
bar (real audio in, real ffmpeg edit, real whisper.cpp transcript, real
LLM-grounded chapters) is unaffected.

## 2. whisper.cpp model download — the documented fallback (not currently triggered)

`scripts/setup_whisper_cpp.sh` clones `ggml-org/whisper.cpp` (pinned tag
`b4938`), builds `whisper-cli` with cmake, and downloads
`ggml-tiny.en.bin` (~75MB) from `huggingface.co/ggerganov/whisper.cpp` via
the repo's own `models/download-ggml-model.sh`. In this build/verification
environment, both `github.com` and `huggingface.co` were reachable and this
completed successfully — real ASR is what actually ran and what is committed
as proof in `tests/fixtures/expected/sample-output/`.

If a future sandbox (e.g. a more locked-down CI runner) cannot reach one of
those hosts, `setup_whisper_cpp.sh` **fails loudly**: non-zero exit, stderr
names exactly what's missing. The pipeline (`transcribe.py` /
`pipeline.py`) will likewise refuse to run and raise `TranscribeError`
unless the caller passes the explicit, opt-in `--allow-mock-transcript`
flag — never on by default, never used in CI (`.github/workflows/ci.yml`
does not pass it). When used, the resulting transcript is unmistakably
prefixed `[MOCK TRANSCRIPT — whisper.cpp model unavailable, see
docs/LIMITATIONS.md]`, and every downstream `chapters.json` carries an
explicit `"generated_from_mock_transcript": true` field so nothing
downstream can mistake it for a real transcript. See
`test_acceptance.py::test_transcript_contains_expected_keywords_unless_mock`,
which explicitly skips (and reports why) rather than silently passing, when
a mock run is detected.

## 3. Chapter-timestamp validation tolerance

The LLM is told every `start_ms` it returns must be copied exactly from a
real transcript segment. In practice, chat-completion models sometimes
round or drift by a second or two even when instructed not to. `chapters.py`
snaps each returned timestamp to the *nearest real segment boundary* if it
is within `snap_tolerance_ms` (default 2000ms / 2s), and hard-rejects
(raises `ChapterValidationError`, pipeline exits non-zero) anything further
off — see `tests/test_chapters_validator.py` for both cases exercised
directly. `chapters.json` therefore only ever contains real, whisper.cpp-
measured timestamps, never an LLM-invented one, but the 2s tolerance means
a chapter boundary can land on the start of an adjacent short segment rather
than the exact one the model "meant". For this demo's ~7s-per-segment
fixtures that has not caused a wrong-segment snap in testing.

## 4. `whisper-cli`'s claimed multi-format input support

`whisper-cli -h` lists several accepted input formats, but the project's
own README states it needs 16-bit mono 16kHz WAV. This pipeline never
relies on the wider claim: `cut_mix.py` always produces an explicit
`episode_16k.wav` (16kHz mono PCM16, via `ffmpeg -ar 16000 -ac 1 -c:a
pcm_s16le`) before every transcription call.

## 5. CI needs `LLM_BASE_URL`/`LLM_MODEL`/`LLM_API_KEY` repo secrets (not yet configured)

`.github/workflows/ci.yml` reads the LLM endpoint from GitHub Actions
secrets, not from any committed value. **Those secrets have not been set on
this repo** — this was a deliberate choice, not an oversight: the
credential used for local verification (see below) is a shared,
budget-capped (5 USD / 7-day) key scoped to the whole demo-portfolio build
round, and wiring it into a public repo's CI (which reruns on every push,
and on same-repo PRs) risks silently burning that shared budget for other
demos in the round. Until a maintainer/operator provisions a dedicated CI
key and adds it as a repo secret, CI will fail at the "Run pipeline" step
(stage 3, chapters) with a clear `LlmConfigError: missing required
environment variable(s)` — loud and diagnosable, not a silent skip. Stages
1–2 (ffmpeg cut/mix, whisper.cpp transcription) do not need any secret and
will run and be verifiable in CI regardless.

Locally, the pipeline was verified end-to-end against the real shared key
at `/home/becke/dev-workspace-scratch/demo-portfolio-llm.env` (see this
repo's build report) — the committed `tests/fixtures/expected/sample-output/`
artifacts are its real output.

## 6. Scope: local pipeline only, no web/tunnel wrapper

Per issue #26 and the parent demo-portfolio issue #21, this pass is
deliberately a **local CLI pipeline**, not a `*.bunsenbrenner.org` web
service. `src/` is kept import-clean (stdlib-only orchestration, no
framework coupling) so a future web/ct-agent wrapper is straightforward,
but building one is out of scope here and was not attempted.

## 3. Wikipedia-topic podcast: grounding, number guard, and German audio

`podcast_producer.wiki_podcast` generates a short podcast **script** for a
Wikipedia topic. Its factual scope is deliberately narrow and honest:

- **Only the REST summary `extract` is used** as a fact source — not the full
  article, not the model's own knowledge. Disambiguation pages, missing
  pages (404), and pages without an extract are refused (`WikiSourceError`),
  never guessed around.
- **Number guard.** When the LLM rewrites the extract into dialogue, the
  result is accepted only if every numeric token in the source (years,
  populations, ordinals, …) still appears. If any is missing, the LLM output
  is discarded and a deterministic, close-to-verbatim fallback script is used
  instead (`generation: "verbatim-fallback"` in `provenance.json`). This
  catches silent figure drops but is intentionally conservative: it does not
  verify *non-numeric* facts, so the LLM path is still a reformulation of the
  (trusted) extract, not an independently fact-checked text. The fallback
  path stays word-for-word faithful.
- **German audio.** Audio synthesis (`--with-audio`) is optional and reuses
  the Piper path. The voice shipped by `scripts/setup_piper_voice.sh` is
  **English** (`en_US-amy-low`), so German text is mispronounced — for real
  German audio, point `PIPER_MODEL_PATH` at a German Piper voice (e.g.
  `de_DE-thorsten-low`). On this macOS build the `piper-tts` wheel's bundled
  espeak-ng data is missing, so Piper fails at runtime; `wiki_podcast`
  handles that gracefully — it reports `audio.produced: false` with the
  reason and still writes the script. The script + provenance are the
  primary, always-produced artifacts; audio is a best-effort extra.
