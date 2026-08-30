"""CLI entrypoint: turn a Wikipedia topic into a short, grounded podcast.

    python -m podcast_producer.wiki_podcast "Kiel" --out-dir out/

Pipeline: fetch the German-Wikipedia summary (wiki_source) -> form a short
Host/Gast script from the verbatim extract, number-guarded (wiki_script) ->
write script + provenance -> (optionally) synthesize speech per turn with
Piper and concatenate into episode.mp3, exactly like the audio path of the
main producer.

The script + provenance are always produced. Audio is opt-in (--with-audio)
and best-effort: if Piper is unavailable it is skipped with a clear note,
never faked, and the script output still stands.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from . import announce, cut_mix
from . import ffmpeg_util as ff
from . import wiki_script, wiki_source


class WikiPodcastError(RuntimeError):
    pass


def _synthesize_audio(script: wiki_script.PodcastScript, out_dir: Path) -> dict:
    """Synthesize each turn with Piper and concat into an episode. Best-effort.

    Returns an audio manifest dict, or {"produced": False, "reason": ...} if
    Piper/voice is unavailable (never raises for a missing voice).
    """
    piper_bin = announce._default_piper_bin()
    model_path = announce._default_voice_model()
    if not piper_bin or not model_path or not Path(model_path).exists():
        return {
            "produced": False,
            "reason": "Piper TTS unavailable (set PIPER_BIN + PIPER_MODEL_PATH, "
                      "ideally to a German voice; see scripts/setup_piper_voice.sh).",
        }

    turns_dir = out_dir / "turns"
    turns_dir.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="wiki_podcast_") as tmp:
            tmp_dir = Path(tmp)
            normalized: list[Path] = []
            turn_manifest = []
            for i, turn in enumerate(script.turns, start=1):
                raw_wav = turns_dir / f"turn{i:02d}.wav"
                announce.synth_announcement(
                    turn["text"], raw_wav, piper_bin=piper_bin, model_path=model_path
                )
                norm = tmp_dir / f"norm_{i:03d}.wav"
                ff.normalize_wav(raw_wav, norm, sample_rate=cut_mix.MASTER_RATE,
                                 channels=cut_mix.MASTER_CHANNELS)
                normalized.append(norm)
                turn_manifest.append({
                    "index": i,
                    "speaker": turn["speaker"],
                    "wav": str(raw_wav),
                    "duration_s": round(ff.probe_duration_seconds(raw_wav), 3),
                })

            master = out_dir / "episode_master.wav"
            ff.concat_wavs(normalized, master)
            mp3 = out_dir / "episode.mp3"
            ff.to_mp3(master, mp3)
    except (announce.AnnounceError, ff.FfmpegError) as exc:
        # Audio is best-effort: a runtime TTS/ffmpeg failure must not lose the
        # already-produced script. Report it and carry on.
        return {
            "produced": False,
            "reason": f"audio synthesis failed at runtime: {exc}",
        }

    return {
        "produced": True,
        "voice_model": model_path,
        "master_wav": str(master),
        "episode_mp3": str(mp3),
        "duration_s": round(ff.probe_duration_seconds(master), 3),
        "turns": turn_manifest,
    }


def run(topic: str, out_dir: Path, *, lang: str = wiki_source.WIKI_LANG,
        use_llm: bool = True, with_audio: bool = False) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== stage 1/3: fetch Wikipedia summary ({lang}) ===", file=sys.stderr)
    src = wiki_source.fetch_summary(topic, lang=lang)
    (out_dir / "wiki_source.json").write_text(
        json.dumps(src.to_dict(), indent=2, ensure_ascii=False)
    )
    print(f"    grounded on: {src.url}", file=sys.stderr)

    print("=== stage 2/3: build grounded script (number-guarded) ===", file=sys.stderr)
    script = wiki_script.build_script(src, use_llm=use_llm)
    (out_dir / "script.json").write_text(
        json.dumps(script.to_dict(), indent=2, ensure_ascii=False)
    )
    (out_dir / "script.txt").write_text(script.as_plaintext())
    (out_dir / "provenance.json").write_text(
        json.dumps(script.provenance, indent=2, ensure_ascii=False)
    )
    print(f"    script generation: {script.generation}, {len(script.turns)} turns",
          file=sys.stderr)

    audio = None
    if with_audio:
        print("=== stage 3/3: synthesize audio (Piper, best-effort) ===", file=sys.stderr)
        audio = _synthesize_audio(script, out_dir)
        if not audio["produced"]:
            print(f"!!! audio skipped: {audio['reason']}", file=sys.stderr)

    summary = {
        "topic": topic,
        "source": src.to_dict(),
        "script": script.to_dict(),
        "audio": audio,
    }
    (out_dir / "wiki_podcast_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False)
    )
    return summary


def _cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topic", help="Wikipedia page title / topic name, e.g. 'Kiel'")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--lang", default=wiki_source.WIKI_LANG)
    parser.add_argument("--no-llm", action="store_true",
                         help="skip the LLM, use the deterministic verbatim script")
    parser.add_argument("--with-audio", action="store_true",
                         help="also synthesize speech per turn with Piper (best-effort)")
    args = parser.parse_args()

    try:
        summary = run(args.topic, args.out_dir, lang=args.lang,
                      use_llm=not args.no_llm, with_audio=args.with_audio)
    except wiki_source.WikiSourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _cli()
