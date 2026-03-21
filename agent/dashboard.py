"""
dashboard.py — generate a Grafana-style dashboard PNG from real log data.

Reads api.log and frontend.log, extracts time-series metrics, and renders
a 3-panel chart:
  - Top:    Error rate (%) per minute
  - Middle: p95 latency (ms) per minute
  - Bottom: Memory (MB) over time
"""

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # non-interactive backend — safe in agent subprocess
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

LOG_DIR    = os.getenv("LOG_DIR",    "./logs")
ASSETS_DIR = os.getenv("ASSETS_DIR", "./assets")

# ── Dark theme constants (matches generate_dashboard.py) ──────────────────────
BG        = "#111827"
PANEL_BG  = "#1F2937"
GRID_COL  = "#374151"
LABEL_COL = "#9CA3AF"
TEXT_COL  = "#D1D5DB"
TITLE_COL = "#F9FAFB"

GREEN  = "#34D399"
RED    = "#EF4444"
YELLOW = "#FBBF24"
BLUE   = "#60A5FA"

ERROR_THRESHOLD_PCT = 15.0
LATENCY_THRESHOLD_MS = 1500.0

log = logging.getLogger(__name__)


# ── Log parsing helpers ────────────────────────────────────────────────────────

def _read_lines(service: str) -> list[str]:
    path = os.path.join(LOG_DIR, f"{service}.log")
    try:
        with open(path) as f:
            return f.readlines()
    except FileNotFoundError:
        return []


def _parse_entries(lines: list[str], since: datetime) -> list[dict]:
    """Parse JSON log lines; silently skip non-JSON and entries before `since`."""
    entries: list[dict] = []
    for line in lines:
        try:
            e = json.loads(line)
            ts_str = e.get("timestamp")
            if not ts_str:
                continue
            ts = datetime.fromisoformat(ts_str)
            # Normalise to UTC-aware for comparison
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < since:
                continue
            e["_ts"] = ts
            entries.append(e)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return entries


# ── Bucket helpers ─────────────────────────────────────────────────────────────

def _minute_key(ts: datetime) -> datetime:
    """Floor a timestamp to the minute."""
    return ts.replace(second=0, microsecond=0)


def _build_buckets(
    entries: list[dict],
    start: datetime,
    window_minutes: int,
) -> list[datetime]:
    """Return an ordered list of per-minute bucket keys covering the window."""
    return [start + timedelta(minutes=i) for i in range(window_minutes)]


def _compute_error_rate(
    entries: list[dict], buckets: list[datetime]
) -> list[float]:
    """Error rate (%) per minute bucket. Zero when no requests."""
    req_counts: dict[datetime, int] = defaultdict(int)
    err_counts: dict[datetime, int] = defaultdict(int)

    for e in entries:
        if e.get("event") != "request":
            continue
        key = _minute_key(e["_ts"])
        req_counts[key] += 1
        if e.get("status_code", 200) >= 400:
            err_counts[key] += 1

    rates: list[float] = []
    for b in buckets:
        total = req_counts.get(b, 0)
        errors = err_counts.get(b, 0)
        rates.append(round(errors / total * 100, 1) if total > 0 else 0.0)
    return rates


def _compute_p95_latency(
    entries: list[dict], buckets: list[datetime]
) -> list[Optional[float]]:
    """p95 latency (ms) per minute bucket. None when no data."""
    bucket_latencies: dict[datetime, list[float]] = defaultdict(list)

    for e in entries:
        if e.get("event") != "request" or "latency_ms" not in e:
            continue
        key = _minute_key(e["_ts"])
        bucket_latencies[key].append(float(e["latency_ms"]))

    p95s: list[Optional[float]] = []
    for b in buckets:
        lats = sorted(bucket_latencies.get(b, []))
        if lats:
            idx = min(int(len(lats) * 0.95), len(lats) - 1)
            p95s.append(lats[idx])
        else:
            p95s.append(None)
    return p95s


def _compute_memory(entries: list[dict]) -> tuple[list[datetime], list[float]]:
    """All (timestamp, memory_mb) pairs with a memory_mb field."""
    points = [
        (e["_ts"], float(e["memory_mb"]))
        for e in entries
        if "memory_mb" in e
    ]
    points.sort(key=lambda x: x[0])
    if not points:
        return [], []
    ts_list, mb_list = zip(*points)
    return list(ts_list), list(mb_list)


# ── Matplotlib helpers ─────────────────────────────────────────────────────────

def _style_ax(ax: plt.Axes) -> None:
    ax.set_facecolor(PANEL_BG)
    ax.tick_params(colors=LABEL_COL, labelsize=8)
    ax.spines[:].set_color(GRID_COL)
    ax.grid(True, color=GRID_COL, linewidth=0.5, linestyle="--")
    ax.yaxis.label.set_color(LABEL_COL)


