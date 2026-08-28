"""CLI entrypoint: orchestrates cut_mix -> transcribe -> chapters -> (announce).

    python -m podcast_producer.pipeline --tracks a.wav b.wav ... --out-dir out/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import announce, chapters, cut_mix, transcribe


def run_pipeline(
    track_specs: list[str],
    out_dir: Path,
    *,
    mix_bed: Path | None = None,
    bed_volume: float = 0.15,
    whisper_cli: str | None = None,
    whisper_model: str | None = None,
    allow_mock_transcript: bool = False,
    with_announcements: bool = False,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = [cut_mix.TrackSpec.parse(t) for t in track_specs]

    print("=== stage 1/4: cut_mix (ffmpeg) ===", file=sys.stderr)
    cm_manifest = cut_mix.build_episode(specs, out_dir, mix_bed_path=mix_bed,
                                         bed_volume=bed_volume)

    print("=== stage 2/4: transcribe (whisper.cpp) ===", file=sys.stderr)
    tr_result = transcribe.transcribe(
        Path(cm_manifest["asr_wav"]), out_dir,
        cli_path=whisper_cli, model_path=whisper_model,
        allow_mock=allow_mock_transcript,
    )
    if tr_result["mock"]:
        print("!!! USING MOCK TRANSCRIPT — see docs/LIMITATIONS.md !!!", file=sys.stderr)

    print("=== stage 3/4: chapters (LLM + validator) ===", file=sys.stderr)
    chapter_list = chapters.generate_chapters(tr_result["segments"])
    chapters_out = {
        "chapters": chapter_list,
        "generated_from_mock_transcript": tr_result["mock"],
    }
    (out_dir / "chapters.json").write_text(json.dumps(chapters_out, indent=2))

    announcements = None
    if with_announcements:
        print("=== stage 4/4: announcements (Piper, optional) ===", file=sys.stderr)
        announcements = announce.generate_announcements(
            chapter_list, out_dir / "announcements"
        )

    summary = {
        "cut_mix": cm_manifest,
        "transcript": {
            "mock": tr_result["mock"],
            "n_segments": len(tr_result["segments"]),
            "srt_path": tr_result["srt_path"],
            "json_path": tr_result["json_path"],
        },
        "chapters": chapters_out,
        "announcements": announcements,
    }
    (out_dir / "pipeline_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def _cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracks", nargs="+", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--mix-bed", type=Path, default=None)
    parser.add_argument("--bed-volume", type=float, default=0.15)
    parser.add_argument("--whisper-cli", default=None)
    parser.add_argument("--whisper-model", default=None)
    parser.add_argument("--allow-mock-transcript", action="store_true")
    parser.add_argument("--with-announcements", action="store_true")
    args = parser.parse_args()

    summary = run_pipeline(
        args.tracks, args.out_dir,
        mix_bed=args.mix_bed, bed_volume=args.bed_volume,
        whisper_cli=args.whisper_cli, whisper_model=args.whisper_model,
        allow_mock_transcript=args.allow_mock_transcript,
        with_announcements=args.with_announcements,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    _cli()
