# Resolve — Autonomous Incident Commander

> **EmpireHacks 2026** — AI-powered production incident response. Detects anomalies, diagnoses root cause across logs, configs, and dashboard screenshots, routes to the right engineer, executes remediation with human approval, and writes the post-mortem. Automatically.

---

## What it does

When your API starts throwing 500s at 2am, Resolve wakes up before you do:

1. **Detects** — continuous monitoring catches error rate spikes, latency regressions, and memory leaks within seconds
2. **Triages** — business priority rules determine investigation order (a 40% error rate on `/checkout` beats a 95% error rate on `/products`)
3. **Diagnoses** — agentic loop reads logs, configs, stack traces, and a live dashboard screenshot to find root cause from 2+ independent sources
4. **Remediates** — proposes a fix, waits for human approval (one click), then executes
5. **Closes** — sends Slack alerts at each phase, verifies the fix, writes a timestamped post-mortem

---

## Why it's different

| Capability | Resolve | Rootly / incident.io |
|---|---|---|
| Vision-based dashboard analysis | ✅ Claude reads Grafana pixels | ❌ |
| Historical incident audio as retrieval source | ✅ Whisper transcription → searchable index | ❌ |
| Unified multimodal memory across incidents | ✅ logs + audio + Slack + runbooks | ❌ |
| Business priority triage (not just technical severity) | ✅ configurable per endpoint | ❌ |
| Single agent, no framework overhead | ✅ raw Anthropic SDK tool-use loop | ❌ |

---

## Demo

```
┌─────────────────────────────────────────────┐
│  RESOLVE INCIDENT COMMANDER          ● LIVE │
│─────────────────────────────────────────────│
│  2 FAULTS ACTIVE                           │
│  bad_deploy  slow_db                       │
│─────────────────────────────────────────────│
│  🔴 INVESTIGATING FIRST: /checkout         │
│     Technical: HIGH | Business: CRITICAL   │
│     Rule: "Revenue-critical override"      │
│                                             │
│  🟡 QUEUED #2: /products                   │
│     Technical: HIGH | Business: MEDIUM     │
│─────────────────────────────────────────────│
│  [ APPROVE REMEDIATION ]  [ REJECT ]       │
└─────────────────────────────────────────────┘
```

---

## Quick start

**Prerequisites:** Docker, Python 3.12+, an Anthropic API key

```bash
git clone https://github.com/atresonia/ProductionLineAgent
cd ProductionLineAgent

# Add your API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > agent/.env

# Start everything
./run.sh
```

Open **http://localhost:5050** — the Bloomberg-terminal-style dashboard.

---

## Inject a fault

```bash
# Single fault
python3 chaos.py bad_deploy      # Payment gateway misconfigured → /checkout 500s
python3 chaos.py slow_db         # 2.5s DB query latency
python3 chaos.py memory_leak     # API memory grows per request
python3 chaos.py db_down         # DB connection refused

# Multi-fault (triggers priority triage)
python3 chaos.py bad_deploy slow_db

# Clear all
python3 chaos.py none
```

The agent detects the anomaly within ~30s, investigates, and surfaces an **APPROVE** button in the dashboard before executing any fix.

---

## Architecture

```
load_gen.py (~2 req/s)
     │
     ▼
frontend:3000 ──► api:8000 ──► db:5432
     │                │
     └──── logs/ ─────┘
               │
               ▼
          agent/agent.py  (monitor loop, 5s poll)
               │
               ▼
          investigator.py  (Claude Opus 4.6 tool-use loop)
               │
        ┌──────┴──────┐
        │  13 tools   │  read_logs, get_error_rate, get_endpoint_error_rates,
        │             │  read_config_file, parse_stack_traces, capture_dashboard,
        │             │  get_active_faults, execute_remediation, send_slack_alert,
        └─────────────┘  search_past_incidents, get_team_availability, ...
               │
               ▼
          dashboard/  (Flask + SSE, Bloomberg Terminal UI)
          postmortems/  (auto-generated markdown)
```

**Single agent, not multi-agent.** Incident investigation is serial causal reasoning — each step depends on the last. Splitting across agents breaks the causal chain. One Claude Opus 4.6 agent in a tool-use loop handles everything, with parallel tool calls within a single turn where applicable.

---

## Two-phase investigation protocol

Every investigation is auditable:

**Phase 1 — Triage**
- Load business priority config (`read_triage_config`)
- Get per-endpoint error rates (`get_endpoint_error_rates`)
- Rank incidents by business impact, not just technical severity
- State the investigation order with explicit rule references

**Phase 2 — Investigate & Remediate**
- Confirm active faults (`get_active_faults`)
- Read logs, configs, stack traces
- Capture dashboard screenshot for visual analysis
- Alert Slack (severity=critical)
- Propose remediation → human approves → execute
- Verify fix with 1-min error window
- Alert Slack (severity=resolved)
- Generate post-mortem

---

## Repo structure

```
agent/
  agent.py          # Entry point: monitor, --trigger, --demo modes
  investigator.py   # Agentic loop: two-phase protocol, tool-use
  monitor.py        # Polling: error rate, latency, memory + ML predictions
  predictor.py      # IsolationForest + LinearRegression for proactive alerts
  postmortem.py     # Auto-generates timestamped post-mortem markdown
  tools.py          # 13 tool implementations + Anthropic schemas + dispatch
services/
  api/app.py        # Flask API: /products /checkout /health /metrics
  frontend/app.py   # Flask frontend: proxies to api
  db/init.sql       # PostgreSQL seed
dashboard/
  app.py            # Flask + SSE state streaming
  templates/        # Bloomberg Terminal UI (orange/black, real-time updates)
configs/
  deployment.yaml   # Intentionally buggy config (agent finds the issues)
chaos.py            # Fault injection: bad_deploy | slow_db | memory_leak | db_down
load_gen.py         # Traffic generator: ~2 req/s
run.sh              # One-command startup
```

---

## Stack

- **Agent reasoning** — Anthropic Claude Opus 4.6 (tool-use loop, no framework)
- **Audio transcription** — OpenAI Whisper (local, no API key)
- **Demo services** — Python / Flask, PostgreSQL, Docker Compose
- **Dashboard** — Flask + Server-Sent Events, Bloomberg Terminal UI
- **Alerting** — Slack Incoming Webhooks
- **ML predictions** — scikit-learn IsolationForest + LinearRegression

---

## Commands

```bash
./run.sh                  # Start full stack
./run.sh bad_deploy       # Start + inject fault
./run.sh multi            # Start + inject bad_deploy + slow_db
./run.sh stop             # Stop dashboard + agent (leaves Docker running)

tail -f logs/agent.log    # Watch live investigation
tail -f logs/dashboard.log
```
