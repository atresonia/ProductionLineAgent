# Resolve — Product Requirements Document
**EmpireHacks 2026 | Track 1: The Operator**
**Version 1.0 | March 21, 2026**

---

## 1. Problem

Production incidents are a coordination failure, not just a technical one.

When something breaks in production today:

- **PagerDuty** pages whoever is next in the rotation — regardless of expertise, availability, or whether they are in a meeting
- **Datadog / Grafana** shows dashboards — an engineer still has to stare at them and reason
- **Slack** has the answer somewhere — buried in a thread from 3 weeks ago that nobody can find at 3am
- **Confluence / Notion** has a runbook — written 18 months ago, probably outdated
- **The incident call from last time** was recorded — and immediately forgotten

The result: a half-asleep engineer spends 47 minutes (industry average MTTR) manually correlating five systems, while the company loses $5,600 every minute.

**The core gap:** every existing tool handles one layer. Nothing handles the full loop — detect, reason, remember, route, act, document.

---

## 2. Vision

> Resolve is the first autonomous incident commander.
>
> It detects anomalies, diagnoses root cause across every source your team uses — logs, Slack, runbooks, past incident calls, deployment configs — figures out who is actually available and best suited to respond, pages them with a full brief, executes the fix with their approval, and writes the post-mortem. All in under 3 minutes.

The bar: hand Resolve a broken system, get back a resolved incident and a written post-mortem.

---

## 3. Users

| User | Pain Today | What Resolve Does for Them |
|---|---|---|
| On-call engineer | Woken at 3am, 47 min of log archaeology, post-mortem to write | Gets a full brief and one-click approval request. Done in 3 min. |
| SRE lead | Manually routes incidents, chases runbook updates, reviews post-mortems | Incidents route themselves. Runbooks are cited automatically. Post-mortems write themselves. |
| Engineering manager | No visibility until someone escalates | Live Slack thread from minute 0. Full timeline on resolution. |

**Primary target for launch:** startups and scale-ups with 10–200 engineers, an on-call rotation, and no dedicated SRE team. They have BlackRock-scale incident pain with none of BlackRock's incident infrastructure.

---

## 4. Core Features

### 4.1 Autonomous Detection
- Polls service logs and metrics every 5 seconds
- Triggers investigation when any threshold is breached:
  - Error rate > 15% over 3 minutes
  - p95 latency > 1,500ms
  - Memory growth > 80MB in 5 minutes
- Debounced — will not re-trigger for the same active incident

### 4.2 Multimodal Context Gathering

This is what separates Resolve from a log parser. Before forming a hypothesis, the agent gathers context from every source your team uses.

| Source | Format | What Resolve Extracts |
|---|---|---|
| Application logs | Structured JSON | Error rates, latency, memory trends |
| Stack traces | Plain text (multi-line) | Exact crash location, call chain |
| Grafana screenshots | Image (Claude vision) | Visual spike shape, timing, slope — information logs cannot express |
| Past incident recordings | Audio (MP3/WAV → Whisper) | What the team tried, what worked, institutional knowledge that was never written down |
| Slack channel history | Unstructured natural language | Prior warnings, related discussions, recent changes mentioned casually |
| Internal runbooks | Markdown prose | Step-by-step response procedures |
| Deployment manifests | YAML | Misconfigured env vars, missing secrets, absent resource limits |
| Team calendar | Structured (JSON / Google Calendar API) | Who is in a meeting, who is on PTO, who is actually available |

**Edge cases Resolve handles explicitly:**
- **Conflicting signals:** stack trace says variable is missing; YAML shows it exists → agent reasons the value is stale, not absent
- **Stale runbooks:** runbook says "restart nginx"; current stack is containerised → agent applies the principle, not the literal command
- **Missing data:** calendar unavailable for an engineer → falls back to Slack status; states the gap explicitly in its report
- **Buried signals:** one relevant Slack message from 3 days ago in a noisy channel → agent surfaces it as primary evidence
- **Audio-only knowledge:** fix was explained verbally on a call, never documented → Whisper transcribes it, agent retrieves it

### 4.3 Root Cause Analysis
- Two-phase protocol:
  - **Phase 1 — Plan:** agent writes its investigation approach before calling any tools
  - **Phase 2 — Investigate:** iterative tool calls until hypothesis is confirmed by at least two independent sources
- Final output: root cause, evidence trail, confidence %, recommended remediation, blast radius

### 4.4 Intelligent Routing
- Checks team availability across calendar + Slack status
- Factors in past incident history: who has solved this type of problem before
- Pages the best available engineer — not just the next in rotation
- DM includes: root cause summary, runbook link, relevant past incident context, one-click remediation approval

### 4.5 Incident Communication
- Posts structured update to `#incidents` Slack channel at:
  - Incident detected
  - Root cause identified
  - Remediation approved
  - Incident resolved
