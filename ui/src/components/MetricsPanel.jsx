import Sparkline from './Sparkline.jsx'
import { TrendingUp, TrendingDown, Minus } from 'lucide-react'

function MetricCard({ label, value, unit, history, color, threshold, trendKey }) {
  const last5 = history.slice(-5)
  const trend = last5.length >= 2
    ? last5[last5.length - 1].v - last5[0].v
    : 0

  const TrendIcon = Math.abs(trend) < 1   ? Minus
                  : trend > 0             ? TrendingUp
                  : TrendingDown

  const trendColor = trendKey === 'memory'
    ? (trend > 5  ? 'text-r-red' : trend > 0 ? 'text-r-amber' : 'text-r-green')
    : (color === '#ff2d55' ? 'text-r-red' : color === '#ffaa00' ? 'text-r-amber' : 'text-r-dim')

  return (
    <div className="bg-r-raised border border-r-border rounded p-3">
      <div className="flex items-center justify-between mb-1">
        <span className="font-mono text-[9px] uppercase tracking-widest text-r-dim">
          {label}
        </span>
        <TrendIcon size={10} className={trendColor} />
      </div>

      <div className="flex items-baseline gap-1 mb-2">
        <span className="font-mono text-2xl font-semibold leading-none" style={{ color }}>
          {value ?? '—'}
        </span>
        <span className="font-mono text-[10px] text-r-dim">{unit}</span>
      </div>

      <Sparkline data={history} color={color} height={36} threshold={threshold} />
    </div>
  )
}

