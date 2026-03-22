import { useState, useEffect, useRef, useCallback } from 'react'
import StatusBar from './components/StatusBar.jsx'
import MetricsPanel from './components/MetricsPanel.jsx'
import InvestigationTrace from './components/InvestigationTrace.jsx'
import SlackFeed from './components/SlackFeed.jsx'

const WS_URL = typeof window !== 'undefined'
  ? `ws://${window.location.hostname}:8765/ws`
  : 'ws://localhost:8765/ws'

export default function App() {
  // ── Agent state ──────────────────────────────────────────────────────────
  const [status,        setStatus]        = useState('monitoring')
  const [wsConnected,   setWsConnected]   = useState(false)
  const [incidentStart, setIncidentStart] = useState(null) // ms epoch from server ts
  const [incidentEnd,   setIncidentEnd]   = useState(null) // ms epoch from server ts

  // Ref so the WS handler always reads the latest value without a stale closure
  const incidentEndRef = useRef(null)
  const setIncidentEndBoth = (val) => {
    incidentEndRef.current = val
    setIncidentEnd(val)
  }

  // ── Trace entries ────────────────────────────────────────────────────────
  const [entries, setEntries] = useState([])

  // ── Slack messages ───────────────────────────────────────────────────────
  const [slackMsgs, setSlackMsgs] = useState([])

  // ── Metrics ──────────────────────────────────────────────────────────────
  const [metrics, setMetrics] = useState({
    api:      { error_rate: 0, p95_latency: 0, memory_mb: 0, status: 'unknown', total_requests: 0 },
    frontend: { error_rate: 0, p95_latency: 0, memory_mb: 0, status: 'unknown', total_requests: 0 },
    fault: 'none',
  })
  const [metricsHistory, setMetricsHistory] = useState({
    error_rate:  [],
    p95_latency: [],
    memory_mb:   [],
  })

  // ── Approval state ───────────────────────────────────────────────────────
  const [approvalEntryId, setApprovalEntryId] = useState(null)

  // ── Background flash on incident start ───────────────────────────────────
  const [flashing, setFlashing] = useState(false)

  // ── WS ref ───────────────────────────────────────────────────────────────
  const wsRef     = useRef(null)
  const reconnRef = useRef(null)
  const entryId   = useRef(0)

  const nextId = () => ++entryId.current

  const addEntry = useCallback((entry) => {
    setEntries(prev => [...prev, { ...entry, _id: nextId() }])
  }, [])

  // ── Handle incoming WS message ───────────────────────────────────────────
  // No state values in deps — use refs for anything that would cause stale closures.
  const handleMessage = useCallback((raw) => {
    let ev
    try { ev = JSON.parse(raw) } catch { return }

    const { type, timestamp } = ev
    // Display time uses server's UTC timestamp (HH:MM:SS)
    const ts = timestamp ? new Date(timestamp).toISOString().split('T')[1].slice(0, 8) : ''
    // Epoch ms for timer arithmetic — server timestamp, not client Date.now()
    const serverMs = timestamp ? new Date(timestamp).getTime() : Date.now()

    switch (type) {
      case 'agent_status':
        setStatus(ev.status)
        // Only set incidentEnd from agent_status if postmortem hasn't already set it
        if (ev.status === 'resolved' && !incidentEndRef.current) {
          setIncidentEndBoth(serverMs)
        }
        break

      case 'anomaly_detected':
        setStatus('investigating')
        setIncidentStart(serverMs)      // ← server timestamp, not Date.now()
        setIncidentEndBoth(null)        // reset for new incident
        setEntries([])
        setSlackMsgs([])
        setFlashing(true)
        setTimeout(() => setFlashing(false), 2200)
        addEntry({ type, ts, description: ev.description, severity: ev.severity })
        break

      case 'plan':
        addEntry({ type, ts, text: ev.text })
        break

      case 'tool_call':
        addEntry({ type, ts, name: ev.name, inputs: ev.inputs })
        break

      case 'tool_result':
        addEntry({ type, ts, name: ev.name, result_preview: ev.result_preview })
        break

      case 'reasoning':
        addEntry({ type, ts, text: ev.text })
        break

      case 'dashboard_image':
        addEntry({ type, ts, base64_png: ev.base64_png })
        break

      case 'approval_request': {
        const id = nextId()
        setEntries(prev => [...prev, {
          type, ts,
          action: ev.action,
          service: ev.service,
          reason: ev.reason,
          decision: null,
          _id: id,
        }])
        setApprovalEntryId(id)
        setStatus('awaiting_approval')
        break
      }

      case 'remediation_result':
        setApprovalEntryId(null)
        addEntry({ type, ts, status: ev.status, message: ev.message })
        if (ev.status === 'approved') setStatus('investigating')
        break

      case 'slack_alert':
        setSlackMsgs(prev => [...prev, {
          ts,
          message:  ev.message,
          severity: ev.severity,
          _id:      nextId(),
        }])
        break

      case 'postmortem':
        addEntry({ type, ts, markdown: ev.markdown, filepath: ev.filepath })
        setStatus('resolved')
        setIncidentEndBoth(serverMs)   // ← server timestamp, not Date.now()
        break

      case 'metrics': {
        const api = ev.api || {}
        const fe  = ev.frontend || {}
        setMetrics({ api, frontend: fe, fault: ev.fault || 'none' })
        const now = Date.now()
        setMetricsHistory(prev => ({
          error_rate:  [...prev.error_rate.slice(-59),  { t: now, v: api.error_rate  || 0 }],
          p95_latency: [...prev.p95_latency.slice(-59), { t: now, v: api.p95_latency || 0 }],
          memory_mb:   [...prev.memory_mb.slice(-59),   { t: now, v: api.memory_mb   || 0 }],
        }))
        break
      }

      default:
        break
    }
  }, [addEntry]) // no incidentEnd in deps — using ref instead

  // ── WebSocket connection ─────────────────────────────────────────────────
  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return
    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen    = () => setWsConnected(true)
    ws.onmessage = (e) => handleMessage(e.data)
    ws.onclose   = () => {
      setWsConnected(false)
      reconnRef.current = setTimeout(connect, 3000)
    }
    ws.onerror = () => ws.close()
  }, [handleMessage])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnRef.current)
      wsRef.current?.close()
    }
  }, [connect])

  // ── Approve / Reject ─────────────────────────────────────────────────────
  const sendDecision = useCallback((decision) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: decision }))
    }
    setEntries(prev => prev.map(e =>
      e._id === approvalEntryId ? { ...e, decision } : e
    ))
    setApprovalEntryId(null)
  }, [approvalEntryId])

  // ── Trigger fault from UI ────────────────────────────────────────────────
  const triggerFault = useCallback(async (fault) => {
    try {
      await fetch(`/api/trigger/${fault}`, { method: 'POST' })
    } catch {
      await fetch(`http://localhost:8765/trigger/${fault}`, { method: 'POST' })
    }
  }, [])

  // ── Derived service health ────────────────────────────────────────────────
  const serviceHealth = {
    frontend: metrics.frontend.status || 'unknown',
    api:      metrics.api.status      || 'unknown',
    db:       metrics.fault === 'db_down' ? 'degraded'
              : metrics.fault !== 'none'  ? 'warning'
              : 'healthy',
  }

  return (
    <div className={`h-screen flex flex-col bg-r-bg overflow-hidden
      ${flashing ? 'animate-bg-flash' : ''}`}>

      <StatusBar
        status={status}
        wsConnected={wsConnected}
        incidentStart={incidentStart}
        incidentEnd={incidentEnd}
        serviceHealth={serviceHealth}
        fault={metrics.fault}
        onTrigger={triggerFault}
      />

      <div className="flex flex-1 min-h-0 overflow-hidden">
        <MetricsPanel
          metrics={metrics}
          history={metricsHistory}
          serviceHealth={serviceHealth}
        />
        <InvestigationTrace
          entries={entries}
          status={status}
          approvalEntryId={approvalEntryId}
          onApprove={() => sendDecision('approve')}
          onReject={()  => sendDecision('reject')}
        />
        <SlackFeed messages={slackMsgs} />
      </div>
    </div>
  )
}
