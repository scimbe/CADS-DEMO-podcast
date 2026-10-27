"""Konfiguration (JSON) validieren; harte Grenzen."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import ConfigError

MAX_TEXT_CHARS = 4000
MIN_TEXT_CHARS = 40
MAX_TITLE_CHARS = 100
LENGTHS = (30, 60, 90)
LANGS = ("de", "en")
MAX_VIDEO_SECONDS = 120          # harte Obergrenze des fertigen Videos
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean(s: str) -> str:
    return _CTRL.sub("", s).replace("\r\n", "\n").strip()


@dataclass(frozen=True)
class Config:
    text: str
    title: str
    length_s: int
    lang: str


def parse(raw: object) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError("Konfiguration muss ein JSON-Objekt sein")
    unknown = set(raw) - {"text", "title", "length", "lang"}
    if unknown:
        raise ConfigError(f"unbekannte Felder: {', '.join(sorted(unknown))}")
    text = raw.get("text")
    if not isinstance(text, str):
        raise ConfigError("text: Text erforderlich")
    if len(text) > MAX_TEXT_CHARS:
        raise ConfigError(f"text: maximal {MAX_TEXT_CHARS} Zeichen (erhalten {len(text)})", "text_too_long")
    text = _clean(text)
    if len(text) < MIN_TEXT_CHARS:
        raise ConfigError(f"text: mindestens {MIN_TEXT_CHARS} Zeichen", "text_too_short")
    title = raw.get("title") or ""
    if not isinstance(title, str) or len(title) > MAX_TITLE_CHARS:
        raise ConfigError(f"title: Text mit hoechstens {MAX_TITLE_CHARS} Zeichen")
    length = raw.get("length", 60)
    if isinstance(length, bool) or length not in LENGTHS:
        raise ConfigError(f"length: erlaubt sind {LENGTHS} (Sekunden)")
    lang = raw.get("lang", "de")
    if lang not in LANGS:
        raise ConfigError(f"lang: erlaubt sind {LANGS}")
    return Config(text, _clean(title), int(length), lang)