function TopologyDiagram({ serviceHealth }) {
  const nodeColor = (s) =>
    s === 'degraded' ? '#ff2d55' : s === 'warning' ? '#ffaa00' : s === 'healthy' ? '#00e676' : '#2a2a4a'

  const edgeColor = (from, to) => {
    const fh = serviceHealth[from]
    const th = serviceHealth[to]
    if (fh === 'degraded' || th === 'degraded') return '#ff2d55'
    if (fh === 'warning'  || th === 'warning')  return '#ffaa00'
    return '#2a2a4a'
  }

  const nodes = [
    { key: 'frontend', label: 'frontend', x: 20,  y: 30 },
    { key: 'api',      label: 'api',      x: 100, y: 30 },
    { key: 'db',       label: 'db',       x: 180, y: 30 },
  ]

  return (
    <div className="bg-r-raised border border-r-border rounded p-3">
      <span className="font-mono text-[9px] uppercase tracking-widest text-r-dim block mb-3">
        Service Topology
      </span>
      <svg viewBox="0 0 200 60" width="100%" height="60">
        {/* Edges */}
        {[['frontend','api'], ['api','db']].map(([a, b]) => {
          const n1 = nodes.find(n => n.key === a)
          const n2 = nodes.find(n => n.key === b)
          const color = edgeColor(a, b)
          const isErr = color !== '#2a2a4a'
          return (
            <g key={`${a}-${b}`}>
              <line
                x1={n1.x + 18} y1={n1.y}
                x2={n2.x - 18} y2={n2.y}
                stroke={color}
                strokeWidth={isErr ? 1.5 : 1}
                strokeDasharray={isErr ? '4 2' : ''}
              />
              {/* Arrow */}
              <polygon
                points={`${n2.x-22},${n2.y-3} ${n2.x-18},${n2.y} ${n2.x-22},${n2.y+3}`}
                fill={color}
              />
            </g>
          )
        })}

        {/* Nodes */}
        {nodes.map(({ key, label, x, y }) => {
          const color = nodeColor(serviceHealth[key])
          return (
            <g key={key}>
              <rect
                x={x - 18} y={y - 12}
                width={36} height={24}
                rx={4}
                fill="#0d0d1a"
                stroke={color}
                strokeWidth={serviceHealth[key] === 'degraded' ? 1.5 : 1}
              />
              <text
                x={x} y={y + 1}
                textAnchor="middle"
                dominantBaseline="middle"
                fill={color}
                fontSize="7"
                fontFamily="JetBrains Mono, monospace"
                fontWeight="600"
              >
                {label}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

// Faults that directly affect revenue-critical paths → red; others → amber
const CRITICAL_FAULTS = new Set(['checkout_degraded', 'bad_deploy', 'db_down'])

export default function MetricsPanel({ metrics, history, serviceHealth }) {
  const api = metrics.api || {}

  // Collect all active faults as an array, normalising both legacy and new formats
  const faultList = metrics.faults?.length > 0
    ? metrics.faults
    : (metrics.fault && metrics.fault !== 'none' ? [metrics.fault] : [])

  // Derive display color from current value
  const errColor = api.error_rate >= 15  ? '#ff2d55'
                 : api.error_rate >= 5   ? '#ffaa00'
                 : '#00e676'
  const latColor = api.p95_latency >= 1500 ? '#ff2d55'
                 : api.p95_latency >= 500  ? '#ffaa00'
                 : '#00e676'
  const memColor = api.memory_mb >= 500   ? '#ff2d55'
                 : api.memory_mb >= 300   ? '#ffaa00'
                 : '#00e5ff'

  return (
    <aside className="w-[280px] flex-shrink-0 flex flex-col border-r border-r-border
      bg-r-panel overflow-y-auto scrollbar-thin">

      <div className="px-3 py-2 border-b border-r-border flex-shrink-0">
        <span className="font-mono text-[9px] uppercase tracking-widest text-r-dim">
          Live Metrics
        </span>
        <span className="font-mono text-[9px] text-r-muted ml-2">3 min · api</span>
      </div>

      <div className="flex flex-col gap-2 p-3">
        <MetricCard
          label="Error Rate"
          value={api.error_rate ?? 0}
          unit="%"
          history={history.error_rate}
          color={errColor}
          threshold={15}
          trendKey="error"
        />
        <MetricCard
          label="p95 Latency"
          value={api.p95_latency ?? 0}
          unit="ms"
          history={history.p95_latency}
          color={latColor}
          threshold={1500}
          trendKey="latency"
        />
        <MetricCard
          label="Memory"
          value={api.memory_mb ?? 0}
          unit="MB"
          history={history.memory_mb}
          color={memColor}
          trendKey="memory"
        />

        <TopologyDiagram serviceHealth={serviceHealth} />

        {/* Fault state — show ALL active faults stacked */}
        {faultList.length > 0 && (
          <div className="bg-r-raised border border-r-border rounded p-3">
            <div className="font-mono text-[9px] uppercase tracking-widest text-r-dim mb-2">
              Active Fault{faultList.length > 1 ? 's' : ''}
            </div>
            <div className="flex flex-col gap-1.5">
              {faultList.map(f => (
                <div key={f} className={`px-2.5 py-1.5 rounded border font-mono text-[11px] font-semibold
                  ${CRITICAL_FAULTS.has(f)
                    ? 'bg-r-red-d border-r-red/30 text-r-red'
                    : 'bg-r-amb-d border-r-amber/30 text-r-amber'}`}>
                  {f}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Request volume */}
        <div className="bg-r-raised border border-r-border rounded p-3">
          <div className="font-mono text-[9px] uppercase tracking-widest text-r-dim mb-2">
            Request Volume
          </div>
          <div className="grid grid-cols-2 gap-2">
            {[
              { svc: 'api',      d: metrics.api },
              { svc: 'frontend', d: metrics.frontend },
            ].map(({ svc, d }) => (
              <div key={svc}>
                <div className="font-mono text-[9px] text-r-dim mb-0.5">{svc}</div>
                <div className="font-mono text-[13px] font-semibold text-r-text">
                  {d?.total_requests ?? 0}
                </div>
                <div className="font-mono text-[9px] text-r-dim">req/3m</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </aside>
  )
}
