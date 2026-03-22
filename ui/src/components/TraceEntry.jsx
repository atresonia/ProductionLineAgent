import { useState } from 'react'
import {
  AlertCircle, FileText, Wrench, Database, Image,
  Brain, Zap, CheckCircle2, XCircle, FileCheck, ChevronDown, ChevronUp,
  ListOrdered, Link2, Minus
} from 'lucide-react'

// ── Shared primitives ────────────────────────────────────────────────────────

function Badge({ text, color }) {
  const styles = {
    amber: 'bg-r-amb-d border-r-amber/40 text-r-amber',
    green: 'bg-r-grn-d border-r-green/40 text-r-green',
    red:   'bg-r-red-d border-r-red/40   text-r-red',
    cyan:  'bg-r-cyan-d border-r-cyan/40 text-r-cyan',
    dim:   'bg-r-muted  border-r-border  text-r-dim',
  }
  return (
    <span className={`inline-flex items-center font-mono text-[10px] font-semibold
      px-2 py-0.5 rounded border ${styles[color] || styles.dim}`}>
      {text}
    </span>
  )
}

function Ts({ ts }) {
  return ts ? <span className="font-mono text-[9px] text-r-muted flex-shrink-0 mt-0.5">{ts}</span> : null
}

// Base card wrapper — gives every entry a visible raised surface
function Card({ accent, children, className = '' }) {
  const borders = {
    red:   'border-l-r-red',
    cyan:  'border-l-r-cyan',
    green: 'border-l-r-green',
    amber: 'border-l-[#ffaa00]',
    dim:   'border-l-r-border',
  }
  return (
    <div className={`flex gap-3 p-3 rounded border border-r-border bg-r-raised
      border-l-2 ${borders[accent] || 'border-l-r-border'} ${className}`}>
      {children}
    </div>
  )
}

function JsonPreview({ data, maxLen = 140 }) {
  const [expanded, setExpanded] = useState(false)
  const raw = typeof data === 'string' ? data : JSON.stringify(data, null, 2)
  const preview = raw.length > maxLen ? raw.slice(0, maxLen) + ' …' : raw
  return (
    <div className="mt-1.5">
      <pre className="font-mono text-[10px] text-r-text/70 leading-relaxed whitespace-pre-wrap break-words">
        {expanded ? raw : preview}
      </pre>
      {raw.length > maxLen && (
        <button
          onClick={() => setExpanded(v => !v)}
          className="font-mono text-[9px] text-r-cyan/60 hover:text-r-cyan mt-1 flex items-center gap-0.5"
        >
          {expanded ? <><ChevronUp size={9}/>collapse</> : <><ChevronDown size={9}/>expand</>}
        </button>
      )}
    </div>
  )
}

// ── Simple markdown renderer (bold, headers, lists, tables) ──────────────────

function renderSimpleMarkdown(text) {
  if (!text) return null
  return text.split('\n').map((line, i) => {
    // Table separator rows — skip entirely
    if (/^\|[\s\-:|]+\|/.test(line)) return null
    // Table data rows — render as spaced columns
    if (/^\|.+\|/.test(line)) {
      const cells = line.split('|').map(c => c.trim()).filter(Boolean)
      return (
        <div key={i} className="flex gap-4">
          {cells.map((c, j) => (
            <span key={j} className="flex-1" dangerouslySetInnerHTML={{
              __html: c.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
            }} />
          ))}
        </div>
      )
    }
    // ## headers
    if (/^## (.+)/.test(line)) {
      return (
        <div key={i} className="font-semibold text-r-text/80 mt-1.5 mb-0.5 not-italic">
          {line.slice(3)}
        </div>
      )
    }
    // Bullet list items
    if (/^[-*] (.+)/.test(line)) {
      return (
        <div key={i} className="flex gap-1.5 ml-2">
          <span className="text-r-dim flex-shrink-0">•</span>
          <span dangerouslySetInnerHTML={{
            __html: line.slice(2).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
          }} />
        </div>
      )
    }
    // Blank line
    if (!line.trim()) return <br key={i} />
    // Regular line with bold support
    return (
      <span key={i} dangerouslySetInnerHTML={{
        __html: line.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      }} />
    )
  }).filter(Boolean)
}

