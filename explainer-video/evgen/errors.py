from __future__ import annotations

"""Fehlertypen mit stabilen Codes und Exit-Codes (siehe API.md)."""


class EvgenError(Exception):
    code = "internal"
    exit_code = 1

    def __init__(self, message: str, code: str | None = None, detail: str = ""):
        super().__init__(message)
        if code:
            self.code = code
        self.detail = detail        # technische Einzelheiten: nie in "message", nur fuer "Technische Details"


class ConfigError(EvgenError):
    code = "invalid_config"
    exit_code = 2


class ScriptError(EvgenError):
    code = "script_failed"
    exit_code = 3


class TtsError(EvgenError):
    code = "tts_failed"
    exit_code = 4


class MixError(EvgenError):
    code = "mix_failed"
    exit_code = 5


class TimeoutError_(EvgenError):
    code = "timeout"
    exit_code = 6
