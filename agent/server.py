"""
server.py — Resolve WebSocket server

Wraps investigator.py and streams every event to connected browser clients
in real-time.  The approval gate blocks the investigation thread until the
UI sends back {type: "approve"} or {type: "reject"}.

Usage:
    cd agent
    pip install fastapi uvicorn[standard] python-dotenv
    python server.py            # runs on ws://localhost:8765

Endpoints:
    WS  /ws                          — real-time event stream + approval
    POST /trigger/{fault_type}       — inject fault + start investigation
    POST /trigger/custom             — body: {"anomaly": "..."}
    GET  /health                     — liveness
    GET  /status                     — current agent state
"""

import asyncio
import json
import os
import sys
import threading
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

# ── Path setup — server.py lives in agent/, project root is parent ────────────
AGENT_DIR   = os.path.dirname(os.path.abspath(__file__))
BASE_DIR    = os.path.dirname(AGENT_DIR)
LOG_DIR     = os.path.join(BASE_DIR, "logs")
CHAOS_DIR   = os.path.join(BASE_DIR, "chaos")
CHAOS_FILE  = os.path.join(CHAOS_DIR, "current_fault")

sys.path.insert(0, AGENT_DIR)
os.chdir(AGENT_DIR)  # investigator.py expects relative paths

import investigator

# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(title="Resolve Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global state ───────────────────────────────────────────────────────────────

_connections:     set[WebSocket] = set()
_agent_status:    str            = "monitoring"
_event_loop:      Optional[asyncio.AbstractEventLoop] = None
_investigation_lock = threading.Lock()
_investigation_running = False

# Approval bridge: investigation thread blocks on this event
_approval_event    = threading.Event()
_approval_decision = "reject"  # "approve" | "reject"

# Recent events buffer for catch-up on new connections (last 200 events)
_event_buffer: deque[dict] = deque(maxlen=200)

# Demos fault descriptions
DEMO_TRIGGERS = {
    "bad_deploy": (
        "Anomaly detected:\n"
        "  • High error rate on api: /checkout returning 500s (~85% error rate)\n"
        "  • Frontend reporting 502s upstream from api\n\n"
        "Investigate root cause, determine blast radius, and recommend remediation."
    ),
    "memory_leak": (
        "Anomaly detected:\n"
        "  • Memory growing rapidly on api: +200MB in 5 minutes (now 890MB)\n"
        "  • Checkout latency increasing with each request\n\n"
        "Investigate root cause, determine blast radius, and recommend remediation."
    ),
    "slow_db": (
        "Anomaly detected:\n"
        "  • High latency on api: p95=2800ms (threshold 1500ms)\n"
        "  • High latency on frontend: p95=3200ms (cascade from api)\n\n"
        "Investigate root cause, determine blast radius, and recommend remediation."
    ),
    "db_down": (
        "Anomaly detected:\n"
        "  • High error rate on api: 503s on all endpoints (DB unreachable)\n"
        "  • Frontend error rate 100% — cannot reach api\n\n"
        "Investigate root cause, determine blast radius, and recommend remediation."
    ),
}

# ── Broadcast helpers ──────────────────────────────────────────────────────────

async def _broadcast(event: dict) -> None:
    _event_buffer.append(event)
    if not _connections:
        return
    msg  = json.dumps(event)
    dead = set()
    for ws in list(_connections):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    _connections.difference_update(dead)


def _emit(event: dict) -> None:
    """Thread-safe: called from investigation thread."""
    if _event_loop and not _event_loop.is_closed():
        asyncio.run_coroutine_threadsafe(_broadcast(event), _event_loop)


def _set_status(status: str) -> None:
    global _agent_status
    _agent_status = status
    _emit({"type": "agent_status", "status": status,
           "timestamp": _now()})


# ── Event callback installed in investigator ──────────────────────────────────

