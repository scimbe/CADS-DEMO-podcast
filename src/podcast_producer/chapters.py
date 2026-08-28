"""Stage 3: ask the LLM for chapter markers, validate them against the real
transcript timeline in code, and write chapters.json.

The LLM's only job is to read segment text and propose chapter boundaries +
titles. It never invents audio content, and it never gets the final say on
timestamps: every returned `start_ms` is snapped to (and must be within
`snap_tolerance_ms` of) an actual whisper.cpp segment boundary. A chapter
whose timestamp doesn't match any real boundary within tolerance is rejected
and the pipeline fails loudly (ChapterValidationError), rather than silently
keeping a hallucinated boundary.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import llm_client

SYSTEM_PROMPT = """You are a podcast chapter-marking assistant.

You will receive a JSON array of transcript segments, each with:
- "start_ms": integer, milliseconds from the start of the episode
- "end_ms": integer
- "text": the words actually spoken in that segment

Your ONLY job: group these segments into chapters and give each chapter a
short, descriptive title, based strictly on the words in "text". You must
NEVER invent, add, or assume any audio content, topic, or fact that is not
present in the given segment text.

Hard rules:
1. Every chapter's "start_ms" MUST be copied exactly from one of the given
   segments' "start_ms" values. Never invent a timestamp. Never interpolate
   or round to a "nice" number.
2. The first chapter's "start_ms" MUST be the first segment's "start_ms".
3. Chapters must be in increasing "start_ms" order, each greater than the last.
4. Titles must be plain text, 3-60 characters, summarizing only what the
   segment text actually says.
5. Return between 1 and (number of segments) chapters — do not create more
   chapters than there are segments.

Respond with ONLY a JSON object of this exact shape, nothing else:
{"chapters": [{"start_ms": <int>, "title": "<string>"}, ...]}
"""


class ChapterValidationError(RuntimeError):
    pass


def _ms_to_hhmmss(ms: int) -> str:
    total_seconds = ms // 1000
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _snap_to_segment(start_ms: int, segment_starts: list[int], tolerance_ms: int) -> int:
    """Return the real segment start_ms nearest to start_ms, if within tolerance."""
    nearest = min(segment_starts, key=lambda s: abs(s - start_ms))
    if abs(nearest - start_ms) > tolerance_ms:
        raise ChapterValidationError(
            f"chapter start_ms={start_ms} is not within {tolerance_ms}ms of any "
            f"real transcript segment boundary (nearest real boundary: {nearest}ms, "
            f"diff {abs(nearest - start_ms)}ms). Refusing to keep a hallucinated "
            f"timestamp — this is a hard validation failure, not a warning."
        )
    return nearest


def _parse_llm_chapters(raw: str) -> list[dict]:
    data = json.loads(raw)
    chapters = data.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise ValueError("expected a non-empty 'chapters' array")
    for c in chapters:
        if "start_ms" not in c or "title" not in c:
            raise ValueError(f"chapter entry missing start_ms/title: {c!r}")
    return chapters


def generate_chapters(
    segments: list[dict],
    *,
    snap_tolerance_ms: int = 2000,
    max_title_len: int = 60,
) -> list[dict]:
    """Call the LLM, validate + snap its output against real segment timing.

    Raises ChapterValidationError if the LLM never produces valid, real-
    timestamped chapters (including after one retry).
    """
    if not segments:
        raise ValueError("no transcript segments to chapter-mark")

    segment_starts = [s["start_ms"] for s in segments]
    user_prompt = (
        "Transcript segments (JSON array):\n" +
        json.dumps(segments, indent=2) +
        "\n\nReturn the chapters JSON object now."
    )

    raw = llm_client.chat_json(SYSTEM_PROMPT, user_prompt)
    try:
        raw_chapters = _parse_llm_chapters(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        # One retry, echoing the parse error back to the model.
        retry_prompt = (
            user_prompt +
            f"\n\nYour previous response failed to parse: {exc}\n"
            f"Previous response was:\n{raw}\n\n"
            f"Return ONLY the corrected JSON object, no other text."
        )
        raw = llm_client.chat_json(SYSTEM_PROMPT, retry_prompt)
        raw_chapters = _parse_llm_chapters(raw)  # let this raise if still bad

    validated = []
    last_start = -1
    for c in raw_chapters:
        start_ms = int(c["start_ms"])
        title = str(c["title"]).strip()
        if not title:
            raise ChapterValidationError(f"empty title for chapter at start_ms={start_ms}")
        if len(title) > max_title_len:
            title = title[:max_title_len].rstrip()
        snapped = _snap_to_segment(start_ms, segment_starts, snap_tolerance_ms)
        if snapped <= last_start:
            continue  # drop non-increasing / duplicate chapter, keep going
        last_start = snapped
        validated.append(
            {
                "index": len(validated) + 1,
                "start_ms": snapped,
                "start_time": _ms_to_hhmmss(snapped),
                "title": title,
            }
        )

    if not validated:
        raise ChapterValidationError("no chapters survived validation")
    if validated[0]["start_ms"] != segment_starts[0]:
        raise ChapterValidationError(
            f"first chapter must start at the episode's first real segment "
            f"boundary ({segment_starts[0]}ms), got {validated[0]['start_ms']}ms"
        )
    return validated


def _cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript-json", required=True, type=Path,
                         help="whisper.cpp transcript.json")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--mock-transcript", action="store_true",
                         help="mark output as generated_from_mock_transcript")
    args = parser.parse_args()

    data = json.loads(args.transcript_json.read_text())
    segments = [
        {
            "start_ms": int(e["offsets"]["from"]),
            "end_ms": int(e["offsets"]["to"]),
            "text": e["text"].strip(),
        }
        for e in data.get("transcription", [])
    ]
    chapters = generate_chapters(segments)
    out = {"chapters": chapters, "generated_from_mock_transcript": args.mock_transcript}
    args.out.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    _cli()
