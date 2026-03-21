# ProductionLineAgent — Autonomous Incident Commander

Autonomous incident response agent for EmpireHacks 2026. Detects anomalies, diagnoses root cause across multimodal sources (logs, configs, dashboard screenshots, past incident audio), routes to best-available engineer, executes remediation with human approval, writes the post-mortem.

**Differentiators vs. Rootly / incident.io / Resolve.ai:**
1. Vision-based dashboard analysis (Claude analyzes Grafana screenshot pixels — nobody else does this)
2. Historical incident call audio as searchable retrieval source (not just transcribing current calls)
3. Unified multimodal memory that compounds across incidents (audio + Slack + runbooks + past reports in one index)

## Tech Stack

- Python 3.12, Flask (demo services), Anthropic SDK (agent reasoning via Claude Opus 4.6 tool use)
- OpenAI Whisper (local audio transcription, no API key)
- PostgreSQL (demo DB), Docker Compose (demo infra)
- Slack API (real webhook), Google Calendar (mocked JSON)

## Repo Structure

```
├── agent/                    # CORE — the autonomous agent
│   ├── agent.py              # Entry point: monitor mode, --trigger, --demo modes
│   ├── investigator.py       # Agentic loop: Claude tool-use with two-phase protocol
│   ├── monitor.py            # Continuous polling: error rate, latency, memory thresholds
│   ├── postmortem.py         # Auto-generates post-mortem markdown from agent conclusion
│   ├── tools.py              # All tool implementations + Anthropic tool schemas + dispatch
│   ├── requirements.txt      # anthropic, python-dotenv, rich
│   └── .env.example
├── services/                 # Demo microservices (Dockerized)
│   ├── api/app.py            # Flask API — /products, /checkout, /health, /metrics
│   ├── frontend/app.py       # Flask frontend — proxies to api
│   └── db/init.sql           # PostgreSQL seed: products + orders tables
├── configs/
│   └── deployment.yaml       # Intentionally buggy: stale PAYMENT_GATEWAY_URL, missing key, no mem limit
├── chaos/                    # Runtime fault flag (current_fault file)
├── logs/                     # Shared log volume — services write, agent reads
├── chaos.py                  # Fault injection: bad_deploy, memory_leak, slow_db, db_down, none
├── load_gen.py               # Traffic generator: ~2 req/s mix of products + checkout
├── generate_dashboard.py     # Creates fake Grafana screenshot (matplotlib) → assets/
├── docker-compose.yml        # db → api → frontend, shared log/chaos volumes
└── PRD.md
```

## Commands

```bash
# Start demo infrastructure
docker compose up --build

# Generate traffic (separate terminal)
python load_gen.py

# Inject a fault
python chaos.py bad_deploy     # payment gateway misconfigured
python chaos.py memory_leak    # API memory grows per request
python chaos.py slow_db        # 2.5s query latency
python chaos.py db_down        # DB connection refused
python chaos.py none           # clear all faults

# Run agent — continuous monitor mode
cd agent && pip install -r requirements.txt && python agent.py

# Run agent — single investigation
cd agent && python agent.py --demo bad_deploy

# Generate dashboard screenshot for vision demo
pip install matplotlib numpy && python generate_dashboard.py
```

## What Already Works

