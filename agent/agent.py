#!/usr/bin/env python3
"""
agent.py — Resolve: Autonomous Incident Response Agent

Usage:
  # Monitor mode (continuous — recommended for demo)
  python agent.py

  # Trigger investigation manually (useful for testing)
  python agent.py --trigger "High error rate on api: 87% of /checkout requests failing"

  # Run against a specific fault type for demo rehearsal
  python agent.py --demo bad_deploy
  python agent.py --demo memory_leak
  python agent.py --demo slow_db
  python agent.py --demo db_down

Environment:
  ANTHROPIC_API_KEY   required
  LOG_DIR             default ./logs
  CHAOS_DIR           default ./chaos
  POLL_INTERVAL       default 5 (seconds)
  ERROR_RATE_THRESHOLD  default 15 (%)
"""

import argparse
import os
import sys
import subprocess
from datetime import datetime, timezone

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

load_dotenv()

console = Console()

# ── Validate env ──────────────────────────────────────────────────────────────

if not os.getenv("ANTHROPIC_API_KEY"):
    console.print("[bold red]Error:[/bold red] ANTHROPIC_API_KEY is not set.")
    console.print("  Create a .env file in the agent/ directory with:")
    console.print("  ANTHROPIC_API_KEY=sk-ant-...")
    sys.exit(1)

# ── Imports (after env check so errors are readable) ──────────────────────────

from investigator import investigate
from monitor import run as run_monitor, check_once
from postmortem import generate as generate_postmortem

# ── Demo fault descriptions ───────────────────────────────────────────────────

DEMO_TRIGGERS = {
    "bad_deploy": (
        "Anomaly detected at {ts}:\n"
        "  • High error rate on api: /checkout returning 500s (~85% error rate)\n"
        "  • Frontend reporting 502s upstream from api\n\n"
        "Investigate root cause, determine blast radius, and recommend remediation."
    ),
    "memory_leak": (
        "Anomaly detected at {ts}:\n"
        "  • Memory growing rapidly on api: +200MB in 5 minutes (now 890MB)\n"
        "  • Checkout latency increasing with each request\n\n"
        "Investigate root cause, determine blast radius, and recommend remediation."
    ),
    "slow_db": (
        "Anomaly detected at {ts}:\n"
        "  • High latency on api: p95=2800ms (threshold 1500ms)\n"
        "  • High latency on frontend: p95=3200ms (cascade from api)\n\n"
        "Investigate root cause, determine blast radius, and recommend remediation."
    ),
    "db_down": (
        "Anomaly detected at {ts}:\n"
        "  • High error rate on api: 503s on all endpoints (DB unreachable)\n"
        "  • Frontend error rate 100% — cannot reach api\n\n"
        "Investigate root cause, determine blast radius, and recommend remediation."
    ),
}

# ── Core incident handler ─────────────────────────────────────────────────────

def handle_incident(anomaly: str, image_path: str | None = None):
    detected_at = datetime.now(timezone.utc)

    result = investigate(anomaly, image_path=image_path, require_approval=True)

    resolved_at = datetime.now(timezone.utc)
    duration_s  = int((resolved_at - detected_at).total_seconds())

    # Generate post-mortem
    pm_path = generate_postmortem(result["conclusion"], detected_at, resolved_at)
    console.print(f"\n  [bold green]Post-mortem written →[/bold green] {pm_path}\n")

    # Summary stats
    duration_str = (
        f"{duration_s // 60}m {duration_s % 60}s"
        if duration_s >= 60 else f"{duration_s}s"
    )
    console.print(Panel(
        f"[bold]Incident closed[/bold]\n\n"
        f"  MTTR      : [cyan]{duration_str}[/cyan]  (industry avg ~47 minutes)\n"
        f"  Post-mortem: [cyan]{pm_path}[/cyan]",
        border_style="green",
    ))


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Resolve — Autonomous Incident Response Agent")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--trigger", metavar="ANOMALY",
                       help="Manually trigger investigation with this anomaly description")
    group.add_argument("--demo", choices=list(DEMO_TRIGGERS.keys()),
                       help="Run a scripted demo investigation for a specific fault type")
    args = parser.parse_args()

    console.print(Panel(
        "[bold white]Resolve[/bold white]  —  Autonomous Incident Response Agent\n"
        "[dim]Anthropic Claude · Tool Use · Real-time Root Cause Analysis[/dim]",
        border_style="white",
    ))

    if args.trigger:
        handle_incident(args.trigger)

    elif args.demo:
        ts      = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        anomaly = DEMO_TRIGGERS[args.demo].format(ts=ts)
        console.print(f"\n  [yellow]Demo mode:[/yellow] {args.demo}\n")
        # For memory_leak, attach the Grafana dashboard screenshot if it exists
        image_path = None
        if args.demo == "memory_leak":
            candidate = os.path.join("..", "assets", "grafana_memory_spike.png")
            if os.path.exists(candidate):
                image_path = candidate
                console.print(f"  [dim]Attaching dashboard screenshot: {candidate}[/dim]")
            else:
                console.print(
                    "  [dim]Tip: run 'python ../generate_dashboard.py' to generate "
                    "a dashboard screenshot for the memory_leak demo.[/dim]"
                )
        handle_incident(anomaly, image_path=image_path)

    else:
        # Continuous monitor mode
        run_monitor(on_anomaly=handle_incident)


if __name__ == "__main__":
    main()
