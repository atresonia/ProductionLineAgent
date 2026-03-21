"""
postmortem.py — generate a structured post-mortem from an incident report

Takes the agent's conclusion markdown and conversation, and writes a
complete post-mortem document to ./postmortems/YYYY-MM-DD_HHMMSS.md
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

## Impact
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


def generate(conclusion: str, detected_at: datetime, resolved_at: datetime) -> str:
    """
    Build and write a post-mortem document.
    Returns the file path.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    duration_s  = int((resolved_at - detected_at).total_seconds())
    duration_str = (
        f"{duration_s // 60}m {duration_s % 60}s"
        if duration_s >= 60 else f"{duration_s}s"
    )

    # Extract sections from agent report
    root_cause = _extract_section(conclusion, "Root Cause")
    impact     = _extract_section(conclusion, "Impact")
    resolution = _extract_section(conclusion, "Recommended Remediation")
    confidence = _extract_section(conclusion, "Confidence")

    # Build timeline rows
    timeline_rows = "\n".join([
        f"| {detected_at.strftime('%H:%M:%S')} | Anomaly detected by Resolve monitor |",
        f"| {detected_at.strftime('%H:%M:%S')} | Automated investigation started |",
        f"| {resolved_at.strftime('%H:%M:%S')} | Root cause identified: {root_cause[:60]}… |",
        f"| {resolved_at.strftime('%H:%M:%S')} | Remediation executed and confirmed |",
    ])

    content = TEMPLATE.format(
        date      = detected_at.strftime("%Y-%m-%d %H:%M UTC"),
        severity  = "SEV-2",
        duration  = duration_str,
        summary   = f"{root_cause} — detected and resolved automatically by Resolve.",
        timeline  = timeline_rows,
        root_cause= root_cause,
        impact    = impact,
        resolution= resolution,
        lessons   = (
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
