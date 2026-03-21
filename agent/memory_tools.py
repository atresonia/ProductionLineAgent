"""
memory_tools.py — Institutional memory: past incidents, runbooks, Slack history

Searches the data/ directory for relevant past incidents, runbooks, and
Slack messages that may help diagnose the current incident.
"""

import json
import os
from datetime import datetime, timezone

DATA_DIR = os.getenv("DATA_DIR", "../data")

INCIDENTS_DIR     = os.path.join(DATA_DIR, "past_incidents")
RUNBOOKS_DIR      = os.path.join(DATA_DIR, "runbooks")
SLACK_HISTORY_FILE = os.path.join(DATA_DIR, "slack_history.json")


def _load_text_files(directory: str) -> list[dict]:
    """Load all text/markdown files from a directory, returning {filename, content}."""
    results = []
    if not os.path.isdir(directory):
        return results
    for fname in sorted(os.listdir(directory)):
        if fname.startswith("."):
            continue
        fpath = os.path.join(directory, fname)
        try:
            with open(fpath) as f:
                results.append({"filename": fname, "content": f.read()})
        except OSError:
            continue
    return results


def search_past_incidents(query: str) -> str:
    """
    Search past incident post-mortems and transcripts for similar patterns.
    Returns the most relevant incidents matching the query keywords.
    """
    files = _load_text_files(INCIDENTS_DIR)
    if not files:
        return "[no past incidents found in data/past_incidents/]"

    query_terms = query.lower().split()
    scored = []
    for f in files:
        text = f["content"].lower()
        score = sum(1 for term in query_terms if term in text)
        if score > 0:
            scored.append((score, f))

    scored.sort(reverse=True, key=lambda x: x[0])

    if not scored:
        return f"[no past incidents matching '{query}']"

    results = []
    for score, f in scored[:3]:
        # Trim to first 800 chars to avoid overwhelming context
        preview = f["content"][:500]
        if len(f["content"]) > 500:
            preview += "\n... [truncated]"
        results.append(f"=== {f['filename']} (relevance: {score} term matches) ===\n{preview}")

    return "\n\n".join(results)


def search_runbooks(query: str) -> str:
    """
    Search internal runbooks for response procedures matching the query.
    Returns relevant runbook sections.
    """
    files = _load_text_files(RUNBOOKS_DIR)
    if not files:
        return "[no runbooks found in data/runbooks/]"

    query_terms = query.lower().split()
    scored = []
    for f in files:
        text = f["content"].lower()
        score = sum(1 for term in query_terms if term in text)
        if score > 0:
            scored.append((score, f))

    scored.sort(reverse=True, key=lambda x: x[0])

    if not scored:
        return f"[no runbooks matching '{query}']"

    results = []
    for score, f in scored[:2]:
        preview = f["content"][:600]
        if len(f["content"]) > 600:
            preview += "\n... [truncated]"
        results.append(f"=== {f['filename']} ===\n{preview}")

    return "\n\n".join(results)


def search_slack(query: str, limit: int = 10) -> str:
    """
    Search Slack channel history for messages related to the query.
    Surfaces recent warnings, related discussions, or changes mentioned casually.
    """
    try:
        with open(SLACK_HISTORY_FILE) as f:
            messages = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return "[Slack history not available]"

    query_terms = query.lower().split()
    scored = []
    for msg in messages:
        text = msg.get("text", "").lower()
        score = sum(1 for term in query_terms if term in text)
        if score > 0:
            scored.append((score, msg))

    scored.sort(reverse=True, key=lambda x: x[0])

    if not scored:
        return f"[no Slack messages matching '{query}']"

    results = []
    for score, msg in scored[:limit]:
        ts  = msg.get("ts", "?")
        usr = msg.get("user", "?")
        ch  = msg.get("channel", "?")
        txt = msg.get("text", "")
        results.append(f"[{ts}] #{ch} @{usr}: {txt}")

    return f"Found {len(results)} relevant Slack message(s):\n\n" + "\n".join(results)
