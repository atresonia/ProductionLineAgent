"""
monitor.py — continuous anomaly detection + proactive prevention

Polls the service logs every POLL_INTERVAL seconds.
When thresholds are breached it returns a list of anomaly dicts,
each with id, description, severity, service, metric, and value.

Proactive prevention: tracks a sliding window of metric readings and
emits early warnings when metrics are trending toward a threshold —
before an incident actually fires.

Thresholds (tunable via env vars):
  ERROR_RATE_THRESHOLD   default 15%   — % of requests returning 4xx/5xx
  MEMORY_DELTA_THRESHOLD default 80 MB — memory growth in last 5 minutes
  LATENCY_THRESHOLD_MS   default 1500  — p95 latency

Severity rules:
  critical : error rate > 50% on any service
  high     : error rate > 15%, or memory delta > 80 MB
  medium   : p95 latency > 1500ms
"""

import json
import os
import time
import uuid
from datetime import datetime, timezone, timedelta

from tools import get_error_rate, get_latency_stats, get_memory_trend, get_endpoint_error_rates

try:
    from config import get_endpoint_priority, get_service_priority
    _config_available = True
except Exception:
    _config_available = False

    def get_service_priority(service: str) -> dict:
        return {"priority": "high", "reason": "default — no triage config loaded"}

    def get_endpoint_priority(service: str, endpoint: str) -> dict:
        return {"priority": "high", "reason": "default — no triage config loaded"}

ERROR_RATE_THRESHOLD   = float(os.getenv("ERROR_RATE_THRESHOLD",   "15"))
MEMORY_DELTA_THRESHOLD = float(os.getenv("MEMORY_DELTA_THRESHOLD", "80"))
LATENCY_THRESHOLD_MS   = float(os.getenv("LATENCY_THRESHOLD_MS",   "1500"))
POLL_INTERVAL          = float(os.getenv("POLL_INTERVAL",          "5"))
WINDOW_MINUTES         = 1   # look at last minute for rate calculations

# Proactive prevention: sliding window of recent readings per service
TREND_WINDOW     = 4      # consecutive readings to confirm a trend
TREND_WARN_RATIO = 0.5    # warn when metric is at 50%+ of threshold and rising
_metric_history: dict[str, list] = {
    "api":      [],   # list of {"error_rate": float, "p95": float}
    "frontend": [],
}


def _parse(result: str) -> dict:
    try:
        return json.loads(result)
    except json.JSONDecodeError:
        return {}


def _biz(service: str, endpoint: str | None = None) -> tuple[str, str]:
    """Return (business_priority, business_reason) for a service/endpoint."""
    if endpoint:
        p = get_endpoint_priority(service, endpoint)
    else:
        p = get_service_priority(service)
    return p["priority"], p["reason"]


# ML predictor snapshots queued between check_once() and check_ml_predictions()
_ml_pending: dict[str, tuple] = {}


def _ml_snapshot(service: str, error_rate: float, p95: float, memory: float) -> None:
    """Feed a new reading into the ML predictor (silent — never raises)."""
    try:
        _ml_pending[service] = (error_rate, p95, memory)
    except Exception:
        pass


def check_ml_predictions() -> str | None:
    """
    Run the IsolationForest predictor on the latest snapshots.
    Returns a warning string if any service looks anomalous, else None.
    Called every poll cycle alongside check_trends() — never triggers an incident.
    """
    try:
        from predictor import predict, format_warning  # noqa
    except ImportError:
        return None

    warnings = []
    for service, (err, p95, mem) in list(_ml_pending.items()):
        pred = predict(service, err, p95, mem)
        if pred:
            warnings.append(format_warning(pred))

    if not warnings:
        return None

    ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    return f"[{ts}] ML prediction — anomalous pattern detected:\n" + "\n".join(warnings)


def _record_metrics(service: str, error_rate: float, p95: float) -> None:
    """Append latest reading to the sliding window (keep last TREND_WINDOW+1)."""
    history = _metric_history[service]
    history.append({"error_rate": error_rate, "p95": p95})
    if len(history) > TREND_WINDOW + 1:
        history.pop(0)


def _is_trending_up(values: list[float]) -> bool:
    """Return True if values are strictly increasing."""
    return len(values) >= TREND_WINDOW and all(
        values[i] < values[i + 1] for i in range(len(values) - 1)
    )


