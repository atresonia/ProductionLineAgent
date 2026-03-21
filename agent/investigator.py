"""
investigator.py — Claude-powered root cause analysis

Runs an agentic loop: Claude calls tools, reads evidence, forms a
hypothesis, and produces a structured incident report.  The loop
continues until Claude emits a final text response (stop_reason=end_turn)
or hits the max-turns safety limit.

Multimodal support:
  Pass image_path= to include a dashboard screenshot in the investigation.
  Claude will analyze the visual signal alongside log data and config files.
"""

import base64
import json
import os
from datetime import datetime, timezone
from anthropic import Anthropic
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from tools import TOOL_SCHEMAS, dispatch

console = Console()

LOG_DIR         = os.getenv("LOG_DIR", "./logs")
RESOLVE_LOG     = os.path.join(LOG_DIR, "resolve.log")

# ─────────────────────────────────────────────────────────────────────────────
# System prompt: two-phase protocol — PLAN first, then INVESTIGATE
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are Resolve, autonomous incident response agent.
Stack: frontend:3000 → api:8000 → db:5432. Logs are mixed JSON + plain text.

PHASE 1 — before any tools, write one line: "Plan: ..."

PHASE 2 — investigate with tools, then:
- send_slack_alert (severity=critical) with root cause
- CALL execute_remediation tool (triggers human y/N gate — do NOT just describe it)
- send_slack_alert (severity=resolved) after fix

FINAL REPORT (be concise):
## Root Cause
## Evidence (bullet per source)
## Confidence [0-100%]
## Remediation taken
## Impact

Meeting transcripts: if a meeting URL appears in Slack, call join_incident_meeting() to transcribe it. Use get_meeting_transcript() once done — avoid duplicating work the team already did live.

Rules: only report what tools return. Confirm root cause with 2+ sources."""

# ── Helpers ───────────────────────────────────────────────────────────────────

def _ilog(entry: dict):
    """Append a structured entry to the Resolve investigation log."""
    os.makedirs(LOG_DIR, exist_ok=True)
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    entry["service"]   = "resolve"
    with open(RESOLVE_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _print_tool_call(name: str, inputs: dict):
    _ilog({"event": "tool_call", "tool": name, "inputs": inputs})
    console.print(
        f"  [bold cyan]→ tool:[/bold cyan] [yellow]{name}[/yellow]  "
        f"[dim]{json.dumps(inputs)}[/dim]"
    )


def _print_tool_result(name: str, result: str):
    _ilog({"event": "tool_result", "tool": name,
           "result_preview": result[:200]})
    preview = result[:300].replace("\n", " ")
    if len(result) > 300:
        preview += " …"
    console.print(f"  [bold cyan]← result:[/bold cyan] [dim]{preview}[/dim]")


def _print_thinking(text: str):
    if text.strip():
        _ilog({"event": "reasoning", "text": text.strip()[:300]})
        console.print(f"  [green]{text.strip()}[/green]")


def _build_image_block(image_path: str) -> dict | None:
    """Read an image file and return an Anthropic vision content block."""
    if not image_path or not os.path.exists(image_path):
        return None
    ext = os.path.splitext(image_path)[1].lower()
    media_map = {".png": "image/png", ".jpg": "image/jpeg",
                 ".jpeg": "image/jpeg", ".gif": "image/gif",
                 ".webp": "image/webp"}
    media_type = media_map.get(ext, "image/png")
    with open(image_path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode()
    return {
        "type":   "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


# ── Main investigation loop ───────────────────────────────────────────────────

def investigate(anomaly: str,
                image_path: str | None = None,
                require_approval: bool = True) -> dict:
    """
    Run the full investigation for an anomaly.

    Args:
        anomaly:          Text description of what triggered the alert.
        image_path:       Optional path to a dashboard screenshot (PNG/JPG).
                          If provided, Claude will analyze the image alongside logs.
        require_approval: Gate on human input before executing remediation.

    Returns dict with: conclusion, messages
    """
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    # ── Build initial user message (text + optional image) ────────────────
    if image_path and os.path.exists(image_path):
        image_block = _build_image_block(image_path)
        initial_content = [
            {"type": "text",
             "text": (f"{anomaly}\n\n"
                      f"A dashboard screenshot has been attached showing metrics "
                      f"at the time of the incident. Analyze the visual signals "
                      f"in the image alongside the log data.")},
            image_block,
        ]
        console.print(f"  [dim]Dashboard image attached: {image_path}[/dim]")
    else:
        initial_content = anomaly

    messages = [{"role": "user", "content": initial_content}]

    _ilog({"event": "investigation_start", "anomaly": anomaly[:200],
           "image": image_path or "none"})

    console.print()
    console.print(Panel(
        f"[bold red]INCIDENT DETECTED[/bold red]\n{anomaly}",
        border_style="red",
    ))
    console.print()

    max_turns  = 15
    conclusion = ""

    for turn in range(max_turns):
        for attempt in range(3):
            try:
                response = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=1024,
                    system=SYSTEM_PROMPT,
                    tools=TOOL_SCHEMAS,
                    messages=messages,
                )
                break
            except Exception as e:
                if attempt == 2:
                    raise
                console.print(f"  [yellow]Network error, retrying ({attempt+1}/3)...[/yellow]")
                import time; time.sleep(2)

        for block in response.content:
            if block.type == "text":
                _print_thinking(block.text)

        # ── Phase complete: agent wrote final report ───────────────────────
        if response.stop_reason == "end_turn":
            for block in response.content:
                if block.type == "text":
                    conclusion = block.text
            break

        # ── Tool use turn ─────────────────────────────────────────────────
        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                _print_tool_call(block.name, block.input)

                # Human approval gate for remediation
                if block.name == "execute_remediation" and require_approval:
                    action  = block.input.get("action", "unknown")
                    service = block.input.get("service", "api")
                    console.print()
                    console.print(Panel(
                        f"[bold yellow]REMEDIATION REQUEST[/bold yellow]\n\n"
                        f"Action  : [cyan]{action}[/cyan]\n"
                        f"Service : [cyan]{service}[/cyan]\n\n"
                        f"Approve? [y/N]",
                        border_style="yellow",
                    ))
                    answer = input("  > ").strip().lower()
                    if answer not in ("y", "yes"):
                        result = '{"status": "rejected", "reason": "operator declined"}'
                        _ilog({"event": "remediation_rejected", "action": action})
                        console.print("  [red]Remediation rejected.[/red]")
                    else:
                        result = dispatch(block.name, block.input)
                        _ilog({"event": "remediation_executed", "action": action})
                        console.print("  [green]Remediation executed.[/green]")
                else:
                    result = dispatch(block.name, block.input)

                _print_tool_result(block.name, result)
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     result,
                })

            messages.append({"role": "user", "content": tool_results})
            continue

        break  # unexpected stop_reason

    # ── Print + log final report ──────────────────────────────────────────
    console.print()
    console.print(Panel(
        Text(conclusion, style="white"),
        title="[bold green]INCIDENT REPORT[/bold green]",
        border_style="green",
    ))
    _ilog({"event": "investigation_complete",
           "conclusion_preview": conclusion[:300]})

    return {"conclusion": conclusion, "messages": messages}
