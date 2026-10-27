"""Regression: Live-Lauf "Nordwind Bootsservice" (Produktion) erfand Saetze, die nicht im Eingabetext stehen.
Der Eingabetext und das LLM-Skript sind exakt die aus dem Lauf; keiner der erfundenen Saetze darf durchkommen."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from evgen import config as C
from evgen import grounding as G

TEXT = ("Die Nordwind Bootsservice GmbH bietet ab März einen neuen Winterlager-Service an. "
        "Boote werden in Bremen gekrant, gereinigt und in einer beheizten Halle gelagert. "
        "Der Preis beträgt 18 Euro pro Meter Bootslänge und Monat. "
        "Anmeldung bis zum 15. Februar im Hafenbüro oder telefonisch.")
CFG = C.parse({"text": TEXT, "length": 60})

INVENTED = [
    "Dies ist eine gute Nachricht für Bootseigner, die ihre Fahrzeuge sicher lagern möchten.",
    "So sind sie optimal vor Witterungseinflüssen geschützt.",
    "Bitte planen Sie rechtzeitig, um den Platz zu sichern.",
    "Nutzen Sie die angegebenen Kanäle für Ihre Buchung.",
    "Zusammenfassend bietet der Service sichere Lagerung ab März.",
]
KEPT = [
    "Die Nordwind Bootsservice GmbH bietet ab März einen neuen Winterlager-Service an.",
    "Die Boote werden in Bremen gekrant, gereinigt und in einer beheizten Halle gelagert.",
    "Der Preis beträgt 18 Euro pro Meter Bootslänge und Monat.",
    "Die Anmeldung muss bis zum 15. Februar erfolgen.",
    "Die Anmeldung ist im Hafenbüro oder telefonisch möglich.",
]

LLM = {"title": "Winterlager-Service", "scenes": [
    {"headline": "Neuer Winterlager-Service ab März", "bullets": ["Nordwind Bootsservice bietet neuen Service", "Start ab März für Bootslagerung"], "sources": [1],
     "narration": f"{KEPT[0]} {INVENTED[0]}"},
    {"headline": "Kränen, Reinigen und Beheizen", "bullets": ["Boote werden in Bremen gekrant", "Reinigung und beheizte Halle"], "sources": [2],
     "narration": f"{KEPT[1]} {INVENTED[1]}"},
    {"headline": "Kosten: 18 Euro pro Meter", "bullets": ["Preis: 18 Euro pro Meter", "Berechnet wird monatlich"], "sources": [3],
     "narration": f"{KEPT[2]} Die Abrechnung erfolgt also monatlich basierend auf der Länge des Bootes."},
    {"headline": "Anmeldeschluss am 15. Februar", "bullets": ["Anmeldung bis zum 15. Februar", "Frist ist strikt einzuhalten"], "sources": [4],
     "narration": f"{KEPT[3]} {INVENTED[2]}"},
    {"headline": "Anmeldung im Hafenbüro", "bullets": ["Anmeldung im Hafenbüro möglich", "Auch telefonisch anfragen"], "sources": [4],
     "narration": f"{KEPT[4]} {INVENTED[3]}"},
    {"headline": "Zusammenfassung der Angebote", "bullets": ["Service startet im März", "Lagerung in beheizter Halle"], "sources": [1, 2, 3],
     "narration": f"{INVENTED[4]} Die Kosten betragen 18 Euro pro Meter und Monat."},
]}


def run():
    return G.validate(LLM, CFG)


def test_source_numbering_does_not_split_ordinals():
    assert len(G.split_sentences(TEXT)) == 4        # "15. Februar" bleibt im Satz; es gibt Satz 5 nicht


def test_no_invented_sentence_passes():
    out, rep = run()
    spoken = " ".join(s["narration"] for s in out["scenes"])
    for bad in INVENTED:
        assert bad not in spoken, bad
    for frag in ("gute Nachricht", "Bootseigner", "optimal", "Witterungseinflüssen", "rechtzeitig", "Bitte", "Nutzen Sie", "sichere Lagerung", "Fahrzeuge"):
        assert frag not in spoken, frag
    assert len(rep.removed) >= 5 and any("Witterungseinflüssen" in r["sentence"] for r in rep.removed)


def test_grounded_sentences_are_kept_verbatim():
    out, _ = run()
    spoken = " ".join(s["narration"] for s in out["scenes"])
    for ok in (KEPT[0], KEPT[1], KEPT[2]):
        assert ok.replace("Die Boote", "Boote") in spoken or ok in spoken, ok


def test_every_spoken_sentence_maps_to_a_cited_source():
    out, _ = run()
    for sc in out["scenes"]:
        assert sc["narration_sources"], sc["headline"]
        for m in sc["narration_sources"]:
            assert 0 <= m["source"] < len(sc["claims"])
            assert G.sentence_ok(m["text"], " ".join(sc["claims"]), TEXT[:0] + "Winterlager-Service")


def test_bullets_and_headlines_are_grounded_and_still_2_to_4():
    out, _ = run()
    for sc in out["scenes"]:
        assert 2 <= len(sc["bullets"]) <= 4
        joined = " ".join(sc["bullets"])
        assert "strikt" not in joined and "Berechnet" not in joined
        assert G.sentence_ok(sc["headline"], TEXT, "")


def test_fully_invented_scene_falls_back_to_the_source_itself():
    s = {k: v for k, v in LLM.items()}
    s["scenes"] = [dict(x) for x in LLM["scenes"]]
    s["scenes"][5]["narration"] = " ".join(INVENTED[:2]) + " Das ist ideal für alle Hobbykapitäne."
    out, _ = G.validate(s, CFG)
    last = out["scenes"][5]["narration"]
    assert "ideal" not in last and "Witterung" not in last
    assert "Nordwind" in last or "Boote" in last or "Preis" in last      # woertlich aus den zitierten Quellsaetzen


@pytest.mark.parametrize("sentence", INVENTED)
def test_invented_sentences_fail_the_filter_directly(sentence):
    assert not G.sentence_ok(sentence, TEXT)


@pytest.mark.parametrize("sentence", KEPT + ["Der Service startet im März.", "Boote werden in Bremen gekrant und gereinigt."])
def test_light_paraphrase_passes_the_filter(sentence):
    assert G.sentence_ok(sentence, TEXT)


def test_invented_number_is_dropped():
    assert not G.sentence_ok("Der Preis beträgt 25 Euro pro Meter Bootslänge und Monat.", TEXT)


def test_evaluative_word_is_blocked_even_if_it_is_the_only_new_word():
    assert not G.sentence_ok("Boote werden in Bremen gekrant, gereinigt und in einer beheizten Halle optimal gelagert.", TEXT)
    assert G.sentence_ok("Boote werden sicher gelagert.", "Boote werden sicher gelagert.")   # steht so in der Quelle
