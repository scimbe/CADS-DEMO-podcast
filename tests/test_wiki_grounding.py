"""Offline unit tests for the Wikipedia-topic podcast grounding.

No network and no real LLM: `urlopen` and `llm_client.chat_json` are
monkeypatched. These tests pin the two safety-critical behaviours:

  * wiki_source refuses disambiguation / empty / missing pages (never invents)
  * wiki_script's number guard keeps the verbatim fallback whenever the LLM
    drops or alters a source number.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from podcast_producer import wiki_script, wiki_source


# --------------------------------------------------------------------------
# number guard + sentence splitting (pure logic)
# --------------------------------------------------------------------------

def test_numbers_in_normalises_ordinal_and_keeps_separators():
    nums = wiki_script.numbers_in("Mit 251.842 Einwohnern, im 13. Jahrhundert, 1900.")
    assert nums == {"251.842", "13", "1900"}


def test_number_guard_passes_when_all_present():
    ok, missing = wiki_script.number_guard("1919 bis 1933", "Von 1919 bis 1933.")
    assert ok and missing == set()


def test_number_guard_fails_on_dropped_number():
    ok, missing = wiki_script.number_guard(
        "251.842 Einwohner im 13. Jahrhundert", "rund 250.000 Einwohner im Mittelalter"
    )
    assert not ok
    assert "251.842" in missing and "13" in missing


def test_sentences_does_not_split_ordinal_dot():
    sents = wiki_script._sentences("Gegründet im 13. Jahrhundert. Heute Großstadt.")
    assert sents == ["Gegründet im 13. Jahrhundert.", "Heute Großstadt."]


# --------------------------------------------------------------------------
# wiki_source.fetch_summary validation (monkeypatched HTTP)
# --------------------------------------------------------------------------

def _fake_urlopen(payload: dict):
    def _open(req, timeout=None):
        body = json.dumps(payload).encode("utf-8")
        return io.BytesIO(body)
    return _open


def test_fetch_summary_standard_page(monkeypatch):
    payload = {
        "type": "standard",
        "title": "Kiel",
        "description": "Landeshauptstadt",
        "extract": "Kiel ist die Landeshauptstadt Schleswig-Holsteins.",
        "content_urls": {"desktop": {"page": "https://de.wikipedia.org/wiki/Kiel"}},
    }
    monkeypatch.setattr(wiki_source.urllib.request, "urlopen", _fake_urlopen(payload))
    src = wiki_source.fetch_summary("Kiel")
    assert src.title == "Kiel"
    assert src.extract.startswith("Kiel ist die Landeshauptstadt")
    assert src.url == "https://de.wikipedia.org/wiki/Kiel"
    assert src.license == "CC BY-SA 4.0"


def test_fetch_summary_disambiguation_aborts(monkeypatch):
    payload = {"type": "disambiguation", "title": "Merkur", "extract": "…"}
    monkeypatch.setattr(wiki_source.urllib.request, "urlopen", _fake_urlopen(payload))
    with pytest.raises(wiki_source.WikiSourceError, match="disambiguation"):
        wiki_source.fetch_summary("Merkur")


def test_fetch_summary_empty_extract_aborts(monkeypatch):
    payload = {"type": "standard", "title": "Leer", "extract": "   "}
    monkeypatch.setattr(wiki_source.urllib.request, "urlopen", _fake_urlopen(payload))
    with pytest.raises(wiki_source.WikiSourceError, match="no usable summary"):
        wiki_source.fetch_summary("Leer")


def test_fetch_summary_404_aborts(monkeypatch):
    def _raise(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    monkeypatch.setattr(wiki_source.urllib.request, "urlopen", _raise)
    with pytest.raises(wiki_source.WikiSourceError, match="404"):
        wiki_source.fetch_summary("Kein_Artikel_xyz")


# --------------------------------------------------------------------------
# build_script: LLM path vs. number-guard fallback (monkeypatched LLM)
# --------------------------------------------------------------------------

def _src() -> wiki_source.WikiSource:
    return wiki_source.WikiSource(
        query="Kiel",
        title="Kiel",
        description="Landeshauptstadt",
        extract="Mit 251.842 Einwohnern. Im 13. Jahrhundert gegründet, 1900 Großstadt.",
        url="https://de.wikipedia.org/wiki/Kiel",
        lang="de",
        license="CC BY-SA 4.0",
        fetched_at="2026-01-01T00:00:00+00:00",
    )


def test_build_script_accepts_llm_when_numbers_preserved(monkeypatch):
    good = json.dumps({"turns": [
        {"speaker": "Host", "text": "Wie groß ist Kiel?"},
        {"speaker": "Gast", "text": "251.842 Einwohner, im 13. Jahrhundert gegründet, 1900 Großstadt."},
    ]})
    monkeypatch.setattr(wiki_script.llm_client, "chat_json", lambda s, u, **k: good)
    monkeypatch.setattr(wiki_script.llm_client, "get_client", lambda: (object(), "test-model"))
    script = wiki_script.build_script(_src(), use_llm=True)
    assert script.generation == "llm"
    assert script.provenance["number_guard"] == "passed"


def test_build_script_falls_back_when_llm_drops_a_number(monkeypatch):
    bad = json.dumps({"turns": [
        {"speaker": "Host", "text": "Wie groß ist Kiel?"},
        {"speaker": "Gast", "text": "Rund 250.000 Einwohner, im Mittelalter gegründet, 1900 Großstadt."},
    ]})
    monkeypatch.setattr(wiki_script.llm_client, "chat_json", lambda s, u, **k: bad)
    script = wiki_script.build_script(_src(), use_llm=True)
    assert script.generation == "verbatim-fallback"
    # Every source number must survive in the fallback script.
    generated = " ".join(t["text"] for t in script.turns)
    ok, missing = wiki_script.number_guard(_src().extract, generated)
    assert ok, f"fallback dropped numbers: {missing}"


def test_build_script_falls_back_when_llm_errors(monkeypatch):
    def _boom(s, u, **k):
        raise RuntimeError("endpoint down")
    monkeypatch.setattr(wiki_script.llm_client, "chat_json", _boom)
    script = wiki_script.build_script(_src(), use_llm=True)
    assert script.generation == "verbatim-fallback"


def test_fallback_script_is_number_faithful(monkeypatch):
    script = wiki_script.build_script(_src(), use_llm=False)
    generated = " ".join(t["text"] for t in script.turns)
    ok, missing = wiki_script.number_guard(_src().extract, generated)
    assert ok, f"fallback dropped numbers: {missing}"
