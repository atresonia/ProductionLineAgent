"""
meeting_bot.py — Recall.ai meeting bot integration

Platform-agnostic transcription for Zoom, Microsoft Teams, Google Meet,
Webex, and Slack Huddles through a single API.  The bot joins the meeting,
streams real-time transcript chunks to the webhook server, and on meeting
end the full speaker-labelled transcript is written to ASSETS_DIR.
"""

import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone

RECALL_API_KEY  = os.getenv("RECALL_API_KEY", "")
RECALL_API_BASE = "https://us-east-1.recall.ai/api/v1"
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "http://localhost:8080")
ASSETS_DIR       = os.getenv("ASSETS_DIR", "./assets")

# In-memory chunk buffer: bot_id -> list[segment]
# Populated by the webhook server in its own process — this module is also
# imported by webhook_server.py so the buffer lives in that process.
_transcript_buffer: dict[str, list] = {}


# ── Recall.ai API calls ────────────────────────────────────────────────────────

def join_meeting_and_transcribe(meeting_url: str) -> dict:
    """
    Send a Recall.ai bot into any meeting (Zoom, Teams, Meet, Webex, Huddle).
    Returns {"bot_id": "...", "status": "joining"} on success.
    """
    if not RECALL_API_KEY:
        return {"error": "RECALL_API_KEY not configured — set it in .env"}

    payload = json.dumps({
        "meeting_url": meeting_url,
        "transcription_options": {"provider": "assembly_ai"},
        "real_time_transcription": {
            "destination_url": f"{WEBHOOK_BASE_URL}/transcript-webhook",
            "partial_results": False,
        },
    }).encode()

    req = urllib.request.Request(
        f"{RECALL_API_BASE}/bot",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Token {RECALL_API_KEY}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode())
            return {
                "bot_id": body.get("id"),
                "status": body.get("status_code", "joining"),
                "note":   "Bot is joining. Transcript chunks will arrive at the webhook server.",
            }
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "detail": e.read().decode()[:300]}
    except urllib.error.URLError as e:
        return {"error": str(e)}


def get_bot_transcript(bot_id: str) -> str:
    """
    Fetch the full transcript for a bot directly from Recall.ai (fallback /
    polling endpoint — use this if the webhook-based file is not yet written).
    Returns a speaker-labelled plain-text transcript.
    """
    if not RECALL_API_KEY:
        return "[RECALL_API_KEY not configured]"

    # First: check if the webhook server has already written a file to disk.
    local = _find_local_transcript(bot_id)
    if local:
        return local

    # Fallback: pull from Recall.ai API.
    req = urllib.request.Request(
        f"{RECALL_API_BASE}/bot/{bot_id}/transcript",
        headers={"Authorization": f"Token {RECALL_API_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return _format_transcript(data)
    except urllib.error.HTTPError as e:
        return f"[HTTP {e.code}: {e.read().decode()[:200]}]"
    except urllib.error.URLError as e:
        return f"[error fetching transcript: {e}]"


def _find_local_transcript(bot_id: str) -> str | None:
    """Return the content of a locally written transcript file, or None."""
    if not os.path.isdir(ASSETS_DIR):
        return None
    prefix = f"transcript_{bot_id[:8]}"
    matches = sorted(
        (f for f in os.listdir(ASSETS_DIR) if f.startswith(prefix)),
        reverse=True,
    )
    if not matches:
        return None
    path = os.path.join(ASSETS_DIR, matches[0])
    with open(path) as f:
        return f.read()


# ── Webhook buffer management (used by webhook_server.py) ─────────────────────

def buffer_transcript_chunk(bot_id: str, segments: list) -> None:
    """Append real-time transcript segments to the in-memory buffer."""
    _transcript_buffer.setdefault(bot_id, []).extend(segments)


def finalize_transcript(bot_id: str) -> str:
    """
    Concatenate all buffered chunks, write to ASSETS_DIR, and return the path.
    Called by the webhook server when it receives the bot.done event.
    """
    segments = _transcript_buffer.pop(bot_id, [])
    text = _format_transcript(segments)

    os.makedirs(ASSETS_DIR, exist_ok=True)
    ts   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(ASSETS_DIR, f"transcript_{bot_id[:8]}_{ts}.txt")
    with open(path, "w") as f:
        f.write(text)
    return path


# ── Formatting ─────────────────────────────────────────────────────────────────

def _format_transcript(segments) -> str:
    """
    Convert Recall.ai transcript segments to speaker-labelled plain text.

    Each segment:
      {"speaker": "Engineer A", "words": [{"text": "hello", ...}, ...], ...}
    """
    if not segments:
        return "[no transcript data]"

    lines = []
    for seg in segments:
        speaker = seg.get("speaker") or "Unknown"
        words   = seg.get("words", [])
        text    = " ".join(w.get("text", "") for w in words).strip()
        if text:
            lines.append(f"[{speaker}]: {text}")

    return "\n".join(lines) if lines else "[no speech detected]"
