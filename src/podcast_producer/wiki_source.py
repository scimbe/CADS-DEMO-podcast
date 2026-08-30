"""Fetch a factual source extract for a topic from the German Wikipedia.

This is the *grounding* stage for the Wikipedia-topic podcast: it retrieves
the REST summary for a page title and returns its `extract` verbatim, to be
used as the ONLY factual source for the generated script. It never invents
content: if the page is a disambiguation page, or has no usable extract, or
does not exist, it fails loudly (WikiSourceError) rather than guessing.

Uses only the Python standard library (urllib) — no extra dependency.

Wikipedia text is licensed CC BY-SA 4.0; the returned provenance records the
page URL and license so downstream output can attribute it correctly.
"""

from __future__ import annotations

import argparse
import sys
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

WIKI_LANG = "de"
SUMMARY_API = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
USER_AGENT = "CADS-Demo-Podcast/1.0"
LICENSE = "CC BY-SA 4.0"


class WikiSourceError(RuntimeError):
    """Raised when no trustworthy factual extract can be obtained."""


@dataclass
class WikiSource:
    """A verbatim factual extract plus its provenance."""

    query: str
    title: str
    description: str
    extract: str
    url: str
    lang: str
    license: str
    fetched_at: str

    def to_dict(self) -> dict:
        return asdict(self)


def _api_url(title: str, lang: str) -> str:
    # The REST API accepts the title as a single path segment; encode it so
    # spaces, slashes and non-ASCII characters survive intact.
    quoted = urllib.parse.quote(title.strip().replace(" ", "_"), safe="")
    return SUMMARY_API.format(lang=lang, title=quoted)


def fetch_summary(topic: str, *, lang: str = WIKI_LANG, timeout: float = 15.0) -> WikiSource:
    """Fetch and validate the Wikipedia REST summary for `topic`.

    Raises WikiSourceError for a missing page (404), a disambiguation page,
    or an empty/absent extract — never returns invented text.
    """
    if not topic or not topic.strip():
        raise WikiSourceError("empty topic")

    url = _api_url(topic, lang)
    req = urllib.request.Request(
        url,
        headers={"accept": "application/json", "user-agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise WikiSourceError(
                f"no {lang}.wikipedia.org page found for {topic!r} (HTTP 404). "
                f"Try a more specific or correctly-spelled title."
            ) from exc
        raise WikiSourceError(
            f"Wikipedia request for {topic!r} failed: HTTP {exc.code}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise WikiSourceError(
            f"could not fetch Wikipedia summary for {topic!r}: {exc}"
        ) from exc

    page_type = payload.get("type")
    if page_type == "disambiguation":
        raise WikiSourceError(
            f"{topic!r} is a Wikipedia disambiguation page — it names no single "
            f"topic, so there is no factual extract to ground a podcast in. "
            f"Pass a more specific title."
        )

    extract = (payload.get("extract") or "").strip()
    if not extract:
        raise WikiSourceError(
            f"the Wikipedia page for {topic!r} has no usable summary extract; "
            f"refusing to invent content."
        )

    content_urls = payload.get("content_urls") or {}
    page_url = (
        content_urls.get("desktop", {}).get("page")
        or f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(payload.get('title', topic).replace(' ', '_'))}"
    )

    return WikiSource(
        query=topic,
        title=payload.get("title", topic),
        description=(payload.get("description") or "").strip(),
        extract=extract,
        url=page_url,
        lang=lang,
        license=LICENSE,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def _cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topic", help="Wikipedia page title / topic name")
    parser.add_argument("--lang", default=WIKI_LANG)
    args = parser.parse_args()
    try:
        src = fetch_summary(args.topic, lang=args.lang)
    except WikiSourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(src.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _cli()
