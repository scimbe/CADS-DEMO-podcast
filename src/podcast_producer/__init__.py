"""podcast_producer: a local pipeline that orchestrates real open-source audio
tools (ffmpeg, whisper.cpp, optionally Piper) to turn raw audio tracks into an
episode master, a real transcript, and LLM-generated chapter markers.

No component of this package invents audio content. ffmpeg does all cutting
and mixing; whisper.cpp does all transcription; the LLM only reads the real
transcript text/timestamps and proposes chapter titles + boundaries, which are
then validated in code against the real segment timeline (see chapters.py).
"""

__version__ = "0.1.0"
