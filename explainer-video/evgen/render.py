"""Deterministische Slides (1920x1080) mit ImageMagick; keine Browser-Abhaengigkeit."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .errors import MixError

W, H = 1920, 1080
BG, INK, MUTED, ACCENT = "#f4f6f8", "#10202e", "#5b6b7a", "#2563eb"
MARGIN, TEXT_W, BULLET_Y = 140, 1640, 450
_FONTS = [  # (regular, bold); erster vorhandener Treffer gewinnt
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]


def fonts() -> tuple[str, str]:
    env = os.environ.get("EVGEN_FONT"), os.environ.get("EVGEN_FONT_BOLD")
    if env[0] and env[1] and os.path.exists(env[0]) and os.path.exists(env[1]):
        return env  # type: ignore[return-value]
    for reg, bold in _FONTS:
        if os.path.exists(reg) and os.path.exists(bold):
            return reg, bold
    raise MixError("Keine Schrift gefunden (EVGEN_FONT/EVGEN_FONT_BOLD setzen oder fonts-dejavu-core installieren)", "font_missing")


def _magick(args: list[str]) -> None:
    try:
        r = subprocess.run(["magick", *args], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        try:
            r = subprocess.run(["convert", *args], capture_output=True, text=True, timeout=60)
        except FileNotFoundError:
            raise MixError("ImageMagick nicht installiert", "imagemagick_missing") from None
    if r.returncode != 0:
        raise MixError("ImageMagick fehlgeschlagen: " + (r.stderr.strip()[:160] or str(r.returncode)))


def _safe(t: str) -> str:
    """ImageMagick deutet %-Escapes und fuehrendes '@' (Datei lesen) in caption: - Nutzertext neutralisieren."""
    return t.replace("%", "%%").lstrip("@")


def _cap(text: str, font: str, size: int, color: str, width: int) -> list[str]:
    return ["(", "-background", "none", "-fill", color, "-font", font, "-pointsize", str(size), "-size", f"{width}x",
            "-gravity", "West", f"caption:{_safe(text)}", ")"]


def render_scene(wd: Path, idx: int, total: int, video_title: str, headline: str, bullets: list[str]) -> list[Path]:
    """Ein einziger ImageMagick-Aufruf je Szene. Rueckgabe: len(bullets)+1 Bilder
    (erst Ueberschrift, dann je Stichpunkt kumulativ eingeblendet)."""
    reg, bold = fonts()
    base = wd / f"s{idx}_base.png"
    outs = [wd / f"s{idx}_v{k}.png" for k in range(1, len(bullets) + 1)]
    cmd = ["-size", f"{W}x{H}", f"xc:{BG}",
           "-fill", ACCENT, "-draw", f"rectangle 0,0 26,{H}", "-draw", f"rectangle {MARGIN},196 {MARGIN + 140},204",
           "-fill", MUTED, "-font", reg, "-pointsize", "34", "-gravity", "SouthEast", "-annotate", "+90+60", f"{idx + 1} / {total}",
           *_cap(video_title.upper()[:60], reg, 34, MUTED, TEXT_W), "-gravity", "NorthWest", "-geometry", f"+{MARGIN}+90", "-composite",
           *_cap(headline, bold, 84, INK, TEXT_W), "-gravity", "NorthWest", "-geometry", f"+{MARGIN}+230", "-composite",
           "-write", str(base), "-write", "mpr:base", "+delete"]
    for k, out in enumerate(outs, 1):
        stack: list[str] = []
        for b in bullets[:k]:
            stack += _cap("\u2022  " + b, reg, 46, INK, TEXT_W - 20)
        cmd += ["(", "mpr:base", "(", *stack, "-background", "none", "-gravity", "North", "-splice", "0x30", "-append", "+repage", ")",
                "-gravity", "NorthWest", "-geometry", f"+{MARGIN}+{BULLET_Y}", "-composite", "-write", str(out), "+delete", ")"]
    _magick([*cmd, "null:"])
    return [base, *outs]