// ── Entry types ───────────────────────────────────────────────────────────────

function AnomalyEntry({ entry }) {
  return (
    <div className="animate-incident-in flex gap-3 p-4 rounded border-2 border-r-red bg-r-red-d">
      <AlertCircle size={16} className="text-r-red flex-shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1.5">
          <span className="font-mono text-[10px] font-semibold text-r-red uppercase tracking-widest">
            Anomaly Detected
          </span>
          <Ts ts={entry.ts} />
        </div>
        <pre className="font-mono text-[11px] text-r-text whitespace-pre-wrap leading-relaxed">
          {entry.description}
        </pre>
      </div>
    </div>
  )
}

function PlanEntry({ entry }) {
  return (
    <Card accent="cyan" className="bg-r-cyan-d/20">
      <FileText size={14} className="text-r-cyan flex-shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1.5">
          <span className="font-mono text-[10px] font-semibold text-r-cyan uppercase tracking-widest">
            Investigation Plan
          </span>
          <Ts ts={entry.ts} />
        </div>
        <p className="font-mono text-[11px] text-r-text leading-relaxed italic">
          {entry.text}
        </p>
      </div>
    </Card>
  )
}

function ToolCallEntry({ entry }) {
  return (
    <Card accent="amber">
      <Wrench size={13} className="text-r-amber flex-shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <Badge text={entry.name} color="amber" />
          <Ts ts={entry.ts} />
        </div>
        <JsonPreview data={entry.inputs} maxLen={120} />
      </div>
    </Card>
  )
}

function ToolResultEntry({ entry }) {
  const [expanded, setExpanded] = useState(false)
  const preview = entry.result_preview || ''
  const maxLen = 200

  return (
    <Card accent="green" className="bg-r-grn-d/20">
      <Database size={13} className="text-r-green flex-shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className="font-mono text-[10px] text-r-green font-semibold uppercase tracking-wider">
            ← {entry.name}
          </span>
          <Ts ts={entry.ts} />
        </div>
        <pre className="font-mono text-[10px] text-r-text/70 leading-relaxed whitespace-pre-wrap break-words">
          {expanded ? preview : preview.slice(0, maxLen) + (preview.length > maxLen ? ' …' : '')}
        </pre>
        {preview.length > maxLen && (
          <button
            onClick={() => setExpanded(v => !v)}
            className="font-mono text-[9px] text-r-cyan/60 hover:text-r-cyan mt-1 flex items-center gap-0.5"
          >
            {expanded ? <><ChevronUp size={9}/>collapse</> : <><ChevronDown size={9}/>expand</>}
          </button>
        )}
      </div>
    </Card>
  )
}

function ReasoningEntry({ entry }) {
  return (
    <Card accent="dim">
      <Brain size={13} className="text-r-dim flex-shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="font-mono text-[9px] text-r-dim uppercase tracking-widest">Reasoning</span>
          <Ts ts={entry.ts} />
        </div>
        <div className="font-mono text-[11px] text-r-text/60 italic leading-relaxed flex flex-col gap-0.5">
          {renderSimpleMarkdown(entry.text)}
        </div>
      </div>
    </Card>
  )
}

function DashboardImageEntry({ entry }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <Card accent="cyan">
      <Image size={13} className="text-r-cyan flex-shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1.5">
          <Badge text="dashboard screenshot" color="cyan" />
          <Ts ts={entry.ts} />
        </div>
        <button
          onClick={() => setExpanded(v => !v)}
          className="font-mono text-[9px] text-r-cyan/70 hover:text-r-cyan flex items-center gap-0.5 mb-2"
        >
          {expanded ? <><ChevronUp size={9}/>hide image</> : <><ChevronDown size={9}/>show image</>}
        </button>
        {expanded && entry.base64_png && (
          <img
            src={`data:image/png;base64,${entry.base64_png}`}
            alt="Dashboard screenshot"
            className="max-w-full rounded border border-r-border"
          />
        )}
      </div>
    </Card>
  )
}

