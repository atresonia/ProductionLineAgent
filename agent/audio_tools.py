"""
audio_tools.py — Audio transcription and past transcript retrieval

Two modes:
  1. transcribe_recording(path)  — run Whisper on an MP3/WAV file, cache result
  2. get_past_transcripts(query) — search pre-cached transcripts in data/audio_transcripts/

For the hackathon demo, pre-transcribed text files are in data/audio_transcripts/.
If whisper is installed and an actual audio file is provided, it will transcribe it live.
"""

import json
import os

DATA_DIR           = os.getenv("DATA_DIR", "../data")
TRANSCRIPTS_DIR    = os.path.join(DATA_DIR, "audio_transcripts")


def transcribe_recording(audio_path: str) -> str:
    """
    Transcribe an audio recording (MP3/WAV) of a past incident call.
    Uses OpenAI Whisper locally — no API key required.

    If a matching .txt transcript already exists in data/audio_transcripts/,
    returns the cached version. Otherwise runs Whisper.
    """
    if not audio_path:
        return "[no audio path provided]"

    # Check for cached transcript first
    basename = os.path.splitext(os.path.basename(audio_path))[0]
    cached_path = os.path.join(TRANSCRIPTS_DIR, f"{basename}.txt")
    if os.path.exists(cached_path):
        with open(cached_path) as f:
            content = f.read()
        return f"[Cached transcript for {basename}]\n\n{content}"

    # Try to run Whisper if installed
    try:
        import whisper  # type: ignore
    except ImportError:
        return (
            f"[whisper not installed — run: pip install openai-whisper]\n"
            f"Looked for cached transcript at: {cached_path}\n"
            f"To add a pre-transcribed version, save the transcript as {cached_path}"
        )

    if not os.path.exists(audio_path):
        return f"[audio file not found: {audio_path}]"

    try:
        model = whisper.load_model("base")
        result = model.transcribe(audio_path)
        transcript = result["text"]

        # Cache it
        os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)
        with open(cached_path, "w") as f:
            f.write(f"WHISPER TRANSCRIPTION\nSource: {audio_path}\n\n---\n\n{transcript}")

        return f"[Transcribed {audio_path}]\n\n{transcript}"
    except Exception as e:
        return f"[Whisper transcription failed: {e}]"


def get_past_transcripts(query: str) -> str:
    """
    Search pre-transcribed incident call recordings for relevant content.
    These are plain-text transcripts of audio from past incident calls —
    they contain institutional knowledge that was never formally documented.
    """
    if not os.path.isdir(TRANSCRIPTS_DIR):
        return "[no audio transcripts directory found at data/audio_transcripts/]"

    files = [f for f in os.listdir(TRANSCRIPTS_DIR) if f.endswith(".txt")]
    if not files:
        return "[no transcripts found in data/audio_transcripts/]"

    query_terms = query.lower().split()
    scored = []

    for fname in sorted(files):
        fpath = os.path.join(TRANSCRIPTS_DIR, fname)
        try:
            with open(fpath) as f:
                content = f.read()
        except OSError:
            continue
        text  = content.lower()
        score = sum(1 for term in query_terms if term in text)
        if score > 0:
            scored.append((score, fname, content))

    scored.sort(reverse=True, key=lambda x: x[0])

    if not scored:
        return f"[no transcripts matching '{query}']"

    results = []
    for score, fname, content in scored[:2]:
        preview = content[:1000]
        if len(content) > 1000:
            preview += "\n... [truncated]"
        results.append(
            f"=== {fname} (relevance: {score} term matches) ===\n{preview}"
        )

    return f"Found {len(results)} relevant transcript(s):\n\n" + "\n\n".join(results)
