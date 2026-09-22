import { ReactNode } from 'react'
import { PointValue } from '../lib/api'
import { fmtNumber, freshnessLabel, ROLE_UNIT } from '../lib/format'

/** Explicit loading / empty / error / insufficient-data states, never a blank box. */
export function Panel({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <h2>{title}</h2>
          {subtitle && <p className="subtitle">{subtitle}</p>}
        </div>
        {actions && <div className="panel-actions">{actions}</div>}
      </header>
      <div className="panel-body">{children}</div>
    </section>
  )
}

export function AsyncState({
  loading,
  error,
  empty,
  emptyLabel = 'Nothing to show',
  children,
}: {
  loading: boolean
  error: string | null
  empty?: boolean
  emptyLabel?: string
  children: ReactNode
}) {
  if (loading) return <p className="state state-loading">Loading…</p>
  if (error) return <p className="state state-error">Error: {error}</p>
  if (empty) return <p className="state state-empty">{emptyLabel}</p>
  return <>{children}</>
}

export function Badge({ kind, children }: { kind: string; children: ReactNode }) {
  return <span className={`badge badge-${kind}`}>{children}</span>
}

export function QualityDot({ state }: { state: string }) {
  return <span className={`dot dot-${state}`} title={state} />
}

/** A measured value always shows its unit, observed time, freshness and quality. */
export function Measurement({ role, point }: { role: string; point?: PointValue }) {
  if (!point || point.state === 'empty') {
    return (
      <span className="measure measure-empty">
        <QualityDot state="empty" /> no data
      </span>
    )
  }
  const unit = ROLE_UNIT[role] ?? ''
  const isNumber = typeof point.value === 'number'
  const bad = point.quality !== 'GOOD'
  return (
    <span className={`measure ${bad ? 'measure-bad' : ''}`} title={`observed ${point.observed_at ?? '--'} | quality ${point.quality}`}>
      <QualityDot state={bad ? 'bad' : point.state} />
      <strong>{isNumber ? fmtNumber(point.value, unit === 'ppm' || unit === 'kWh' ? 1 : 2) : point.value ?? '--'}</strong>
      {unit && <em>{unit}</em>}
      <small>{bad ? point.quality.toLowerCase() : freshnessLabel(point.state, point.age_seconds)}</small>
    </span>
  )
}

export function KeyValue({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {rows.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  )
}
