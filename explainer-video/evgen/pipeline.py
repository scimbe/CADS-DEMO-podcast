"""Orchestrierung: Config -> Szenen-Skript -> (TTS || Slides) -> Clips -> MP4/VTT/Poster."""
from __future__ import annotations

import json
import os
import random
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import config as C
from . import grounding as G
from . import render, script, tts, video
from .errors import EvgenError, TimeoutError_, TtsError

TOTAL_TIMEOUT = float(os.environ.get("EVGEN_TOTAL_TIMEOUT", "240"))
TTS_CHUNK_TIMEOUT = float(os.environ.get("EVGEN_TTS_CHUNK_TIMEOUT", "60"))
STUB_SCRIPT = {"title": "Selbsttest", "scenes": [
    {"headline": f"Selbsttest Größe {i}", "bullets": ["Größe", "Rückseite", "schwächer"],
     "narration": f"Dies ist Szene {i} des Selbsttests. Größe, Rückseite und schwächer werden korrekt gesprochen.", "claims": []}
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


# Der TTS-Node (geteilt mit der Callcenter-Demo) serialisiert die Inferenz und antwortet bei Auslastung mit 503:
# hoechstens EVGEN_TTS_CONCURRENCY (2) Aufrufe dieses Laufs gleichzeitig; bei Auslastung/Timeout Backoff mit Jitter
# (0,5 / 1 / 2 / 4 s ...) innerhalb eines Zeitbudgets je Szene; scheitert eine Szene, bekommt sie am Ende einen zweiten Anlauf.
TTS_SLOTS = threading.Semaphore(int(os.environ.get("EVGEN_TTS_CONCURRENCY", "2")))
SCENE_BUDGET_S = float(os.environ.get("EVGEN_TTS_SCENE_BUDGET", "30"))
BACKOFF_SCALE = float(os.environ.get("EVGEN_TTS_BACKOFF_SCALE", "1"))   # nur fuer Tests < 1
BACKOFF_S = (0.5, 1.0, 2.0, 4.0)
NO_RETRY = ("tts_not_configured", "tts_lang_unavailable")


def backoff_delay(attempt: int) -> float:
    base = BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]
    return base * random.uniform(0.75, 1.25) * BACKOFF_SCALE


def synth_retry(backend, chunk: str, budget_end: float) -> bytes:
    attempt = 0
    while True:
        try:
            with TTS_SLOTS:
                return backend.synth(chunk, "A", TTS_CHUNK_TIMEOUT)
        except TtsError as e:
            delay = backoff_delay(attempt)
            if e.code in NO_RETRY or time.monotonic() + delay > budget_end:
                raise
            attempt += 1
            time.sleep(delay)


def synth_scene(backend, text: str, wd: Path, i: int, budget_s: float | None = None) -> tuple[Path, float]:
    budget_end = time.monotonic() + (SCENE_BUDGET_S if budget_s is None else budget_s)
    parts = []
    for k, chunk in enumerate(tts.split_text(text)):
        raw = wd / f"n{i}_{k}.wav"
        raw.write_bytes(synth_retry(backend, chunk, budget_end))
        parts.append(raw)
    if len(parts) == 1:
        return parts[0], video.duration(parts[0])
    joined = wd / f"n{i}.wav"
    video.concat(parts, joined, wd, audio=True)
    return joined, video.duration(joined)


def run_scene_jobs(n: int, job, workers: int, deadline: float, on_done=None) -> dict:
    """Szenen parallel; eine an TTS gescheiterte Szene wird am Ende noch einmal versucht, danach freundlicher Fehler."""
    results: dict[int, dict] = {}
    failed: list[tuple[int, TtsError]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(job, i): i for i in range(n)}
        for f in as_completed(futs):
            if time.monotonic() > deadline:
                pool.shutdown(wait=False, cancel_futures=True)
                raise TimeoutError_("Das Zeitlimit für dieses Video ist erreicht – bitte noch einmal versuchen.")
            try:
                results[futs[f]] = f.result()
            except TtsError as e:
                if e.code in NO_RETRY:
                    raise
                failed.append((futs[f], e))
                continue
            if on_done:
                on_done(len(results))
    for i, first in failed:
        try:
            results[i] = job(i)
        except TtsError as e2:
            raise TtsError(tts.FRIENDLY_BUSY, "tts_busy", f"Szene {i + 1}: {first.detail or first} / zweiter Anlauf: {e2.detail or e2}") from None
        if on_done:
            on_done(len(results))
    return results


def run(raw_cfg: object, out_dir: Path) -> dict:
    t0 = time.monotonic()
    deadline = t0 + TOTAL_TIMEOUT
    timing: dict[str, float] = {}
    def prog(stage: str, pct: int, msg: str) -> None:
        emit("PROGRESS", {"stage": stage, "pct": pct, "message": msg})

    prog("config", 2, "Eingabe prüfen")
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
    prog("script", 30, f"Skript fertig: {len(scenes)} Szenen, {rep.words} Wörter, {rep.claims} Belege")

    backend = tts.make(cfg.lang)
    warnings = list(rep.warnings)
    try:
        with tempfile.TemporaryDirectory(prefix="evgen_") as td:
            wd = Path(td)
            t = time.monotonic()
            n_sc = len(scenes)

            def job(i: int) -> dict:
                """Eine Szene komplett: Sprache (TTS) -> Folien -> Tonspur -> Clip. Szenen laufen parallel."""
                wav, sp = synth_scene(backend, scenes[i]["narration"], wd, i)
                frames = render.render_scene(wd, i, n_sc, title, scenes[i]["headline"], scenes[i]["bullets"])
                ln = video.scene_len(sp)
                nw, clip = wd / f"sn{i}.wav", wd / f"clip{i}.mp4"
                video.narration_wav(wav, nw, ln)
                video.scene_clip(frames, ln, sp, clip, wd)
                return {"len": ln, "speech": sp, "wav": nw, "clip": clip, "poster": frames[-1]}

            done = run_scene_jobs(n_sc, job, int(os.environ.get("EVGEN_WORKERS", "4")), deadline,
                                  lambda k: prog("scenes", 30 + int(58 * k / n_sc), f"Szene {k}/{n_sc} fertig (Sprache, Folien, Clip)"))
            timing["scenes_wall_s"] = round(time.monotonic() - t, 1)

            t = time.monotonic()
            lens = [done[i]["len"] for i in range(n_sc)]
            keep = drop_to_limit(lens)
            if keep < n_sc:
                warnings.append(f"Video auf das {C.MAX_VIDEO_SECONDS}-s-Limit gekürzt ({n_sc - keep} Szenen entfallen)")
                scenes, lens = scenes[:keep], lens[:keep]
            wavs = [done[i]["wav"] for i in range(keep)]
            clips = [done[i]["clip"] for i in range(keep)]
            vtt_scenes, pos = [], 0.0
            for i, s_ in enumerate(scenes):
                vtt_scenes.append({"narration": s_["narration"], "start": pos, "speech_s": done[i]["speech"]})
                pos += lens[i]
            master, norm_a, silent = wd / "master.wav", wd / "norm.wav", wd / "silent.mp4"
            video.concat(wavs, master, wd, audio=True)
            pre = video.loudnorm(master, norm_a)
            video.concat(clips, silent, wd, audio=False)
            mp4 = out_dir / "video.mp4"
            video.mux(silent, norm_a, mp4)
            video.poster(done[0]["poster"], out_dir / "poster.jpg")
            (out_dir / "subtitles.vtt").write_text(video.vtt(vtt_scenes), encoding="utf-8")
            timing["encode_s"] = round(time.monotonic() - t, 1)
    finally:
        backend.close()

    dur = video.duration(mp4)
    if dur > C.MAX_VIDEO_SECONDS + 1:
        raise EvgenError(f"Video laenger als {C.MAX_VIDEO_SECONDS}s", "duration_limit")
    loud = video.measure(mp4)
    out_script = {"title": title, "lang": cfg.lang, "voice": "Thomas (Piper de_DE-thorsten-high)", "scenes": scenes, "removed_sentences": rep.removed, "source": "user-text"}
    (out_dir / "script.json").write_text(json.dumps(out_script, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elapsed = round(time.monotonic() - t0, 1)
    meta = {"duration_s": round(dur, 2), "requested_length_s": cfg.length_s, "scenes": len(scenes), "words": rep.words, "claims": rep.claims,
            "llm": info, "tts_backend": backend.name, "voice": "Thomas", "loudness": loud, "pre_normalisation": pre,
            "timing_s": timing, "elapsed_s": elapsed, "warnings": warnings, "stub": stub or backend.name == "stub"}
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    prog("done", 100, "Fertig")
    emit("RESULT", {"title": title, "duration_s": meta["duration_s"], "elapsed_s": elapsed, "warnings": warnings,
                    "tts_backend": backend.name, "stub": meta["stub"],
                    "files": ["video.mp4", "subtitles.vtt", "script.json", "poster.jpg", "meta.json"]})
    return meta
