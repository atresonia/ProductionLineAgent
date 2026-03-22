/**
 * Sparkline — minimal SVG line chart from a [{t, v}] history array.
 * Fills below the line, colored by current status.
 */
export default function Sparkline({ data = [], color = '#00e5ff', height = 36, threshold = null }) {
  if (data.length < 2) {
    return <div style={{ height }} className="flex items-end">
      <div className="w-full h-px bg-r-border" />
    </div>
  }

  const W = 220
  const H = height
  const PAD = 2

  const vals = data.map(d => d.v)
  const min  = Math.min(...vals)
  const max  = Math.max(...vals) || 1
  const range = max - min || 1

  const toX = (i) => PAD + (i / (data.length - 1)) * (W - PAD * 2)
  const toY = (v) => H - PAD - ((v - min) / range) * (H - PAD * 2)

  const pts  = data.map((d, i) => `${toX(i)},${toY(d.v)}`).join(' ')
  const area = `M ${toX(0)},${H} L ${pts.replace(/(\d+\.?\d*),(\d+\.?\d*)/g, '$1,$2')} L ${toX(data.length-1)},${H} Z`
  const line = `M ${pts.replace(/(\d+\.?\d*),(\d+\.?\d*) /g, 'L $1,$2 ').replace(/^M L/, 'M ')}`

  const threshY = threshold != null ? toY(threshold) : null

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      width="100%"
      height={height}
      className="overflow-visible"
    >
      <defs>
        <linearGradient id={`sg-${color.replace('#','')}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stopColor={color} stopOpacity="0.18" />
          <stop offset="100%" stopColor={color} stopOpacity="0.02" />
        </linearGradient>
      </defs>

      {/* Area fill */}
      <path d={area} fill={`url(#sg-${color.replace('#','')})`} />

      {/* Threshold line */}
      {threshY != null && (
        <line
          x1={PAD} y1={threshY} x2={W - PAD} y2={threshY}
          stroke={color} strokeOpacity="0.3" strokeWidth="1"
          strokeDasharray="3 3"
        />
      )}

      {/* Line */}
      <polyline
        points={pts}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />

      {/* Last point dot */}
      {data.length > 0 && (
        <circle
          cx={toX(data.length - 1)}
          cy={toY(data[data.length - 1].v)}
          r="2.5"
          fill={color}
        />
      )}
    </svg>
  )
}
