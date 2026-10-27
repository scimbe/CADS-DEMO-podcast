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


_ABBR = ("z. B.", "z.B.", "d. h.", "d.h.", "u. a.", "u.a.", "bzw.", "ca.", "Nr.", "ggf.", "inkl.", "evtl.", "usw.", "etc.", "St.", "Dr.", "Prof.", "vs.")


_MONTHS = "(?:Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)"


def sentences(text: str) -> list[str]:
    """Satzgrenzen nach . ! ? -- aber nicht nach Ordnungszahlen ("15. Februar") und gaengigen Abkuerzungen."""
    keep = text
    for ab in _ABBR:
        keep = keep.replace(ab, ab.replace(".", "\u2024").replace(" ", "\u00a0"))
    # Ordnungszahlen/Datum ("15. Februar", "3. Oktober", "1. Platz"): 1-2 Ziffern + Punkt + Wort; Jahreszahlen (3+ Ziffern) nur vor
    # Kleinbuchstaben oder Monatsnamen ("2024. Danach" bleibt eine Satzgrenze)
    keep = re.sub(r"(?<!\d)(\d{1,2})\.(\s+)(?=[A-Za-zÄÖÜäöüß])", lambda m: m.group(1) + "\u2024" + m.group(2), keep)
    keep = re.sub(r"(\d{3,})\.(\s+)(?=[a-zäöüß]|" + _MONTHS + r")", lambda m: m.group(1) + "\u2024" + m.group(2), keep)
    parts = re.split(r"(?<=[.!?])\s+", keep.strip())
    return [x.replace("\u2024", ".").replace("\u00a0", " ") for x in parts if x.strip()]


def split_sentences(text: str, limit: int = 320) -> list[str]:
    """Eingabetext in nummerierbare Saetze zerlegen (Absaetze/Aufzaehlungen zaehlen als Grenze)."""
    out: list[str] = []
    for para in re.split(r"\n+", text):
        for s in sentences(para):
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


_DANGLING = {"einen", "eine", "einer", "einem", "der", "die", "das", "den", "dem", "des", "und", "oder", "in", "im", "zu", "zum", "zur",
             "mit", "für", "von", "an", "auf", "bei", "ab", "bis", "um", "als", "wie", "neuen", "neue", "neuer", "a", "the", "of", "and"}


def shorten(s: str, limit: int = 70) -> str:
    """Satz auf einen Stichpunkt kuerzen: Satzzeichen weg, an Komma/Wortgrenze schneiden, keine haengenden Artikel."""
    s = s.strip().rstrip(".!?;: ")
    if len(s) <= limit:
        return s
    cut = s.rfind(", ", 0, limit)
    if cut < 20:
        cut = s.rfind(" ", 0, limit)
    toks = s[:cut if cut > 0 else limit].rstrip(", ").split(" ")
    while len(toks) > 2 and toks[-1].lower() in _DANGLING:
        toks.pop()
    return " ".join(toks)


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

    clauses = [x.strip() for sent in cited for x in re.split(r",\s+|\s+(?:und|sowie|and|oder)\s+", sent) if len(x.strip()) >= 10]
    if len(clauses) >= 2 and len(cited) == 1:
        for part in clauses:                # ein Quellsatz: dessen Teilsaetze
            add(part)
    for sent in cited:                      # sonst/danach: ganze (gekuerzte) Quellsaetze
        if len(out) < 2:
            add(sent)
    for part in clauses:
        if len(out) < 2:
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