- **Demo infra**: Docker Compose stack (frontend → api → db) with structured JSON logging + plain-text noise
- **Chaos injection**: File-based fault toggle, services read on each request, zero-downtime switching
- **13 agent tools**: read_logs, search_logs, get_error_rate, get_latency_stats, get_memory_trend, get_recent_errors, get_deploy_history, list_services, read_config_file, list_config_files, parse_stack_traces, send_slack_alert, execute_remediation
- **Agentic loop**: Claude Opus 4.6 tool-use, two-phase plan-then-investigate protocol, human approval gate on remediation
- **Vision support**: Dashboard screenshot passed as base64 image to Claude (generate_dashboard.py creates the PNG)
- **Post-mortem generation**: Auto-extracts sections from agent conclusion, writes timestamped markdown
- **Monitor mode**: Polls every 5s, debounces (won't re-trigger same active incident)

## What Still Needs to Be Built

These are the **differentiator features** that don't exist in competing products:

1. **Audio transcription pipeline** — Whisper integration to transcribe past incident call recordings. Files go in `data/audio/`, transcripts indexed for retrieval. Tool: `transcribe_recording`, `get_past_transcripts`
2. **Institutional memory / search** — Searchable index across past incident reports, audio transcripts, Slack messages, runbooks. Tools: `search_past_incidents`, `search_runbooks`, `search_slack`. Use ChromaDB or SQLite FTS5.
3. **Mock data corpus** — Slack history JSON, team calendar JSON, runbook markdown files, past incident reports, past audio transcripts. Lives in `data/mock/`
4. **Intelligent routing** — Check calendar + Slack status + incident history to pick best engineer. Tool: `get_team_availability`, `page_engineer`
5. **Structured Slack thread** — Post updates at each phase: detected → investigating → root cause found → remediation approved → resolved. Currently only fires one alert.

## Coding Conventions

- Type hints everywhere. No `Any`.
- Tool functions return JSON strings (structured dicts serialized). Never raw prose.
- Every tool has a matching schema in `TOOL_SCHEMAS` list and entry in `TOOL_FN_MAP` dict (both in tools.py).
- Schema is the contract — write it first, implement second. Description must say WHEN to use the tool, not just what it does.
- Use `logging` or the existing `log()` helper. Never bare `print()` in agent code (Rich console is fine for CLI output).
- Imports: stdlib → third-party → local. Absolute imports only.
- Tools must catch exceptions and return error JSON `{"error": str}` — never crash the agent loop.

## Agent Prompt Rules

The system prompt lives in `investigator.py` as `SYSTEM_PROMPT`. When modifying it:
- Use XML tags for injected context: `<context>`, `<evidence>`, `<output_format>`
- Separate instructions from data — never put commands inside evidence blocks
- The two-phase protocol (PLAN then INVESTIGATE) is non-negotiable. It makes reasoning auditable.
- Two-source confirmation rule: root cause needs evidence from 2+ independent sources before declaring confirmed
- Keep Slack messages terse and technical. No filler ("I'm looking into this!")

## Key Design Decisions

- **File-based chaos**: Fault state is a single file (`chaos/current_fault`). Services check it per-request. Agent clears it to "none" for remediation. No Docker restart needed.
- **Shared log volume**: Services write to `/app/logs/`, mounted at `./logs/` on host. Agent reads the same files. No log aggregation service needed.
- **Mixed log format**: API deliberately emits both JSON lines AND plain-text noise (nginx-style, Java-style). Agent must parse both. This tests robustness.
- **Human-in-the-loop**: `execute_remediation` prompts in terminal. Never auto-executes.
- **Multimodal input**: Screenshots go as base64 image blocks. Audio goes through Whisper → text → indexed. Both are differentiators — prioritize them.

### Architecture: Single Agent, Not Multi-Agent

We use one Claude Opus 4.6 agent in a tool-use loop. This is deliberate, not a limitation.

Why not multi-agent:
- Incident investigation is serial causal reasoning — each step depends on the last
- Splitting reasoning across agents breaks the causal chain (Cognition's "conflicting implicit decisions" problem)
- Multi-agent costs 15x more tokens for problems that aren't embarrassingly parallel (Anthropic's own finding)
- Our problem has 3 services and one incident at a time — no parallelism benefit

What we do instead:
- Parallel tool calls within a single agent turn (Claude batches independent calls naturally)
- Two-phase protocol (plan then investigate) for coherent reasoning
- Context compression at index time for institutional memory (summarize past incidents when stored, not when retrieved)
- Human-in-the-loop as the coordination mechanism (not agent-to-agent coordination)

## What NOT to Do

- Do not hardcode investigation paths per fault type. The agent must dynamically select tools based on evidence.
- Do not skip the plan phase. It's what makes the demo impressive and the agent auditable.
- Do not build a real observability pipeline. Mock data is fine. The agent reasoning is the product.
- Do not over-engineer the memory index. SQLite FTS5 or a flat JSON file with keyword search is sufficient for hackathon.
- Do not use LangChain, CrewAI, or any agent framework. Raw Anthropic SDK tool-use loop is simpler and more demo-able.

## Git Workflow

- Branches: `feat/`, `fix/`, `refactor/`
- Conventional commits: `feat:`, `fix:`, `docs:`, `test:`
- Test the full demo flow (docker compose up → load_gen → chaos → agent) before any PR