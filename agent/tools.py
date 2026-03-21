"""
tools.py — every tool the Resolve agent can call

Each function reads from the shared ./logs/ directory and ./chaos/
directory that the Docker services write to.  The agent itself never
touches running containers — remediation is simulated by writing the
chaos flag back to "none", which the services pick up on the next
request (clean demo reset, zero Docker commands needed).
"""

import base64
import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

from meeting_bot import join_meeting_and_transcribe, get_bot_transcript
from transcriber import transcribe_file, list_transcripts, get_transcript_content

LOG_DIR     = os.getenv("LOG_DIR",    "./logs")
CONFIG_DIR  = os.getenv("CONFIG_DIR", "./configs")
ASSETS_DIR  = os.getenv("ASSETS_DIR", "./assets")
CHAOS_FILE  = os.path.join(os.getenv("CHAOS_DIR", "./chaos"), "current_fault")
SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL", "")

from memory_tools import search_past_incidents, search_runbooks, search_slack
from calendar_tools import get_team_availability
from audio_tools import transcribe_recording, get_past_transcripts

# ── helpers ───────────────────────────────────────────────────────────────────

def _read_log_lines(service: str) -> list[str]:
    path = os.path.join(LOG_DIR, f"{service}.log")
    try:
        with open(path) as f:
            return f.readlines()
    except FileNotFoundError:
        return []

def _parse_entries(lines: list[str], since: datetime | None = None) -> list[dict]:
    entries = []
    for line in lines:
        try:
            e = json.loads(line)
            if since:
                ts = datetime.fromisoformat(e["timestamp"])
                if ts < since:
                    continue
            entries.append(e)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return entries

# ── tool implementations ──────────────────────────────────────────────────────

def read_logs(service: str, lines: int = 30) -> str:
    """Return the last N raw log lines for a service."""
    all_lines = _read_log_lines(service)
    if not all_lines:
        return f"[no logs found for '{service}']"
    return "".join(all_lines[-lines:])


def search_logs(service: str, keyword: str, lines: int = 200) -> str:
    """Return log lines containing keyword (case-insensitive)."""
    all_lines = _read_log_lines(service)
    matches = [l for l in all_lines[-lines:] if keyword.lower() in l.lower()]
    if not matches:
        return f"[no lines containing '{keyword}' in last {lines} lines of {service}]"
    return "".join(matches[-15:])


def get_error_rate(service: str, window_minutes: int = 5) -> str:
    """Return request count, error count, and error-rate % for the last N minutes."""
    since   = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    entries = _parse_entries(_read_log_lines(service), since=since)

    requests = [e for e in entries if e.get("event") == "request"]
    errors   = [e for e in requests if e.get("status_code", 200) >= 400]

    if not requests:
        return json.dumps({"service": service, "window_minutes": window_minutes,
                           "total_requests": 0, "error_rate_pct": 0,
                           "note": "no request events in window"})

    rate = round(len(errors) / len(requests) * 100, 1)
    # surface the most recent error messages for context
    recent_errors = [
        {"time": e.get("timestamp","?")[-8:], "endpoint": e.get("endpoint","?"),
         "status": e.get("status_code"), "error": e.get("error","?")}
        for e in errors[-3:]
    ]
    return json.dumps({
        "service":          service,
        "window_minutes":   window_minutes,
        "total_requests":   len(requests),
        "errors":           len(errors),
        "error_rate_pct":   rate,
        "recent_errors":    recent_errors,
    }, indent=2)


