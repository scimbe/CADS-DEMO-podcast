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
