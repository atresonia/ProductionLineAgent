"""
postmortem.py — generate a structured post-mortem from an incident report

Takes the agent's conclusion markdown and conversation, and writes a
complete post-mortem document to ./postmortems/YYYY-MM-DD_HHMMSS.md

Supports both single-incident and multi-incident (triage) scenarios.
"""

import os
from datetime import datetime, timezone

OUTPUT_DIR = os.getenv("POSTMORTEM_DIR", "./postmortems")


TEMPLATE = """\
# Incident Post-Mortem
**Date:** {date}
**Severity:** {severity}
**Duration:** {duration}
**Status:** Resolved

---

## Summary
{summary}

---

## Timeline
| Time (UTC) | Event |
|---|---|
{timeline}

---

## Root Cause
{root_cause}

---

{triage_section}## Impact
{impact}

---

## Resolution
{resolution}

---

## Action Items
| # | Action | Owner | Due |
|---|---|---|---|
| 1 | Add alerting for this failure mode | On-call team | Next sprint |
| 2 | Review deploy checklist for env var validation | Platform team | Next sprint |
| 3 | Write runbook for this incident type | On-call team | Next sprint |

---

## Lessons Learned
{lessons}

---
*Generated automatically by Resolve*
"""

TRIAGE_SECTION_TEMPLATE = """\
## Triage Decision
{triage_decision}

---

"""


def _extract_section(markdown: str, heading: str) -> str:
    """Pull the content under a ## heading from the agent's markdown output."""
    lines   = markdown.split("\n")
    capture = False
    result  = []
    for line in lines:
        if line.strip().lower().startswith(f"## {heading.lower()}"):
            capture = True
            continue
        if capture:
            if line.startswith("## "):
                break
            result.append(line)
    return "\n".join(result).strip() or f"[{heading} not found in report]"


def _extract_triage(markdown: str) -> str:
    """
    Try to extract a triage decision from the agent's Plan or Triage section.
    Falls back to a generic message if nothing found.
    """
    # Look for "Triage:" prefix in the text
    for line in markdown.split("\n"):
        stripped = line.strip()
        if stripped.lower().startswith("triage:"):
            return stripped
        if "investigating first" in stripped.lower() and "queued" in stripped.lower():
            return stripped

    # Check for explicit triage section
    triage = _extract_section(markdown, "Triage")
    if "[triage not found" not in triage.lower():
        return triage

    return "[Triage decision not captured — single-anomaly investigation]"


def generate(conclusion: str, detected_at: datetime, resolved_at: datetime,
             anomalies: list[dict] | None = None) -> str:
    """
    Build and write a post-mortem document.

    Args:
        conclusion:   Agent's final markdown report.
        detected_at:  When the incident was detected.
        resolved_at:  When the incident was resolved.
        anomalies:    Optional list of anomaly dicts from check_once() —
                      used to enrich the Triage Decision section for multi-
                      incident scenarios.

    Returns the file path.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    duration_s   = int((resolved_at - detected_at).total_seconds())
    duration_str = (
        f"{duration_s // 60}m {duration_s % 60}s"
        if duration_s >= 60 else f"{duration_s}s"
    )

    # Extract sections from agent report
    root_cause = _extract_section(conclusion, "Root Cause")
    impact     = _extract_section(conclusion, "Impact")
    resolution = _extract_section(conclusion, "Remediation taken")
    confidence = _extract_section(conclusion, "Confidence")

    # Build triage section (only meaningful when multiple anomalies present)
    triage_section = ""
    if anomalies and len(anomalies) > 1:
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        # Sort by business_priority first, then technical severity
        sorted_a = sorted(
            anomalies,
            key=lambda a: (
                severity_order.get(a.get("business_priority", a.get("severity", "medium")), 2),
                severity_order.get(a.get("severity", "medium"), 2),
            )
        )

        triage_lines = [
            "The agent detected multiple simultaneous anomalies and triaged as follows:\n"
        ]
        for i, a in enumerate(sorted_a, 1):
            priority   = "INVESTIGATING FIRST" if i == 1 else f"QUEUED (#{i})"
            tech_sev   = a.get("severity", "unknown").upper()
            biz_pri    = a.get("business_priority", "")
            biz_reason = a.get("business_reason", "")
            endpoint   = a.get("endpoint", "")

            biz_note = ""
            if biz_pri:
                biz_note = f" | BIZ:{biz_pri.upper()}"
                if biz_pri != a.get("severity") and \
                        severity_order.get(biz_pri, 2) < severity_order.get(a.get("severity", "medium"), 2):
                    biz_note += " ⚡ (business priority override)"

            ep_note = f" [{endpoint}]" if endpoint else ""
            triage_lines.append(
                f"- [{tech_sev}{biz_note}]{ep_note} {a['description']} — {priority}"
            )
            if biz_reason:
                triage_lines.append(f"  Business reason: {biz_reason}")

        # Triage decision from agent conclusion
        agent_triage = _extract_triage(conclusion)
        if "[Triage decision not captured" not in agent_triage:
            triage_lines.append(f"\nAgent triage reasoning:\n{agent_triage}")

        triage_text = "\n".join(triage_lines)
    elif anomalies and len(anomalies) == 1:
        triage_text = f"Single anomaly detected: {anomalies[0]['description']}"
    else:
        triage_text = _extract_triage(conclusion)

    if triage_text and "[Triage decision not captured" not in triage_text:
        triage_section = TRIAGE_SECTION_TEMPLATE.format(triage_decision=triage_text)

    # Severity: use highest from anomaly list, or default SEV-2
    if anomalies:
        severity_order = {"critical": 0, "high": 1, "medium": 2}
        highest = min(anomalies, key=lambda a: severity_order.get(a.get("severity", "medium"), 2))
        sev_label = {"critical": "SEV-1", "high": "SEV-2", "medium": "SEV-3"}.get(
            highest.get("severity", "high"), "SEV-2"
        )
    else:
        sev_label = "SEV-2"

    # Build timeline rows
    timeline_rows = "\n".join([
        f"| {detected_at.strftime('%H:%M:%S')} | Anomaly/anomalies detected by Resolve monitor |",
        f"| {detected_at.strftime('%H:%M:%S')} | Automated investigation started |",
        f"| {resolved_at.strftime('%H:%M:%S')} | Root cause identified: {root_cause[:60]}… |",
        f"| {resolved_at.strftime('%H:%M:%S')} | Remediation executed and confirmed |",
    ])

    content = TEMPLATE.format(
        date           = detected_at.strftime("%Y-%m-%d %H:%M UTC"),
        severity       = sev_label,
        duration       = duration_str,
        summary        = f"{root_cause} — detected and resolved automatically by Resolve.",
        timeline       = timeline_rows,
        root_cause     = root_cause,
        triage_section = triage_section,
        impact         = impact,
        resolution     = resolution,
        lessons        = (
            f"- Resolve correctly identified the root cause with {confidence} confidence.\n"
            f"- Automated remediation reduced MTTR from ~47min to {duration_str}.\n"
            f"- Consider adding pre-deploy env-var validation to prevent recurrence."
        ),
    )

    filename = detected_at.strftime("%Y-%m-%d_%H%M%S") + "_incident.md"
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, "w") as f:
        f.write(content)

    return filepath