def check_trends() -> str | None:
    """
    Scan metric history for deteriorating trends that haven't yet hit thresholds.
    Returns a warning string if a trend is detected, else None.
    Called every poll cycle alongside check_once().
    """
    warnings = []

    for service in ("api", "frontend"):
        history = _metric_history[service]
        if len(history) < TREND_WINDOW:
            continue

        recent_err = [h["error_rate"] for h in history[-TREND_WINDOW:]]
        recent_p95 = [h["p95"]        for h in history[-TREND_WINDOW:]]
        current_err = recent_err[-1]
        current_p95 = recent_p95[-1]

        # Error rate: trending up AND above 50% of threshold but below it
        warn_err_floor = ERROR_RATE_THRESHOLD * TREND_WARN_RATIO
        if (_is_trending_up(recent_err)
                and warn_err_floor <= current_err < ERROR_RATE_THRESHOLD):
            delta = current_err - recent_err[0]
            eta = ((ERROR_RATE_THRESHOLD - current_err) / delta * POLL_INTERVAL / 60
                   if delta > 0 else None)
            msg = (f"error rate on {service} trending up "
                   f"({recent_err[0]:.1f}% → {current_err:.1f}%)")
            if eta:
                msg += f", ~{eta:.1f}m to threshold"
            warnings.append(msg)

        # Latency: trending up AND above 50% of threshold but below it
        warn_lat_floor = LATENCY_THRESHOLD_MS * TREND_WARN_RATIO
        if (_is_trending_up(recent_p95)
                and warn_lat_floor <= current_p95 < LATENCY_THRESHOLD_MS):
            delta = current_p95 - recent_p95[0]
            eta = ((LATENCY_THRESHOLD_MS - current_p95) / delta * POLL_INTERVAL / 60
                   if delta > 0 else None)
            msg = (f"p95 latency on {service} trending up "
                   f"({recent_p95[0]:.0f}ms → {current_p95:.0f}ms)")
            if eta:
                msg += f", ~{eta:.1f}m to threshold"
            warnings.append(msg)

    if not warnings:
        return None

    ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    summary = "\n".join(f"  ⚠ {w}" for w in warnings)
    return f"[{ts}] Proactive warning — deteriorating trend detected:\n{summary}"