def _x_labels(buckets: list[datetime], max_ticks: int = 8) -> tuple[list[int], list[str]]:
    """Evenly spaced tick positions and HH:MM labels."""
    n = len(buckets)
    step = max(1, n // max_ticks)
    positions = list(range(0, n, step))
    labels = [buckets[i].strftime("%H:%M") for i in positions]
    return positions, labels


# ── Insufficient-data chart ────────────────────────────────────────────────────

def _save_insufficient(out_path: str) -> str:
    fig, ax = plt.subplots(figsize=(12, 6), facecolor=BG)
    ax.set_facecolor(PANEL_BG)
    ax.text(
        0.5, 0.5,
        "Insufficient data\n(need ≥ 5 log entries)",
        ha="center", va="center",
        color=LABEL_COL, fontsize=16,
        transform=ax.transAxes,
    )
    ax.set_xticks([])
    ax.set_yticks([])
    ax.spines[:].set_color(GRID_COL)
    fig.suptitle("Resolve  ·  Dashboard", color=TITLE_COL, fontsize=13, fontweight="bold")
    _save_fig(fig, out_path)
    return out_path


def _save_fig(fig: plt.Figure, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    plt.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


# ── Public API ─────────────────────────────────────────────────────────────────

def generate_dashboard(window_minutes: int = 15) -> str:
    """
    Build a 3-panel Grafana-style dashboard from live log data.

    Reads api.log and frontend.log from LOG_DIR, extracts per-minute
    error rate, p95 latency, and memory readings, then renders a dark-
    themed PNG and saves it to ASSETS_DIR.

    Returns the absolute path to the saved PNG.
    """
    os.makedirs(ASSETS_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = os.path.abspath(os.path.join(ASSETS_DIR, f"dashboard_{timestamp}.png"))

    now   = datetime.now(timezone.utc)
    since = now - timedelta(minutes=window_minutes)

    # Collect entries from both services
    api_lines      = _read_lines("api")
    frontend_lines = _read_lines("frontend")
    all_entries    = (
        _parse_entries(api_lines, since)
        + _parse_entries(frontend_lines, since)
    )

    if len(all_entries) < 5:
        log.warning("dashboard: fewer than 5 log entries — rendering placeholder")
        return _save_insufficient(out_path)

    buckets     = _build_buckets(all_entries, _minute_key(since), window_minutes)
    error_rates = _compute_error_rate(all_entries, buckets)
    p95_lats    = _compute_p95_latency(all_entries, buckets)
    mem_ts, mem_mb = _compute_memory(all_entries)

    # x-axis labels for per-minute panels
    x_pos, x_labels = _x_labels(buckets)

    # ── Figure ─────────────────────────────────────────────────────────────────
    fig, (ax_err, ax_lat, ax_mem) = plt.subplots(
        3, 1, figsize=(12, 9), facecolor=BG
    )
    fig.suptitle(
        f"Resolve  ·  api + frontend  ·  Last {window_minutes} minutes",
        color=TITLE_COL, fontsize=13, fontweight="bold", y=0.99,
    )

    bucket_indices = list(range(len(buckets)))

    # ── Panel 1: Error rate ────────────────────────────────────────────────────
    bar_colours = [RED if r > ERROR_THRESHOLD_PCT else GREEN for r in error_rates]
    # Use a minimum display height of 1% so 0% buckets render as visible green stubs.
    # The actual data values are unchanged (used for y-axis scale, tooltips, etc.).
    display_rates = [max(r, 1.0) for r in error_rates]
    ax_err.bar(bucket_indices, display_rates, color=bar_colours, width=0.8)
    ax_err.axhline(
        y=ERROR_THRESHOLD_PCT, color=YELLOW, linewidth=1, linestyle="--", alpha=0.8
    )
    ax_err.text(
        len(buckets) - 0.5, ERROR_THRESHOLD_PCT + 1.5,
        f"alert {ERROR_THRESHOLD_PCT:.0f}%",
        color=YELLOW, fontsize=7, ha="right",
    )
    ax_err.set_ylabel("Error Rate (%)", color=LABEL_COL, fontsize=9)
    ax_err.set_title("Request Error Rate", color=TEXT_COL, fontsize=10, pad=4)
    ax_err.set_ylim(0, max(105, max(error_rates) * 1.15) if error_rates else 105)
    ax_err.set_xticks(x_pos)
    ax_err.set_xticklabels(x_labels, rotation=0)
    _style_ax(ax_err)

    # ── Panel 2: p95 Latency ───────────────────────────────────────────────────
    # Interpolate None gaps so the line is continuous
    lat_values = np.array(
        [v if v is not None else np.nan for v in p95_lats], dtype=float
    )
    ax_lat.plot(bucket_indices, lat_values, color=BLUE, linewidth=2, marker="o",
                markersize=3)
    ax_lat.fill_between(bucket_indices, lat_values, alpha=0.12, color=BLUE)

    # Y-axis: pin to data range with 20% headroom; only extend to threshold if
    # data is within 2× of it (avoids crushing healthy latency to a flat line).
    valid_lats = lat_values[~np.isnan(lat_values)]
    if len(valid_lats) > 0:
        data_max = float(valid_lats.max())
        y_top = max(data_max * 1.2, 50)          # at least 50ms so axis isn't tiny
        if data_max > LATENCY_THRESHOLD_MS * 0.5:
            y_top = max(y_top, LATENCY_THRESHOLD_MS * 1.05)
        ax_lat.set_ylim(0, y_top)

    # Only draw the threshold label if it fits in the visible range
    if ax_lat.get_ylim()[1] >= LATENCY_THRESHOLD_MS * 0.9:
        ax_lat.axhline(
            y=LATENCY_THRESHOLD_MS, color=YELLOW, linewidth=1, linestyle="--", alpha=0.8
        )
        ax_lat.text(
            len(buckets) - 0.5, LATENCY_THRESHOLD_MS + ax_lat.get_ylim()[1] * 0.02,
            f"threshold {LATENCY_THRESHOLD_MS:.0f}ms",
            color=YELLOW, fontsize=7, ha="right",
        )

    ax_lat.set_ylabel("p95 Latency (ms)", color=LABEL_COL, fontsize=9)
    ax_lat.set_title("p95 Request Latency", color=TEXT_COL, fontsize=10, pad=4)
    ax_lat.set_xticks(x_pos)
    ax_lat.set_xticklabels(x_labels, rotation=0)
    _style_ax(ax_lat)

    # ── Panel 3: Memory ────────────────────────────────────────────────────────
    if mem_mb:
        # Map memory timestamps to a float x-axis (minutes from `since`)
        mem_x = [(t - since).total_seconds() / 60 for t in mem_ts]
        ax_mem.plot(mem_x, mem_mb, color=BLUE, linewidth=2)
        ax_mem.fill_between(mem_x, mem_mb, alpha=0.12, color=BLUE)

        # Detect growing trend: last half avg > first half avg by > 10 MB
        half = max(1, len(mem_mb) // 2)
        first_half_avg = sum(mem_mb[:half]) / half
        last_half_avg  = sum(mem_mb[half:]) / max(1, len(mem_mb) - half)
        is_growing = (last_half_avg - first_half_avg) > 10

        if is_growing:
            peak_x = mem_x[-1]
            peak_y = mem_mb[-1]
            ax_mem.annotate(
                "GROWING",
                xy=(peak_x, peak_y),
                xytext=(peak_x - window_minutes * 0.15, peak_y * 0.92),
                color=RED, fontsize=9, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=RED, lw=1.5),
            )

        ax_mem.set_xlim(0, window_minutes)
        ax_mem.xaxis.set_major_formatter(
            plt.FuncFormatter(lambda val, _: f"{since + timedelta(minutes=val):%H:%M}")
        )
        ax_mem.set_xticks(np.linspace(0, window_minutes, min(8, window_minutes + 1)))

        # Y-axis: pin to data range with 10% padding so small changes are visible.
        lo = min(mem_mb)
        hi = max(mem_mb)
        spread = hi - lo
        pad = max(spread * 0.3, 5)   # at least 5 MB padding so axis isn't a point
        ax_mem.set_ylim(max(0, lo - pad), hi + pad)
    else:
        ax_mem.text(
            0.5, 0.5, "No memory data",
            ha="center", va="center", color=LABEL_COL, fontsize=11,
            transform=ax_mem.transAxes,
        )

    ax_mem.set_ylabel("Memory (MB)", color=LABEL_COL, fontsize=9)
    ax_mem.set_title("Memory Usage", color=TEXT_COL, fontsize=10, pad=4)
    _style_ax(ax_mem)

    # ── Legend ─────────────────────────────────────────────────────────────────
    patches = [
        mpatches.Patch(color=GREEN,  label=f"error rate <{ERROR_THRESHOLD_PCT:.0f}%"),
        mpatches.Patch(color=RED,    label=f"error rate >{ERROR_THRESHOLD_PCT:.0f}%"),
        mpatches.Patch(color=BLUE,   label="latency / memory"),
        mpatches.Patch(color=YELLOW, label="alert threshold"),
    ]
    fig.legend(
        handles=patches, loc="lower center", ncol=4,
        facecolor=PANEL_BG, edgecolor=GRID_COL,
        labelcolor=TEXT_COL, fontsize=8,
        bbox_to_anchor=(0.5, 0.005),
    )

    plt.tight_layout(rect=[0, 0.04, 1, 0.97])
    _save_fig(fig, out_path)
    log.info("dashboard: saved → %s", out_path)
    return out_path