def get_latency_stats(service: str, window_minutes: int = 5) -> str:
    """Return p50 / p95 / p99 latency for the last N minutes."""
    since   = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    entries = _parse_entries(_read_log_lines(service), since=since)

    latencies = sorted(
        e["latency_ms"] for e in entries
        if e.get("event") == "request" and "latency_ms" in e
    )
    if not latencies:
        return json.dumps({"service": service, "note": "no latency data in window"})

    n = len(latencies)
    return json.dumps({
        "service":        service,
        "window_minutes": window_minutes,
        "sample_count":   n,
        "p50_ms":  latencies[n // 2],
        "p95_ms":  latencies[min(int(n * 0.95), n - 1)],
        "p99_ms":  latencies[min(int(n * 0.99), n - 1)],
        "max_ms":  latencies[-1],
    }, indent=2)


def get_memory_trend(service: str, window_minutes: int = 10) -> str:
    """Return memory readings over the last N minutes and whether it is growing."""
    since   = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    entries = _parse_entries(_read_log_lines(service), since=since)

    readings = [
        {"timestamp": e["timestamp"][-8:], "memory_mb": e["memory_mb"]}
        for e in entries if "memory_mb" in e
    ]
    if not readings:
        return json.dumps({"service": service, "note": "no memory data in window"})

    first_mb = readings[0]["memory_mb"]
    last_mb  = readings[-1]["memory_mb"]
    delta    = round(last_mb - first_mb, 1)
    trend    = "GROWING" if delta > 10 else "stable" if abs(delta) < 5 else "shrinking"

    return json.dumps({
        "service":        service,
        "window_minutes": window_minutes,
        "first_mb":       first_mb,
        "last_mb":        last_mb,
        "delta_mb":       delta,
        "trend":          trend,
        "samples":        readings[-4:],
    }, indent=2)


def get_recent_errors(service: str, limit: int = 20) -> str:
    """Return the most recent ERROR and WARN log entries for a service."""
    all_lines = _read_log_lines(service)
    error_lines = [l for l in all_lines if '"level": "ERROR"' in l or '"level": "WARN"' in l]
    if not error_lines:
        return f"[no errors or warnings found for '{service}']"
    return "".join(error_lines[-min(limit, 10):])


def get_deploy_history(window_minutes: int = 60) -> str:
    """
    Scan API logs for any entries that reference a deploy event or version
    change — a heuristic for 'what changed recently'.
    """
    since     = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    api_lines = _read_log_lines("api")
    entries   = _parse_entries(api_lines, since=since)

    deploy_hints = [
        e for e in entries
        if any(k in json.dumps(e).lower()
               for k in ("deploy", "v2.", "version", "startup", "restart", "config"))
    ]
    if not deploy_hints:
        return f"[no deploy-related events found in last {window_minutes} minutes]"
    return json.dumps(deploy_hints[-10:], indent=2)


def list_services() -> str:
    """List the services being monitored and their log file sizes."""
    result = {}
    for svc in ("frontend", "api"):
        path = os.path.join(LOG_DIR, f"{svc}.log")
        try:
            lines = sum(1 for _ in open(path))
            size  = os.path.getsize(path)
            result[svc] = {"log_lines": lines, "log_bytes": size}
        except FileNotFoundError:
            result[svc] = {"note": "log file not found"}
    return json.dumps(result, indent=2)


def execute_remediation(action: str, service: str = "api") -> str:
    """
    Simulate a remediation action.

    Actions:
      rollback  — revert to previous version (clears the bad_deploy fault)
      restart   — restart the service (clears most faults)
      scale_up  — add capacity (illustrative for memory/load faults)

    In the demo environment this writes "none" to the chaos flag so the
    Docker services immediately resume normal behaviour.
    """
    valid = {"rollback", "restart", "scale_up"}
    if action not in valid:
        return f"[unknown action '{action}'. valid: {', '.join(valid)}]"

    os.makedirs(os.path.dirname(CHAOS_FILE), exist_ok=True)
    with open(CHAOS_FILE, "w") as f:
        f.write("none")

    messages = {
        "rollback":  f"Rolled back {service} to previous stable version. Fault cleared.",
        "restart":   f"Restarted {service}. Fault cleared.",
        "scale_up":  f"Scaled {service} to additional replicas. Fault cleared.",
    }
    return json.dumps({"status": "success", "action": action,
                        "service": service, "message": messages[action]})


# ── NEW: multimodal + external integration tools ─────────────────────────────

def read_config_file(filename: str) -> str:
    """
    Read a deployment config or manifest file (YAML, JSON, .env).
    Useful for finding misconfigured env vars, missing secrets, or
    resource limits that may be contributing to the incident.
    """
    safe_name = os.path.basename(filename)   # prevent path traversal
    path = os.path.join(CONFIG_DIR, safe_name)
    try:
        with open(path) as f:
            content = f.read()
        return f"=== {safe_name} ===\n{content}"
    except FileNotFoundError:
        available = os.listdir(CONFIG_DIR) if os.path.isdir(CONFIG_DIR) else []
        return f"[file not found: {safe_name}. Available: {available}]"


def parse_stack_traces(service: str, limit: int = 5) -> str:
    """
    Extract multi-line Python/Java/Go stack traces from a service's log file.
    These appear as plain text (not JSON) after unhandled exceptions.
    Returns the most recent N stack traces found.
    """
    path = os.path.join(LOG_DIR, f"{service}.log")
    try:
        with open(path) as f:
            content = f.read()
    except FileNotFoundError:
        return f"[log file not found for {service}]"

    # Stack traces start with an EXCEPTION marker or 'Traceback'
    traces = []
    current = []
    in_trace = False

    for line in content.splitlines():
        if "EXCEPTION" in line or "Traceback" in line:
            if current:
                traces.append("\n".join(current))
            current = [line]
            in_trace = True
        elif in_trace:
            if line.strip() == "" and current:
                traces.append("\n".join(current))
                current = []
                in_trace = False
            else:
                current.append(line)

    if current:
        traces.append("\n".join(current))

    if not traces:
        return f"[no stack traces found in {service} logs]"

    recent = traces[-limit:]
    return f"Found {len(traces)} stack trace(s). Showing last {len(recent)}:\n\n" + \
           "\n\n---\n\n".join(recent)


def send_slack_alert(message: str, severity: str = "warning") -> str:
    """
    Post an incident alert to the configured Slack channel via webhook.
    severity: 'critical' | 'warning' | 'resolved'
    """
    if not SLACK_WEBHOOK:
        return json.dumps({
            "status": "skipped",
            "reason": "SLACK_WEBHOOK_URL not configured",
            "would_have_sent": message[:120],
        })

    colour = {"critical": "#E11D48", "warning": "#F59E0B", "resolved": "#10B981"}.get(severity, "#6B7280")
    emoji  = {"critical": ":red_circle:", "warning": ":large_yellow_circle:", "resolved": ":large_green_circle:"}.get(severity, ":white_circle:")

    payload = json.dumps({
        "attachments": [{
            "color":  colour,
            "text":   f"{emoji}  *Resolve* — {message}",
            "footer": f"resolve · {datetime.now(timezone.utc).strftime('%H:%M UTC')}",
        }]
    }).encode()

    try:
        req = urllib.request.Request(
            SLACK_WEBHOOK,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.dumps({"status": "sent", "http_status": resp.status,
                               "channel": "Slack", "severity": severity})
    except urllib.error.URLError as e:
        return json.dumps({"status": "error", "error": str(e)})


def list_config_files() -> str:
    """List available deployment config and manifest files."""
    try:
        files = os.listdir(CONFIG_DIR)
        return json.dumps({"config_dir": CONFIG_DIR, "files": files})
    except FileNotFoundError:
        return json.dumps({"config_dir": CONFIG_DIR, "files": [], "note": "directory not found"})


def transcribe_recording(filename: str) -> str:
    """
    Transcribe an MP3/WAV incident call recording using Whisper (runs locally).
    Pass just the filename — file must be in the assets/ directory.
    If a transcript already exists for this file it is returned immediately.
    """
    return transcribe_file(filename)


def get_past_transcripts(query: str = "") -> str:
    """
    List available incident call transcripts, optionally filtered by keyword.
    Returns file names, excerpts, and paths. Use this to discover what past
    recordings have been transcribed and find relevant institutional knowledge.
    """
    return list_transcripts(query)


def read_transcript(filename: str) -> str:
    """Read the full content of a saved transcript file."""
    return get_transcript_content(filename)


def join_incident_meeting(meeting_url: str) -> str:
    """
    Send a Recall.ai bot into any incident war-room meeting to transcribe it.
    Accepts Zoom, Microsoft Teams, Google Meet, Webex, or Slack Huddle URLs.
    Returns the bot_id — pass it to get_meeting_transcript() to retrieve the
    finished transcript once the meeting ends.
    """
    result = join_meeting_and_transcribe(meeting_url)
    return json.dumps(result, indent=2)


def get_meeting_transcript(bot_id: str) -> str:
    """
    Retrieve the speaker-labelled transcript for a meeting bot.
    First checks for a locally written file (written by the webhook server
    when the meeting ended), then falls back to polling the Recall.ai API.
    """
    return get_bot_transcript(bot_id)


# ── Anthropic tool schema ─────────────────────────────────────────────────────

TOOL_SCHEMAS = [
    {
        "name": "read_logs",
        "description": "Read the most recent raw log lines from a service. Use this first to get a broad view of what is happening.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "enum": ["api", "frontend"],
                            "description": "The service whose logs to read"},
                "lines":   {"type": "integer", "default": 60,
                            "description": "How many recent lines to return"},
            },
            "required": ["service"],
        },
    },
    {
        "name": "search_logs",
        "description": "Search log lines containing a keyword. Useful for finding all occurrences of an error message, endpoint, or event type.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "enum": ["api", "frontend"]},
                "keyword": {"type": "string", "description": "Case-insensitive search term"},
                "lines":   {"type": "integer", "default": 200,
                            "description": "How many recent lines to search"},
            },
            "required": ["service", "keyword"],
        },
    },
    {
        "name": "get_error_rate",
        "description": "Calculate the error rate (% of requests returning 4xx/5xx) for a service over a time window.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service":         {"type": "string", "enum": ["api", "frontend"]},
                "window_minutes":  {"type": "integer", "default": 5},
            },
            "required": ["service"],
        },
    },
    {
        "name": "get_latency_stats",
        "description": "Get p50, p95, p99 latency statistics for a service over a time window.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service":        {"type": "string", "enum": ["api", "frontend"]},
                "window_minutes": {"type": "integer", "default": 5},
            },
            "required": ["service"],
        },
    },
    {
        "name": "get_memory_trend",
        "description": "Get memory usage readings over time for a service, including whether memory is growing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service":        {"type": "string", "enum": ["api", "frontend"]},
                "window_minutes": {"type": "integer", "default": 10},
            },
            "required": ["service"],
        },
    },
    {
        "name": "get_recent_errors",
        "description": "Return the most recent ERROR and WARN log entries for a service.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "enum": ["api", "frontend"]},
                "limit":   {"type": "integer", "default": 20},
            },
            "required": ["service"],
        },
    },
    {
        "name": "get_deploy_history",
        "description": "Scan logs for recent deploy events, version changes, or configuration updates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "window_minutes": {"type": "integer", "default": 60},
            },
            "required": [],
        },
    },
    {
        "name": "list_services",
        "description": "List all monitored services and their log file status.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "read_config_file",
        "description": "Read a deployment config or manifest file (YAML, JSON, .env). Use this to find misconfigured env vars, missing secrets, or resource limits that may be causing the incident.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Config filename, e.g. deployment.yaml"},
            },
            "required": ["filename"],
        },
    },
    {
        "name": "list_config_files",
        "description": "List all available deployment config and manifest files.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "parse_stack_traces",
        "description": "Extract multi-line stack traces from a service log. These are plain-text (not JSON) and appear after unhandled exceptions. Essential for pinpointing crash locations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "enum": ["api", "frontend"]},
                "limit":   {"type": "integer", "default": 5, "description": "Max stack traces to return"},
            },
            "required": ["service"],
        },
    },
    {
        "name": "send_slack_alert",
        "description": "Post an incident alert or resolution notice to the Slack channel. Use this to notify the team when you have identified the root cause or when the incident is resolved.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message":  {"type": "string", "description": "The alert message to send"},
                "severity": {"type": "string", "enum": ["critical", "warning", "resolved"],
                             "description": "Alert severity level"},
            },
            "required": ["message", "severity"],
        },
    },
    {
        "name": "execute_remediation",
        "description": "Execute a remediation action on a service. ONLY call this after the human operator has approved.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action":  {"type": "string", "enum": ["rollback", "restart", "scale_up"],
                            "description": "rollback=revert deploy, restart=restart service, scale_up=add capacity"},
                "service": {"type": "string", "enum": ["api", "frontend"], "default": "api"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "search_past_incidents",
        "description": "Search past incident post-mortems and transcripts for similar patterns.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords describing the current incident"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_runbooks",
        "description": "Search internal runbooks for response procedures relevant to the current incident type.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords to find the relevant runbook"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_slack",
        "description": "Search Slack channel history for related discussions, warnings, or recent changes. Often surfaces the signal that explains the incident.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords to search in Slack messages"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_team_availability",
        "description": "Check which engineers are available and get a paging recommendation based on expertise and past incident history.",
        "input_schema": {
            "type": "object",
            "properties": {
                "incident_type": {"type": "string", "description": "Incident type hint (e.g. 'payment', 'memory', 'database', 'deploy')"},
            },
            "required": [],
        },
    },
    {
        "name": "get_past_transcripts",
        "description": "Search transcribed recordings of past incident calls for institutional knowledge.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords to find relevant incident call transcripts"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "transcribe_recording",
        "description": "Transcribe an audio recording (MP3/WAV) using Whisper. Pass just the filename from assets/.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Audio filename in assets/ e.g. incident_2026-03-10.mp3"},
            },
            "required": ["filename"],
        },
    },
    {
        "name": "read_transcript",
        "description": "Read the full content of a saved transcript file by filename.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Transcript filename"},
            },
            "required": ["filename"],
        },
    },
    {
        "name": "join_incident_meeting",
        "description": "Send a Recall.ai bot into an incident war-room meeting (Zoom, Teams, Meet, Webex, Slack Huddle) to transcribe it. Returns a bot_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "meeting_url": {"type": "string", "description": "Full meeting URL"},
            },
            "required": ["meeting_url"],
        },
    },
    {
        "name": "get_meeting_transcript",
        "description": "Retrieve the speaker-labelled transcript for a completed meeting bot.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bot_id": {"type": "string", "description": "The bot_id returned by join_incident_meeting"},
            },
            "required": ["bot_id"],
        },
    },
]

