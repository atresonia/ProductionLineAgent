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
from model_client import ModelClient
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from tools import TOOL_SCHEMAS, dispatch

console = Console()

LOG_DIR         = os.getenv("LOG_DIR", "./logs")
RESOLVE_LOG     = os.path.join(LOG_DIR, "resolve.log")

# ── WebSocket callback hooks — set by server.py before calling investigate() ──
# _event_callback(dict): called for every logged event (thread-safe)
# _approval_callback(action, service, reason) -> bool: replaces terminal input()
_event_callback:    "callable | None" = None
_approval_callback: "callable | None" = None

# ─────────────────────────────────────────────────────────────────────────────
# System prompt: two-phase protocol — PLAN first, then INVESTIGATE
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are Resolve, autonomous incident response agent.
Stack: frontend:3000 → api:8000 → db:5432. Logs are mixed JSON + plain text.

PHASE 1 — before any tools, write one line: "Plan: ..."

If multiple anomalies are reported, TRIAGE FIRST in Phase 1:
1. Call read_triage_config to load the team's priority rules.
2. Call get_endpoint_error_rates to see per-endpoint breakdown.
3. State each anomaly with its technical severity AND its business priority from the config.
4. Apply the team's triage rules to determine investigation order.
5. Explain your decision using the team's language — reference their reasons and rule names.
6. If business priority conflicts with technical severity, follow business priority and explain why.
7. Format your triage output using this style (NO markdown tables):

   🔴 INVESTIGATING FIRST: /checkout (7.8% error rate)
      Technical: HIGH | Business: CRITICAL ($5,600/min)
      Rule applied: "Revenue-critical override"

   🟡 QUEUED #2: /products (16.0% error rate)
      Technical: HIGH | Business: MEDIUM (no direct revenue loss)
      Rule applied: "Catalog can wait"

   Use 🔴 for BIZ:CRITICAL, 🟡 for BIZ:HIGH or BIZ:MEDIUM. Never use markdown tables.

When multiple endpoints on the same service are failing with different error rates:
- Do NOT assume the highest error rate is most important.
- A 40% error rate on /checkout (revenue-critical, $5,600/min) is worse than 95% on /products
  (medium priority, no direct revenue loss).
- Always check business priority before deciding investigation order.

After resolving the first anomaly:
- Call get_error_rate and get_latency_stats to verify whether queued anomalies self-resolved.
- If a lower-priority anomaly resolved as a side effect, state:
  "Cascade resolution: <anomaly> resolved as side effect of <fix> — confirming with metrics."
- If still present, investigate it independently with the same two-phase protocol.
- When calling execute_remediation with multiple faults active, always pass the
  'fault' parameter to clear only the targeted fault — never clear all faults at once.

PHASE 2 — investigate with tools, then:
- FIRST: call get_active_faults immediately at the start of Phase 2. This is mandatory.
  If get_active_faults returns active faults, there IS an ongoing incident — a temporarily
  lower error rate does NOT mean self-recovery. The fault is still injected and WILL cause
  more errors. You MUST proceed to remediation.
- After quantifying impact with error rates and logs, call capture_dashboard to get a visual
  timeline of the incident. The spike shape is diagnostic — a cliff indicates a deploy, a ramp
  indicates a leak, a step indicates a threshold breach.
- Always capture at least one dashboard screenshot per investigation, even when handling multiple
  faults. The visual evidence is essential for the post-mortem.
- send_slack_alert (severity=critical) with root cause
- CALL execute_remediation tool (triggers human y/N gate — do NOT just describe it).
  You MUST call this before sending severity=resolved. NEVER skip execute_remediation
  when get_active_faults shows active faults. A lower error rate in the current window
  does NOT mean the incident is resolved — it means fewer requests hit the fault recently.
- After executing remediation, call get_error_rate or get_endpoint_error_rates with window_minutes=1 (NOT 5) to verify the fix worked — the 5-min window contains pre-fix errors and will appear elevated. If the error rate has NOT dropped significantly (still above threshold):
  1. State that the remediation did not resolve the issue.
  2. Call get_active_faults to check if other faults are still present.
  3. Re-investigate with a revised hypothesis.
  4. Attempt a different remediation targeting the correct fault.
  Only escalate to manual intervention after TWO failed remediation attempts.
- send_slack_alert (severity=resolved) ONLY after execute_remediation has been called and verified

FINAL REPORT (be concise):
## Root Cause
## Evidence (bullet per source)
## Confidence [0-100%]
## Remediation taken
## Impact

