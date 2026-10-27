"""OpenAI-kompatibler Chat-Aufruf (nur stdlib). Schluessel nur aus Env/Datei, nie geloggt."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

from .errors import ScriptError

FRIENDLY_LLM = "Das Sprachmodell ist gerade nicht erreichbar – bitte in einer Minute erneut versuchen."


def _cfg() -> tuple[str, str, str]:
    base = os.environ.get("LLM_BASE_URL") or os.environ.get("LITELLM_BASE_URL") or ""
    base = base.rstrip("/")
    if base and not base.endswith("/v1"):
        base += "/v1"
    key = os.environ.get("LLM_API_KEY") or os.environ.get("LITELLM_API_KEY") or ""
    kf = os.environ.get("LLM_API_KEY_FILE")
    if not key and kf and os.path.isfile(kf):
        key = open(kf, encoding="utf-8").read().strip()
    model = os.environ.get("LLM_MODEL") or os.environ.get("LITELLM_DEFAULT_MODEL") or ""
    if not (base and key and model):
        raise ScriptError("LLM nicht konfiguriert (LLM_BASE_URL, LLM_API_KEY[_FILE], LLM_MODEL)", "llm_not_configured")
    return base, key, model


def models() -> list[str]:
    _, _, model = _cfg()
    fb = os.environ.get("EVGEN_LLM_FALLBACK_MODEL")
    return [model] + ([fb] if fb and fb != model else [])


def chat(model: str, messages: list[dict], max_tokens: int, timeout: float) -> str:
    base, key, _ = _cfg()
    body = json.dumps({"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.4}).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body, headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + key,
        "User-Agent": "evgen/0.1 (CADS demo)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
        return d["choices"][0]["message"]["content"] or ""
    except urllib.error.HTTPError as e:
        raise ScriptError(FRIENDLY_LLM, "llm_unreachable", f"{model}: HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError, OSError) as e:
        raise ScriptError(FRIENDLY_LLM, "llm_unreachable", f"{model}: {type(e).__name__}") from None


def extract_json(text: str) -> object:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e <= s:
        raise ValueError("kein JSON-Objekt in der Antwort")
    return json.loads(text[s:e + 1])
