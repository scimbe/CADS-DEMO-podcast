"""Orchestrierung: Config -> Szenen-Skript -> (TTS || Slides) -> Clips -> MP4/VTT/Poster."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import config as C
from . import grounding as G
from . import render, script, tts, video
from .errors import EvgenError, TimeoutError_

TOTAL_TIMEOUT = float(os.environ.get("EVGEN_TOTAL_TIMEOUT", "240"))
TTS_CHUNK_TIMEOUT = float(os.environ.get("EVGEN_TTS_CHUNK_TIMEOUT", "60"))
STUB_SCRIPT = {"title": "Selbsttest", "scenes": [
    {"headline": f"Selbsttest Teil {i}", "bullets": ["Erster Punkt", "Zweiter Punkt", "Dritter Punkt"],
     "narration": f"Dies ist Szene {i} des Selbsttests. Sie prueft Folien, Ton, Untertitel und den Zusammenbau des Videos.", "claims": []}
    for i in range(1, 5)]}


def emit(kind: str, obj: dict) -> None:
    print(f"{kind} {json.dumps(obj, ensure_ascii=False)}", flush=True)


def drop_to_limit(lengths: list[float], limit: float = C.MAX_VIDEO_SECONDS, keep: int = 2) -> int:
    """Anzahl Szenen, die unter das harte Limit passen (mindestens `keep`)."""
    total, n = 0.0, 0
    for d in lengths:
        if total + d > limit and n >= keep:
            break
        total += d
        n += 1
    return n


def synth_scene(backend, text: str, wd: Path, i: int) -> tuple[Path, float]:
    parts = []
    for k, chunk in enumerate(tts.split_text(text)):
        raw = wd / f"n{i}_{k}.wav"
        raw.write_bytes(backend.synth(chunk, "A", TTS_CHUNK_TIMEOUT))
        parts.append(raw)
    if len(parts) == 1:
        return parts[0], video.duration(parts[0])
    joined = wd / f"n{i}.wav"
    video.concat(parts, joined, wd, audio=True)
    return joined, video.duration(joined)


def run(raw_cfg: object, out_dir: Path) -> dict:
    t0 = time.monotonic()
    deadline = t0 + TOTAL_TIMEOUT
    timing: dict[str, float] = {}
    def prog(stage: str, pct: int, msg: str) -> None:
        emit("PROGRESS", {"stage": stage, "pct": pct, "message": msg})

    prog("config", 2, "Eingabe pruefen")
    cfg = C.parse(raw_cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    stub = os.environ.get("EVGEN_TTS_BACKEND") == "stub" and os.environ.get("EVGEN_STUB_SCRIPT") == "1"
    render.fonts()  # frueh scheitern

    t = time.monotonic()
    if stub:
        scr, rep, info = STUB_SCRIPT, G.Report(), {"model": "stub", "attempts": 0}
    else:
        prog("script", 8, "Szenen-Skript aus deinem Text erzeugen")
        scr, rep, info = script.generate(cfg, deadline, lambda m: prog("script", 15, m))
    timing["script_s"] = round(time.monotonic() - t, 1)
    scenes = scr["scenes"]
    title = cfg.title or scr["title"]
    prog("script", 30, f"Skript fertig: {len(scenes)} Szenen, {rep.words} Woerter, {rep.claims} Belege")

    backend = tts.make(cfg.lang)
    warnings = list(rep.warnings)
    try:
        with tempfile.TemporaryDirectory(prefix="evgen_") as td:
            wd = Path(td)
            t = time.monotonic()
            pool = ThreadPoolExecutor(max_workers=3)
            futs = {pool.submit(synth_scene, backend, s["narration"], wd, i): i for i, s in enumerate(scenes)}
            frames = [render.render_scene(wd, i, len(scenes), title, s["headline"], s["bullets"]) for i, s in enumerate(scenes)]
            timing["render_s"] = round(time.monotonic() - t, 1)
            prog("render", 45, f"{len(scenes)} Folien gerendert (1920x1080)")
            speech: dict[int, tuple[Path, float]] = {}
            for n, f in enumerate(as_completed(futs), 1):
                if time.monotonic() > deadline:
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise TimeoutError_("Gesamt-Zeitlimit beim Sprechen erreicht")
                speech[futs[f]] = f.result()
                prog("tts", 45 + int(25 * n / len(scenes)), f"Szene {n}/{len(scenes)} vertont (Thomas)")
            pool.shutdown()
            timing["tts_wall_s"] = round(time.monotonic() - t, 1)  # inkl. paralleler Folien

            t = time.monotonic()
            lens = [video.scene_len(speech[i][1]) for i in range(len(scenes))]
            keep = drop_to_limit(lens)
            if keep < len(scenes):
                warnings.append(f"Video auf das {C.MAX_VIDEO_SECONDS}-s-Limit gekuerzt ({len(scenes) - keep} Szenen entfallen)")
                scenes, lens = scenes[:keep], lens[:keep]
            clips, wavs, vtt_scenes, pos = [], [], [], 0.0
            for i, s in enumerate(scenes):
                nw = wd / f"sn{i}.wav"
                video.narration_wav(speech[i][0], nw, lens[i])
                wavs.append(nw)
                clip = wd / f"clip{i}.mp4"
                video.scene_clip(frames[i], lens[i], speech[i][1], clip, wd)
                clips.append(clip)
                vtt_scenes.append({"narration": s["narration"], "start": pos, "speech_s": speech[i][1]})
                pos += lens[i]
                prog("encode", 70 + int(18 * (i + 1) / len(scenes)), f"Szene {i + 1}/{len(scenes)} codiert")
            master, norm_a, silent = wd / "master.wav", wd / "norm.wav", wd / "silent.mp4"
            video.concat(wavs, master, wd, audio=True)
            pre = video.loudnorm(master, norm_a)
            video.concat(clips, silent, wd, audio=False)
            mp4 = out_dir / "video.mp4"
            video.mux(silent, norm_a, mp4)
            video.poster(frames[0][-1], out_dir / "poster.jpg")
            (out_dir / "subtitles.vtt").write_text(video.vtt(vtt_scenes), encoding="utf-8")
            timing["encode_s"] = round(time.monotonic() - t, 1)
    finally:
        backend.close()

    dur = video.duration(mp4)
    if dur > C.MAX_VIDEO_SECONDS + 1:
        raise EvgenError(f"Video laenger als {C.MAX_VIDEO_SECONDS}s", "duration_limit")
    loud = video.measure(mp4)
    out_script = {"title": title, "lang": cfg.lang, "voice": "Thomas (Piper de_DE-thorsten-high)", "scenes": scenes, "source": "user-text"}
    (out_dir / "script.json").write_text(json.dumps(out_script, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elapsed = round(time.monotonic() - t0, 1)
    meta = {"duration_s": round(dur, 2), "requested_length_s": cfg.length_s, "scenes": len(scenes), "words": rep.words, "claims": rep.claims,
            "llm": info, "tts_backend": backend.name, "voice": "Thomas", "loudness": loud, "pre_normalisation": pre,
            "timing_s": timing, "elapsed_s": elapsed, "warnings": warnings, "stub": stub or backend.name == "stub"}
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    prog("done", 100, "Fertig")
    emit("RESULT", {"title": title, "duration_s": meta["duration_s"], "elapsed_s": elapsed, "warnings": warnings,
                    "files": ["video.mp4", "subtitles.vtt", "script.json", "poster.jpg", "meta.json"]})
    return meta
