import { useState, useEffect } from 'react'
import { Wifi, WifiOff, Zap, ChevronDown } from 'lucide-react'

const STATUS_CONFIG = {
  monitoring:         { label: 'MONITORING',        color: 'text-r-dim',   bg: 'bg-r-muted',  dot: 'bg-r-dim' },
  investigating:      { label: 'INVESTIGATING',      color: 'text-r-amber', bg: 'bg-r-amb-d',  dot: 'bg-r-amber animate-dot-pulse' },
  awaiting_approval:  { label: 'AWAITING APPROVAL', color: 'text-r-amber', bg: 'bg-r-amb-d',  dot: 'bg-r-amber animate-dot-pulse' },
  resolved:           { label: 'RESOLVED',           color: 'text-r-green', bg: 'bg-r-grn-d',  dot: 'bg-r-green' },
  idle:               { label: 'IDLE',               color: 'text-r-dim',   bg: 'bg-r-muted',  dot: 'bg-r-dim' },
}

// Single-fault triggers shown in the menu
const SINGLE_FAULT_TRIGGERS = [
  { key: 'bad_deploy',        label: 'bad_deploy',        desc: 'Payment gateway misconfigured' },
  { key: 'memory_leak',       label: 'memory_leak',       desc: 'API memory leak' },
  { key: 'slow_db',           label: 'slow_db',           desc: 'DB query slowdown' },
  { key: 'db_down',           label: 'db_down',           desc: 'DB connection refused' },
  { key: 'catalog_down',      label: 'catalog_down',      desc: '/products 503 — catalog unavailable' },
  { key: 'checkout_degraded', label: 'checkout_degraded', desc: '/checkout ~40% failures (intermittent)' },
]

// Multi-fault triage scenarios — each key is an array passed to onTrigger
const MULTI_FAULT_TRIGGERS = [
  {
    key:   ['catalog_down', 'checkout_degraded'],
    label: 'catalog_down + checkout_degraded',
    desc:  'Business priority demo — 95% /products vs 40% /checkout (revenue-critical wins)',
  },
  {
    key:   ['bad_deploy', 'slow_db'],
    label: 'bad_deploy + slow_db',
    desc:  'CRITICAL checkout errors + MEDIUM DB latency (triage demo)',
  },
  {
    key:   ['bad_deploy', 'memory_leak'],
    label: 'bad_deploy + memory_leak',
    desc:  'CRITICAL checkout errors + HIGH memory growth',
  },
]

