import { Link, useSearchParams } from 'react-router-dom'
import { api, IssueSummary } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { AsyncState, Badge, Panel } from '../components/common'
import { fmtNumber, fmtTime } from '../lib/format'

export default function Issues() {
  const [params, setParams] = useSearchParams()
  const state = params.get('state') ?? ''
  const equipmentId = params.get('equipment_id') ?? ''

  const query = new URLSearchParams()
  if (state) query.set('state', state)
  if (equipmentId) query.set('equipment_id', equipmentId)

  const { data, loading, error, reload } = useAsync(
    () => api.get<{ issues: IssueSummary[] }>(`/issues?${query.toString()}`),
    [state, equipmentId],
    8000,
  )

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    value ? next.set(key, value) : next.delete(key)
    setParams(next)
  }

  return (
    <div className="page">
      <Panel
        title="Issues"
        subtitle="An AFDD issue is a reason to investigate, not proof of a mechanical root cause."
        actions={
          <>
            <select className="input" value={state} onChange={(e) => setFilter('state', e.target.value)}>
              <option value="">all states</option>
              <option value="open">open</option>
              <option value="closed">closed</option>
            </select>
            {equipmentId && (
              <button className="btn" onClick={() => setFilter('equipment_id', '')}>
                clear {equipmentId}
              </button>
            )}
            <button className="btn" onClick={reload}>Refresh</button>
          </>
        }
      >
        <AsyncState loading={loading && !data} error={error} empty={!data?.issues.length}
                    emptyLabel="No issues match this filter.">
          <table className="table">
            <thead>
              <tr>
                <th>Opened (observed)</th>
                <th>Equipment</th>
                <th>Severity</th>
                <th>State</th>
                <th>Difference</th>
                <th>Threshold</th>
                <th>Affected rooms</th>
                <th>Rule</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data?.issues.map((issue) => (
                <tr key={issue.issue_id}>
                  <td className="mono">{fmtTime(issue.opened_at)}</td>
                  <td>
                    <Link className="link" to={`/equipment/${issue.equipment_id}`}>{issue.equipment_name}</Link>
                    <div className="muted mono">{issue.equipment_id}</div>
                  </td>
                  <td><Badge kind={issue.severity === 'Critical' ? 'critical' : 'warn'}>{issue.severity}</Badge></td>
                  <td>
                    <Badge kind={issue.state === 'open' ? 'critical' : 'ok'}>{issue.state}</Badge>
                    {issue.close_reason && <div className="muted">{issue.close_reason}</div>}
                    {issue.data_quality !== 'good' && <div className="muted warn-text">{issue.data_quality}</div>}
                  </td>
                  <td className="mono">{fmtNumber(issue.calculated_difference)} {issue.unit}</td>
                  <td className="mono">
                    {fmtNumber(issue.threshold)} {issue.unit}
                    <div className="muted">{issue.threshold_source}</div>
                  </td>
                  <td>{issue.affected_room_ids.length}</td>
                  <td className="mono">{issue.rule_key} v{issue.rule_version}</td>
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
