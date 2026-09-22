import { useMemo } from 'react'
import { TrendPoint } from '../lib/api'
import { fmtClock, fmtNumber } from '../lib/format'

type Props = {
  points: TrendPoint[]
  threshold: number | null
  windowStart?: string | null
  triggerAt?: string | null
  closedAt?: string | null
  height?: number
}

const PAD = { top: 16, right: 16, bottom: 44, left: 48 }
const GAP_FACTOR = 2.5 // a break wider than this many expected intervals is a real gap

/**
 * Supply-air temperature against its setpoint, with the tolerance band, the
 * qualifying window, the trigger point, run status and data gaps all on one
 * axis. The chart is the evidence, so nothing is smoothed or interpolated: a
 * gap in the data is drawn as a gap.
 */
export function TrendChart({ points, threshold, windowStart, triggerAt, closedAt, height = 280 }: Props) {
  const width = 960
  const model = useMemo(() => {
    const rows = points
      .map((p) => ({
        t: new Date(p.observed_at).getTime(),
        sat: numeric(p.supply_air_temperature),
        sp: numeric(p.supply_air_temperature_setpoint),
        run: (p.run_status as string) ?? null,
        satQuality: (p.supply_air_temperature__quality as string) ?? 'GOOD',
        spQuality: (p.supply_air_temperature_setpoint__quality as string) ?? 'GOOD',
      }))
      .filter((r) => Number.isFinite(r.t))
      .sort((a, b) => a.t - b.t)

    if (rows.length === 0) return null

    const step = rows.length > 1 ? median(rows.slice(1).map((r, i) => r.t - rows[i].t)) : 60000
    const t0 = rows[0].t
    const t1 = rows[rows.length - 1].t || t0 + step

    const values: number[] = []
    for (const r of rows) {
      if (r.sat !== null) values.push(r.sat)
      if (r.sp !== null) {
        values.push(r.sp)
        if (threshold !== null) values.push(r.sp + threshold, r.sp - threshold)
      }
    }
    const lo = Math.min(...values) - 0.8
    const hi = Math.max(...values) + 0.8

    const x = (t: number) =>
      PAD.left + ((t - t0) / Math.max(t1 - t0, 1)) * (width - PAD.left - PAD.right)
    const y = (v: number) =>
      PAD.top + (1 - (v - lo) / Math.max(hi - lo, 0.001)) * (height - PAD.top - PAD.bottom)

    return { rows, step, t0, t1, lo, hi, x, y }
  }, [points, threshold, height])

  if (!model) return <p className="state state-empty">No observations in this window.</p>
  const { rows, step, x, y, lo, hi } = model

  const segments = (pick: (r: (typeof rows)[number]) => number | null) => {
    const out: string[] = []
    let current: string[] = []
    let previousT: number | null = null
    for (const r of rows) {
      const v = pick(r)
      const broken = previousT !== null && r.t - previousT > step * GAP_FACTOR
      if (v === null || broken) {
        if (current.length > 1) out.push(current.join(' '))
        current = []
      }
      if (v !== null) {
        current.push(`${current.length ? 'L' : 'M'}${x(r.t).toFixed(1)},${y(v).toFixed(1)}`)
        previousT = r.t
      }
    }
    if (current.length > 1) out.push(current.join(' '))
    return out
  }

  const bandPath = () => {
    if (threshold === null) return null
    const upper: string[] = []
    const lower: string[] = []
    for (const r of rows) {
      if (r.sp === null) continue
      upper.push(`${upper.length ? 'L' : 'M'}${x(r.t).toFixed(1)},${y(r.sp + threshold).toFixed(1)}`)
      lower.unshift(`L${x(r.t).toFixed(1)},${y(r.sp - threshold).toFixed(1)}`)
    }
    return upper.length ? `${upper.join(' ')} ${lower.join(' ')} Z` : null
  }

  const ticks = 5
  const yTicks = Array.from({ length: ticks }, (_, i) => lo + ((hi - lo) * i) / (ticks - 1))
  const xTicks = Array.from({ length: 6 }, (_, i) => model.t0 + ((model.t1 - model.t0) * i) / 5)

  const ws = windowStart ? new Date(windowStart).getTime() : null
  const tg = triggerAt ? new Date(triggerAt).getTime() : null
  const cl = closedAt ? new Date(closedAt).getTime() : null

  const runY = height - PAD.bottom + 16

  return (
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${width} ${height + 26}`} className="chart" role="img"
           aria-label="Supply-air temperature against setpoint">
        {/* qualifying window */}
        {ws !== null && tg !== null && (
          <rect x={x(ws)} y={PAD.top} width={Math.max(x(tg) - x(ws), 1)}
                height={height - PAD.top - PAD.bottom} className="chart-window" />
        )}
        {/* tolerance band */}
        {bandPath() && <path d={bandPath()!} className="chart-band" />}

        {yTicks.map((v) => (
          <g key={v}>
            <line x1={PAD.left} x2={width - PAD.right} y1={y(v)} y2={y(v)} className="chart-grid" />
            <text x={PAD.left - 8} y={y(v) + 4} className="chart-axis" textAnchor="end">
              {fmtNumber(v, 1)}
            </text>
          </g>
        ))}
        {xTicks.map((t) => (
          <text key={t} x={x(t)} y={height - PAD.bottom + 34} className="chart-axis" textAnchor="middle">
            {fmtClock(new Date(t).toISOString())}
          </text>
        ))}

        {segments((r) => r.sp).map((d, i) => <path key={`sp${i}`} d={d} className="chart-line chart-sp" />)}
        {segments((r) => r.sat).map((d, i) => <path key={`sat${i}`} d={d} className="chart-line chart-sat" />)}

        {/* run status strip: a gap here is a gap in the data, not an OFF period */}
        {rows.map((r, i) => (
          <rect key={`run${i}`} x={x(r.t) - 1.5} y={runY} width={3} height={7}
                className={`chart-run chart-run-${r.run === 'ON' ? 'on' : r.run === 'OFF' ? 'off' : 'unknown'}`} />
        ))}

        {tg !== null && (
          <g>
            <line x1={x(tg)} x2={x(tg)} y1={PAD.top} y2={height - PAD.bottom} className="chart-trigger" />
            <text x={x(tg) + 6} y={PAD.top + 12} className="chart-label">trigger</text>
          </g>
        )}
        {ws !== null && (
          <line x1={x(ws)} x2={x(ws)} y1={PAD.top} y2={height - PAD.bottom} className="chart-marker" />
        )}
        {cl !== null && (
          <g>
            <line x1={x(cl)} x2={x(cl)} y1={PAD.top} y2={height - PAD.bottom} className="chart-marker" />
            <text x={x(cl) + 6} y={PAD.top + 12} className="chart-label">closed</text>
          </g>
        )}
      </svg>
      <ul className="legend">
        <li><i className="swatch swatch-sat" /> supply-air temperature (triggering)</li>
        <li><i className="swatch swatch-sp" /> setpoint (triggering)</li>
        <li><i className="swatch swatch-band" /> within {threshold ?? '--'} °C of setpoint</li>
        <li><i className="swatch swatch-window" /> qualifying window</li>
        <li><i className="swatch swatch-run" /> run status: green ON, grey OFF, gap = no reading</li>
      </ul>
    </div>
  )
}

function numeric(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  return null
}

function median(values: number[]): number {
  if (!values.length) return 60000
  const sorted = [...values].sort((a, b) => a - b)
  return sorted[Math.floor(sorted.length / 2)] || 60000
}