function IncidentTimer({ start, end }) {
  const [elapsed, setElapsed] = useState('00:00')

  useEffect(() => {
    if (!start) { setElapsed('00:00'); return }
    const tick = () => {
      const ms = (end || Date.now()) - start
      const s  = Math.floor(ms / 1000)
      const m  = Math.floor(s / 60)
      const h  = Math.floor(m / 60)
      if (h > 0) setElapsed(`${String(h).padStart(2,'0')}:${String(m%60).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`)
      else        setElapsed(`${String(m).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`)
    }
    tick()
    if (end) return
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [start, end])

  if (!start) return (
    <div className="flex flex-col items-center gap-0.5">
      <span className="font-mono text-2xl font-semibold text-r-muted tracking-wider">—:——</span>
      <span className="font-mono text-[9px] text-r-muted uppercase tracking-widest">incident timer</span>
    </div>
  )

  return (
    <div className="flex flex-col items-center gap-0.5">
      <span className={`font-mono text-2xl font-semibold tracking-wider
        ${end ? 'text-r-green' : 'text-r-red'}`}>
        {elapsed}
      </span>
      <span className="font-mono text-[9px] text-r-dim uppercase tracking-widest">
        {end ? 'resolved' : 'elapsed'}
      </span>
    </div>
  )
}

function ServiceDot({ label, status }) {
  const color = status === 'degraded' ? 'bg-r-red animate-dot-pulse'
              : status === 'warning'  ? 'bg-r-amber'
              : status === 'healthy'  ? 'bg-r-green'
              : 'bg-r-dim'
  return (
    <div className="flex items-center gap-1.5">
      <div className={`w-1.5 h-1.5 rounded-full ${color}`} />
      <span className="font-mono text-[11px] text-r-dim">{label}</span>
    </div>
  )
}

export default function StatusBar({
  status, wsConnected, incidentStart, incidentEnd,
  serviceHealth, fault, faults = [], incidentTotal = 0, incidentCurrent = 0,
  onTrigger
}) {
  const cfg      = STATUS_CONFIG[status] || STATUS_CONFIG.monitoring
  const isActive = status === 'investigating' || status === 'awaiting_approval'
  const [menuOpen, setMenuOpen] = useState(false)

  // Build status label — show progress counter during multi-incident triage
  const statusLabel = (isActive && incidentTotal > 1)
    ? `INVESTIGATING ${incidentCurrent}/${incidentTotal}`
    : cfg.label

  // Active fault chips (multi-fault or single)
  const activeFaults = faults.length > 0 ? faults : (fault && fault !== 'none' ? [fault] : [])

  return (
    <div className={`flex items-center h-12 px-4 gap-4 flex-shrink-0
      border-b border-r-border bg-r-surface
      ${isActive ? 'border-b-r-red/30' : ''}`}
    >
      {/* Brand */}
      <div className="flex items-center gap-2 flex-shrink-0">
        <span className="font-mono text-[13px] font-semibold text-r-cyan tracking-widest uppercase">
          Resolve
        </span>
        {wsConnected
          ? <Wifi size={12} className="text-r-dim" />
          : <WifiOff size={12} className="text-r-dim animate-dot-pulse" />
        }
      </div>

      {/* Status badge */}
      <div className={`flex items-center gap-1.5 px-2 py-0.5 rounded
        border border-r-border ${cfg.bg}`}>
        <div className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />
        <span className={`font-mono text-[10px] font-semibold tracking-widest ${cfg.color}`}>
          {statusLabel}
        </span>
      </div>

      {/* Active fault chips — one per fault */}
      <div className="flex items-center gap-1.5">
        {activeFaults.map(f => (
          <div key={f} className="flex items-center gap-1 px-2 py-0.5 rounded
            bg-r-red-d border border-r-red/20">
            <span className="font-mono text-[10px] text-r-red font-semibold">{f}</span>
          </div>
        ))}
      </div>

      {/* Center — timer */}
      <div className="flex-1 flex justify-center">
        <IncidentTimer start={incidentStart} end={incidentEnd} />
      </div>

      {/* Service health */}
      <div className="flex items-center gap-4 flex-shrink-0">
        <ServiceDot label="frontend" status={serviceHealth.frontend} />
        <ServiceDot label="api"      status={serviceHealth.api} />
        <ServiceDot label="db"       status={serviceHealth.db} />
      </div>

      {/* Trigger menu */}
      <div className="relative flex-shrink-0">
        <button
          onClick={() => setMenuOpen(v => !v)}
          className="flex items-center gap-1 px-2.5 py-1 rounded
            border border-r-border text-r-dim hover:text-r-text hover:border-r-bright
            font-mono text-[10px] uppercase tracking-wider transition-colors"
        >
          <Zap size={10} />
          inject
          <ChevronDown size={9} className={`transition-transform ${menuOpen ? 'rotate-180' : ''}`} />
        </button>

        {menuOpen && (
          <div className="absolute right-0 top-full mt-1 z-50 w-64
            bg-r-surface border border-r-border rounded shadow-2xl overflow-hidden">

            {/* Single faults */}
            {SINGLE_FAULT_TRIGGERS.map(f => (
              <button
                key={f.key}
                onClick={() => { onTrigger(f.key); setMenuOpen(false) }}
                className="w-full text-left px-3 py-2 hover:bg-r-raised
                  flex flex-col gap-0.5 border-b border-r-border/50"
              >
                <span className="font-mono text-[11px] font-semibold text-r-amber">
                  {f.label}
                </span>
                <span className="font-mono text-[10px] text-r-dim">{f.desc}</span>
              </button>
            ))}

            {/* Multi-fault triage scenarios */}
            <div className="px-3 pt-2 pb-1">
              <span className="font-mono text-[9px] text-r-dim uppercase tracking-widest">
                triage scenarios
              </span>
            </div>
            {MULTI_FAULT_TRIGGERS.map(f => (
              <button
                key={f.label}
                onClick={() => { onTrigger(f.key); setMenuOpen(false) }}
                className="w-full text-left px-3 py-2 hover:bg-r-raised
                  flex flex-col gap-0.5 border-b border-r-border/50"
              >
                <span className="font-mono text-[11px] font-semibold text-r-red">
                  {f.label}
                </span>
                <span className="font-mono text-[10px] text-r-dim">{f.desc}</span>
              </button>
            ))}

            {/* Clear */}
            <button
              onClick={() => { onTrigger('none'); setMenuOpen(false) }}
              className="w-full text-left px-3 py-2 hover:bg-r-raised
                flex flex-col gap-0.5"
            >
              <span className="font-mono text-[11px] font-semibold text-r-green">
                clear fault
              </span>
              <span className="font-mono text-[10px] text-r-dim">Restore normal operation</span>
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
