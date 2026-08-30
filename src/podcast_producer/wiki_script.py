"""Turn a verbatim Wikipedia extract into a short, natural podcast script.

The extract from `wiki_source` is the ONLY factual source. Two paths produce
a script from it:

1. LLM reformulation (default, if an LLM endpoint is configured): the model
   rewrites the extract as a short Host/Gast dialogue. Its output is only
   trusted if it passes a **number guard** — every numeric token present in
   the source extract (years, populations, ordinals, ...) must still appear
   in the generated script. If any source number is missing, the LLM output
   is discarded and the deterministic fallback is used instead. This stops
   the model from silently dropping or altering a figure.

2. Deterministic fallback (no LLM, or guard failed): a template dialogue
   that frames the source and reads its sentences close to verbatim, so no
   fact can be distorted.

The source URL/license is carried through into the returned provenance.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field

from . import llm_client, wiki_source

HOST = "Host"
GUEST = "Gast"

# A numeric token: a run of digits, optionally with German thousands/decimal
# separators or a trailing ordinal dot (e.g. "251.842", "1900", "13.", "1,5").
_NUMBER_RE = re.compile(r"\d[\d.,]*")

SYSTEM_PROMPT = """Du bist Autor:in für einen kurzen, seriösen Wissens-Podcast.

Du bekommst einen FAKTENTEXT (einen Auszug aus der deutschen Wikipedia). Das
ist deine EINZIGE Faktenquelle. Schreibe daraus ein kurzes, natürliches
Gespräch zwischen zwei Stimmen: "Host" (moderiert, stellt kurze Fragen) und
"Gast" (erklärt).

Absolute Regeln:
1. Erfinde NICHTS. Verwende ausschließlich Fakten, Namen, Orte und Zahlen,
   die wörtlich im Faktentext stehen. Keine Zusatzinfos aus deinem Wissen.
2. Alle Zahlen (Jahre, Einwohnerzahlen, Mengen usw.) müssen exakt so
   übernommen werden, wie sie im Faktentext stehen. Verändere, runde oder
   ergänze keine Zahl.
3. Halte es kurz: 4 bis 8 Gesprächsbeiträge insgesamt.
4. Deutsch, natürlicher gesprochener Ton, keine Bühnenanweisungen.