def _on_event(entry: dict) -> None:
    """
    Called from investigator._ilog for every logged event.
    Translates resolve.log events → WebSocket event types.
    """
    ev   = entry.get("event", "")
    ts   = entry.get("timestamp", _now())

    if ev == "investigation_start":
        _set_status("investigating")
        _emit({
            "type":        "anomaly_detected",
            "description": entry.get("anomaly", ""),
            "severity":    "critical",
            "timestamp":   ts,
        })

    elif ev == "reasoning":
        text = entry.get("text", "")
        # Detect Phase 1 plan
        if text.strip().startswith("Plan:"):
            _emit({
                "type":      "plan",
                "text":      text.strip()[5:].strip(),
                "timestamp": ts,
            })
        else:
            _emit({
                "type":      "reasoning",
                "text":      text,
                "timestamp": ts,
            })

    elif ev == "tool_call":
        name   = entry.get("tool", "")
        inputs = entry.get("inputs", {})
        _emit({
            "type":      "tool_call",
            "name":      name,
            "inputs":    inputs,
            "timestamp": ts,
        })
        # Mirror send_slack_alert calls to the Slack feed
        if name == "send_slack_alert":
            _emit({
                "type":      "slack_alert",
                "message":   inputs.get("message", ""),
                "severity":  inputs.get("severity", "info"),
                "timestamp": ts,
            })

    elif ev == "tool_result":
        name    = entry.get("tool", "")
        preview = entry.get("result_preview", "")
        _emit({
            "type":           "tool_result",
            "name":           name,
            "result_preview": preview,
            "timestamp":      ts,
        })

    elif ev == "image_attached":
        path = entry.get("path", "")
        if path and os.path.exists(path):
            import base64
            try:
                with open(path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                _emit({
                    "type":       "dashboard_image",
                    "base64_png": b64,
                    "timestamp":  ts,
                })
            except Exception:
                pass

    elif ev == "remediation_rejected":
        _set_status("investigating")
        _emit({
            "type":      "remediation_result",
            "status":    "rejected",
            "message":   f"Operator declined: {entry.get('action', '')}",
            "timestamp": ts,
        })

    elif ev == "remediation_executed":
        _emit({
            "type":      "remediation_result",
            "status":    "approved",
            "message":   f"Executed: {entry.get('action', '')}",
            "timestamp": ts,
        })

    elif ev == "investigation_complete":
        pass  # handled after investigate() returns


# ── Approval callback installed in investigator ───────────────────────────────

def _approval_cb(action: str, service: str, reason: str = "") -> bool:
    """
    Blocks the investigation thread until the UI sends approve/reject.
    Called from the investigation thread (NOT async).
    """
    _set_status("awaiting_approval")
    _emit({
        "type":      "approval_request",
        "action":    action,
        "service":   service,
        "reason":    reason,
        "timestamp": _now(),
    })
    _approval_event.clear()
    _approval_event.wait(timeout=300)  # 5-minute timeout
    return _approval_decision == "approve"


# ── Metrics polling ────────────────────────────────────────────────────────────

def _read_log_lines(service: str) -> list[str]:
    path = os.path.join(LOG_DIR, f"{service}.log")
    try:
        with open(path) as f:
            return f.readlines()[-2000:]
    except FileNotFoundError:
        return []


def _compute_metrics(service: str) -> dict:
    since  = datetime.now(timezone.utc) - timedelta(minutes=3)
    lines  = _read_log_lines(service)
    entries: list[dict] = []
    for line in lines:
        try:
            e  = json.loads(line)
            ts = datetime.fromisoformat(e["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= since:
                entries.append(e)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue

    reqs = [e for e in entries if e.get("event") == "request"]
    if not reqs:
        return {"error_rate": 0, "p95_latency": 0, "memory_mb": 0,
                "status": "unknown", "total_requests": 0}

    errors = sum(1 for e in reqs if (e.get("status_code") or 200) >= 400)
    total  = len(reqs)
    error_rate = round(errors / total * 100 if total else 0, 1)

    latencies  = sorted(e.get("latency_ms", 0) for e in reqs)
    p95_idx    = max(0, int(len(latencies) * 0.95) - 1)
    p95        = latencies[p95_idx] if latencies else 0

    mem_entries = [e for e in entries if e.get("event") == "metrics"]
    memory_mb   = mem_entries[-1].get("memory_mb", 0) if mem_entries else 0

    if error_rate >= 15 or p95 >= 1500:
        status = "degraded"
    elif error_rate > 0 or p95 >= 500:
        status = "warning"
    else:
        status = "healthy"

    return {
        "error_rate":      error_rate,
        "p95_latency":     p95,
        "memory_mb":       memory_mb,
        "total_requests":  total,
        "status":          status,
    }


def _read_chaos() -> str:
    try:
        with open(CHAOS_FILE) as f:
            return f.read().strip() or "none"
    except FileNotFoundError:
        return "none"


async def _metrics_loop() -> None:
    """Background task: poll metrics every 5s and broadcast to all clients."""
    while True:
        try:
            await _broadcast({
                "type":      "metrics",
                "timestamp": _now(),
                "fault":     _read_chaos(),
                "api":       _compute_metrics("api"),
                "frontend":  _compute_metrics("frontend"),
            })
        except Exception:
            pass
        await asyncio.sleep(5)


async def _monitor_loop() -> None:
    """
    Background task: mirrors monitor.py's anomaly detection.
    Polls check_once() every 5s; auto-starts investigation when threshold breached.
    Debounces — won't re-trigger while an investigation is already running.
    """
    from monitor import check_once
    recovered = True  # start in recovered state so first anomaly fires immediately

    while True:
        try:
            anomaly = await asyncio.get_running_loop().run_in_executor(None, check_once)

            if anomaly and not _investigation_running:
                recovered = False
                await _start_investigation(anomaly)

            elif not anomaly and not recovered:
                recovered = True
                _set_status("monitoring")
                _emit({
                    "type":      "agent_status",
                    "status":    "monitoring",
                    "message":   "System recovered — monitoring resumed",
                    "timestamp": _now(),
                })

        except Exception:
            pass

        await asyncio.sleep(5)


# ── Investigation runner ───────────────────────────────────────────────────────

def _run_investigation(anomaly: str, image_path: Optional[str] = None) -> None:
    """Runs in a thread pool. Streams events via _on_event callbacks."""
    global _investigation_running, _approval_decision

    # Install callbacks
    investigator._event_callback    = _on_event
    investigator._approval_callback = _approval_cb

    try:
        result = investigator.investigate(
            anomaly,
            image_path=image_path,
            require_approval=True,
        )
        conclusion = result.get("conclusion", "")

        # Generate post-mortem
        from postmortem import generate as gen_pm
        detected_at  = datetime.now(timezone.utc)
        resolved_at  = datetime.now(timezone.utc)
        pm_path      = gen_pm(conclusion, detected_at, resolved_at)

        # Read and emit the markdown
        try:
            with open(pm_path) as f:
                pm_md = f.read()
        except Exception:
            pm_md = conclusion

        _emit({
            "type":      "postmortem",
            "markdown":  pm_md,
            "filepath":  pm_path,
            "timestamp": _now(),
        })

        _set_status("resolved")

    except Exception as e:
        _emit({
            "type":      "error",
            "message":   str(e),
            "timestamp": _now(),
        })
        _set_status("monitoring")
    finally:
        global _investigation_running
        _investigation_running = False
        investigator._event_callback    = None
        investigator._approval_callback = None


async def _start_investigation(anomaly: str, image_path: Optional[str] = None) -> bool:
    global _investigation_running
    if _investigation_running:
        return False
    _investigation_running = True
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _run_investigation, anomaly, image_path)
    return True


# ── WebSocket endpoint ─────────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    _connections.add(ws)

    try:
        # Send catch-up: current status + buffered events
        await ws.send_text(json.dumps({
            "type":      "agent_status",
            "status":    _agent_status,
            "timestamp": _now(),
        }))
        for event in list(_event_buffer):
            await ws.send_text(json.dumps(event))

        # Listen for client messages (approve / reject)
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            await _handle_client_msg(msg)

    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _connections.discard(ws)


async def _handle_client_msg(msg: dict) -> None:
    global _approval_decision
    t = msg.get("type", "")
    if t in ("approve", "reject"):
        _approval_decision = t
        _approval_event.set()


# ── HTTP endpoints ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "agent": _agent_status}


@app.get("/status")
async def status():
    return {
        "agent_status": _agent_status,
        "fault":        _read_chaos(),
        "metrics": {
            "api":      _compute_metrics("api"),
            "frontend": _compute_metrics("frontend"),
        },
    }


@app.post("/trigger/{fault_type}")
async def trigger(fault_type: str):
    if fault_type not in DEMO_TRIGGERS and fault_type != "none":
        return {"error": f"Unknown fault type: {fault_type}. "
                         f"Valid: {list(DEMO_TRIGGERS.keys())}"}

    # Inject chaos
    os.makedirs(CHAOS_DIR, exist_ok=True)
    with open(CHAOS_FILE, "w") as f:
        f.write(fault_type)

    if fault_type == "none":
        _set_status("monitoring")
        return {"status": "chaos cleared"}

    anomaly = DEMO_TRIGGERS[fault_type].format(
        ts=datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    )

    image_path = None
    if fault_type == "memory_leak":
        candidate = os.path.join(BASE_DIR, "assets", "grafana_memory_spike.png")
        if os.path.exists(candidate):
            image_path = candidate

    started = await _start_investigation(anomaly, image_path)
    return {
        "status":    "investigation started" if started else "already investigating",
        "fault":     fault_type,
        "anomaly":   anomaly[:120],
    }


@app.post("/trigger/custom")
async def trigger_custom(body: dict):
    anomaly = body.get("anomaly", "")
    if not anomaly:
        return {"error": "anomaly field required"}
    started = await _start_investigation(anomaly)
    return {"status": "started" if started else "already investigating"}


# ── Startup ────────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    global _event_loop
    _event_loop = asyncio.get_running_loop()
    asyncio.create_task(_metrics_loop())
    asyncio.create_task(_monitor_loop())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY not set. Create agent/.env with your key.")
        sys.exit(1)
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="info")
