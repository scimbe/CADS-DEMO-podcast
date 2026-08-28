"""Thin, explicit wrapper around the ffmpeg/ffprobe binaries.

Every audio operation in this project goes through here so there is exactly
one place that shells out to ffmpeg, and every call is logged (command +
return code) so pipeline runs are debuggable from stderr alone.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


class FfmpegError(RuntimeError):
    """Raised when an ffmpeg/ffprobe subprocess exits non-zero."""


def _find(binary: str) -> str:
    path = shutil.which(binary)
    if path is None:
        raise FfmpegError(
            f"required binary '{binary}' not found on PATH. Install ffmpeg "
            f"(provides both ffmpeg and ffprobe)."
        )
    return path


FFMPEG = None
FFPROBE = None


def ffmpeg_bin() -> str:
    global FFMPEG
    if FFMPEG is None:
        FFMPEG = _find("ffmpeg")
    return FFMPEG


def ffprobe_bin() -> str:
    global FFPROBE
    if FFPROBE is None:
        FFPROBE = _find("ffprobe")
    return FFPROBE


def run(args: list[str], *, quiet: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess, echo the command to stderr, raise on failure."""
    print(f"+ {' '.join(args)}", file=sys.stderr)
    result = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise FfmpegError(
            f"command failed (exit {result.returncode}): {' '.join(args)}\n"
            f"--- stderr ---\n{result.stderr}"
        )
    if not quiet and result.stderr:
        print(result.stderr, file=sys.stderr)
    return result


def ffmpeg(args: list[str]) -> subprocess.CompletedProcess:
    return run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", *args])


def probe_duration_seconds(path: Path) -> float:
    result = run(
        [
            ffprobe_bin(),
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(path),
        ]
    )
    return float(result.stdout.strip())


def normalize_wav(
    src: Path,
    dst: Path,
    *,
    sample_rate: int,
    channels: int,
    trim_start: float | None = None,
    trim_end: float | None = None,
) -> None:
    """Convert src to PCM16 WAV at the given rate/channels, optionally trimming.

    Trim uses -ss/-to placed *after* -i so ffmpeg decodes+re-encodes (not
    stream copy): for PCM WAV inputs this is exact to the sample and, unlike
    `-c copy`, works correctly regardless of the input's own sample rate.
    """
    args = ["-i", str(src)]
    if trim_start is not None:
        args += ["-ss", str(trim_start)]
    if trim_end is not None:
        args += ["-to", str(trim_end)]
    args += [
        "-ar", str(sample_rate),
        "-ac", str(channels),
        "-c:a", "pcm_s16le",
        str(dst),
    ]
    ffmpeg(args)


def concat_wavs(parts: list[Path], dst: Path) -> None:
    """Concatenate WAVs that already share sample rate/channels/format.

    Uses the ffmpeg concat *demuxer* (not the filter) since all inputs are
    already-normalized PCM WAV of identical format — this is a fast, lossless
    join, which is what "cutting tracks together" means for a podcast edit.
    """
    list_file = dst.parent / f".{dst.stem}.concat_list.txt"
    list_file.parent.mkdir(parents=True, exist_ok=True)
    with open(list_file, "w") as f:
        for p in parts:
            f.write(f"file '{p.resolve()}'\n")
    ffmpeg(
        [
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(dst),
        ]
    )
    list_file.unlink(missing_ok=True)


def mix_bed(main: Path, bed: Path, dst: Path, *, bed_volume: float = 0.15) -> None:
    """Mix a (looped, attenuated) background bed under `main`, same duration.

    This is the actual "mixing" operation (as opposed to concatenation):
    two simultaneous audio streams combined with the `amix` filter, the bed
    attenuated with `volume` and looped/truncated to the main track's length.
    """
    main_dur = probe_duration_seconds(main)
    ffmpeg(
        [
            "-i", str(main),
            "-stream_loop", "-1", "-i", str(bed),
            "-filter_complex",
            f"[1:a]volume={bed_volume}[bed];[0:a][bed]amix=inputs=2:duration=first:dropout_transition=0[out]",
            "-map", "[out]",
            "-t", str(main_dur),
            "-c:a", "pcm_s16le",
            str(dst),
        ]
    )


def to_mp3(src: Path, dst: Path, *, bitrate: str = "128k") -> None:
    ffmpeg(["-i", str(src), "-c:a", "libmp3lame", "-b:a", bitrate, str(dst)])


def tone(dst: Path, *, frequency: int = 880, duration: float = 1.0,
         sample_rate: int = 44100, channels: int = 2) -> None:
    """Generate a pure sine-tone WAV via ffmpeg's own `lavfi` sine source.

    This is the literal "ffmpeg's own tone generator" fixture: no external
    audio, no recording, just a synthesized signal, used as a stinger/
    transition edit element between spoken segments.
    """
    ffmpeg(
        [
            "-f", "lavfi",
            "-i", f"sine=frequency={frequency}:duration={duration}:sample_rate={sample_rate}",
            "-ac", str(channels),
            "-c:a", "pcm_s16le",
            str(dst),
        ]
    )
