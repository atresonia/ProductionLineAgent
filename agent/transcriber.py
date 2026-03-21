"""
transcriber.py — Local audio transcription via OpenAI Whisper

Transcribes MP3/WAV/M4A incident call recordings without any API key.
Whisper runs entirely on-device.

Requirements:
    pip install openai-whisper
    brew install ffmpeg        # macOS
    apt install ffmpeg         # Ubuntu/Debian

Usage:
    from transcriber import transcribe_file, list_transcripts
"""

import os
import json
from datetime import datetime, timezone

ASSETS_DIR      = os.getenv("ASSETS_DIR", "../assets")
TRANSCRIPT_DIR  = os.path.join(ASSETS_DIR, "transcripts")
WHISPER_MODEL   = os.getenv("WHISPER_MODEL", "base")   # tiny|base|small|medium|large

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aiff", ".aac"}

# Module-level model cache — loaded once, reused across calls
_model = None


def _load_model():
    """Load the Whisper model (cached after first call)."""
    global _model
    if _model is not None:
        return _model, None

    try:
        import whisper
    except ImportError:
        return None, (
            "openai-whisper is not installed.\n"
            "Install it with:  pip install openai-whisper\n"
            "Also requires:    brew install ffmpeg  (macOS)"
        )

    try:
        _model = whisper.load_model(WHISPER_MODEL)
        return _model, None
    except Exception as e:
        return None, f"Failed to load Whisper model '{WHISPER_MODEL}': {e}"


def transcribe_file(filename: str) -> str:
    """
    Transcribe an audio file from ASSETS_DIR.

    Checks ASSETS_DIR and ASSETS_DIR/transcripts for the file.
    If a .txt transcript already exists for this file, returns it directly
    (no re-transcription needed).

    Returns plain-text transcript on success, or an error message.
    """
    # Resolve path — check assets root then transcripts subdir
    safe_name = os.path.basename(filename)
    candidates = [
        os.path.join(ASSETS_DIR, safe_name),
        os.path.join(TRANSCRIPT_DIR, safe_name),
    ]
    audio_path = next((p for p in candidates if os.path.exists(p)), None)

    if audio_path is None:
        available = _list_audio_files()
        return (
            f"[audio file not found: {safe_name}]\n"
            f"Available audio files: {available or ['(none)']}\n"
            f"Place MP3/WAV files in {ASSETS_DIR}/"
        )

    # Check for a cached transcript first
    cached = _cached_transcript_path(audio_path)
    if cached and os.path.exists(cached):
        with open(cached) as f:
            return f.read()

    # Load Whisper and transcribe
    model, err = _load_model()
    if err:
        return f"[transcription unavailable] {err}"

    try:
        result = model.transcribe(audio_path)
        text   = result.get("text", "").strip()
    except Exception as e:
        return f"[transcription failed: {e}]"

    # Save transcript to disk
    transcript = _format_transcript(safe_name, text)
    os.makedirs(TRANSCRIPT_DIR, exist_ok=True)
    out_path = cached or os.path.join(
        TRANSCRIPT_DIR,
        os.path.splitext(safe_name)[0] + ".txt"
    )
    with open(out_path, "w") as f:
        f.write(transcript)

    return transcript


def list_transcripts(query: str = "") -> str:
    """
    List all available transcripts (pre-existing .txt files and transcribed audio).
    Optionally filter by keyword — returns matching excerpts.
    """
    os.makedirs(TRANSCRIPT_DIR, exist_ok=True)
    txt_files = sorted(
        f for f in os.listdir(TRANSCRIPT_DIR) if f.endswith(".txt")
    )

    if not txt_files:
        return json.dumps({
            "transcripts": [],
            "note": (
                f"No transcripts found in {TRANSCRIPT_DIR}/. "
                "Place MP3/WAV files in the assets/ directory and call "
                "transcribe_recording() to generate transcripts."
            ),
        }, indent=2)

    results = []
    for fname in txt_files:
        path = os.path.join(TRANSCRIPT_DIR, fname)
        with open(path) as f:
            content = f.read()

        if query and query.lower() not in content.lower():
            continue

        # Pull a short excerpt (first speaker line that matches query, or first 3 lines)
        lines  = [l for l in content.splitlines() if l.strip()]
        if query:
            excerpt_lines = [l for l in lines if query.lower() in l.lower()][:3]
        else:
            excerpt_lines = lines[:3]
        excerpt = " | ".join(excerpt_lines)

        results.append({
            "file":    fname,
            "excerpt": excerpt[:300],
            "path":    path,
        })

    if query and not results:
        return json.dumps({
            "query":       query,
            "transcripts": [],
            "note":        f"No transcripts contain '{query}'",
        }, indent=2)

    return json.dumps({
        "query":       query or "(all)",
        "count":       len(results),
        "transcripts": results,
    }, indent=2)


def get_transcript_content(filename: str) -> str:
    """Read the full content of a saved transcript file."""
    safe_name = os.path.basename(filename)
    if not safe_name.endswith(".txt"):
        safe_name = os.path.splitext(safe_name)[0] + ".txt"

    path = os.path.join(TRANSCRIPT_DIR, safe_name)
    if not os.path.exists(path):
        return f"[transcript not found: {safe_name}]"

    with open(path) as f:
        return f.read()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _cached_transcript_path(audio_path: str) -> str:
    """Return the expected .txt path for a given audio file."""
    base = os.path.splitext(os.path.basename(audio_path))[0]
    return os.path.join(TRANSCRIPT_DIR, base + ".txt")


def _list_audio_files() -> list[str]:
    """List audio files available in ASSETS_DIR."""
    if not os.path.isdir(ASSETS_DIR):
        return []
    return [
        f for f in os.listdir(ASSETS_DIR)
        if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS
    ]


def _format_transcript(source_filename: str, raw_text: str) -> str:
    """Wrap raw Whisper output with metadata header."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"Source: {source_filename}\n"
        f"Transcribed: {ts}\n"
        f"Model: whisper-{WHISPER_MODEL}\n\n"
        f"---\n\n"
        f"{raw_text}"
    )
