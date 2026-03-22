# Resolve — Design System
**EmpireHacks 2026 | Incident Commander Dashboard**

---

## Intent

**Who:** On-call engineer at 3am. Half-awake, pulled by a pager. Time is money — $5,600/minute. Second audience: a hackathon judge watching a live 5-minute demo who needs to lean forward and say "whoa."

**What they must do:** Understand what broke (Zone 2), watch the agent investigate live (Zone 3), approve the fix with one click (Zone 3 approval card), see it resolve.

**Feel:** Mission control during an Apollo launch. Dense with information but not chaotic. Dark. Every pixel earns its place. Datadog's dark theme × Bloomberg terminal × Linear's clarity.

---

## Color Palette

```css
/* Backgrounds — 3-level elevation */
r-bg:      #070711   /* page — deepest, slight blue-black */
r-surface: #0d0d1a   /* panel chrome, headers */
r-panel:   #0f0f1e   /* sidebar panels */
r-raised:  #121228   /* cards, hover states */

/* Borders */
r-border:  #1c1c34   /* default panel borders */
r-bright:  #2a2a4a   /* active / hover borders */

/* Text */
r-text:    #c0c0e0   /* primary — soft white, not harsh */
r-dim:     #4a4a70   /* labels, keys, panel titles */
r-muted:   #22223a   /* timestamps, placeholders, empty states */

/* Accent — electric cyan (server room monitors) */
r-cyan:    #00e5ff
r-cyan-d:  #003a50   /* cyan tinted background */

/* Status — traffic light semantics engineers already know */
r-red:     #ff2d55   /* critical / error */
r-red-d:   #3a0014   /* red tinted background */
r-amber:   #ffaa00   /* warning / in-progress / tool calls */
r-amb-d:   #3a2800   /* amber tinted background */
r-green:   #00e676   /* healthy / success / resolved */
r-grn-d:   #003a1e   /* green tinted background */
```

**Why these colors:**
- Near-black with subtle blue cast: reduces eye strain at 3am, avoids "dead monitor" feel
- Electric cyan: the color of terminals and CRTs — signals interactive without garish
- Status semantics match what SREs already know: red=bad, amber=watch, green=good
- Tinted backgrounds (r-red-d etc.) let colored borders read clearly at high contrast

---

## Typography

```css
font-mono: 'JetBrains Mono', 'Cascadia Code', 'Fira Code', monospace
font-ui:   'Inter', system-ui, sans-serif
```

**Rules:**
- **All data** → mono: metrics, tool names, log lines, timestamps, chips, JSON
- **Layout labels** → ui: panel titles, descriptions, empty states
- Panel section headers: 9px mono, `uppercase`, `tracking-widest`, `text-r-dim`
- Tool call badges: 10px mono, bold, `uppercase`, colored per type
- Metric values: 24px mono, bold — the number is the hero
- Timestamps: 9px mono, `text-r-muted`, right-aligned in trace

---

## Layout

```
┌────────────────────────────────────────────────────────────┐  48px
│  STATUS BAR  — brand / status badge / timer / health dots  │
├──────────┬─────────────────────────────────┬───────────────┤
│  METRICS │     INVESTIGATION TRACE          │  SLACK FEED   │
│  280px   │     flex-1 (hero zone)           │  260px        │
│          │                                  │               │
│ 3 metric │  Scrolling event timeline        │ #incidents    │
│ cards    │  Each entry type distinct        │ styled like   │
│          │  Approval card = full width,     │ Slack with    │
│ Topology │  impossible to miss              │ attachment    │
│ diagram  │                                  │ color bars    │
└──────────┴─────────────────────────────────┴───────────────┘
```

Grid: `280px 1fr 260px` columns. Status bar spans all 3.

---

## Investigation Trace — Entry Types

Each entry type has a distinct left-border + tinted background combination:

