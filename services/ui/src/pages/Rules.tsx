import { Link } from 'react-router-dom'
import { api, RuleSummary } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { AsyncState, Badge, Panel } from '../components/common'
import { fmtTime } from '../lib/format'

export default function Rules() {
  const { data, loading, error, reload } = useAsync(
    () => api.get<{ rules: RuleSummary[] }>('/rules'),
    [],
  )

  return (
    <div className="page">
      <Panel
        title="Rules"
        subtitle="Target scope and fault logic are stored as separate configuration. Versions are immutable; activation is explicit."
        actions={<button className="btn" onClick={reload}>Refresh</button>}
      >
        <AsyncState loading={loading} error={error} empty={!data?.rules.length}
                    emptyLabel="No rules installed. Run `afdd seed`.">
          <table className="table">
            <thead>
              <tr><th>Rule</th><th>Status</th><th>Active version</th><th>Latest</th><th>Open issues</th><th>Updated</th><th /></tr>
            </thead>
            <tbody>
              {data?.rules.map((rule) => (
                <tr key={rule.rule_key}>
                  <td>
                    <strong>{rule.name}</strong>
                    <div className="muted mono">{rule.rule_key}</div>
                    <div className="muted intent">{rule.intent}</div>
                  </td>
                  <td>
                    <Badge kind={rule.status === 'active' ? 'ok' : rule.status === 'disabled' ? 'warn' : 'neutral'}>
                      {rule.status}
                    </Badge>
                  </td>
                  <td className="mono">{rule.active_version ?? '--'}</td>
                  <td className="mono">{rule.latest_version ?? '--'}</td>
                  <td>{rule.open_issues}</td>
                  <td className="mono">{fmtTime(rule.updated_at)}</td>
                  <td><Link className="btn btn-small" to={`/rules/${rule.rule_key}`}>Open</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </AsyncState>
      </Panel>
    </div>
  )
}