def check_once() -> list[dict] | None:
    """
    Run one anomaly-detection pass across all services.

    Returns a list of anomaly dicts if anything is wrong, else None.
    Each dict: {id, description, severity, service, endpoint, metric, value,
                business_priority, business_reason}
    Severity: critical (error > 50%), high (error > 15% or mem delta > 80MB),
              medium (p95 > 1500ms).

    Endpoint-level anomalies are reported separately when per-endpoint breakdown
    shows divergent failure rates across endpoints on the same service.
    Also records metrics into the sliding window for trend/ML detection.
    """
    anomalies: list[dict] = []

    for service in ("api", "frontend"):
        svc_biz_pri, svc_biz_reason = _biz(service)

        # ── per-endpoint error rates (api only — api has meaningful endpoints) ──
        ep_anomalies_added: set[str] = set()
        ep_rates: dict[str, float] = {}
        _svc_rate = 0.0  # aggregate error rate for trend/ML recording
        if service == "api":
            ep_data = _parse(get_endpoint_error_rates(service, WINDOW_MINUTES))
            endpoints = ep_data.get("endpoints", {})
            # Only report endpoint-level anomalies when endpoints diverge meaningfully
            ep_rates = {
                ep: info["error_rate_pct"]
                for ep, info in endpoints.items()
                if ep not in ("/health", "/metrics") and info.get("total", 0) >= 3
            }
            if ep_rates:
                max_rate = max(ep_rates.values())
                min_rate = min(ep_rates.values())
                # Endpoints are diverging — report each failing endpoint separately
                if max_rate - min_rate >= 20 and max_rate >= ERROR_RATE_THRESHOLD:
                    for ep, rate in ep_rates.items():
                        if rate >= ERROR_RATE_THRESHOLD:
                            ep_total  = endpoints[ep]["total"]
                            ep_errors = endpoints[ep]["errors"]
                            severity  = "critical" if rate > 50 else "high"
                            bp, br    = _biz(service, ep)
                            anomalies.append({
                                "id":                str(uuid.uuid4()),
                                "description":       (
                                    f"High error rate on {service}{ep}: {rate}% "
                                    f"({ep_errors}/{ep_total} requests failing in last {WINDOW_MINUTES}m)"
                                ),
                                "severity":          severity,
                                "service":           service,
                                "endpoint":          ep,
                                "metric":            "error_rate",
                                "value":             rate,
                                "business_priority": bp,
                                "business_reason":   br,
                            })
                            ep_anomalies_added.add(ep)

        # ── aggregate service error rate (skip if any endpoint-level data covers it) ──
        # Even if endpoints didn't diverge enough to report individually, suppress the
        # service-level aggregate when any endpoint is above threshold — endpoint data
        # is strictly more informative and prevents 3-anomaly accumulation across polls.
        any_ep_above_threshold = bool(ep_rates) and any(
            rate >= ERROR_RATE_THRESHOLD for rate in ep_rates.values()
        )
        if not ep_anomalies_added and not any_ep_above_threshold:
            er    = _parse(get_error_rate(service, WINDOW_MINUTES))
            rate  = er.get("error_rate_pct", 0)
            _svc_rate = rate
            if rate >= ERROR_RATE_THRESHOLD:
                total  = er.get("total_requests", "?")
                errors = er.get("errors", "?")
                severity = "critical" if rate > 50 else "high"
                anomalies.append({
                    "id":                str(uuid.uuid4()),
                    "description":       (
                        f"High error rate on {service}: {rate}% "
                        f"({errors}/{total} requests failing in last {WINDOW_MINUTES}m)"
                    ),
                    "severity":          severity,
                    "service":           service,
                    "endpoint":          None,
                    "metric":            "error_rate",
                    "value":             rate,
                    "business_priority": svc_biz_pri,
                    "business_reason":   svc_biz_reason,
                })

        # ── latency ────────────────────────────────────────────────────────
        ls  = _parse(get_latency_stats(service, WINDOW_MINUTES))
        p95 = ls.get("p95_ms", 0)
        if p95 >= LATENCY_THRESHOLD_MS:
            anomalies.append({
                "id":                str(uuid.uuid4()),
                "description":       (
                    f"High latency on {service}: p95={p95}ms "
                    f"(threshold {LATENCY_THRESHOLD_MS}ms)"
                ),
                "severity":          "medium",
                "service":           service,
                "endpoint":          None,
                "metric":            "p95_latency",
                "value":             p95,
                "business_priority": svc_biz_pri,
                "business_reason":   svc_biz_reason,
            })

        # ── memory growth ──────────────────────────────────────────────────
        mt    = _parse(get_memory_trend(service, window_minutes=5))
        delta = mt.get("delta_mb", 0)
        last  = mt.get("last_mb", 0)
        if delta >= MEMORY_DELTA_THRESHOLD:
            anomalies.append({
                "id":                str(uuid.uuid4()),
                "description":       (
                    f"Memory growing rapidly on {service}: "
                    f"+{delta}MB in 5 minutes (now {last}MB)"
                ),
                "severity":          "high",
                "service":           service,
                "endpoint":          None,
                "metric":            "memory_delta",
                "value":             delta,
                "business_priority": svc_biz_pri,
                "business_reason":   svc_biz_reason,
            })

        # Record for trend/ML — only when healthy to avoid polluting baselines
        if _svc_rate < ERROR_RATE_THRESHOLD and p95 < LATENCY_THRESHOLD_MS:
            _record_metrics(service, _svc_rate, p95)
            _ml_snapshot(service, _svc_rate, p95, last)

    return anomalies if anomalies else None


def _build_anomaly_string(anomalies: list[dict]) -> str:
    """Convert list of anomaly dicts to a human-readable investigation prompt."""
    ts      = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    bullets = []
    for a in anomalies:
        biz = a.get("business_priority", "")
        biz_note = f" [BIZ:{biz.upper()}]" if biz else ""
        bullets.append(f"  • [{a['severity'].upper()}]{biz_note} {a['description']}")
    return (
        f"[{ts}] {len(anomalies)} anomaly/anomalies detected:\n"
        + "\n".join(bullets)
        + "\n\nCall read_triage_config first to load the team's priority rules, "
        "then triage by business priority and severity."
    )


COLLECTION_WINDOW_SECS = 10.0