- Updates are written for an engineering audience — specific, not generic

### 4.6 Remediation Execution
- Human-in-the-loop: engineer approves before any action is taken
- Supported actions: rollback deploy, restart service, scale up replicas
- Agent explains exactly what it will do and why before asking for approval

### 4.7 Post-Mortem Generation
- Auto-generated immediately on resolution
- Contains: timeline, root cause, evidence, contributing factors, remediation taken, prevention recommendations
- Written to disk + posted to Slack
- Indexed for future incident retrieval

### 4.8 Institutional Memory
- Every resolved incident is indexed (logs, transcript, post-mortem)
- When a new incident fires, agent searches past incidents for similar patterns
- Surfaces: "We had a near-identical incident on March 3rd. Here is what the team tried and what worked."
- Audio from past incident calls is transcribed and included in the index

---

## 5. Technical Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         RESOLVE                                 │
│                                                                 │
│  ┌─────────────┐                                                │
│  │   Monitor   │  polls every 5s → threshold breach detected   │
│  └──────┬──────┘                                                │
│         │                                                       │
│         ▼                                                       │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Investigator (Claude claude-opus-4-6)           │   │
│  │                                                         │   │
│  │  Phase 1: Plan    → writes investigation approach       │   │
│  │  Phase 2: Execute → calls tools iteratively             │   │
│  │                                                         │   │
│  │  Tools:                                                 │   │
│  │  ├── Log Tools      read_logs, search_logs,             │   │
│  │  │                  get_error_rate, get_latency_stats,  │   │
│  │  │                  get_memory_trend, get_recent_errors │   │
│  │  ├── System Tools   get_deploy_history, list_services,  │   │
│  │  │                  parse_stack_traces                  │   │
│  │  ├── Config Tools   read_config_file, list_config_files │   │
│  │  ├── Comms Tools    send_slack_alert, page_engineer     │   │
│  │  ├── Memory Tools   search_past_incidents,              │   │
│  │  │                  search_runbooks, search_slack       │   │
│  │  ├── Audio Tools    transcribe_recording,               │   │
│  │  │                  get_past_transcripts                │   │
│  │  ├── Calendar Tools get_team_availability               │   │
│  │  └── Action Tools   execute_remediation                 │   │
│  └─────────────────────────────────────────────────────────┘   │
│         │                                                       │
│         ▼                                                       │
│  ┌─────────────┐   ┌──────────────┐   ┌────────────────────┐  │
│  │  Slack API  │   │  Post-mortem │   │  Incident Index    │  │
│  │  (external) │   │  Generator   │   │  (institutional    │  │
│  │             │   │              │   │   memory)          │  │
│  └─────────────┘   └──────────────┘   └────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘

Fake infrastructure (demo environment):
  Docker Compose: frontend (Flask) → api (Flask) → db (PostgreSQL)
  chaos.py: injects bad_deploy / memory_leak / slow_db / db_down
  load_gen.py: generates realistic traffic
```

**Stack:**
- Reasoning engine: Claude claude-opus-4-6 via Anthropic API (tool use)
- Audio transcription: OpenAI Whisper (runs locally, no API key needed)
- Frontend UI: Lovable
- Backend: Python / FastAPI
- Demo infra: Docker Compose
- External integrations: Slack API (real), Google Calendar API (mocked for hackathon)

---

## 6. Multimodal Input Summary

| Modality | Input | How Processed |
|---|---|---|
| **Vision** | Grafana / dashboard screenshots | Passed directly to Claude as base64 image. Claude processes pixels — no text conversion. |
| **Audio** | Past incident call recordings (MP3/WAV) | Whisper transcribes locally → text indexed → Claude retrieves relevant segments |
| **Unstructured text** | Slack messages, runbooks, stack traces | Claude reads natural language and multi-line plain text directly |
| **Structured text** | JSON logs, YAML configs, metrics | Parsed and queried via tools |
| **Calendar / availability** | Team schedule data | Structured JSON (Google Calendar API or mock) |

---

## 7. Hackathon Scope

### In Scope (Built for Demo)
- Full agent pipeline: detect → plan → investigate → route → remediate → document
- Log tools: all six
- Screenshot ingestion (Claude vision)
- Audio transcription (Whisper, local)
- Slack posting (real Slack API)
- Config file reading (YAML)
- Stack trace extraction
- Intelligent paging recommendation
- Post-mortem generation
- Institutional memory (indexed past incidents, searchable)
- Demo infrastructure (Docker Compose + chaos scripts)
- Lovable UI: live incident dashboard with agent reasoning trace

### Mocked for Hackathon (Real in Production)
- Google Calendar: JSON file with team availability (same data, no OAuth)
- Slack history search: JSON file of past messages (same data, no search API setup)
- Confluence / Notion: local markdown runbook files (same data, no API)
- PagerDuty integration: agent DMs via Slack instead

### Out of Scope for Hackathon
- Multi-tenant SaaS infrastructure
- Real-time audio (live call transcription)
- Custom alerting rule builder
- Mobile app
- SSO / enterprise auth

---

## 8. Demo Flow (5 minutes)

```
Setup:    docker compose up + load_gen.py running
          Slack channel visible on second screen

