"""
slack_watcher.py — Watch #incidents Slack channel for meeting URLs

Polls the Slack conversations.history API every SLACK_POLL_INTERVAL seconds.
When a Zoom / Teams / Meet / Webex / Slack Huddle URL is detected in a new
message, it automatically sends a Recall.ai bot into the meeting to transcribe.

Runs as a background thread alongside the metrics monitor.

Required env vars:
    SLACK_BOT_TOKEN             xoxb-... Bot User OAuth Token
                                Scopes needed: channels:history, chat:write
    SLACK_INCIDENTS_CHANNEL_ID  Channel ID for #incidents (e.g. C01234ABCDE)
                                (Right-click channel in Slack → Copy link → last segment)

Optional:
    SLACK_POLL_INTERVAL         Seconds between polls (default: 15)
    RECALL_API_KEY              If set, bot joins the meeting via Recall.ai
                                If not set, URL is logged but not joined
"""

import json
import os
import re
import time
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone

from rich.console import Console

console = Console()

SLACK_BOT_TOKEN            = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_INCIDENTS_CHANNEL_ID = os.getenv("SLACK_INCIDENTS_CHANNEL_ID", "")
SLACK_POLL_INTERVAL        = float(os.getenv("SLACK_POLL_INTERVAL", "15"))
RECALL_API_KEY             = os.getenv("RECALL_API_KEY", "")

SLACK_API = "https://slack.com/api"

# Regex patterns for supported meeting platforms
_MEETING_URL_PATTERN = re.compile(
    r"https://"
    r"(?:"
    r"(?:[a-z0-9-]+\.)?zoom\.us/j/[^\s>]+"           # Zoom
    r"|teams\.microsoft\.com/l/meetup-join/[^\s>]+"   # Microsoft Teams
    r"|meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}" # Google Meet
    r"|[a-z0-9-]+\.webex\.com/[^\s>]+"                # Webex
    r"|app\.slack\.com/huddle/[^\s>]+"                # Slack Huddle
    r")",
    re.IGNORECASE,
)


# ── Slack API helpers ──────────────────────────────────────────────────────────

def _slack_get(method: str, params: dict) -> dict:
    """Call a Slack API GET method and return parsed JSON."""
    query = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
    url   = f"{SLACK_API}/{method}?{query}"
    req   = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def _slack_post(method: str, payload: dict) -> dict:
    """Call a Slack API POST method and return parsed JSON."""
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        f"{SLACK_API}/{method}",
        data=data,
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type":  "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def _post_to_incidents(text: str) -> None:
    """Post a message back to #incidents."""
    if not SLACK_BOT_TOKEN or not SLACK_INCIDENTS_CHANNEL_ID:
        return
    try:
        _slack_post("chat.postMessage", {
            "channel": SLACK_INCIDENTS_CHANNEL_ID,
            "text":    text,
        })
    except Exception as e:
        console.print(f"  [dim yellow][slack] failed to post: {e}[/dim yellow]")


# ── URL detection & joining ────────────────────────────────────────────────────

def _extract_meeting_urls(text: str) -> list[str]:
    """Return all meeting URLs found in a Slack message text."""
    return _MEETING_URL_PATTERN.findall(text)


