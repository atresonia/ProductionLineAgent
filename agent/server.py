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
    POST /trigger/{fault_type}       — inject single fault + start investigation
    POST /trigger                    — body: {"faults": ["bad_deploy", "slow_db"]}
    POST /trigger/custom             — body: {"anomaly": "..."}
    GET  /health                     — liveness
    GET  /status                     — current agent state
"""

import asyncio
import json
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

# ── Path setup — server.py lives in agent/, project root is parent ────────────
AGENT_DIR   = os.path.dirname(os.path.abspath(__file__))
BASE_DIR    = os.path.dirname(AGENT_DIR)
LOG_DIR     = os.path.join(BASE_DIR, "logs")
CHAOS_DIR   = os.path.join(BASE_DIR, "chaos")
CHAOS_FILE  = os.path.join(CHAOS_DIR, "current_fault")   # legacy
CHAOS_JSON  = os.path.join(CHAOS_DIR, "faults.json")     # new primary

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
_investigation_lock   = threading.Lock()
_investigation_running = False

# Multi-incident tracking
_incident_total:   int = 0   # how many anomalies in the current triage session
_incident_current: int = 0   # which one is being investigated (1-based)

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
    "catalog_down": (
        "Anomaly detected:\n"
        "  • [CRITICAL] High error rate on api/products: ~95% requests returning 503\n"
        "  • /checkout appears unaffected\n\n"
        "Investigate root cause, determine blast radius, and recommend remediation."
    ),
    "checkout_degraded": (
        "Anomaly detected:\n"
        "  • [HIGH] Intermittent error rate on api/checkout: ~40% requests returning 500\n"
        "  • /products appears unaffected\n\n"
        "Investigate root cause, determine blast radius, and recommend remediation."
    ),
}

# Multi-fault composite trigger descriptions
MULTI_FAULT_TRIGGERS = {
    frozenset(["bad_deploy", "slow_db"]): (
        "2 anomalies detected simultaneously:\n"
        "  • [CRITICAL] High error rate on api: /checkout returning 500s (~87% error rate)\n"
        "  • [MEDIUM] High latency on api: p95=2800ms (threshold 1500ms) — slow DB queries\n\n"
        "Triage by severity. Investigate the checkout error rate first (critical), "
        "then check if the latency anomaly self-resolved."
    ),
    frozenset(["bad_deploy", "memory_leak"]): (
        "2 anomalies detected simultaneously:\n"
        "  • [CRITICAL] High error rate on api: /checkout returning 500s (~85% error rate)\n"
        "  • [HIGH] Memory growing rapidly on api: +200MB in 5 minutes (now 890MB)\n\n"
        "Triage by severity. Investigate the checkout error rate first (critical)."
    ),
    frozenset(["slow_db", "memory_leak"]): (
        "2 anomalies detected simultaneously:\n"
        "  • [HIGH] Memory growing rapidly on api: +200MB in 5 minutes (now 890MB)\n"
        "  • [MEDIUM] High latency on api: p95=2800ms (threshold 1500ms) — slow DB queries\n\n"
        "Triage by severity. Investigate the memory growth first (high), "
        "then check if the latency anomaly self-resolved."
    ),
    frozenset(["catalog_down", "checkout_degraded"]): (
        "2 endpoint anomalies detected on api:\n"
        "  • [CRITICAL/BIZ:MEDIUM] High error rate on api/products: ~95% requests returning 503\n"
        "  • [HIGH/BIZ:CRITICAL] Intermittent error rate on api/checkout: ~40% requests returning 500\n\n"
        "IMPORTANT: Call read_triage_config first. Despite /products having a higher error rate, "
        "the team's config marks /checkout as revenue-critical ($5,600/min). "
        "Apply the 'Revenue-critical override' rule — investigate /checkout first."
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
        text_lower = text.strip().lower()

        # Detect Phase 1 plan
        if text.strip().startswith("Plan:"):
            _emit({
                "type":      "plan",
                "text":      text.strip()[5:].strip(),
                "timestamp": ts,
            })
        # Detect triage statement embedded in plan text
        elif "triage:" in text_lower:
            _emit({
                "type":      "plan",
                "text":      text.strip(),
                "timestamp": ts,
            })
        else:
            _emit({
                "type":      "reasoning",
                "text":      text,
                "timestamp": ts,
            })

        # Detect incident switch (agent moves to second anomaly)
        if any(phrase in text_lower for phrase in (
            "now investigating", "moving to", "next anomaly",
            "investigating the second", "investigating 2/2",
            "turning to the", "address the second",
        )):
            global _incident_current
            if _incident_current < _incident_total:
                _incident_current += 1
            _emit({
                "type":       "incident_switch",
                "from_index": _incident_current - 1,
                "to_index":   _incident_current,
                "total":      _incident_total,
                "reason":     text.strip()[:120],
                "timestamp":  ts,
            })
            _set_status(f"investigating_{_incident_current}_{_incident_total}")

        # Detect cascade resolution
        if any(phrase in text_lower for phrase in (
            "cascade resolution", "side effect", "resolved as a side effect",
            "appears to have resolved", "self-resolved", "self resolved",
        )):
            _emit({
                "type":       "cascade_resolved",
                "reason":     text.strip()[:200],
                "timestamp":  ts,
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


# ── Chaos file helpers ─────────────────────────────────────────────────────────

def _read_faults() -> list[str]:
    """Return the list of currently active faults."""
    try:
        with open(CHAOS_JSON) as f:
            data = json.load(f)
            faults = data.get("active_faults", [])
            if isinstance(faults, list):
                return faults
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    # Legacy fallback
    try:
        with open(CHAOS_FILE) as f:
            fault = f.read().strip()
            if fault and fault != "none":
                return [fault]
    except FileNotFoundError:
        pass
    return []


def _read_chaos() -> str:
    """Return active faults as a comma-separated string, or 'none'."""
    faults = _read_faults()
    return ",".join(faults) if faults else "none"


def _write_faults(faults: list[str]) -> None:
    """Write active fault list to both JSON and legacy file."""
    os.makedirs(CHAOS_DIR, exist_ok=True)
    with open(CHAOS_JSON, "w") as f:
        json.dump({"active_faults": faults}, f)
    with open(CHAOS_FILE, "w") as f:
        f.write(faults[0] if faults else "none")


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


async def _metrics_loop() -> None:
    """Background task: poll metrics every 5s and broadcast to all clients."""
    while True:
        try:
            faults = _read_faults()
            await _broadcast({
                "type":      "metrics",
                "timestamp": _now(),
                "fault":     faults[0] if faults else "none",   # primary (legacy)
                "faults":    faults,                            # full list
                "api":       _compute_metrics("api"),
                "frontend":  _compute_metrics("frontend"),
            })
        except Exception:
            pass
        await asyncio.sleep(5)


_COLLECTION_WINDOW_SECS = 10.0


async def _monitor_loop() -> None:
    """
    Background task: mirrors monitor.py's anomaly detection.
    Polls check_once() every 5s; auto-starts investigation when threshold breached.
    Debounces — won't re-trigger while an investigation is already running.

    Collection window: on first anomaly detection (healthy → unhealthy) waits
    _COLLECTION_WINDOW_SECS before triggering so slower-building faults have time
    to cross their thresholds.  Emits {type: "collecting"} WS events during the
    window so the UI can show a "Scanning for additional issues…" indicator.
    """
    from monitor import check_once, _build_anomaly_string
    ready            = True
    collecting       = False
    collection_start = 0.0
    # Keyed by (service, metric, endpoint) to deduplicate across polls
    collected: dict[tuple[str, str, str], dict] = {}

    while True:
        try:
            anomalies = await asyncio.get_running_loop().run_in_executor(None, check_once)

            if anomalies and not _investigation_running and ready:
                if not collecting:
                    # Healthy → unhealthy: start collection window
                    collecting       = True
                    collection_start = time.monotonic()
                    for a in anomalies:
                        collected.setdefault((a["service"], a["metric"], a.get("endpoint") or ""), a)
                    await _broadcast({
                        "type":              "collecting",
                        "anomalies_so_far":  list(collected.values()),
                        "seconds_remaining": int(_COLLECTION_WINDOW_SECS),
                        "timestamp":         _now(),
                    })
                else:
                    # Still in window — accumulate any new anomaly types
                    for a in anomalies:
                        collected.setdefault((a["service"], a["metric"], a.get("endpoint") or ""), a)

                    elapsed           = time.monotonic() - collection_start
                    seconds_remaining = max(0, int(_COLLECTION_WINDOW_SECS - elapsed))

                    if elapsed >= _COLLECTION_WINDOW_SECS:
                        collecting    = False
                        ready         = False
                        all_anomalies = list(collected.values())
                        collected     = {}
                        anomaly_str   = _build_anomaly_string(all_anomalies)
                        await _start_investigation(anomaly_str, anomalies=all_anomalies)
                    else:
                        await _broadcast({
                            "type":              "collecting",
                            "anomalies_so_far":  list(collected.values()),
                            "seconds_remaining": seconds_remaining,
                            "timestamp":         _now(),
                        })

            elif not anomalies and collecting:
                # Cleared before window elapsed — cancel
                collecting = False
                collected  = {}

            elif not anomalies and not ready:
                ready      = True
                collecting = False
                collected  = {}
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

def _run_investigation(anomaly: str,
                       image_path: Optional[str] = None,
                       anomalies: Optional[list[dict]] = None) -> None:
    """Runs in a thread pool. Streams events via _on_event callbacks."""
    global _investigation_running, _approval_decision
    global _incident_total, _incident_current

    # Install callbacks
    investigator._event_callback    = _on_event
    investigator._approval_callback = _approval_cb

    # Set up multi-incident tracking
    if anomalies and len(anomalies) > 1:
        severity_order = {"critical": 0, "high": 1, "medium": 2}
        # Sort by business_priority first (when configured), then technical severity
        sorted_anomalies = sorted(
            anomalies,
            key=lambda a: (
                severity_order.get(a.get("business_priority", a.get("severity", "medium")), 2),
                severity_order.get(a.get("severity", "medium"), 2),
            )
        )
        _incident_total   = len(sorted_anomalies)
        _incident_current = 1

        # Emit triage event before investigation starts
        _emit({
            "type":           "triage",
            "anomalies":      sorted_anomalies,
            "priority_order": [a["id"] for a in sorted_anomalies],
            "count":          len(sorted_anomalies),
            "timestamp":      _now(),
        })
    else:
        _incident_total   = 1
        _incident_current = 1

    try:
        result = investigator.investigate(
            anomaly,
            image_path=image_path,
            require_approval=True,
        )
        conclusion = result.get("conclusion", "")

        # Generate post-mortem (with anomaly list for triage section)
        from postmortem import generate as gen_pm
        detected_at  = datetime.now(timezone.utc)
        resolved_at  = datetime.now(timezone.utc)
        pm_path      = gen_pm(conclusion, detected_at, resolved_at,
                              anomalies=anomalies)

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
        _investigation_running = False
        _incident_total        = 0
        _incident_current      = 0
        investigator._event_callback    = None
        investigator._approval_callback = None


async def _start_investigation(anomaly: str,
                               image_path: Optional[str] = None,
                               anomalies: Optional[list[dict]] = None) -> bool:
    global _investigation_running
    if _investigation_running:
        return False
    _investigation_running = True
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _run_investigation, anomaly, image_path, anomalies)
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
    faults = _read_faults()
    return {
        "agent_status": _agent_status,
        "fault":        faults[0] if faults else "none",
        "faults":       faults,
        "metrics": {
            "api":      _compute_metrics("api"),
            "frontend": _compute_metrics("frontend"),
        },
    }


class MultiFaultBody(BaseModel):
    faults: list[str]


@app.post("/trigger")
async def trigger_multi(body: MultiFaultBody):
    """Inject multiple faults simultaneously and start a triage investigation."""
    faults = body.faults
    if not faults:
        return {"error": "faults list must be non-empty"}

    valid_faults = set(DEMO_TRIGGERS.keys())
    invalid = [f for f in faults if f not in valid_faults]
    if invalid:
        return {"error": f"Unknown faults: {invalid}. Valid: {sorted(valid_faults)}"}

    # Write chaos files
    _write_faults(faults)

    fault_set = frozenset(faults)
    if fault_set in MULTI_FAULT_TRIGGERS:
        anomaly = MULTI_FAULT_TRIGGERS[fault_set]
    else:
        bullets = "\n".join(
            f"  • {DEMO_TRIGGERS[f].split(chr(10))[1].strip()}" for f in faults
        )
        anomaly = (
            f"{len(faults)} faults injected simultaneously:\n{bullets}\n\n"
            "Triage by severity and investigate in priority order."
        )

    # Build synthetic anomaly dicts for triage events
    severity_map = {
        "bad_deploy":        ("critical", "error_rate",   87.0,   None,        "high",   "Core application server"),
        "db_down":           ("critical", "error_rate",   100.0,  None,        "critical", "Single dependency — all services cascade"),
        "memory_leak":       ("high",     "memory_delta", 200.0,  None,        "high",   "Core application server"),
        "slow_db":           ("medium",   "p95_latency",  2800.0, None,        "high",   "Core application server"),
        "catalog_down":      ("critical", "error_rate",   95.0,   "/products", "medium", "Product catalog — no direct revenue loss"),
        "checkout_degraded": ("high",     "error_rate",   40.0,   "/checkout", "critical", "Payment processing — $5,600/min downtime cost"),
    }
    import uuid as _uuid
    anomaly_dicts = []
    for f in faults:
        sm = severity_map.get(f, ("high", "unknown", 0, None, "high", ""))
        severity, metric, value, endpoint, biz_pri, biz_reason = sm
        anomaly_dicts.append({
            "id":                str(_uuid.uuid4()),
            "description":       DEMO_TRIGGERS[f].split("\n")[1].strip().lstrip("• "),
            "severity":          severity,
            "service":           "api",
            "endpoint":          endpoint,
            "metric":            metric,
            "value":             value,
            "business_priority": biz_pri,
            "business_reason":   biz_reason,
        })

    image_path = None
    if "memory_leak" in faults:
        candidate = os.path.join(BASE_DIR, "assets", "grafana_memory_spike.png")
        if os.path.exists(candidate):
            image_path = candidate

    started = await _start_investigation(anomaly, image_path, anomaly_dicts)
    return {
        "status":  "investigation started" if started else "already investigating",
        "faults":  faults,
        "anomaly": anomaly[:160],
    }


@app.post("/trigger/{fault_type}")
async def trigger(fault_type: str):
    """Single-fault trigger — backward compatible with existing UI."""
    if fault_type not in DEMO_TRIGGERS and fault_type != "none":
        return {"error": f"Unknown fault type: {fault_type}. "
                         f"Valid: {list(DEMO_TRIGGERS.keys())}"}

    # Inject chaos
    if fault_type == "none":
        _write_faults([])
        _set_status("monitoring")
        return {"status": "chaos cleared"}

    _write_faults([fault_type])

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
