"""
calendar_tools.py — Team availability and intelligent paging recommendation

Reads team_calendar.json (mock for hackathon; Google Calendar API in production)
and recommends who to page based on:
  1. Current availability (not in a meeting, not on PTO)
  2. Past incident resolution history (who has solved this type of problem before)
  3. Domain expertise match
"""

import json
import os
from datetime import datetime, timezone

DATA_DIR          = os.getenv("DATA_DIR", "../data")
TEAM_CALENDAR_FILE = os.path.join(DATA_DIR, "team_calendar.json")


def _load_team() -> list[dict]:
    try:
        with open(TEAM_CALENDAR_FILE) as f:
            data = json.load(f)
            return data.get("team", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def get_team_availability(incident_type: str = "") -> str:
    """
    Return current team availability and recommend who to page for this incident.

    incident_type: hint about the kind of incident (e.g. 'payment', 'memory', 'database')
    Uses calendar status + Slack status + past incident history to rank engineers.
    """
    team = _load_team()
    if not team:
        return "[team calendar not available — falling back to on-call rotation]"

    now = datetime.now(timezone.utc)
    incident_keywords = incident_type.lower().split() if incident_type else []

    available = []
    busy      = []

    for member in team:
        avail = member.get("availability", {})
        status = avail.get("status", "unknown")
        note   = avail.get("note", "")

        # Check calendar events
        in_meeting = False
        for event in member.get("calendar", []):
            try:
                start = datetime.fromisoformat(event["start"])
                end   = datetime.fromisoformat(event["end"])
                if start <= now <= end:
                    in_meeting = True
                    break
            except (KeyError, ValueError):
                continue

        if in_meeting or status in ("busy", "pto"):
            busy.append({
                "name":         member["name"],
                "slack":        member.get("slack_handle", ""),
                "role":         member.get("role", ""),
                "status":       "busy",
                "reason":       note,
            })
        else:
            # Score relevance to incident type
            expertise = member.get("expertise", [])
            past      = member.get("past_incidents_resolved", [])
            relevance = sum(1 for kw in incident_keywords
                            if any(kw in ex for ex in expertise)
                            or any(kw in p for p in past))

            available.append({
                "name":         member["name"],
                "slack":        member.get("slack_handle", ""),
                "role":         member.get("role", ""),
                "expertise":    expertise,
                "past_resolved": past,
                "relevance_score": relevance,
                "status":       "available",
                "note":         note,
            })

    # Sort available by relevance desc
    available.sort(key=lambda x: x["relevance_score"], reverse=True)

    result = {
        "query_incident_type": incident_type or "unspecified",
        "timestamp_utc":       now.strftime("%H:%M UTC"),
        "available":           available,
        "busy":                busy,
    }

    # Add paging recommendation
    if available:
        best = available[0]
        result["recommendation"] = (
            f"Page {best['name']} ({best['slack']}) — "
            f"available now, expertise: {', '.join(best['expertise'])}, "
            f"has resolved {len(best['past_resolved'])} similar incident(s) before"
        )
    else:
        result["recommendation"] = (
            "All primary responders are busy. "
            "Escalate to engineering manager or use on-call rotation fallback."
        )

    return json.dumps(result, indent=2)
