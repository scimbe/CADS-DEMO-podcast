"""ffmpeg: Szenen-Clips, Audio-Master (EBU R128), MP4 (H.264/AAC), Poster, WebVTT."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
from pathlib import Path

from .errors import MixError
from .grounding import sentences

SR = 44100
FPS = 25
PRESET = os.environ.get("EVGEN_X264_PRESET", "veryfast")
LEAD_S, TAIL_S = 0.35, 0.45
FADE_S = 0.3
TARGET_LUFS, TRUE_PEAK = -16.0, -1.5


def run(args: list[str], timeout: float = 180) -> subprocess.CompletedProcess:
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-y", *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise MixError("ffmpeg-Timeout", "timeout") from None
    except FileNotFoundError:
        raise MixError("ffmpeg nicht installiert", "ffmpeg_missing") from None
    if r.returncode != 0:
        tail = r.stderr.strip().splitlines()
        raise MixError("ffmpeg fehlgeschlagen: " + (tail[-1][:200] if tail else str(r.returncode)))
    return r


def probe(path: Path, entry: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", entry, "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True, timeout=30)
    try:
        return float(r.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        raise MixError(f"ffprobe konnte {path.name} nicht lesen") from None


def duration(path: Path) -> float:
    return probe(path, "format=duration")


def narration_wav(src: Path, dst: Path, scene_s: float) -> None:
    """Sprache auf 44,1 kHz mono, LEAD_S Vorlauf, auf exakt scene_s aufgefuellt."""
    run(["-i", str(src), "-af", f"highpass=f=70,aresample={SR},adelay={int(LEAD_S * 1000)}:all=1,apad=whole_dur={scene_s:.3f}",
         "-t", f"{scene_s:.3f}", "-ac", "1", "-c:a", "pcm_s16le", str(dst)])


def scene_len(speech_s: float) -> float:
    """Szenenlaenge auf volle Frames aufgerundet, damit Audio- und Videosumme exakt uebereinstimmen."""
    return math.ceil((LEAD_S + speech_s + TAIL_S) * FPS) / FPS


def scene_clip(frames: list[Path], scene_s: float, speech_s: float, dst: Path, wd: Path) -> None:
    """Video-only Clip: Ueberschrift sofort, Stichpunkte gleichmaessig waehrend der Sprache eingeblendet, Fade-in/out."""
    n = len(frames) - 1
    marks = [0.0] + [LEAD_S + 0.5 + 0.7 * speech_s * k / max(n, 1) for k in range(n)] + [scene_s]
    lst = wd / (dst.stem + ".txt")
    lines = []
    for k, f in enumerate(frames):
        lines += [f"file '{f.resolve()}'", f"duration {marks[k + 1] - marks[k]:.3f}"]
    lines.append(f"file '{frames[-1].resolve()}'")
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run(["-f", "concat", "-safe", "0", "-i", str(lst), "-vf",
         f"fps={FPS},scale=1920:1080,format=yuv420p,fade=t=in:st=0:d={FADE_S},fade=t=out:st={scene_s - FADE_S:.3f}:d={FADE_S}",
         "-t", f"{scene_s:.3f}", "-an", "-c:v", "libx264", "-preset", PRESET, "-crf", "24", "-tune", "stillimage", str(dst)], timeout=120)


def concat(parts: list[Path], dst: Path, wd: Path, audio: bool) -> None:
    lst = wd / (dst.stem + "_concat.txt")
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts), encoding="utf-8")
    codec = ["-c:a", "pcm_s16le"] if audio else ["-c", "copy"]
    run(["-f", "concat", "-safe", "0", "-i", str(lst), *codec, str(dst)], timeout=240)


def _json_block(stderr: str) -> dict:
    m = re.findall(r"\{[^{}]*\}", stderr, flags=re.S)
    if not m:
        raise MixError("loudnorm lieferte keine Messwerte")
    return json.loads(m[-1])


def loudnorm(src: Path, dst: Path) -> dict:
    base = f"loudnorm=I={TARGET_LUFS}:TP={TRUE_PEAK}:LRA=11"
    m = _json_block(run(["-i", str(src), "-af", base + ":print_format=json", "-f", "null", "-"]).stderr)
    af = (f"{base}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
          f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,aresample={SR}")
    run(["-i", str(src), "-af", af, "-ac", "1", "-c:a", "pcm_s16le", str(dst)])
    return {"input_lufs": float(m["input_i"]), "input_true_peak_db": float(m["input_tp"])}


def mux(video: Path, audio: Path, dst: Path) -> None:
    run(["-i", str(video), "-i", str(audio), "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart", "-shortest", str(dst)])


def poster(png: Path, dst: Path) -> None:
    run(["-i", str(png), "-vf", "scale=1280:720", "-q:v", "3", "-frames:v", "1", str(dst)])


def measure(media: Path) -> dict:
    r = run(["-nostats", "-i", str(media), "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"])
    tail = r.stderr.split("Summary:")[-1]
    def g(pat: str) -> float:
        mm = re.search(pat, tail)
        if not mm:
            raise MixError("ebur128-Auswertung fehlgeschlagen")
        return float(mm.group(1))
    return {"integrated_lufs": g(r"I:\s+(-?[\d.]+) LUFS"), "lra": g(r"LRA:\s+(-?[\d.]+) LU"), "true_peak_dbfs": g(r"Peak:\s+(-?[\d.]+) dBFS")}


def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def split_cues(text: str, limit: int = 84) -> list[str]:
    """Satzweise Untertitel; lange Saetze an Kommas/Leerzeichen geteilt."""
    cues = []
    for s in sentences(text):
        while len(s) > limit:
            cut = max(s.rfind(", ", 0, limit), s.rfind(" ", 0, limit))
            cut = cut if cut > 20 else limit
            cues.append(s[:cut + 1].strip(" "))
            s = s[cut + 1:].strip()
        if s:
            cues.append(s)
    return cues


def vtt(scenes: list[dict]) -> str:
    """scenes: [{narration, start, speech_s}] -> WebVTT, Zeit proportional zur Zeichenzahl innerhalb der Sprechzeit."""
    out = ["WEBVTT", ""]
    n = 1
    for sc in scenes:
        cues = split_cues(sc["narration"])
        total = sum(len(c) for c in cues) or 1
        t = sc["start"] + LEAD_S
        for c in cues:
            d = sc["speech_s"] * len(c) / total
            out += [str(n), f"{_ts(t)} --> {_ts(t + d)}", c, ""]
            t += d
            n += 1
    return "\n".join(out)
