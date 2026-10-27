"""Text-to-Speech-Backends. Auswahl per EVGEN_TTS_BACKEND:

  channel  Produktion: ct-agent-Channel `audio_generation` (llm2) -> Piper de_DE-thorsten-high ("Thomas") auf dem GPU-Node
  http     direkter HTTP-POST /synthesize (z.B. lokaler Tunnel auf 127.0.0.1:8096)
  ssh      Entwicklung: ssh <host> curl 127.0.0.1:8096/synthesize (nur fuer Tests)
  stub     Testtoene (offline-Selbsttest), kein Sprachinhalt
"""
from __future__ import annotations

import json
import math
import os
import queue
import re
import struct
import subprocess
import threading
import time
import urllib.request
import wave
from pathlib import Path

from .errors import TtsError
from .grounding import sentences

CHUNK_CHARS = 600


def split_text(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Satzgrenzen-sicher in Stuecke <= limit zerlegen."""
    sents = sentences(text)
    out, cur = [], ""
    for s in sents:
        while len(s) > limit:
            cut = s.rfind(" ", 0, limit) or limit
            out.append((cur + " " + s[:cut]).strip() if cur else s[:cut])
            cur, s = "", s[cut:].strip()
        if cur and len(cur) + 1 + len(s) > limit:
            out.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        out.append(cur)
    return [c for c in out if c]


def _check_wav(b: bytes) -> bytes:
    if len(b) < 1000 or b[:4] != b"RIFF":
        raise TtsError("TTS lieferte kein gueltiges WAV")
    return b


class StubTts:
    name = "stub"

    def available(self, lang: str) -> bool:
        return True

    def synth(self, text: str, voice: str, timeout: float) -> bytes:
        secs = max(0.8, len(text) / 15.0)
        rate = 22050
        f = 220 if voice == "A" else 330
        frames = b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * f * i / rate) * (0.6 + 0.4 * math.sin(2 * math.pi * 3 * i / rate)))) for i in range(int(rate * secs)))
        import io
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(frames)
        return buf.getvalue()

    def close(self) -> None:
        pass


class HttpTts:
    name = "http"

    def __init__(self, lang: str = "de") -> None:
        self.lang = lang
        self.urls = {"de": os.environ.get("EVGEN_TTS_URL", ""), "en": os.environ.get("EVGEN_TTS_URL_EN", "")}

    def available(self, lang: str) -> bool:
        return bool(self.urls.get(lang))

    def synth(self, text: str, voice: str, timeout: float) -> bytes:
        req = urllib.request.Request(self.urls[self.lang].rstrip("/") + "/synthesize", data=json.dumps({"text": text}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return _check_wav(r.read())
        except TtsError:
            raise
        except Exception as e:
            raise TtsError(f"TTS-HTTP fehlgeschlagen: {type(e).__name__}") from None

    def close(self) -> None:
        pass


class SshTts:
    name = "ssh"

    def __init__(self) -> None:
        self.host = os.environ["EVGEN_TTS_SSH"]
        self.port = os.environ.get("EVGEN_TTS_SSH_PORT", "8096")

    def available(self, lang: str) -> bool:
        return lang == "de"

    def synth(self, text: str, voice: str, timeout: float) -> bytes:
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", self.host,
               f"curl -s -f -m {int(timeout)} -H 'content-type: application/json' --data-binary @- 127.0.0.1:{self.port}/synthesize"]
        try:
            r = subprocess.run(cmd, input=json.dumps({"text": text}).encode(), capture_output=True, timeout=timeout + 15)
        except subprocess.TimeoutExpired:
            raise TtsError("TTS-Timeout (ssh)", "timeout") from None
        if r.returncode != 0:
            raise TtsError(f"TTS ssh/curl Exit {r.returncode}")
        return _check_wav(r.stdout)

    def close(self) -> None:
        pass


class ChannelTts:
    """ct-agent `channel` im Persistent-Call-Modus: eine stdin-Zeile = ein Aufruf,
    eine NDJSON-Zeile {"ok":true,"output":"<URL>"} zurueck (Vertrag wie im Callcenter-Demo)."""
    name = "channel"

    def __init__(self) -> None:
        for v in ("CT_AGENT_BIN", "CT_RELAY_ENV", "CT_AUDIO_CHANNEL_ID"):
            if not os.environ.get(v):
                raise TtsError(f"Channel-TTS nicht konfiguriert: {v} fehlt", "tts_not_configured")
        self.proc: subprocess.Popen | None = None
        self.q: queue.Queue = queue.Queue()
        self.lock = threading.Lock()

    def available(self, lang: str) -> bool:
        return lang == "de"   # der Handler bedient nur die deutsche Piper-Stimme

    def _start(self) -> None:
        cmd = ('set -a; source "$CT_RELAY_ENV"; set +a; CT_CHANNEL_ROLE=initiate CT_CHANNEL_CALL_SERVICE=audio_generation '
               'CT_CHANNEL_CALL_PERSISTENT=1 CT_CHANNEL_RELAY_ONLY=1 CT_CHANNEL_ID="$CT_AUDIO_CHANNEL_ID" '
               'CT_CHANNEL_GRANT="$CT_CHANNEL_GRANT_2E" CT_CHANNEL_HOLDER_KEY="$CT_CHANNEL_HOLDER_KEY" CT_CHANNEL_NOISE_KEY="$CT_CHANNEL_NOISE_KEY" '
               'CT_CHANNEL_FRONT_DOOR=bunsenbrenner.org:443 CT_CHANNEL_FRONT_DOOR_CERT="$CT_CHANNEL_FRONT_DOOR_CERT" CT_CHANNEL_FRONT_DOOR_ONLY=1 '
               'CT_CHANNEL_BROKER=bunsenbrenner.org:4435 CT_CHANNEL_RELAY=bunsenbrenner.org:4436 "$CT_AGENT_BIN" channel 2>/dev/null')
        self.proc = subprocess.Popen(["bash", "-c", cmd], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
        self.q = queue.Queue()
        threading.Thread(target=lambda p=self.proc, q=self.q: [q.put(l) for l in p.stdout], daemon=True).start()

    def _call(self, payload: dict, timeout: float) -> str:
        if self.proc is None or self.proc.poll() is not None:
            self._start()
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()
        end = time.monotonic() + timeout
        while True:
            try:
                line = self.q.get(timeout=max(0.1, end - time.monotonic()))
            except queue.Empty:
                self.close()
                raise TtsError("TTS-Timeout (channel)", "timeout") from None
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                env = json.loads(line)
            except ValueError:
                continue
            if env.get("ok") is True:
                return str(env.get("output", ""))
            self.close()
            raise TtsError("TTS-Channel-Fehler: " + str(env.get("error", ""))[:120])

    def synth(self, text: str, voice: str, timeout: float) -> bytes:
        with self.lock:   # ein Channel-Prozess, ein Aufruf zugleich; Download danach parallel
            out = self._call({"text": text, "voice": "primary"}, timeout)
        if not re.fullmatch(r"https://[a-z0-9.-]*bunsenbrenner\.org/audio/[A-Za-z0-9_-]+\.wav", out):
            raise TtsError("TTS-Channel lieferte keine Audio-URL: " + out[:80])
        last: Exception | None = None
        for _ in range(3):
            try:
                with urllib.request.urlopen(out, timeout=timeout) as r:
                    return _check_wav(r.read())
            except Exception as e:  # kurzzeitige Tunnel-Aussetzer
                last = e
                time.sleep(1.0)
        raise TtsError(f"Audio-Abruf fehlgeschlagen: {type(last).__name__}")

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()  # type: ignore[union-attr]
                self.proc.terminate()
            except Exception:
                pass
        self.proc = None


def make(lang: str):
    kind = os.environ.get("EVGEN_TTS_BACKEND", "channel")
    if kind == "stub":
        b = StubTts()
    elif kind == "ssh":
        b = SshTts()
    elif kind == "http":
        b = HttpTts(lang)
    elif kind == "channel":
        b = ChannelTts()
    else:
        raise TtsError(f"unbekanntes TTS-Backend: {kind}", "tts_not_configured")
    if not b.available(lang):
        raise TtsError(f"Fuer Sprache '{lang}' ist im Backend '{kind}' keine Stimme verfuegbar", "tts_lang_unavailable")
    return b
