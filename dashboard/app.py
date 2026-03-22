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
import sys
import time
from datetime import datetime, timezone, timedelta

from flask import Flask, jsonify, render_template, Response, stream_with_context, request, send_from_directory

# Agent modules live one level up
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent"))
os.environ.setdefault("LOG_DIR",    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"))
os.environ.setdefault("CHAOS_DIR",  os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chaos"))
os.environ.setdefault("CONFIG_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "configs"))
os.environ.setdefault("ASSETS_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets"))
os.environ.setdefault("DATA_DIR",   os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))

app = Flask(__name__)

BASE_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR       = os.path.join(BASE_DIR, "logs")
CHAOS_FILE    = os.path.join(BASE_DIR, "chaos", "current_fault")
CHAOS_JSON    = os.path.join(BASE_DIR, "chaos", "faults.json")
RESOLVE_LOG   = os.path.join(LOG_DIR, "resolve.log")
APPROVAL_FILE = os.path.join(BASE_DIR, "chaos", "approval_decision")
ASSETS_DIR    = os.path.join(BASE_DIR, "assets")


# ── Readers ────────────────────────────────────────────────────────────────────

def _read_faults() -> list[str]:
    """Return list of currently active faults (supports multi-fault JSON format)."""
    try:
        with open(CHAOS_JSON) as f:
            data = json.load(f)
        faults = data.get("active_faults", [])
        if isinstance(faults, list) and faults:
            return faults
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    try:
        with open(CHAOS_FILE) as f:
            fault = f.read().strip()
        if fault and fault != "none":
            return [fault]
    except FileNotFoundError:
        pass
    return []


def _read_chaos() -> str:
    faults = _read_faults()
    return ",".join(faults) if faults else "none"


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
    faults        = _read_faults()
    fault         = ",".join(faults) if faults else "none"
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

    # Find latest dashboard screenshot from image_attached events
    dashboard_image = None
    for entry in reversed(resolve_steps):
        if entry.get("event") == "image_attached":
            path = entry.get("path", "")
            fname = os.path.basename(path)
            if fname:
                dashboard_image = f"/assets/{fname}"
            break

    return {
        "fault":                    fault,
        "faults":                   faults,
        "incident_active":          bool(faults),
        "incident_start":           incident_start,
        "pending_remediation":      pending_remediation,
        "remediation_description":  remediation_description,
        "dashboard_image":          dashboard_image,
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


@app.route("/assets/<path:filename>")
def assets(filename):
    return send_from_directory(ASSETS_DIR, filename)


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


# ── Chat endpoint ───────────────────────────────────────────────────────────────

def _build_nl_system() -> str:
    """Build the chat system prompt, injecting live agent context from resolve.log."""
    # Read last few resolve.log entries so chat knows what the agent already did
    agent_ctx = ""
    try:
        entries = _read_resolve_log()
        # Summarise the last investigation: what tools ran, what conclusion was reached
        tools_used = [e.get("tool","") for e in entries if e.get("event") == "tool_call"]
        remediations = [e for e in entries if e.get("event") == "remediation_executed"]
        conclusion = next((e.get("text","") for e in reversed(entries)
                           if e.get("event") == "reasoning" and len(e.get("text","")) > 80), "")
        chaos = _read_chaos()
        lines = [f"CURRENT FAULT: {chaos}"]
        if tools_used:
            lines.append(f"AGENT ALREADY RAN: {', '.join(dict.fromkeys(tools_used[-12:]))}")
        if remediations:
            acts = [r.get("action","?") for r in remediations]
            lines.append(f"REMEDIATIONS EXECUTED: {', '.join(acts)}")
        # Check if a remediation is currently pending operator approval
        pending = False
        pending_desc = ""
        for entry in reversed(entries):
            ev = entry.get("event")
            if ev == "tool_result" and entry.get("tool") == "execute_remediation":
                break
            if ev == "tool_call" and entry.get("tool") == "execute_remediation":
                pending = True
                inp = entry.get("inputs", {})
                pending_desc = inp.get("description", "") or inp.get("action", "")
                break
        if pending:
            lines.append(f"REMEDIATION PENDING APPROVAL (not yet executed): {pending_desc} — operator must click APPROVE in the dashboard first")
        if conclusion:
            lines.append(f"AGENT CONCLUSION (last reasoning): {conclusion[:300]}")
        agent_ctx = "\n".join(lines)
    except Exception:
        pass

    return f"""You are Resolve, an ops assistant for a production system.
Stack: frontend:3000 → api:8000 → db:5432.

IMPORTANT — current agent state:
{agent_ctx}

Rules:
- ALWAYS use window_minutes=1 when checking error rate after a remediation — the 5-min window
  contains pre-fix errors and will show false positives. Only use window_minutes=5 for historical context.
- If the agent has already executed a remediation, say so and check the 1-min window to confirm recovery.
- If REMEDIATION PENDING APPROVAL is listed above, tell the operator it has NOT been executed yet and they must approve it in the dashboard. Do NOT say it was already run.
- Do NOT recommend actions the agent already took unless the operator explicitly asks to retry.
- Be concise and direct — no markdown tables for simple status updates.

Common queries:
- "give me updates" / "what's wrong?" → check current fault + get_error_rate(window=1) + summarise what agent did
- "show me the logs" → read_logs
- "is it fixed?" → get_error_rate with window_minutes=1
- "what happened?" → summarise agent_ctx above, call search_past_incidents if needed
- "rollback / restart <service>" → execute_remediation"""


def _run_chat(message: str) -> dict:
    """Run one NL chat turn against the full tool set."""
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent", ".env"))
        from model_client import ModelClient
        from tools import TOOL_SCHEMAS, dispatch
    except Exception as e:
        return {"reply": f"[chat unavailable: {e}]", "tool_calls": []}

    client = ModelClient()
    system = _build_nl_system()
    messages = [{"role": "user", "content": message}]
    tool_calls_made = []
    reply = ""

    for _ in range(6):
        try:
            response = client.chat(system, messages, TOOL_SCHEMAS)
        except Exception as e:
            return {"reply": f"[API error: {e}]", "tool_calls": tool_calls_made}

        if response.stop_reason == "end_turn":
            for block in response.content:
                if block.type == "text":
                    reply = block.text
            break

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                tool_calls_made.append({"name": block.name, "inputs": block.input})
                result = dispatch(block.name, block.input)
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     result,
                })
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    return {"reply": reply or "[no response]", "tool_calls": tool_calls_made}


@app.route("/api/chat", methods=["POST"])
def chat():
    """
    Natural language ops interface.
    Body: {"message": "what's wrong with the api?"}
    Returns: {"reply": "...", "tool_calls": [...]}
    """
    body    = request.get_json(silent=True) or {}
    message = body.get("message", "").strip()
    if not message:
        return jsonify({"error": "message required"}), 400

    # Shortcut: plain approve/reject without hitting the LLM
    lower = message.lower()
    if lower in ("approve", "yes", "y", "approve remediation"):
        os.makedirs(os.path.dirname(APPROVAL_FILE), exist_ok=True)
        with open(APPROVAL_FILE, "w") as f:
            f.write("y")
        return jsonify({"reply": "Remediation approved.", "tool_calls": []})
    if lower in ("reject", "no", "n", "reject remediation", "deny"):
        os.makedirs(os.path.dirname(APPROVAL_FILE), exist_ok=True)
        with open(APPROVAL_FILE, "w") as f:
            f.write("n")
        return jsonify({"reply": "Remediation rejected.", "tool_calls": []})

    result = _run_chat(message)
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True, threaded=True)
