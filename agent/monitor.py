"""
monitor.py — continuous anomaly detection

Polls the service logs every POLL_INTERVAL seconds.
When a threshold is breached it returns an anomaly description
that the main agent loop passes to the investigator.

Thresholds (tunable via env vars):
  ERROR_RATE_THRESHOLD   default 15%   — % of requests returning 4xx/5xx
  MEMORY_DELTA_THRESHOLD default 80 MB — memory growth in last 5 minutes
  LATENCY_THRESHOLD_MS   default 1500  — p95 latency
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta

from tools import get_error_rate, get_latency_stats, get_memory_trend

ERROR_RATE_THRESHOLD   = float(os.getenv("ERROR_RATE_THRESHOLD",   "15"))
MEMORY_DELTA_THRESHOLD = float(os.getenv("MEMORY_DELTA_THRESHOLD", "80"))
LATENCY_THRESHOLD_MS   = float(os.getenv("LATENCY_THRESHOLD_MS",   "1500"))
POLL_INTERVAL          = float(os.getenv("POLL_INTERVAL",          "5"))
WINDOW_MINUTES         = 1   # look at last 3 minutes for rate calculations


def _parse(result: str) -> dict:
    try:
        return json.loads(result)
    except json.JSONDecodeError:
        return {}


def check_once() -> str | None:
    """
    Run one anomaly-detection pass across all services.
    Returns an anomaly description string if something is wrong, else None.
    """
    anomalies = []

    for service in ("api", "frontend"):
        # ── error rate ─────────────────────────────────────────────────────
        er = _parse(get_error_rate(service, WINDOW_MINUTES))
        rate = er.get("error_rate_pct", 0)
        if rate >= ERROR_RATE_THRESHOLD:
            total   = er.get("total_requests", "?")
            errors  = er.get("errors", "?")
            anomalies.append(
                f"High error rate on {service}: {rate}% "
                f"({errors}/{total} requests failing in last {WINDOW_MINUTES}m)"
            )

        # ── latency ────────────────────────────────────────────────────────
        ls = _parse(get_latency_stats(service, WINDOW_MINUTES))
        p95 = ls.get("p95_ms", 0)
        if p95 >= LATENCY_THRESHOLD_MS:
            anomalies.append(
                f"High latency on {service}: p95={p95}ms "
                f"(threshold {LATENCY_THRESHOLD_MS}ms)"
            )

        # ── memory growth ──────────────────────────────────────────────────
        mt = _parse(get_memory_trend(service, window_minutes=5))
        delta = mt.get("delta_mb", 0)
        last  = mt.get("last_mb", 0)
        if delta >= MEMORY_DELTA_THRESHOLD:
            anomalies.append(
                f"Memory growing rapidly on {service}: "
                f"+{delta}MB in 5 minutes (now {last}MB)"
            )

    if not anomalies:
        return None

    ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    summary = "\n".join(f"  • {a}" for a in anomalies)
    return (
        f"[{ts}] Anomaly detected:\n{summary}\n\n"
        f"Investigate root cause, determine blast radius, and recommend remediation."
    )


def run(on_anomaly, poll_interval: float = POLL_INTERVAL):
    """
    Poll continuously.  Calls on_anomaly(description) when something is wrong.
    Will not re-trigger for the same incident until the system recovers
    (error rate drops back below threshold).
    """
    from rich.console import Console
    console = Console()

    console.print(
        f"\n  [bold green]Resolve monitor started[/bold green]  "
        f"— polling every {poll_interval}s\n"
        f"  Thresholds: error_rate>{ERROR_RATE_THRESHOLD}%  "
        f"p95>{LATENCY_THRESHOLD_MS}ms  "
        f"memory_delta>{MEMORY_DELTA_THRESHOLD}MB\n"
    )

    incident_active = False

    while True:
        anomaly = check_once()

        if anomaly and not incident_active:
            incident_active = True
            on_anomaly(anomaly)

        elif not anomaly and incident_active:
            incident_active = False
            console.print(
                "\n  [bold green]✓ System recovered — monitoring resumed[/bold green]\n"
            )

        elif not anomaly:
            now = datetime.now().strftime("%H:%M:%S")
            console.print(f"  [dim]{now}  all services healthy[/dim]", end="\r")

        time.sleep(poll_interval)