0:00      Services healthy. Normal traffic visible.

0:20      "We just deployed v2.1."
          → python chaos.py bad_deploy

0:35      Resolve detects: error rate 87% on /checkout

0:45      Agent writes investigation plan (visible in UI)

1:00      Agent calls tools — reasoning trace streams live:
          - reads api logs → confirms 500s on /checkout
          - checks error rate → 87% in last 3 minutes
          - searches Slack history → finds @priya's message:
            "heads up — payment gateway URL changed"
          - reads deployment.yaml → finds stale PAYMENT_GATEWAY_URL
          - reads past incident transcript (audio) →
            "don't just update the env var, restart the service"
          - checks calendar → Priya in meeting, Marcus available

1:45      Root cause confirmed:
          "PAYMENT_GATEWAY_URL points to decommissioned endpoint.
           Introduced in v2.1 deploy. Config not updated when
           gateway migrated."

2:00      Slack channel receives:
          🔴 SEV-2 | api /checkout failing | Root cause identified
          Marcus paged with full brief + runbook + past incident context

2:15      Remediation request: "Rollback api to v2.0. Approve? [y/N]"
          → y

2:25      Services recover. Error rate → 0%.

2:35      Slack channel:
          ✅ Resolved in 2m 14s | Rollback to v2.0 | Post-mortem: [link]

2:45      Post-mortem shown: timeline, root cause, evidence, prevention.
          Incident indexed for future retrieval.

3:00      "Manual average for this type of incident: 47 minutes.
           Resolve: 2 minutes 14 seconds."
```

---

## 9. Judging Rubric Coverage

| Criterion | Weight | How Resolve Covers It |
|---|---|---|
| **Agentic Autonomy** | 35% | Two-phase protocol (plan then act). Dynamic tool selection — different fault types produce different investigation paths. No hardcoded playbook. Agent unblocks itself when one source is insufficient by trying another. |
| **Tool Use & Integration** | 25% | 15 tools across 6 categories. Real external deliverables: Slack incident thread (visible to whole team), post-mortem document. `execute_remediation` makes a real change that running services respond to immediately. |
| **Multimodal & Unstructured Robustness** | 25% | Five input modalities: vision (screenshots), audio (Whisper pipeline), unstructured text (Slack, runbooks, stack traces), structured data (logs, YAML), calendar. Handles conflicting signals, stale runbooks, missing calendar data, and noise in Slack history explicitly. |
| **State & Context Mgmt** | 15% | Full conversation history threaded across all tool turns. Investigation logged to `resolve.log` with structured entries. Incident state tracked to prevent duplicate triggers. Institutional memory persists across incidents. |

---

## 10. Startup Path

```
Hackathon demo
     │
     ▼
3-5 beta customers via team network (engineers who've felt this pain)
     │
     ▼
Product-market fit signal: does MTTR actually drop?
     │
     ▼
Launch: $75/engineer/month on-call rotation
     │
     ▼
Year 1: Bottom-up adoption at startups (10-50 engineers)
         Target: 200 customers = $900K MRR
     │
     ▼
Year 2: Land enterprise SRE teams
         Target: 50 enterprise contracts at $50K/year = $2.5M ARR
     │
     ▼
Moat: institutional memory compounds. The longer a team uses Resolve,
      the more past incidents are indexed, the better the routing,
      the faster future incidents resolve. Data flywheel.
```

**Market:**
- PagerDuty: $3.5B market cap (alerting only)
- Datadog: $30B market cap (dashboards only)
- Neither reasons. Neither acts. Neither remembers.

**Comparable exits:** VictorOps (acquired by Splunk $120M) → Splunk (acquired by Cisco $28B). The incident management layer is valuable. Nobody has built the AI-native version.

---

## 11. What Makes This Winnable

Most teams at this hackathon will build one of:
- An AI assistant that answers questions
- A document summarizer with a chat interface
- A "chatbot for X"

Resolve does none of these. It:
1. **Detects** problems without being asked
2. **Reasons** across five different input modalities with conflicting signals
3. **Acts** in external systems (Slack, production services)
4. **Remembers** across incidents — gets smarter over time
5. **Communicates** to the whole team autonomously

The demo is visceral. Judges watch a real production incident — injected live — get resolved in 2 minutes with a full post-mortem written automatically. No judge has seen that before.

---

*Resolve — EmpireHacks 2026*
