"""
webhook_server.py — Flask server for Recall.ai real-time transcript webhooks

Run this alongside the agent to receive transcript chunks that Recall.ai
pushes during a live meeting:

    python webhook_server.py

Routes:
    POST /transcript-webhook   ← real-time transcript chunks (Recall streaming)
    POST /meeting-end-webhook  ← bot.done event — finalises transcript to disk

Recall.ai requires a publicly reachable URL.  For local development use
ngrok or a similar tunnel and set WEBHOOK_BASE_URL accordingly.

Environment variables:
    WEBHOOK_PORT      Port to listen on (default: 8080)
    WEBHOOK_BASE_URL  Public base URL Recall posts to (set in .env)
    ASSETS_DIR        Where finalised transcript files are written
"""

import json
import os

from flask import Flask, request, jsonify

from meeting_bot import buffer_transcript_chunk, finalize_transcript

app  = Flask(__name__)
PORT = int(os.getenv("WEBHOOK_PORT", "8080"))


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.post("/transcript-webhook")
def transcript_webhook():
    """
    Receives real-time transcript chunks pushed by Recall.ai during a meeting.

    Expected body:
    {
        "bot_id": "...",
        "transcript": [
            {
                "speaker": "Engineer A",
                "words": [{"text": "We", ...}, {"text": "tried", ...}],
                "start_time": 1.2,
                "end_time":   2.8
            },
            ...
        ]
    }
    """
    body       = request.get_json(silent=True) or {}
    bot_id     = body.get("bot_id", "")
    transcript = body.get("transcript", [])

    if not bot_id:
        return jsonify({"error": "missing bot_id"}), 400

    if transcript:
        buffer_transcript_chunk(bot_id, transcript)
        print(f"[webhook] buffered {len(transcript)} segment(s) for bot {bot_id[:8]}")

    return jsonify({"ok": True})


@app.post("/meeting-end-webhook")
def meeting_end_webhook():
    """
    Triggered by Recall.ai when the bot leaves or the meeting ends
    (event type: bot.done).  Finalises and writes the full transcript to disk
    so the agent can pick it up via get_meeting_transcript().

    Expected body:
    {
        "bot_id": "...",
        "event":  "bot.done"
    }
    """
    body   = request.get_json(silent=True) or {}
    bot_id = body.get("bot_id", "")

    if not bot_id:
        return jsonify({"error": "missing bot_id"}), 400

    path = finalize_transcript(bot_id)
    print(f"[webhook] transcript finalised → {path}")

    return jsonify({"ok": True, "transcript_path": path})


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"[webhook-server] Listening on :{PORT}")
    print(f"[webhook-server] WEBHOOK_BASE_URL = {os.getenv('WEBHOOK_BASE_URL', 'not set')}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