# ── dispatch ──────────────────────────────────────────────────────────────────

TOOL_FN_MAP = {
    "read_logs":              lambda i: read_logs(i["service"], i.get("lines", 30)),
    "search_logs":            lambda i: search_logs(i["service"], i["keyword"], i.get("lines", 200)),
    "get_error_rate":         lambda i: get_error_rate(i["service"], i.get("window_minutes", 5)),
    "get_latency_stats":      lambda i: get_latency_stats(i["service"], i.get("window_minutes", 5)),
    "get_memory_trend":       lambda i: get_memory_trend(i["service"], i.get("window_minutes", 10)),
    "get_recent_errors":      lambda i: get_recent_errors(i["service"], i.get("limit", 20)),
    "get_deploy_history":     lambda i: get_deploy_history(i.get("window_minutes", 60)),
    "list_services":          lambda i: list_services(),
    "read_config_file":       lambda i: read_config_file(i["filename"]),
    "list_config_files":      lambda i: list_config_files(),
    "parse_stack_traces":     lambda i: parse_stack_traces(i["service"], i.get("limit", 5)),
    "send_slack_alert":       lambda i: send_slack_alert(i["message"], i.get("severity", "warning")),
    "execute_remediation":    lambda i: execute_remediation(i["action"], i.get("service", "api")),
    "search_past_incidents":  lambda i: search_past_incidents(i["query"]),
    "search_runbooks":        lambda i: search_runbooks(i["query"]),
    "search_slack":           lambda i: search_slack(i["query"], i.get("limit", 10)),
    "get_team_availability":  lambda i: get_team_availability(i.get("incident_type", "")),
    "get_past_transcripts":   lambda i: get_past_transcripts(i.get("query", "")),
    "transcribe_recording":   lambda i: transcribe_recording(i.get("filename") or i.get("audio_path", "")),
    "read_transcript":        lambda i: read_transcript(i["filename"]),
    "join_incident_meeting":  lambda i: join_incident_meeting(i["meeting_url"]),
    "get_meeting_transcript": lambda i: get_meeting_transcript(i["bot_id"]),
}

def dispatch(name: str, inputs: dict) -> str:
    fn = TOOL_FN_MAP.get(name)
    if fn is None:
        return f"[unknown tool: {name}]"
    try:
        return fn(inputs)
    except Exception as e:
        return f"[tool error in {name}: {e}]"
