import { Link, useParams } from 'react-router-dom'
import { api, IssueSummary, TrendPoint } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { AsyncState, Badge, KeyValue, Panel } from '../components/common'
import { TrendChart } from '../components/TrendChart'
import { fmtDuration, fmtNumber, fmtTime, ROLE_LABEL, ROLE_UNIT } from '../lib/format'

type Detail = {
  entity_id: string
  name: string
  brick_class: string
  metadata: Record<string, string | null>
  points: { point_id: string; source_name: string; brick_class: string; unit: string | null; role: string; description: string }[]
  installed_space: { entity_id: string; name: string } | null
  served_space: { entity_id: string; name: string } | null
  measurement_scope: { entity_id: string; name: string } | null
  property: { entity_id: string; name: string; property_type: string } | null
}

type Current = {
  points: {
    point_id: string
    role: string
    source_name: string
    unit: string | null
    observed_at: string | null
    value_number: number | null
    value_text: string | null
    quality: string | null
    age_seconds: number | null
    state: string
  }[]
}

export default function Equipment() {
  const { equipmentId = '' } = useParams()
  const detail = useAsync(() => api.get<Detail>(`/ontology/equipment/${equipmentId}`), [equipmentId])
  const current = useAsync(() => api.get<Current>(`/equipment/${equipmentId}/current`), [equipmentId], 5000)
  const spatial = useAsync(() => api.get<any>(`/ontology/equipment/${equipmentId}/affected-spaces`), [equipmentId])
  const series = useAsync(
    () => api.get<{ series: TrendPoint[] }>(`/equipment/${equipmentId}/series`),
    [equipmentId],
    10000,
  )
  const issues = useAsync(
    () => api.get<{ issues: IssueSummary[] }>(`/issues?equipment_id=${equipmentId}`),
    [equipmentId],
    10000,
  )

  return (
    <div className="page">
      <Panel title={detail.data?.name ?? equipmentId}
             subtitle={<span className="mono">{equipmentId} · {detail.data?.brick_class}</span>}>
        <AsyncState loading={detail.loading} error={detail.error} empty={!detail.data}
                    emptyLabel="Unknown equipment.">
          {detail.data && (
            <KeyValue
              rows={[
                ['Property', detail.data.property ? `${detail.data.property.name} (${detail.data.property.property_type})` : '--'],
                ['Installed in (brick:hasLocation)', detail.data.installed_space?.name ?? '--'],
                ['Serves (brick:feeds)', detail.data.served_space?.name ?? '--'],
                ['Measurement scope (app:measures)', detail.data.measurement_scope?.name ?? '--'],
                ['Points (brick:hasPoint)', detail.data.points.length],
              ]}
            />
          )}
        </AsyncState>
      </Panel>

      <Panel title="Current values" subtitle="Units, observed time, freshness and quality for every registered point.">
        <AsyncState loading={current.loading && !current.data} error={current.error}
                    empty={!current.data?.points.length}>
          <table className="table table-compact">
            <thead><tr><th>Measurement</th><th>Value</th><th>Observed</th><th>Age</th><th>Quality</th></tr></thead>
            <tbody>
              {current.data?.points.map((p) => (
                <tr key={p.point_id}>
                  <td>{ROLE_LABEL[p.role] ?? p.role} <span className="muted mono tiny">{p.point_id}</span></td>
                  <td className="mono">
                    {p.value_number !== null ? fmtNumber(p.value_number) : (p.value_text ?? '--')}{' '}
                    {ROLE_UNIT[p.role] ?? p.unit ?? ''}
                  </td>
                  <td className="mono">{fmtTime(p.observed_at)}</td>
                  <td className="mono">{fmtDuration(p.age_seconds)}</td>
                  <td>
                    <Badge kind={p.state === 'fresh' && p.quality === 'GOOD' ? 'ok' : p.state === 'empty' ? 'neutral' : 'warn'}>
                      {p.state === 'empty' ? 'no data' : `${p.state} / ${p.quality?.toLowerCase()}`}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </AsyncState>
      </Panel>

      <Panel title="Supply-air trend" subtitle="Full replay window. Gaps are drawn as gaps.">
        <AsyncState loading={series.loading && !series.data} error={series.error}
                    empty={!series.data?.series.length} emptyLabel="No observations yet.">
          {series.data && <TrendChart points={series.data.series} threshold={null} />}
        </AsyncState>
      </Panel>

      <Panel title="Potentially affected spaces"
             subtitle="brick:feeds to the zone, brick:hasPart to its rooms. The plant room is where it sits, not who it serves.">
        <AsyncState loading={spatial.loading} error={spatial.error} empty={!spatial.data}>
          {spatial.data && (
            <div className="split">
              <div>
                <h3>Served zone</h3>
                <p className="mono">{spatial.data.served_zone?.name ?? '--'}</p>
                <h3>Affected rooms</h3>
                <ul className="list">
                  {(spatial.data.affected_rooms ?? []).map((r: any) => (
                    <li key={r.entity_id}><strong>{r.name}</strong> <span className="muted mono">{r.entity_id}</span></li>
                  ))}
                </ul>
              </div>
              <div>
                <h3>Installed location</h3>
                <p className="mono">{spatial.data.installed_space?.name ?? '--'}</p>
                <h3>Relationship path</h3>
                <ul className="list mono tiny">
                  {(spatial.data.path ?? []).map((hop: any, i: number) => (
                    <li key={i}>{hop.relation ?? 'start'} → {hop.entity_id} <span className="muted">({hop.role})</span></li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </AsyncState>
      </Panel>

      <Panel title="Issues for this equipment">
        <AsyncState loading={issues.loading && !issues.data} error={issues.error}
                    empty={!issues.data?.issues.length} emptyLabel="No issues recorded.">
          <table className="table table-compact">
            <thead><tr><th>Opened</th><th>Severity</th><th>State</th><th>Difference</th><th /></tr></thead>
            <tbody>
              {issues.data?.issues.map((issue) => (
                <tr key={issue.issue_id}>
                  <td className="mono">{fmtTime(issue.opened_at)}</td>
                  <td><Badge kind={issue.severity === 'Critical' ? 'critical' : 'warn'}>{issue.severity}</Badge></td>
                  <td><Badge kind={issue.state === 'open' ? 'critical' : 'ok'}>{issue.state}</Badge></td>
                  <td className="mono">{fmtNumber(issue.calculated_difference)} {issue.unit}</td>
                  <td><Link className="btn btn-small" to={`/issues/${issue.issue_id}`}>Investigate</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </AsyncState>
      </Panel>
    </div>
  )
}