# ---- Satzweiser Beleg-Filter (deterministisch, kein LLM) -------------------------------------------------
# Jeder Satz des Erzaehltexts (und jeder Stichpunkt) muss durch die zitierten Quellsaetze gedeckt sein:
# Inhaltswoerter (>= 4 Buchstaben, keine Funktionswoerter) muessen dort -- auch gebeugt oder als Teil eines
# Kompositums -- vorkommen. Hoechstens ein Drittel (neue Verben/Adjektive, leichtes Umformulieren); neue Substantive/Namen nie. Wertende Woerter,
# Ratschlaege und Anrede sind nie erlaubt, wenn die Quelle sie nicht selbst enthaelt.
_STOP = set("""
dies diese dieser dieses diesem diesen eine einen einem einer eines sind wird werden wurde wurden worden haben hatte hatten
kann kann koennen konnte soll sollen muss muessen auch nicht noch schon sehr oder aber wenn dass damit sowie ueber unter
durch nach bevor waehrend ihre ihrer ihren ihrem ihres seine seiner seinen seinem dabei dazu hier dort dann also weil denn
jede jeder jedes jeden mehr viel wieder zwischen gegen ohne zudem ausserdem somit daher deshalb dafuer dabei davon
dessen deren welche welcher welches welchen wobei womit worauf sich sein seit bereits etwa rund bis ab von vom zum zur
that this with from have been were which their there also about into your will would could should them they what when
moeglich erfolgen erfolgt erfolgte gibt geben steht stehen gilt gelten bietet bieten findet finden liegt liegen kommt kommen
""".split())
_EVAL = tuple("""gut best bess optim ideal perfek hervorr ausgez sicher bequem komfort einfach guenst toll grossart wichtig empfehl
vorteil zuverl professionell entspannt sorgenfrei bitte rechtzeitig unbedingt strikt problemlos praktisch attraktiv beliebt
erfolgreich einzigartig moderne spannend gluecklich fantastisch ideal excellent great perfect best easy convenient""".split())
NOVEL_SHARE_MAX = 0.34


def _words(s: str) -> list[str]:
    return [w for w in norm(s).split() if len(w) >= 4 and w not in _STOP]


def _variants(w: str) -> set[str]:
    v = {w}
    if len(w) >= 7 and w.startswith("ge"):
        v.add(w[2:])
    return v


def _covered(w: str, src_words: set[str]) -> bool:
    """Wort durch Quelle gedeckt: gleiche Wortanfaenge (Flexion), Teil eines Kompositums oder Kompositum aus Quellwort."""
    for x in _variants(w):
        for sw in src_words:
            for y in _variants(sw):
                if x == y or (len(x) >= 5 and len(y) >= 5 and x[:4] == y[:4]):
                    return True
                if len(y) >= 4 and (y in x or (len(x) >= 5 and x in y)):
                    return True
    return False


def novel_words(sentence: str, sources_text: str, extra_ok: str = "") -> tuple[list[str], list[str]]:
    """(neue Inhaltswoerter, davon der Grund zur Ablehnung). Leere Listen = Satz ist gedeckt.
    Abgelehnt wird bei: wertendem/ratgebendem Wort, neuem Substantiv/Namen (gross, nicht Satzanfang) oder zu vielen neuen Woertern."""
    src = set(_words(sources_text + " " + extra_ok))
    raw = re.findall(r"[^\W\d_]+", nfc(sentence))
    novel, nouns = [], []
    for k, w in enumerate(raw):
        nw = norm(w)
        if len(nw) < 4 or nw in _STOP or _covered(nw, src):
            continue
        novel.append(nw)
        if k > 0 and w[:1].isupper():
            nouns.append(nw)
    n = len(_words(sentence))
    evaluative = [w for w in novel if w.startswith(_EVAL)]
    allowed = max(1, int(NOVEL_SHARE_MAX * n)) if n >= 3 else 0
    bad = evaluative or nouns or len(novel) > allowed
    return (novel, evaluative or nouns or novel) if bad else ([], [])


def sentence_ok(sentence: str, sources_text: str, extra_ok: str = "") -> bool:
    novel, _ = novel_words(sentence, sources_text, extra_ok)
    return not novel and not (numbers(sentence) - numbers(sources_text) - numbers(extra_ok))


def merge_alternatives(kept: list[str], source: str) -> list[str]:
    """Aufeinanderfolgende Saetze, die sich nur durch EINE Alternative unterscheiden ("... im Hafenbüro möglich." /
    "... telefonisch möglich."), zu einem Satz mit "oder"/"und" verbinden -- nur wenn genau diese Verbindung
    ("im Hafenbüro oder telefonisch") im Quelltext steht."""
    src = norm(source)
    out: list[str] = []
    for sent in kept:
        if out:
            a, b = out[-1].rstrip(".!?").split(), sent.rstrip(".!?").split()
            i = 0
            while i < min(len(a), len(b)) and a[i] == b[i]:
                i += 1
            j = 0
            while j < min(len(a), len(b)) - i and a[-1 - j] == b[-1 - j]:
                j += 1
            da, db = a[i:len(a) - j], b[i:len(b) - j]
            if i + j >= 3 and 1 <= len(da) <= 3 and 1 <= len(db) <= 3 and da != db:
                for conj in ("oder", "und"):
                    if f"{norm(' '.join(da))} {conj} {norm(' '.join(db))}" in src:
                        out[-1] = " ".join(a[:i] + da + [conj] + db + a[len(a) - j:]) + out[-1][len(out[-1].rstrip(".!?")):]
                        break
                else:
                    out.append(sent)
                continue
        out.append(sent)
    return out