## Triage Decision
State which triage rule was applied, business priority vs technical severity for each incident,
and whether the priority decision was correct in hindsight.

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
    if _event_callback:
        try:
            _event_callback(dict(entry))
        except Exception:
            pass


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
    client = ModelClient()

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

    max_turns         = 40
    conclusion        = ""
    remediation_count = 0
    report_injected   = False

    for turn in range(max_turns):
        response = client.chat(SYSTEM_PROMPT, messages, TOOL_SCHEMAS)

        for block in response.content:
            if block.type == "text":
                _print_thinking(block.text)

        # ── Phase complete: agent wrote final report ───────────────────────
        if response.stop_reason == "end_turn":
            num_text = sum(1 for b in response.content if b.type == "text")
            _ilog({"event": "end_turn", "turn": turn, "num_text_blocks": num_text,
                   "conclusion_length": len(conclusion)})
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
                    reason  = block.input.get("reason", "")

                    if _approval_callback:
                        approved = _approval_callback(action, service, reason)
                    else:
                        # Clear any stale decision file before waiting
                        _approval_file = os.path.join(
                            os.getenv("CHAOS_DIR", "../chaos"), "approval_decision"
                        )
                        try:
                            os.remove(_approval_file)
                        except FileNotFoundError:
                            pass

                        console.print()
                        console.print(Panel(
                            f"[bold yellow]REMEDIATION REQUEST[/bold yellow]\n\n"
                            f"Action  : [cyan]{action}[/cyan]\n"
                            f"Service : [cyan]{service}[/cyan]\n\n"
                            f"Approve? [y/N]  (or click APPROVE in the dashboard UI)",
                            border_style="yellow",
                        ))

                        # Poll approval file (written by dashboard) OR terminal input
                        import threading, time as _time
                        _decision = [None]

                        def _poll_file():
                            for _ in range(120):  # 2-min timeout
                                try:
                                    with open(_approval_file) as f:
                                        val = f.read().strip().lower()
                                    if val in ("y", "n"):
                                        _decision[0] = val
                                        return
                                except FileNotFoundError:
                                    pass
                                _time.sleep(1)

                        poller = threading.Thread(target=_poll_file, daemon=True)
                        poller.start()

                        # Also allow terminal input (non-blocking via thread)
                        def _read_terminal():
                            try:
                                val = input("  > ").strip().lower()
                                if _decision[0] is None:
                                    _decision[0] = "y" if val in ("y", "yes") else "n"
                            except Exception:
                                pass

                        term = threading.Thread(target=_read_terminal, daemon=True)
                        term.start()

                        # Wait for either
                        while _decision[0] is None:
                            _time.sleep(0.2)

                        # Clean up file
                        try:
                            os.remove(_approval_file)
                        except FileNotFoundError:
                            pass

                        approved = _decision[0] == "y"

                    if not approved:
                        result = '{"status": "rejected", "reason": "operator declined"}'
                        _ilog({"event": "remediation_rejected", "action": action})
                        console.print("  [red]Remediation rejected.[/red]")
                    else:
                        result = dispatch(block.name, block.input)
                        _ilog({"event": "remediation_executed", "action": action})
                        console.print("  [green]Remediation executed.[/green]")
                        try:
                            r_data = json.loads(result)
                            if isinstance(r_data, dict) and r_data.get("status") == "success":
                                remediation_count += 1
                        except (json.JSONDecodeError, KeyError, TypeError):
                            pass
                else:
                    result = dispatch(block.name, block.input)

                _print_tool_result(block.name, result)

                # If the tool returned an image_path, attach the image as a
                # vision content block alongside the text summary.
                tool_content: str | list = result
                try:
                    result_data = json.loads(result)
                    if isinstance(result_data, dict) and "image_path" in result_data:
                        image_block = _build_image_block(result_data["image_path"])
                        if image_block:
                            tool_content = [
                                {"type": "text", "text": result},
                                image_block,
                            ]
                            _ilog({"event": "image_attached",
                                   "path": result_data["image_path"]})
                            console.print(
                                "  [bold magenta]"
                                "[Dashboard image attached — model analyzing visual signal]"
                                "[/bold magenta]"
                            )
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass

                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     tool_content,
                })

            # After 2+ successful remediations, inject a prompt to write the final report
            # so the agent doesn't exhaust remaining turns on more tool calls.
            if remediation_count >= 2 and not report_injected:
                tool_results.append({
                    "type": "text",
                    "text": (
                        "Both incidents have been resolved. "
                        "Write your final ## Root Cause report now."
                    ),
                })
                report_injected = True
                _ilog({"event": "report_prompt_injected",
                       "remediation_count": remediation_count})

            messages.append({"role": "user", "content": tool_results})
            continue

        break  # unexpected stop_reason
    else:
        # for-loop exhausted all turns without a break (no end_turn received)
        _ilog({"event": "loop_exit", "reason": "max_turns_reached", "turn": turn,
               "conclusion_length": len(conclusion)})

    # ── Fallback conclusion if agent hit max_turns or end_turn without report ─
    if not conclusion:
        reasoning_lines:    list[str] = []
        remediation_lines:  list[str] = []
        last_error_rate:    str       = ""

        try:
            with open(RESOLVE_LOG) as f:
                for raw in f:
                    try:
                        entry = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    ev = entry.get("event", "")
                    if ev == "reasoning":
                        text = entry.get("text", "").strip()
                        if text:
                            reasoning_lines.append(f"- {text}")
                    elif ev == "remediation_executed":
                        action = entry.get("action", "unknown")
                        remediation_lines.append(f"- {action}")
                    elif ev == "tool_result" and entry.get("tool") in (
                        "get_error_rate", "get_endpoint_error_rates"
                    ):
                        last_error_rate = entry.get("result_preview", "")
        except OSError:
            pass

        parts = [
            "## Root Cause",
            "[Auto-generated from investigation log — agent did not produce a structured conclusion]",
            "",
        ]
        if reasoning_lines:
            parts += ["**Agent reasoning:**"] + reasoning_lines + [""]
        if remediation_lines:
            parts += ["**Remediations executed:**"] + remediation_lines + [""]
        if last_error_rate:
            parts += ["**Last error-rate reading:**", last_error_rate, ""]

        conclusion = "\n".join(parts)
        _ilog({"event": "fallback_conclusion_generated",
               "reason": "agent loop ended without structured report"})

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