| Type              | Left border     | Background      | Icon          | Tag color |
|-------------------|-----------------|-----------------|---------------|-----------|
| anomaly_detected  | `border-r-red`  | `bg-r-red-d`    | AlertCircle   | red       |
| plan              | `border-r-cyan` | `bg-r-cyan-d/40`| FileText      | cyan      |
| tool_call         | `border-amber/50`| `bg-r-surface` | Wrench        | amber     |
| tool_result       | `border-green/40`| `bg-r-grn-d/30`| Database      | green     |
| reasoning         | transparent     | transparent     | Brain         | dim       |
| dashboard_image   | `border-cyan/30`| `bg-r-surface`  | Image         | cyan      |
| approval_request  | `border-2 amber`| `bg-r-amb-d`    | Zap           | amber     |
| remediation_result| green or red    | tinted          | Check/X       | —         |
| postmortem        | `border-r-cyan` | `bg-r-surface`  | FileCheck     | cyan      |

**The approval card** is the demo climax. It breaks out of the timeline format:
- 2px border (not 1px like others) — visually louder
- Full APPROVE / REJECT buttons with green/red styling
- `animate-incident-in` on mount — scale + opacity transition
- Once decided, border color changes and buttons disappear

---

## Animations

```css
dot-pulse:    opacity 1 → 0.2 → 1, 1.4s, infinite  /* status dots */
slide-up:     translateY(12px) opacity(0) → (0) opacity(1), 0.25s  /* trace entries */
incident-in:  scale(0.97) → scale(1), 0.6s  /* anomaly + approval cards */
bg-flash:     background rgba(255,45,85,0.04) × 3 pulses  /* root bg on incident start */
```

The `bg-flash` on the root `<div>` makes the transition from healthy → incident feel visceral.

---

## Components

### StatusBar (Zone 1, 48px)
- Left: `RESOLVE` in cyan mono + WS indicator + status badge
- Center: monospace timer `MM:SS` counting from detection (red when active, green when resolved)
- Right: three `●` service dots (green/amber/red) + fault inject dropdown
- Status badge states: MONITORING (dim), INVESTIGATING (amber + pulse), AWAITING APPROVAL (amber + pulse), RESOLVED (green)

### MetricsPanel (Zone 2, 280px)
- Three MetricCard stacks: Error Rate (threshold 15%), p95 Latency (threshold 1500ms), Memory MB
- Each card: large value + unit + SVG sparkline (last 60 samples, 5 min at 5s intervals)
- Sparkline colors match current threshold status (green/amber/red)
- Dashed threshold line on sparkline at the threshold value
- TopologyDiagram: SVG `frontend → api → db` with colored edges based on service health
- Active fault chip at bottom

### InvestigationTrace (Zone 3, flex-1)
- Scrollable, auto-scrolls to bottom on new entries
- "Jump to latest" pill button appears when user has scrolled up
- Entry cards use `animate-slide-up` — new entries slide in from below
- Max-width 3xl, centered for readability

### SlackFeed (Zone 4, 260px)
- Mimics Slack: avatar + bot name + timestamp + attachment card
- Each message has a 4px left-colored bar (red for critical, green for resolved)
- Non-interactive footer "Message #incidents" input (visual only)
- Auto-scrolls to bottom

---

## WebSocket Event Types (server → client)

```typescript
agent_status:        { status: 'idle' | 'monitoring' | 'investigating' | 'awaiting_approval' | 'resolved' }
anomaly_detected:    { description, severity, timestamp }
plan:                { text, timestamp }
tool_call:           { name, inputs, timestamp }
tool_result:         { name, result_preview, timestamp }
reasoning:           { text, timestamp }
dashboard_image:     { base64_png, timestamp }
approval_request:    { action, service, reason, timestamp }
remediation_result:  { status: 'approved' | 'rejected', message, timestamp }
slack_alert:         { message, severity, timestamp }
postmortem:          { markdown, filepath, timestamp }
metrics:             { api: {...}, frontend: {...}, fault, timestamp }
```

Client → server:
```typescript
{ type: 'approve' }
{ type: 'reject' }
```
