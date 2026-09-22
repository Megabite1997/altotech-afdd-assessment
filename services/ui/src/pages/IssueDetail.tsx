import { Link, useParams } from 'react-router-dom'
import { api, IssueDetail as Detail } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { AsyncState, Badge, KeyValue, Panel, QualityDot } from '../components/common'
import { TrendChart } from '../components/TrendChart'
import { fmtDuration, fmtNumber, fmtTime, ROLE_LABEL, ROLE_UNIT } from '../lib/format'

export default function IssueDetail() {
  const { issueId = '' } = useParams()
  const { data, loading, error } = useAsync(() => api.get<Detail>(`/issues/${issueId}`), [issueId])

  return (
    <div className="page">
      <AsyncState loading={loading} error={error} empty={!data} emptyLabel="Issue not found.">
        {data && <Body issue={data} />}
      </AsyncState>
    </div>
  )
}

function Body({ issue }: { issue: Detail }) {
  const gaps = issue.evidence.filter((e) => e.quality && e.quality !== 'GOOD')
  return (
    <>
      <Panel
        title={`${issue.severity} - ${issue.equipment_name}`}
        subtitle={
          <>
            Opened {fmtTime(issue.opened_at)} on observation time, under rule{' '}
            <Link className="link mono" to={`/rules/${issue.rule_key}`}>{issue.rule_key}</Link> version{' '}
            <strong>{issue.rule_version}</strong>, which is preserved exactly as it was when the issue opened.
          </>
        }
        actions={<Badge kind={issue.state === 'open' ? 'critical' : 'ok'}>{issue.state}</Badge>}
      >
        <KeyValue
          rows={[
            ['Trigger window opened', <span className="mono">{fmtTime(issue.trigger_started_at)}</span>],
            ['Condition qualified at', <span className="mono">{fmtTime(issue.trigger_observed_at)}</span>],
            ['Qualifying duration', fmtDuration(issue.qualifying_seconds)],
            ['Calculated difference', <span className="mono">{fmtNumber(issue.calculated_difference)} {issue.unit}</span>],
            ['Threshold applied', <span className="mono">{fmtNumber(issue.threshold)} {issue.unit} ({issue.threshold_source})</span>],
            ['Data quality', issue.data_quality === 'good'
              ? <Badge kind="ok">complete</Badge>
              : <Badge kind="warn">{issue.data_quality}</Badge>],
            ['Closed', issue.closed_at ? `${fmtTime(issue.closed_at)} (${issue.close_reason})` : 'still open'],
            ['Recurrence', issue.recurrence_index === 0 ? 'first occurrence' : `occurrence #${issue.recurrence_index + 1}`],
          ]}
        />
      </Panel>

      <Panel
        title="Why it triggered"
        subtitle="Supply-air temperature and its setpoint are the measurements that triggered this issue. Everything below this chart is context, not cause."
      >
        <TrendChart
          points={issue.trend}
          threshold={issue.threshold}
          windowStart={issue.trigger_started_at}
          triggerAt={issue.trigger_observed_at}
          closedAt={issue.closed_at}
        />
      </Panel>

      <Panel title="Potentially affected spaces"
             subtitle="Followed through brick:feeds and brick:hasPart. The AHU's installation room is not an affected space.">
        <div className="path">
          {issue.spatial.path.map((hop, i) => (
            <span key={`${hop.entity_id}-${i}`} className={`hop hop-${hop.role}`}>
              {hop.relation && <em className="mono">{hop.relation}</em>}
              <span className="mono">{hop.entity_id}</span>
            </span>
          ))}
        </div>
        <div className="split">
          <div>
            <h3>Served zone</h3>
            <p className="mono">{issue.spatial.served_zone?.name ?? '--'} ({issue.spatial.served_zone?.entity_id ?? '--'})</p>
            <h3>Affected rooms ({issue.spatial.affected_rooms.length})</h3>
            <ul className="list">
              {issue.spatial.affected_rooms.map((room) => (
                <li key={room.entity_id}>
                  <strong>{room.name}</strong> <span className="muted mono">{room.entity_id}</span>
                  <span className="muted"> · {room.usage_type}</span>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h3>Installed location (not affected)</h3>
            <p className="mono">{issue.spatial.installed_space?.name ?? '--'}</p>
            <p className="muted">
              The unit physically sits here. It is kept separate from the spaces it serves so a technician is
              never sent to the wrong room.
            </p>
          </div>
        </div>
      </Panel>

      <Panel title="Context, not cause"
             subtitle="Room IAQ, AHU alarm and floor electricity are shown for the investigation. None of them took part in the trigger.">
        <div className="split">
          <div>
            <h3>Room conditions</h3>
            <table className="table table-compact">
              <thead><tr><th>Room</th><th>Measurement</th><th>Value</th><th>Observed</th></tr></thead>
              <tbody>
                {issue.room_context.map((row, i) => (
                  <tr key={i}>
                    <td>{row.room_name}</td>
                    <td>{ROLE_LABEL[row.role] ?? row.role}</td>
                    <td className="mono">
                      <QualityDot state={row.quality === 'GOOD' ? 'fresh' : 'bad'} />
                      {fmtNumber(row.value_number)} {ROLE_UNIT[row.role] ?? ''}
                    </td>
                    <td className="mono">{fmtTime(row.observed_at)}</td>
                  </tr>
                ))}
                {!issue.room_context.length && <tr><td colSpan={4} className="state state-empty">No room context.</td></tr>}
              </tbody>
            </table>
          </div>
          <div>
            <h3>Floor electricity</h3>
            <table className="table table-compact">
              <thead><tr><th>Measurement</th><th>Value</th><th>Observed</th></tr></thead>
              <tbody>
                {issue.floor_meter.map((row, i) => (
                  <tr key={i}>
                    <td>{ROLE_LABEL[row.role] ?? row.role}</td>
                    <td className="mono">{fmtNumber(row.value_number)} {row.unit ?? ''}</td>
                    <td className="mono">{fmtTime(row.observed_at)}</td>
                  </tr>
                ))}
                {!issue.floor_meter.length && <tr><td colSpan={3} className="state state-empty">No floor meter.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </Panel>

      <Panel title="Evidence"
             subtitle={`${issue.evidence.length} observations retained, ${gaps.length} with non-good quality. Leading context is included so the window start is checkable.`}>
        <div className="scroll-y">
          <table className="table table-compact">
            <thead>
              <tr><th>Observed</th><th>Supply-air</th><th>Setpoint</th><th>|difference|</th><th>Run</th><th>Quality</th><th>In window</th></tr>
            </thead>
            <tbody>
              {issue.evidence.map((row) => (
                <tr key={row.observed_at} className={row.in_window ? 'row-window' : ''}>
                  <td className="mono">{fmtTime(row.observed_at)}</td>
                  <td className="mono">{fmtNumber(row.supply_air_temp)}</td>
                  <td className="mono">{fmtNumber(row.setpoint)}</td>
                  <td className="mono">{fmtNumber(row.difference)}</td>
                  <td>{row.run_status ?? '--'}</td>
                  <td>{row.quality === 'GOOD' ? <Badge kind="ok">good</Badge> : <Badge kind="warn">{row.quality}</Badge>}</td>
                  <td>{row.in_window ? 'yes' : 'context'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Lifecycle" subtitle="Every state change the evaluator recorded for this issue.">
        <ol className="timeline">
          {issue.lifecycle.map((event, i) => (
            <li key={i}>
              <span className="mono">{fmtTime(event.at)}</span>
              <Badge kind={event.type === 'opened' ? 'critical' : event.type === 'closed' ? 'ok' : 'warn'}>{event.type}</Badge>
              <code>{JSON.stringify(event.detail)}</code>
            </li>
          ))}
        </ol>
      </Panel>

      <Panel title="Rule as it was when this issue opened"
             subtitle="The snapshot is stored on the issue, so later edits to the rule never rewrite history.">
        <div className="split">
          <div>
            <h3>Effective configuration</h3>
            <pre className="code">{JSON.stringify(issue.effective_config, null, 2)}</pre>
          </div>
          <div>
            <h3>Rule snapshot (v{issue.rule_version})</h3>
            <pre className="code scroll-y">{JSON.stringify(issue.rule_snapshot, null, 2)}</pre>
          </div>
        </div>
      </Panel>
    </>
  )
}