Antworte mit NUR diesem JSON-Objekt, sonst nichts:
{"turns": [{"speaker": "Host"|"Gast", "text": "<string>"}, ...]}
"""


@dataclass
class PodcastScript:
    title: str
    turns: list[dict]
    generation: str  # "llm" or "verbatim-fallback"
    provenance: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "turns": self.turns,
            "generation": self.generation,
            "provenance": self.provenance,
        }

    def as_plaintext(self) -> str:
        lines = [f"# {self.title}", ""]
        for t in self.turns:
            lines.append(f"{t['speaker']}: {t['text']}")
        return "\n".join(lines) + "\n"


def numbers_in(text: str) -> set[str]:
    """Return the set of numeric tokens in `text`, normalised of trailing dots.

    A trailing '.' (German ordinal, e.g. "13.") and trailing separators are
    stripped so "13." and "13" compare equal; interior separators are kept
    ("251.842" stays distinct from "251").
    """
    tokens = set()
    for m in _NUMBER_RE.findall(text):
        tok = m.rstrip(".,")
        if tok:
            tokens.add(tok)
    return tokens


def number_guard(source_text: str, generated_text: str) -> tuple[bool, set[str]]:
    """Check that every numeric token in the source appears in the generation.

    Returns (ok, missing_tokens).
    """
    src = numbers_in(source_text)
    gen = numbers_in(generated_text)
    missing = {n for n in src if n not in gen}
    return (not missing, missing)


def _sentences(text: str) -> list[str]:
    """Split an extract into sentences without breaking on common abbrevs."""
    # Protect a few German abbreviations that end in a dot mid-sentence.
    protected = text
    for abbr in ("z. B.", "u. a.", "d. h.", "bzw.", "ca.", "Nr.", "St.", "v. Chr.", "n. Chr."):
        protected = protected.replace(abbr, abbr.replace(" ", " ").replace(".", "․"))
    # Protect ordinal-number dots (e.g. "13. Jahrhundert", "1. Mai") so they
    # are not mistaken for sentence ends.
    protected = re.sub(r"(\d)\.(\s)", r"\1․\2", protected)
    parts = re.split(r"(?<=[.!?])\s+", protected)
    out = []
    for p in parts:
        p = p.replace("․", ".").replace(" ", " ").strip()
        if p:
            out.append(p)
    return out


def build_fallback_script(src: wiki_source.WikiSource) -> PodcastScript:
    """Deterministic, number-faithful script built only from the extract."""
    sentences = _sentences(src.extract)
    turns: list[dict] = [
        {"speaker": HOST,
         "text": f"Willkommen zu einer kurzen Folge. Unser Thema heute: {src.title}. "
                 f"Alle Angaben stammen aus der deutschen Wikipedia."},
    ]
    if src.description:
        turns.append({"speaker": HOST,
                      "text": f"Worum geht es dabei in einem Satz?"})
        turns.append({"speaker": GUEST,
                      "text": f"Kurz gesagt: {src.description}."})
    # Read the source sentences as the guest, close to verbatim.
    for i, sentence in enumerate(sentences):
        if i == 0 and not src.description:
            turns.append({"speaker": HOST, "text": "Erzähl mal, worum geht es?"})
        turns.append({"speaker": GUEST, "text": sentence})
    turns.append({"speaker": HOST,
                  "text": f"Mehr dazu steht im Wikipedia-Artikel zu {src.title}. "
                          f"Danke fürs Zuhören."})
    return PodcastScript(
        title=src.title,
        turns=turns,
        generation="verbatim-fallback",
        provenance=_provenance(src, generation="verbatim-fallback", number_guard="n/a (verbatim)"),
    )


def _provenance(src: wiki_source.WikiSource, *, generation: str,
                number_guard: str, llm_model: str | None = None) -> dict:
    prov = {
        "source": f"Wikipedia ({src.lang})",
        "title": src.title,
        "url": src.url,
        "license": src.license,
        "fetched_at": src.fetched_at,
        "extract": src.extract,
        "generation": generation,
        "number_guard": number_guard,
    }
    if llm_model:
        prov["llm_model"] = llm_model
    return prov


def _parse_turns(raw: str) -> list[dict]:
    data = json.loads(raw)
    turns = data.get("turns")
    if not isinstance(turns, list) or not turns:
        raise ValueError("expected a non-empty 'turns' array")
    out = []
    for t in turns:
        speaker = str(t.get("speaker", "")).strip()
        text = str(t.get("text", "")).strip()
        if not text:
            raise ValueError(f"turn with empty text: {t!r}")
        # Normalise speaker to exactly Host/Gast.
        speaker = HOST if speaker.lower().startswith("host") else GUEST
        out.append({"speaker": speaker, "text": text})
    return out


def build_script(src: wiki_source.WikiSource, *, use_llm: bool = True) -> PodcastScript:
    """Produce a podcast script from a Wikipedia source.

    With `use_llm`, attempt LLM reformulation and accept it only if it passes
    the number guard; otherwise (or on any LLM error) fall back to the
    deterministic, number-faithful template.
    """
    if not use_llm:
        return build_fallback_script(src)

    user_prompt = (
        "FAKTENTEXT (deine einzige Quelle):\n\"\"\"\n"
        + src.extract
        + "\n\"\"\"\n\nGib jetzt das JSON-Objekt mit den Gesprächsbeiträgen zurück."
    )
    try:
        raw = llm_client.chat_json(SYSTEM_PROMPT, user_prompt)
        turns = _parse_turns(raw)
    except llm_client.LlmConfigError as exc:
        print(f"[wiki_script] no LLM configured ({exc}); using verbatim fallback")
        return build_fallback_script(src)
    except Exception as exc:  # noqa: BLE001 - any LLM/parse failure -> safe fallback
        print(f"[wiki_script] LLM script generation failed ({exc}); using verbatim fallback")
        return build_fallback_script(src)

    generated_text = " ".join(t["text"] for t in turns)
    ok, missing = number_guard(src.extract, generated_text)
    if not ok:
        print(f"[wiki_script] number guard FAILED — source numbers missing from "
              f"LLM output: {sorted(missing)}. Keeping the verbatim fallback instead.")
        return build_fallback_script(src)

    model = None
    try:
        _, model = llm_client.get_client()
    except Exception:  # noqa: BLE001 - model name is best-effort metadata
        pass
    return PodcastScript(
        title=src.title,
        turns=turns,
        generation="llm",
        provenance=_provenance(src, generation="llm",
                               number_guard="passed", llm_model=model),
    )


def _cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topic", help="Wikipedia page title / topic name")
    parser.add_argument("--lang", default=wiki_source.WIKI_LANG)
    parser.add_argument("--no-llm", action="store_true",
                         help="skip the LLM, use the deterministic verbatim script")
    args = parser.parse_args()
    src = wiki_source.fetch_summary(args.topic, lang=args.lang)
    script = build_script(src, use_llm=not args.no_llm)
    print(json.dumps(script.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _cli()
