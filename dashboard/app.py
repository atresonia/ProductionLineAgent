"""
dashboard/app.py — Resolve incident commander web dashboard

Reads the same log files and chaos state the agent uses.
Streams live updates via SSE.

Usage:
    cd dashboard && pip install flask && python app.py
    Open http://localhost:5050
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta

from flask import Flask, jsonify, render_template, Response, stream_with_context

app = Flask(__name__)

BASE_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR       = os.path.join(BASE_DIR, "logs")
CHAOS_FILE    = os.path.join(BASE_DIR, "chaos", "current_fault")
RESOLVE_LOG   = os.path.join(LOG_DIR, "resolve.log")
APPROVAL_FILE = os.path.join(BASE_DIR, "chaos", "approval_decision")


# ── Readers ────────────────────────────────────────────────────────────────────

def _read_chaos() -> str:
    try:
        with open(CHAOS_FILE) as f:
            return f.read().strip() or "none"
    except FileNotFoundError:
        return "none"


def _read_log_lines(service: str) -> list[str]:
    path = os.path.join(LOG_DIR, f"{service}.log")
    try:
        with open(path) as f:
            return f.readlines()[-2000:]
    except FileNotFoundError:
        return []


def _parse_entries(lines: list[str], since_minutes: int = 3) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    entries: list[dict] = []
    for line in lines:
        try:
            e = json.loads(line)
            ts = datetime.fromisoformat(e["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= since:
                entries.append(e)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return entries


def _get_metrics(service: str) -> dict:
    lines   = _read_log_lines(service)
    entries = _parse_entries(lines, since_minutes=3)

    request_entries = [e for e in entries if e.get("event") == "request"]
    if not request_entries:
        return {
            "error_rate_pct": 0,
            "p95_ms": 0,
            "last_memory_mb": 0,
            "total_requests": 0,
            "status": "unknown",
        }

    errors = sum(1 for e in request_entries if (e.get("status_code") or 200) >= 400)
    total  = len(request_entries)
    error_rate = round(errors / total * 100 if total > 0 else 0, 1)

    latencies = sorted(e.get("latency_ms", 0) for e in request_entries)
    p95_idx   = max(0, int(len(latencies) * 0.95) - 1)
    p95       = latencies[p95_idx] if latencies else 0

    metric_entries = [e for e in entries if e.get("event") == "metrics"]
    last_memory    = metric_entries[-1].get("memory_mb", 0) if metric_entries else 0

    if error_rate >= 15 or p95 >= 1500:
        status = "degraded"
    elif error_rate > 0 or p95 >= 500:
        status = "warning"
    else:
        status = "healthy"

    return {
        "error_rate_pct": error_rate,
        "p95_ms":         p95,
        "last_memory_mb": last_memory,
        "total_requests": total,
        "status":         status,
    }


def _read_resolve_log() -> list[dict]:
    """Return the last 100 entries from resolve.log."""
    try:
        with open(RESOLVE_LOG) as f:
            lines = f.readlines()[-200:]
        entries: list[dict] = []
        for line in lines:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return entries[-100:]
    except FileNotFoundError:
        return []


def _get_state() -> dict:
    fault         = _read_chaos()
    resolve_steps = _read_resolve_log()

    # Find the most recent investigation_start timestamp
    incident_start: str | None = None
    for entry in reversed(resolve_steps):
        if entry.get("event") == "investigation_start":
            incident_start = entry.get("timestamp")
            break

    # Pending remediation: last tool_call was execute_remediation with no subsequent result
    pending_remediation = False
    remediation_description = ""
    for entry in reversed(resolve_steps):
        ev = entry.get("event")
        if ev == "tool_result" and entry.get("tool") == "execute_remediation":
            break
        if ev == "tool_call" and entry.get("tool") == "execute_remediation":
            pending_remediation = True
            inp = entry.get("inputs", {})
            remediation_description = inp.get("description", "") or inp.get("action", "")
            break

    return {
        "fault":                    fault,
        "incident_active":          fault != "none",
        "incident_start":           incident_start,
        "pending_remediation":      pending_remediation,
        "remediation_description":  remediation_description,
        "metrics": {
            "api":      _get_metrics("api"),
            "frontend": _get_metrics("frontend"),
        },
        "resolve_steps": resolve_steps,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def state():
    return jsonify(_get_state())


@app.route("/api/stream")
def stream():
    """SSE endpoint — sends full state every 2 s."""
    def generate():
        while True:
            data = json.dumps(_get_state())
            yield f"data: {data}\n\n"
            time.sleep(2)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/approve", methods=["POST"])
def approve():
    """Write approval decision so the agent's approval gate can read it."""
    os.makedirs(os.path.dirname(APPROVAL_FILE), exist_ok=True)
    with open(APPROVAL_FILE, "w") as f:
        f.write("y")
    return jsonify({"status": "approved"})


@app.route("/api/reject", methods=["POST"])
def reject():
    os.makedirs(os.path.dirname(APPROVAL_FILE), exist_ok=True)
    with open(APPROVAL_FILE, "w") as f:
        f.write("n")
    return jsonify({"status": "rejected"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True, threaded=True)
