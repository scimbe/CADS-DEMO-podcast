"""Unit-Tests: Skript-Validierung (Szenenzahl, Laenge, Belegpflicht/Grounding) und Config."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from evgen import config as C
from evgen import grounding as G
from evgen import pipeline, script, video
from evgen.errors import ConfigError

TEXT = ("Ein Regenbogen entsteht, wenn Sonnenlicht auf Regentropfen trifft. Das Licht wird im Tropfen gebrochen und reflektiert. "
        "Weisses Licht besteht aus mehreren Farben und jede Farbe wird unterschiedlich stark gebrochen. "
        "Der Hauptregenbogen erscheint unter einem Winkel von etwa 42 Grad. Der Nebenbogen erscheint unter etwa 51 Grad.")
CFG = C.parse({"text": TEXT, "length": 30, "lang": "de"})


def scene(n, **kw):
    s = {"headline": "Thema " + "ABCDEFGH"[n % 8], "bullets": ["Sonnenlicht und Regentropfen", "Licht wird gebrochen"],
         "narration": "Sonnenlicht trifft auf Regentropfen und wird dabei im Tropfen gebrochen und reflektiert, so entsteht der Regenbogen.",
         "sources": [1, 2]}
    s.update(kw)
    return s


def good():
    return {"title": "Der Regenbogen", "scenes": [scene(i) for i in range(1, 5)]}


def test_valid_script_passes_and_cites_sentences():
    out, rep = G.validate(good(), CFG)
    assert rep.scenes == 4 and rep.claims == 8
    assert out["scenes"][0]["claims"][0].startswith("Ein Regenbogen entsteht")


@pytest.mark.parametrize("n", [3, 9])
def test_scene_count_bounds(n):
    s = good(); s["scenes"] = [scene(i) for i in range(n)]
    with pytest.raises(G.ScriptProblems, match="Szenen"):
        G.validate(s, CFG)


def test_missing_or_bad_sources_rejected():
    s = good(); s["scenes"][1]["sources"] = [99]
    with pytest.raises(G.ScriptProblems, match="sources"):
        G.validate(s, CFG)
    s = good(); del s["scenes"][2]["sources"]
    with pytest.raises(G.ScriptProblems, match="sources"):
        G.validate(s, CFG)


def test_invented_number_rejected():
    s = good(); s["scenes"][0]["narration"] = "Der Regenbogen ist 73 Grad gross und entsteht durch Sonnenlicht auf Regentropfen im Himmel."
    with pytest.raises(G.ScriptProblems, match="Zahlen"):
        G.validate(s, CFG)


def test_invented_content_rejected():
    s = good()
    s["scenes"][0]["narration"] = "Kolumbus entdeckte Amerika waehrend Kartoffelplantagen verwaltet wurden, sagen Historiker uebereinstimmend."
    s["scenes"][0]["bullets"] = ["Kolumbus entdeckte Amerika", "Kartoffelplantagen weltweit"]
    with pytest.raises(G.ScriptProblems, match="erfunden"):
        G.validate(s, CFG)


def test_too_long_narration_rejected():
    s = good()
    for sc in s["scenes"]:
        sc["narration"] = " ".join(["Sonnenlicht trifft auf Regentropfen und wird gebrochen"] * 6)
    with pytest.raises(G.ScriptProblems) as e:
        G.validate(s, CFG)
    assert any("zu lang" in p or "6..70" in p for p in e.value.problems)


def test_short_input_relaxes_lower_bound_and_warns():
    cfg = C.parse({"text": "Kurzer Text ueber Regenbogen und Sonnenlicht im Regen.", "length": 90})
    lo, hi, limited = G.bounds(cfg.length_s, cfg.text)
    assert limited and lo < hi


def test_problems_are_listed_for_retry_feedback():
    s = good(); s["scenes"][0]["sources"] = [99]; s["scenes"][1]["headline"] = ""
    with pytest.raises(G.ScriptProblems) as e:
        G.validate(s, CFG)
    assert len(e.value.problems) >= 2


def test_prompt_contains_numbered_sentences_and_treats_text_as_data():
    msgs = script.build_messages(CFG, ["Szene 1: Zahlen nicht ..."])
    assert "[1] Ein Regenbogen" in msgs[1]["content"] and "Daten" in msgs[1]["content"]
    assert "abgelehnt" in msgs[2]["content"]


@pytest.mark.parametrize("bad", [
    {"text": "x" * 4001}, {"text": "kurz"}, {"text": TEXT, "length": 45}, {"text": TEXT, "lang": "fr"},
    {"text": TEXT, "extra": 1}, "kein objekt"])
def test_config_rejects(bad):
    with pytest.raises(ConfigError):
        C.parse(bad)


def test_config_limits_and_defaults():
    c = C.parse({"text": "a" * 4000})
    assert (c.length_s, c.lang) == (60, "de") and len(c.text) == 4000


def test_scene_len_is_frame_aligned_and_scene_drop_respects_limit():
    assert abs(video.scene_len(7.13) * 25 - round(video.scene_len(7.13) * 25)) < 1e-9
    assert pipeline.drop_to_limit([30.0] * 6, limit=120) == 4
    assert pipeline.drop_to_limit([100.0, 100.0], limit=120, keep=2) == 2


def test_vtt_timing_is_monotonic_and_inside_scene():
    out = video.vtt([{"narration": "Erster Satz. Zweiter Satz, etwas laenger.", "start": 0.0, "speech_s": 4.0},
                     {"narration": "Dritter Satz.", "start": 5.0, "speech_s": 2.0}])
    assert out.startswith("WEBVTT") and "00:00:00.350 -->" in out and "00:00:05.350 -->" in out


UML = ("Die Größe des Tropfens bestimmt den Bogen. Auf der Rückseite wird das Licht reflektiert. Rotes Licht wird schwächer gebrochen. "
       "Violettes Licht erscheint innen und wird stärker gebrochen. Der Beobachter steht mit dem Rücken zur Sonne.")
UCFG = C.parse({"text": UML, "length": 30})


def uscene(**kw):
    s = {"headline": "Größe und Rückseite", "bullets": ["Größe des Tropfens", "Rückseite reflektiert, schwächer gebrochen"],
         "narration": "Die Größe des Tropfens bestimmt den Bogen, auf der Rückseite wird das Licht reflektiert und rotes Licht schwächer gebrochen.",
         "sources": [1, 2, 3]}
    s.update(kw)
    return s


def test_umlauts_and_eszett_roundtrip_unchanged():
    assert C.parse({"text": UML}).text == UML                      # Config laesst Text unveraendert
    scr = {"title": "Größe, Rückseite, schwächer", "scenes": [uscene() for _ in range(4)]}
    out, rep = G.validate(copy.deepcopy(scr), UCFG)
    assert out["title"] == "Größe, Rückseite, schwächer"
    assert out["scenes"][0]["headline"] == "Größe und Rückseite"
    assert "schwächer" in out["scenes"][0]["narration"] and "Rückseite" in out["scenes"][0]["bullets"][1]
    assert out["scenes"][0]["claims"][0].startswith("Die Größe des Tropfens")   # Quellsatz mit Umlauten


def test_decomposed_umlauts_are_normalised_to_nfc():
    import unicodedata
    nfd = unicodedata.normalize("NFD", "Größe, Rückseite")
    assert C.parse({"text": nfd + " " * 3 + "x" * 40}).text.startswith("Größe, Rückseite")


def test_comparison_folds_umlauts_so_ascii_source_and_umlaut_script_match():
    ascii_cfg = C.parse({"text": UML.replace("ö", "oe").replace("ü", "ue").replace("ä", "ae").replace("ß", "ss"), "length": 30})
    out, _ = G.validate({"title": "Größe", "scenes": [uscene() for _ in range(4)]}, ascii_cfg)
    assert out["scenes"][0]["headline"] == "Größe und Rückseite"        # Anzeige bleibt mit Umlauten


def test_vtt_keeps_umlauts():
    assert "Größe, Rückseite, schwächer" in video.vtt([{"narration": "Größe, Rückseite, schwächer.", "start": 0.0, "speech_s": 3.0}])


def test_scene_without_bullets_is_repaired_from_cited_sources():
    s = {"title": "Größe", "scenes": [uscene() for _ in range(4)]}
    del s["scenes"][2]["bullets"]
    s["scenes"][1]["bullets"] = ["nur einer"]
    out, rep = G.validate(s, UCFG)
    for sc in out["scenes"]:
        assert 2 <= len(sc["bullets"]) <= 4 and all(3 <= len(b) <= 70 for b in sc["bullets"])
    assert any("Szene 3" in w for w in rep.warnings) and any("Szene 2" in w for w in rep.warnings)


def test_too_many_or_too_long_bullets_are_trimmed():
    s = {"title": "Größe", "scenes": [uscene() for _ in range(4)]}
    s["scenes"][0]["bullets"] = ["Größe des Tropfens"] * 3 + ["Rückseite reflektiert " * 8, "fünf", "sechs"]
    out, _ = G.validate(s, UCFG)
    assert 2 <= len(out["scenes"][0]["bullets"]) <= 4 and all(len(b) <= 70 for b in out["scenes"][0]["bullets"])


def test_unrepairable_scene_is_rejected():
    cfg = C.parse({"text": "Kurz. " + "x" * 50, "length": 30})
    assert G.repair_bullets(None, ["Kurz."]) == []


def test_every_frame_set_has_one_image_per_bullet_plus_title():
    from evgen import render
    import tempfile
    if not (shutil_which("magick")):
        pytest.skip("ImageMagick fehlt")
    with tempfile.TemporaryDirectory() as d:
        fr = render.render_scene(Path(d), 0, 4, "Titel", "Größe und Rückseite", ["Größe", "Rückseite", "schwächer"])
        assert len(fr) == 4 and all(f.stat().st_size > 1000 for f in fr)


def shutil_which(x):
    import shutil
    return shutil.which(x)