def ground_narration(nar: str, cited: list[str], title: str) -> tuple[str, list[dict], list[str]]:
    """Behaelt nur gedeckte Saetze. Bleibt zu wenig uebrig, wird die Quelle selbst (woertlich) gesprochen.
    Rueckgabe: (Erzaehltext, [{text, source}], entfernte Saetze)."""
    src = " ".join(cited)
    kept, removed = [], []
    for sent in sentences(nar):
        (kept if sentence_ok(sent, src, title) else removed).append(sent)
    kept = merge_alternatives(kept, src)
    text = " ".join(kept)
    if word_count(text) < 6:
        text, kept = "", []
        for c in cited:
            if word_count(" ".join([text, c])) <= 60:
                text = (text + " " + c).strip()
                kept.append(c)
        if word_count(text) < 6:       # extrem kurzer Quellsatz: trotzdem nur Quelle
            text, kept = " ".join(cited)[:400], list(cited)
    mapping = []
    for sent in kept:
        ws = set(_words(sent))
        best = max(range(len(cited)), key=lambda k: len(ws & set(_words(cited[k]))))
        mapping.append({"text": sent, "source": best})
    return text, mapping, removed


@dataclass
class Report:
    words: int = 0
    scenes: int = 0
    claims: int = 0
    max_novel_ratio: float = 0.0
    min_words: int = 0
    max_words: int = 0
    warnings: list[str] = field(default_factory=list)
    removed: list = field(default_factory=list)


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
    removed_all: list[dict] = []
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
        cited_text = " ".join(cited)
        nar, mapping, gone = ground_narration(nar, cited, cfg.title)
        if gone:
            removed_all += [{"scene": i + 1, "sentence": x} for x in gone]
        if not sentence_ok(head, cited_text, cfg.title):
            head = next((b for b in bl if isinstance(b, str) and 3 <= len(b.strip()) <= 60 and sentence_ok(b, cited_text, cfg.title)), None) or shorten(cited[0], 60)
            rep.warnings.append(f"Szene {i + 1}: Überschrift war nicht im Text belegt und wurde aus dem Quellsatz abgeleitet")
        good_b = [b for b in bl if sentence_ok(b, cited_text, cfg.title)]
        if len(good_b) != len(bl):
            fixed = repair_bullets(good_b, cited)
            if not fixed:
                p.append(f"{tag}: bullets nicht belegbar"); continue
            bl = fixed
        total += word_count(nar)
        rep.claims += len(cited)
        cl = cited
        out.append({"narration": nfc(re.sub(r"\s+", " ", nar).strip()), "headline": nfc(head.strip()),
                    "bullets": [nfc(b.strip()) for b in bl], "claims": cl, "sources": list(dict.fromkeys(src)),
                    "narration_sources": mapping})
    lo, hi, limited = bounds(cfg.length_s, cfg.text)
    rep.words, rep.scenes, rep.min_words, rep.max_words = total, len(scenes), lo, hi
    if total < lo:
        p.append(f"Erzähltext zu kurz: {total} Wörter, mindestens {lo}")
    if total > hi:
        p.append(f"Erzähltext zu lang: {total} Wörter, höchstens {hi}")
    if p:
        raise ScriptProblems(p)
    rep.removed = removed_all
    if removed_all:
        rep.warnings.append(f"{len(removed_all)} Satz/Sätze entfernt, die nicht im Text belegt waren (keine Erfindungen)")
    if limited:
        rep.warnings.append("Video kürzer als gewaehlt: der Text gibt für mehr Inhalt nicht genug her (keine Erfindungen)")
    return {"title": nfc(title.strip()), "scenes": out}, rep