function ApprovalEntry({ entry, isPending, onApprove, onReject }) {
  const decided = entry.decision
  return (
    <div className={`animate-incident-in rounded border-2 p-4
      ${decided === 'approve' ? 'border-r-green bg-r-grn-d/40'
      : decided === 'reject'  ? 'border-r-red/60 bg-r-red-d/40'
      : 'border-r-amber       bg-r-amb-d'}`}
    >
      <div className="flex items-start gap-3">
        <Zap size={16} className={
          decided === 'approve' ? 'text-r-green' :
          decided === 'reject'  ? 'text-r-red'   :
          'text-r-amber animate-dot-pulse'
        } />
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-2">
            <span className="font-mono text-[10px] font-semibold uppercase tracking-widest text-r-amber">
              {decided
                ? (decided === 'approve' ? '✓ Approved' : '✗ Rejected')
                : 'Remediation Request'}
            </span>
            <Ts ts={entry.ts} />
          </div>

          <div className="space-y-1 mb-2">
            <div className="font-mono text-[11px]">
              <span className="text-r-dim">action:  </span>
              <span className="text-r-text font-semibold">{entry.action}</span>
            </div>
            <div className="font-mono text-[11px]">
              <span className="text-r-dim">service: </span>
              <span className="text-r-text">{entry.service}</span>
            </div>
            {entry.reason && (
              <div className="font-mono text-[11px] text-r-text/60 italic">{entry.reason}</div>
            )}
          </div>

          {!decided && isPending && (
            <div className="flex gap-2 mt-3">
              <button
                onClick={onApprove}
                className="flex items-center gap-1.5 px-5 py-2 rounded font-mono
                  text-[11px] font-semibold text-r-green bg-r-grn-d
                  border border-r-green/40 hover:border-r-green hover:bg-green-900/20
                  transition-colors"
              >
                <CheckCircle2 size={13} />
                APPROVE
              </button>
              <button
                onClick={onReject}
                className="flex items-center gap-1.5 px-5 py-2 rounded font-mono
                  text-[11px] font-semibold text-r-red bg-r-red-d
                  border border-r-red/40 hover:border-r-red
                  transition-colors"
              >
                <XCircle size={13} />
                REJECT
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function RemediationResultEntry({ entry }) {
  const ok = entry.status === 'approved'
  return (
    <Card accent={ok ? 'green' : 'red'} className={ok ? 'bg-r-grn-d/20' : 'bg-r-red-d/20'}>
      {ok
        ? <CheckCircle2 size={14} className="text-r-green flex-shrink-0 mt-0.5" />
        : <XCircle      size={14} className="text-r-red   flex-shrink-0 mt-0.5" />
      }
      <div>
        <div className="flex items-center gap-2 mb-0.5">
          <span className={`font-mono text-[10px] font-semibold uppercase tracking-widest
            ${ok ? 'text-r-green' : 'text-r-red'}`}>
            {ok ? 'Remediation Executed' : 'Remediation Rejected'}
          </span>
          <Ts ts={entry.ts} />
        </div>
        <p className="font-mono text-[11px] text-r-text/70">{entry.message}</p>
      </div>
    </Card>
  )
}

function PostmortemEntry({ entry }) {
  const [expanded, setExpanded] = useState(false)

  const renderMarkdown = (md) =>
    md.split('\n').map((line, i) => {
      if (/^# (.+)/.test(line))  return <h1 key={i}>{line.slice(2)}</h1>
      if (/^## (.+)/.test(line)) return <h2 key={i}>{line.slice(3)}</h2>
      if (/^\| /.test(line)) {
        if (/^[\| \-:]+$/.test(line.replace(/\|/g, '').trim())) return null
        const cells = line.split('|').filter(c => c.trim())
        const isHeader = md.split('\n')[i + 1]?.includes('---')
        return <tr key={i}>{cells.map((c, j) =>
          isHeader ? <th key={j}>{c.trim()}</th> : <td key={j}>{c.trim()}</td>
        )}</tr>
      }
      if (/^[-*] (.+)/.test(line)) return <li key={i}>{line.slice(2)}</li>
      if (/^---$/.test(line))      return <hr key={i} />
      if (line.trim() === '')      return <br key={i} />
      return <p key={i} dangerouslySetInnerHTML={{
        __html: line.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      }} />
    }).filter(Boolean)

  return (
    <Card accent="cyan">
      <FileCheck size={14} className="text-r-cyan flex-shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1.5">
          <Badge text="post-mortem" color="cyan" />
          <Ts ts={entry.ts} />
          {entry.filepath && (
            <span className="font-mono text-[9px] text-r-dim truncate max-w-[200px]">
              {entry.filepath.split('/').slice(-1)[0]}
            </span>
          )}
        </div>
        <button
          onClick={() => setExpanded(v => !v)}
          className="font-mono text-[9px] text-r-cyan/70 hover:text-r-cyan flex items-center gap-0.5 mb-2"
        >
          {expanded
            ? <><ChevronUp size={9}/>collapse</>
            : <><ChevronDown size={9}/>read post-mortem</>}
        </button>
        {expanded && (
          <div className="pm-content border border-r-border rounded p-3 bg-r-panel">
            {renderMarkdown(entry.markdown || '')}
          </div>
        )}
      </div>
    </Card>
  )
}

// ── Multi-incident entry types ────────────────────────────────────────────────

const SEVERITY_STYLES = {
  critical: { badge: 'bg-r-red-d border-r-red/40 text-r-red',     label: 'CRITICAL' },
  high:     { badge: 'bg-r-amb-d border-r-amber/40 text-r-amber', label: 'HIGH' },
  medium:   { badge: 'bg-r-cyan-d border-r-cyan/40 text-r-cyan',  label: 'MEDIUM' },
}

function SeverityBadge({ severity }) {
  const s = SEVERITY_STYLES[severity] || SEVERITY_STYLES.medium
  return (
    <span className={`font-mono text-[9px] font-semibold px-1.5 py-0.5 rounded border ${s.badge}`}>
      {s.label}
    </span>
  )
}

const BIZ_PRIORITY_STYLES = {
  critical: 'text-r-red font-semibold',
  high:     'text-r-amber font-semibold',
  medium:   'text-r-cyan',
  low:      'text-r-dim',
}

function TriageEntry({ entry }) {
  const anomalies = entry.anomalies || []
  // Sort by business_priority first (if present), then technical severity
  const order = { critical: 0, high: 1, medium: 2, low: 3 }
  const sorted = [...anomalies].sort((a, b) => {
    const bizA = order[a.business_priority] ?? order[a.severity] ?? 2
    const bizB = order[b.business_priority] ?? order[b.severity] ?? 2
    if (bizA !== bizB) return bizA - bizB
    return (order[a.severity] ?? 2) - (order[b.severity] ?? 2)
  })

  // Detect business priority overrides — where biz priority differs from tech severity rank
  const hasOverride = sorted.some(a =>
    a.business_priority && a.business_priority !== a.severity &&
    (order[a.business_priority] ?? 2) < (order[a.severity] ?? 2)
  )

  // Find the first anomaly where biz_priority > tech_severity (lower number = higher priority)
  const overrideAnomaly = sorted.find(a =>
    a.business_priority && a.business_priority !== a.severity &&
    (order[a.business_priority] ?? 2) < (order[a.severity] ?? 2)
  )

  return (
    <div className="animate-incident-in flex gap-3 p-4 rounded border-2 border-r-amber bg-r-amb-d/30">
      <ListOrdered size={16} className="text-r-amber flex-shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-2">
          <span className="font-mono text-[10px] font-semibold text-r-amber uppercase tracking-widest">
            Triage — {anomalies.length} anomalies detected
          </span>
          <Ts ts={entry.ts} />
        </div>

        {/* Business priority override callout */}
        {hasOverride && overrideAnomaly && (
          <div className="mb-2.5 px-2.5 py-1.5 rounded border border-r-red/40 bg-r-red-d/30
            flex items-start gap-1.5">
            <Zap size={11} className="text-r-red flex-shrink-0 mt-0.5" />
            <span className="font-mono text-[10px] text-r-red leading-relaxed">
              Business priority override — {overrideAnomaly.endpoint || overrideAnomaly.service} marked {overrideAnomaly.business_priority}-priority by team
              {overrideAnomaly.business_reason ? `: ${overrideAnomaly.business_reason}` : ''}
            </span>
          </div>
        )}

        <div className="flex flex-col gap-2">
          {sorted.map((a, i) => {
            const bizPriStyle = BIZ_PRIORITY_STYLES[a.business_priority] || 'text-r-dim'
            const bizOverride = a.business_priority && a.business_priority !== a.severity &&
              (order[a.business_priority] ?? 2) < (order[a.severity] ?? 2)
            return (
              <div key={a.id || i} className="flex flex-col gap-0.5">
                <div className="flex items-start gap-2">
                  <span className="font-mono text-[10px] text-r-dim flex-shrink-0 mt-0.5">
                    {i + 1}.
                  </span>
                  <SeverityBadge severity={a.severity} />
                  {a.business_priority && (
                    <span className={`font-mono text-[9px] px-1.5 py-0.5 rounded border
                      border-r-border bg-r-muted ${bizPriStyle}`}>
                      BIZ:{a.business_priority.toUpperCase()}
                      {bizOverride && ' ⚡'}
                    </span>
                  )}
                  <span className="font-mono text-[11px] text-r-text leading-relaxed">
                    {a.endpoint ? (
                      <><span className="text-r-cyan">{a.endpoint}</span>{' '}</>
                    ) : null}
                    {a.description}
                    {i === 0 && (
                      <span className="ml-2 font-mono text-[9px] text-r-amber">
                        — investigating first
                      </span>
                    )}
                    {i > 0 && (
                      <span className="ml-2 font-mono text-[9px] text-r-dim">
                        — queued
                      </span>
                    )}
                  </span>
                </div>
                {a.business_reason && (
                  <div className="ml-6 font-mono text-[9px] text-r-dim italic leading-relaxed">
                    {a.business_reason}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function IncidentSeparatorEntry({ entry }) {
  return (
    <div className="flex items-center gap-3 py-2">
      <div className="flex-1 h-px bg-r-border" />
      <div className="flex items-center gap-1.5 px-3 py-1 rounded border border-r-border bg-r-surface">
        <Minus size={10} className="text-r-dim" />
        <span className="font-mono text-[9px] text-r-dim uppercase tracking-widest">
          {entry.label || `Incident ${entry.to_index} / ${entry.total}`}
        </span>
        <Minus size={10} className="text-r-dim" />
      </div>
      <div className="flex-1 h-px bg-r-border" />
    </div>
  )
}

function CascadeResolvedEntry({ entry }) {
  return (
    <Card accent="green" className="bg-r-grn-d/30">
      <Link2 size={13} className="text-r-green flex-shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="font-mono text-[10px] font-semibold text-r-green uppercase tracking-widest">
            Cascade Resolution
          </span>
          <Ts ts={entry.ts} />
        </div>
        <p className="font-mono text-[11px] text-r-text/80 leading-relaxed italic">
          {entry.reason || 'Anomaly resolved as a side effect of the previous fix.'}
        </p>
      </div>
    </Card>
  )
}

// ── Dispatch ─────────────────────────────────────────────────────────────────

export default function TraceEntry({ entry, approvalEntryId, onApprove, onReject }) {
  const isPending = entry._id === approvalEntryId

  switch (entry.type) {
    case 'anomaly_detected':   return <AnomalyEntry entry={entry} />
    case 'triage':             return <TriageEntry entry={entry} />
    case 'incident_separator': return <IncidentSeparatorEntry entry={entry} />
    case 'cascade_resolved':   return <CascadeResolvedEntry entry={entry} />
    case 'plan':               return <PlanEntry entry={entry} />
    case 'tool_call':          return <ToolCallEntry entry={entry} />
    case 'tool_result':        return <ToolResultEntry entry={entry} />
    case 'reasoning':          return <ReasoningEntry entry={entry} />
    case 'dashboard_image':    return <DashboardImageEntry entry={entry} />
    case 'approval_request':   return (
      <ApprovalEntry
        entry={entry}
        isPending={isPending}
        onApprove={onApprove}
        onReject={onReject}
      />
    )
    case 'remediation_result': return <RemediationResultEntry entry={entry} />
    case 'postmortem':         return <PostmortemEntry entry={entry} />
    default:                   return null
  }
}
