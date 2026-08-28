"""Stage 1: cut + concatenate + (optionally) mix raw tracks into an episode.

Input: an ordered list of track specs (path + optional trim window) and an
optional background "bed" track to mix underneath the whole episode.
Output: episode_master.wav (44.1kHz stereo PCM16), episode.mp3, and
episode_16k.wav (16kHz mono PCM16 — whisper.cpp's required ASR input format).
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import ffmpeg_util as ff

MASTER_RATE = 44100
MASTER_CHANNELS = 2
ASR_RATE = 16000
ASR_CHANNELS = 1

TRACK_SPEC_RE = re.compile(
    r"^(?P<path>[^:]+)(?::(?P<start>[0-9.]*):(?P<end>[0-9.]*))?$"
)


@dataclass
class TrackSpec:
    path: Path
    trim_start: float | None = None
    trim_end: float | None = None

    @classmethod
    def parse(cls, spec: str) -> "TrackSpec":
        """Parse 'path.wav' or 'path.wav:1.5:6.0' (either bound optional)."""
        m = TRACK_SPEC_RE.match(spec)
        if not m:
            raise ValueError(f"bad track spec: {spec!r}")
        start = m.group("start")
        end = m.group("end")
        return cls(
            path=Path(m.group("path")),
            trim_start=float(start) if start else None,
            trim_end=float(end) if end else None,
        )


def build_episode(
    tracks: list[TrackSpec],
    out_dir: Path,
    *,
    mix_bed_path: Path | None = None,
    bed_volume: float = 0.15,
) -> dict:
    """Run the full cut/concat/(mix) pipeline. Returns a manifest dict."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="podcast_producer_") as tmp:
        tmp_dir = Path(tmp)
        normalized: list[Path] = []
        manifest_tracks = []
        for i, spec in enumerate(tracks):
            norm_path = tmp_dir / f"norm_{i:03d}.wav"
            ff.normalize_wav(
                spec.path,
                norm_path,
                sample_rate=MASTER_RATE,
                channels=MASTER_CHANNELS,
                trim_start=spec.trim_start,
                trim_end=spec.trim_end,
            )
            normalized.append(norm_path)
            manifest_tracks.append(
                {
                    "source": str(spec.path),
                    "trim_start": spec.trim_start,
                    "trim_end": spec.trim_end,
                    "duration_s": round(ff.probe_duration_seconds(norm_path), 3),
                }
            )

        concat_path = tmp_dir / "concat.wav"
        ff.concat_wavs(normalized, concat_path)

        master_path = out_dir / "episode_master.wav"
        mixed = False
        if mix_bed_path is not None:
            ff.mix_bed(concat_path, mix_bed_path, master_path, bed_volume=bed_volume)
            mixed = True
        else:
            master_path.write_bytes(concat_path.read_bytes())

        mp3_path = out_dir / "episode.mp3"
        ff.to_mp3(master_path, mp3_path)

        asr_path = out_dir / "episode_16k.wav"
        ff.normalize_wav(master_path, asr_path, sample_rate=ASR_RATE, channels=ASR_CHANNELS)

    manifest = {
        "tracks": manifest_tracks,
        "mixed_background_bed": mixed,
        "bed_source": str(mix_bed_path) if mix_bed_path else None,
        "bed_volume": bed_volume if mixed else None,
        "master_wav": str(master_path),
        "episode_mp3": str(mp3_path),
        "asr_wav": str(asr_path),
        "duration_s": round(ff.probe_duration_seconds(master_path), 3),
    }
    (out_dir / "cut_mix_manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def _cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tracks", nargs="+", required=True,
        help="ordered track specs: path.wav or path.wav:trim_start:trim_end",
    )
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--mix-bed", type=Path, default=None,
                         help="optional background bed WAV, mixed under the episode")
    parser.add_argument("--bed-volume", type=float, default=0.15)
    args = parser.parse_args()

    specs = [TrackSpec.parse(t) for t in args.tracks]
    manifest = build_episode(specs, args.out_dir, mix_bed_path=args.mix_bed,
                              bed_volume=args.bed_volume)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    _cli()
