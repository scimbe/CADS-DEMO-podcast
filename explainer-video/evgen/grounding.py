"""Szenen-Skript validieren: Struktur, Laenge, Belegpflicht (jede Aussage muss aus dem Eingabetext stammen)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .config import Config

WORDS_PER_SEC = 2.6              # Thomas (Piper thorsten-high), kalibriert im Testlauf (siehe API.md)
SCENE_PAD_S = 0.8                # Vor-/Nachlauf je Szene (0,35 + 0,45)
MIN_SCENES, MAX_SCENES = 4, 8
NOVEL_WORD_MAX = 0.5

_NUM = re.compile(r"\d[\d.,]*")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


class ScriptProblems(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def split_sentences(text: str, limit: int = 320) -> list[str]:
    """Eingabetext in nummerierbare Saetze zerlegen (Absaetze/Aufzaehlungen zaehlen als Grenze)."""
    out: list[str] = []
    for para in re.split(r"\n+", text):
        for s in re.split(r"(?<=[.!?])\s+", para.strip()):
            s = s.strip(" -*\u2022\t")
            while len(s) > limit:
                cut = max(s.rfind(", ", 0, limit), s.rfind(" ", 0, limit))
                cut = cut if cut > 40 else limit
                out.append(s[:cut + 1].strip())
                s = s[cut + 1:].strip()
            if s:
                out.append(s)
    return out


_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def norm(s: str) -> str:
    """Nur zum VERGLEICHEN: NFC, klein, Umlaute gefaltet (aeusserst/äußerst gleich). Angezeigter Text bleibt unveraendert."""
    return " ".join(_WORD.findall(nfc(s).lower().translate(_FOLD)))


def numbers(s: str) -> set[str]:
    return {m.rstrip(".,") for m in _NUM.findall(s) if m.rstrip(".,")}


def shorten(s: str, limit: int = 70) -> str:
    """Satz auf einen Stichpunkt kuerzen: Satzzeichen weg, an Komma/Wortgrenze schneiden."""
    s = s.strip().rstrip(".!?;: ")
    if len(s) <= limit:
        return s
    cut = s.rfind(", ", 0, limit)
    if cut < 20:
        cut = s.rfind(" ", 0, limit)
    return s[:cut if cut > 0 else limit].rstrip(", ")


def repair_bullets(bullets: object, cited: list[str]) -> list[str]:
    """Gueltige Stichpunkte (2..4, je 3..70 Zeichen) herstellen: vorhandene bereinigen, fehlende aus den
    ZITIERTEN Quellsätzen ableiten (damit bleiben sie belegt). Leere Liste, wenn das nicht gelingt."""
    out: list[str] = []
    for b in bullets if isinstance(bullets, list) else []:
        if isinstance(b, str) and len(b.strip()) >= 3:
            b = shorten(nfc(b))
            if b and b not in out:
                out.append(b)
    def add(part: str) -> None:
        b = shorten(part)
        if len(b) >= 3 and b not in out and len(out) < 4:
            out.append(b)

    for sent in cited:                      # 1. ganze (gekürzte) Quellsätze
        if len(out) < 2:
            add(sent)
    for sent in cited:                      # 2. sonst Teilsaetze
        for part in re.split(r",\s+|\s+(?:und|sowie|and)\s+", sent):
            if len(out) < 2 and len(part) >= 15:
                add(part)
    return out[:4] if len(out) >= 2 else []


def word_count(s: str) -> int:
    return len(_WORD.findall(s))


def stems(s: str) -> set[str]:
    return {w[:5] for w in norm(s).split() if len(w) >= 4}


def scene_count(length_s: int) -> int:
    return {30: 4, 60: 6, 90: 8}[length_s]


def target_words(length_s: int) -> int:
    return int((length_s - SCENE_PAD_S * scene_count(length_s)) * WORDS_PER_SEC)


def bounds(length_s: int, text: str) -> tuple[int, int, bool]:
    """(min, max, limited_by_input). Obergrenze hart, Untergrenze durch die Textmenge begrenzt."""
    tgt = target_words(length_s)
    lo, hi, limited = int(tgt * 0.7), int(tgt * 1.15), False
    cap = max(25, int(word_count(text) * 0.8))
    if cap < lo:
        lo, limited = cap, True
    return lo, hi, limited


@dataclass
class Report:
    words: int = 0
    scenes: int = 0
    claims: int = 0
    max_novel_ratio: float = 0.0
    min_words: int = 0
    max_words: int = 0
    warnings: list[str] = field(default_factory=list)


def validate(script: object, cfg: Config) -> tuple[dict, Report]:
    p: list[str] = []
    rep = Report()
    if not isinstance(script, dict):
        raise ScriptProblems(["Antwort ist kein JSON-Objekt"])
    title = script.get("title")
    scenes = script.get("scenes")
    if not isinstance(title, str) or not 3 <= len(title.strip()) <= 80:
        p.append("title fehlt oder hat nicht 3..80 Zeichen")
    if not isinstance(scenes, list) or not (MIN_SCENES <= len(scenes) <= MAX_SCENES):
        raise ScriptProblems(p + [f"scenes: {MIN_SCENES} bis {MAX_SCENES} Szenen erforderlich"])
    sents = split_sentences(cfg.text)
    total = 0
    out = []
    for i, sc in enumerate(scenes):
        tag = f"Szene {i + 1}"
        if not isinstance(sc, dict):
            p.append(f"{tag}: kein Objekt"); continue
        nar, head, bl, src = sc.get("narration"), sc.get("headline"), sc.get("bullets"), sc.get("sources")
        if not isinstance(nar, str) or not 6 <= word_count(nar) <= 70:
            p.append(f"{tag}: narration braucht 6..70 Wörter"); continue
        if not isinstance(head, str) or not 3 <= len(head.strip()) <= 60:
            p.append(f"{tag}: headline braucht 3..60 Zeichen"); continue
        if (not isinstance(src, list) or not 1 <= len(src) <= 3 or not all(isinstance(x, int) and not isinstance(x, bool) and 1 <= x <= len(sents) for x in src)):
            p.append(f"{tag}: sources braucht 1..3 Satznummern aus 1..{len(sents)}"); continue
        cited = [sents[x - 1] for x in dict.fromkeys(src)]
        if not isinstance(bl, list) or not 2 <= len(bl) <= 4 or not all(isinstance(b, str) and 3 <= len(b.strip()) <= 70 for b in bl):
            fixed = repair_bullets(bl, cited)
            if not fixed:
                p.append(f"{tag}: bullets braucht 2..4 Punkte mit je 3..70 Zeichen"); continue
            bl = fixed
            rep.warnings.append(f"Szene {i + 1}: Stichpunkte fehlten oder waren ungültig und wurden aus den Quellsätzen abgeleitet")
        total += word_count(nar)
        cited_text = " ".join(cited)
        extra = numbers(" ".join([nar, head, *bl])) - numbers(cited_text) - numbers(cfg.title)
        if extra:
            p.append(f"{tag}: Zahlen nicht in den genannten Quellsätzen: {', '.join(sorted(extra))}")
        cited_stems = stems(cited_text) | stems(cfg.title) | stems(head)
        long_w = [w for w in norm(" ".join([nar, *bl])).split() if len(w) >= 7]
        if long_w:
            ratio = sum(1 for w in long_w if w[:5] not in cited_stems) / len(long_w)
            rep.max_novel_ratio = max(rep.max_novel_ratio, round(ratio, 2))
            if ratio > NOVEL_WORD_MAX:
                p.append(f"{tag}: {int(ratio * 100)}% der Inhaltswörter stehen nicht in den Quellsätzen {list(dict.fromkeys(src))} (erfunden?)")
        rep.claims += len(cited)
        cl = cited
        out.append({"narration": nfc(re.sub(r"\s+", " ", nar).strip()), "headline": nfc(head.strip()),
                    "bullets": [nfc(b.strip()) for b in bl], "claims": cl, "sources": list(dict.fromkeys(src))})
    lo, hi, limited = bounds(cfg.length_s, cfg.text)
    rep.words, rep.scenes, rep.min_words, rep.max_words = total, len(scenes), lo, hi
    if total < lo:
        p.append(f"Erzähltext zu kurz: {total} Wörter, mindestens {lo}")
    if total > hi:
        p.append(f"Erzähltext zu lang: {total} Wörter, höchstens {hi}")
    if p:
        raise ScriptProblems(p)
    if limited:
        rep.warnings.append("Video kürzer als gewaehlt: der Text gibt für mehr Inhalt nicht genug her (keine Erfindungen)")
    return {"title": nfc(title.strip()), "scenes": out}, rep
