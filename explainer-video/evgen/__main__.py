"""CLI: python3 -m evgen <config.json|-> [out_dir]"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import pipeline
from .errors import EvgenError, ConfigError


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python3 -m evgen <config.json|-> [out_dir]", file=sys.stderr)
        return 2
    try:
        raw = json.load(sys.stdin if argv[1] == "-" else open(argv[1], encoding="utf-8"))
    except (OSError, ValueError) as e:
        pipeline.emit("ERROR", {"code": "invalid_config", "message": f"Konfiguration nicht lesbar: {type(e).__name__}"})
        return ConfigError.exit_code
    try:
        pipeline.run(raw, Path(argv[2] if len(argv) > 2 else "out"))
        return 0
    except EvgenError as e:
        pipeline.emit("ERROR", {"code": e.code, "message": str(e)})
        return e.exit_code
    except Exception as e:  # nie Stacktrace/Geheimnisse an den Client
        print(f"internal error: {type(e).__name__}: {e}", file=sys.stderr)
        pipeline.emit("ERROR", {"code": "internal", "message": "interner Fehler"})
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