def run(on_anomaly, poll_interval: float = POLL_INTERVAL,
        on_warning=None):
    """
    Poll continuously.  Calls on_anomaly(description) when something is wrong.
    Calls on_warning(description) for proactive trend warnings (optional).
    Will not re-trigger for the same incident until the system recovers
    (anomalies drop back to None).

    Collection window: on first anomaly detection (healthy → unhealthy), waits
    COLLECTION_WINDOW_SECS before triggering so slower-building faults have time
    to cross their thresholds.  All unique anomalies accumulated across polls in
    that window are passed to on_anomaly together.
    """
    from rich.console import Console
    console = Console()

    console.print(
        f"\n  [bold green]Resolve monitor started[/bold green]  "
        f"— polling every {poll_interval}s\n"
        f"  Thresholds: error_rate>{ERROR_RATE_THRESHOLD}%  "
        f"p95>{LATENCY_THRESHOLD_MS}ms  "
        f"memory_delta>{MEMORY_DELTA_THRESHOLD}MB\n"
        f"  Proactive prevention: watching for trends (window={TREND_WINDOW} readings)\n"
    )

    incident_active    = False
    collecting         = False
    collection_start   = 0.0
    recovery_until     = 0.0   # grace period after incident_active flips False
    # Keyed by (service, metric, endpoint) to deduplicate across polls
    collected: dict[tuple[str, str, str], dict] = {}

    while True:
        anomalies = check_once()

        in_grace = time.time() < recovery_until

        if anomalies and not incident_active and not in_grace:
            if not collecting:
                # Healthy → unhealthy: start collection window
                collecting       = True
                collection_start = time.time()
                for a in anomalies:
                    collected.setdefault((a["service"], a["metric"], a.get("endpoint") or ""), a)
                console.print(
                    "\n  [bold yellow]⚠ Anomaly detected — collecting for "
                    f"{int(COLLECTION_WINDOW_SECS)}s to check for additional "
                    "issues...[/bold yellow]"
                )
            else:
                # Still in window — accumulate any new anomaly types
                for a in anomalies:
                    collected.setdefault((a["service"], a["metric"], a.get("endpoint") or ""), a)

                elapsed = time.time() - collection_start
                if elapsed >= COLLECTION_WINDOW_SECS:
                    collecting      = False
                    incident_active = True
                    all_anomalies   = list(collected.values())
                    collected       = {}
                    count           = len(all_anomalies)
                    noun            = "anomaly" if count == 1 else "anomalies"
                    console.print(
                        f"\n  [bold red]Collection complete — {count} {noun} "
                        f"detected, starting triage.[/bold red]\n"
                    )
                    on_anomaly(_build_anomaly_string(all_anomalies))

                    # Post-investigation cooldown: give the sliding window 60s to
                    # flush stale pre-fix errors before accepting a new incident.
                    console.print(
                        "\n  [bold cyan]Incident resolved — cooldown 60s before "
                        "resuming monitoring...[/bold cyan]\n"
                    )
                    time.sleep(60)

        elif not anomalies and collecting:
            # Cleared before window elapsed — cancel quietly
            collecting = False
            collected  = {}
            console.print(
                "\n  [dim]Anomalies cleared during collection window "
                "— monitoring resumed[/dim]\n"
            )

        elif not anomalies and incident_active:
            incident_active = False
            # 30-second grace period: stale errors may still be in the sliding
            # window even though the system has actually recovered.
            recovery_until  = time.time() + 30
            # Clear rule-based history and ML predictor after recovery
            for svc in _metric_history:
                _metric_history[svc].clear()
            try:
                from predictor import reset as ml_reset
                ml_reset()
            except Exception:
                pass
            console.print(
                "\n  [bold green]✓ System recovered — 30s grace period before "
                "re-arming monitor[/bold green]\n"
            )

        elif not anomalies:
            # Rule-based trend check
            warning = check_trends()
            if warning:
                console.print(f"\n  [bold yellow]{warning}[/bold yellow]\n")
                if on_warning:
                    on_warning(warning)

            # ML-based prediction check
            ml_warning = check_ml_predictions()
            if ml_warning:
                console.print(f"\n  [bold magenta]{ml_warning}[/bold magenta]\n")
                if on_warning:
                    on_warning(ml_warning)
            now = datetime.now().strftime("%H:%M:%S")
            if in_grace:
                remaining = int(recovery_until - time.time())
                console.print(
                    f"  [dim]{now}  grace period ({remaining}s remaining)[/dim]",
                    end="\r",
                )
            else:
                console.print(f"  [dim]{now}  all services healthy[/dim]", end="\r")

        time.sleep(poll_interval)