def _join_meeting(url: str) -> None:
    """Send a Recall.ai bot into the meeting and post confirmation to Slack."""
    from meeting_bot import join_meeting_and_transcribe

    console.print(
        f"\n  [bold cyan][slack-watcher][/bold cyan] Meeting URL detected: {url}"
    )

    if not RECALL_API_KEY:
        console.print(
            "  [dim yellow][slack-watcher] RECALL_API_KEY not set — "
            "logging URL but not joining.[/dim yellow]"
        )
        _post_to_incidents(
            f":eyes: Resolve spotted a meeting URL: {url}\n"
            f"Set RECALL_API_KEY to enable automatic transcription."
        )
        return

    result = join_meeting_and_transcribe(url)
    bot_id = result.get("bot_id")

    if bot_id:
        console.print(
            f"  [bold green][slack-watcher] Recall.ai bot joined.[/bold green] "
            f"bot_id={bot_id}"
        )
        _post_to_incidents(
            f":microphone: *Resolve* is transcribing this war-room call.\n"
            f"Platform: {_platform_name(url)}\n"
            f"Bot ID: `{bot_id}`\n"
            f"Transcript will be indexed automatically when the meeting ends."
        )
    else:
        err = result.get("error", "unknown error")
        console.print(
            f"  [bold red][slack-watcher] Failed to join: {err}[/bold red]"
        )
        _post_to_incidents(
            f":warning: Resolve tried to join the meeting but failed: {err}"
        )


def _platform_name(url: str) -> str:
    """Return a human-readable platform name from a URL."""
    url_lower = url.lower()
    if "zoom.us"              in url_lower: return "Zoom"
    if "teams.microsoft.com"  in url_lower: return "Microsoft Teams"
    if "meet.google.com"      in url_lower: return "Google Meet"
    if "webex.com"            in url_lower: return "Webex"
    if "slack.com/huddle"     in url_lower: return "Slack Huddle"
    return "Unknown platform"


# ── Main polling loop ──────────────────────────────────────────────────────────

class SlackWatcher:
    """
    Polls #incidents for new messages containing meeting URLs.
    Runs in a background thread — call .start() to launch.
    """

    def __init__(self):
        self._joined_urls:    set[str] = set()   # avoid joining same URL twice
        self._last_ts:        str      = str(datetime.now(timezone.utc).timestamp())
        self._thread:         threading.Thread | None = None

    def _check_configured(self) -> bool:
        if not SLACK_BOT_TOKEN:
            console.print(
                "  [dim yellow][slack-watcher] SLACK_BOT_TOKEN not set — "
                "Slack watching disabled.[/dim yellow]"
            )
            return False
        if not SLACK_INCIDENTS_CHANNEL_ID:
            console.print(
                "  [dim yellow][slack-watcher] SLACK_INCIDENTS_CHANNEL_ID not set — "
                "Slack watching disabled.[/dim yellow]"
            )
            return False
        return True

    def poll_once(self) -> None:
        """Fetch new messages since last check and act on any meeting URLs."""
        try:
            resp = _slack_get("conversations.history", {
                "channel": SLACK_INCIDENTS_CHANNEL_ID,
                "oldest":  self._last_ts,
                "limit":   50,
            })
        except Exception as e:
            console.print(f"  [dim red][slack-watcher] poll error: {e}[/dim red]")
            return

        if not resp.get("ok"):
            console.print(
                f"  [dim red][slack-watcher] Slack API error: "
                f"{resp.get('error', 'unknown')}[/dim red]"
            )
            return

        messages = resp.get("messages", [])
        if not messages:
            return

        # Update cursor to the newest message timestamp
        self._last_ts = messages[0].get("ts", self._last_ts)

        # Process messages oldest-first
        for msg in reversed(messages):
            text = msg.get("text", "")
            urls = _extract_meeting_urls(text)
            for url in urls:
                if url not in self._joined_urls:
                    self._joined_urls.add(url)
                    _join_meeting(url)

    def _run_loop(self) -> None:
        if not self._check_configured():
            return

        console.print(
            f"\n  [bold cyan][slack-watcher][/bold cyan] Watching "
            f"#{SLACK_INCIDENTS_CHANNEL_ID} for meeting URLs "
            f"(polling every {SLACK_POLL_INTERVAL}s)\n"
        )

        while True:
            self.poll_once()
            time.sleep(SLACK_POLL_INTERVAL)

    def start(self) -> None:
        """Start the watcher in a background daemon thread."""
        self._thread = threading.Thread(
            target=self._run_loop,
            name="slack-watcher",
            daemon=True,   # exits automatically when main process exits
        )
        self._thread.start()
