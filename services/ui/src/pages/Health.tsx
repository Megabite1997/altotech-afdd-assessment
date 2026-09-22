import { api, PipelineHealth } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { AsyncState, Badge, KeyValue, Panel } from '../components/common'
import { fmtDuration, fmtTime } from '../lib/format'

type DataQuality = {
  recent_rejections: {
    reason_code: string
    source_record_id: string | null
    device_id: string | null
    point_ref: string | null
    observed_at: string | null
    detail: Record<string, unknown>
    rejected_at: string
  }[]
  non_good_observations: {
    equipment_id: string
    point_id: string
    quality: string
    n: number
    first_at: string
    last_at: string
  }[]
}

const REASON_HELP: Record<string, string> = {
  duplicate_event: 'The same source record arrived twice. The second copy had no effect.',
  unknown_device: 'The device is not in the ontology registry, so the event was rejected rather than guessed at.',
  unknown_point: 'The device reported a measurement that is not registered for it.',
  malformed_envelope: 'The event was missing required envelope fields.',
  invalid_observed_at: 'The observation timestamp could not be parsed.',
}

export default function Health() {
  const health = useAsync(() => api.get<PipelineHealth>('/health/pipeline'), [], 5000)
  const quality = useAsync(() => api.get<DataQuality>('/health/data-quality'), [], 10000)

  return (
    <div className="page">
      <Panel title="Pipeline health"
             subtitle="Is the data behind the dashboard real, complete and current?"
             actions={<button className="btn" onClick={health.reload}>Refresh</button>}>
        <AsyncState loading={health.loading && !health.data} error={health.error} empty={!health.data}>
          {health.data && (
            <>
              <KeyValue
                rows={[
                  ['Platform time', <span className="mono">{fmtTime(health.data.clocks.platform_time)}</span>],
                  ['Newest observation (data time)', <span className="mono">{fmtTime(health.data.clocks.data_time)}</span>],
                  ['Evaluation clock', <Badge kind="neutral">{health.data.clocks.evaluation_clock}</Badge>],
                  ['Time since last ingest', fmtDuration(health.data.clocks.ingest_to_platform_lag_seconds)],
                  ['Observations stored', health.data.observations.toLocaleString()],
                  ['Out-of-order arrivals', health.data.out_of_order_arrivals],
                  ['Points older than 3 intervals', health.data.stale_points],
                ]}
              />
              <div className="split">
                <div>
                  <h3>Ingestion outcomes</h3>
                  <table className="table table-compact">
                    <thead><tr><th>Status</th><th>Events</th></tr></thead>
                    <tbody>
                      {Object.entries(health.data.ingest_events).map(([status, n]) => (
                        <tr key={status}>
                          <td><Badge kind={status === 'accepted' ? 'ok' : status === 'rejected' ? 'critical' : 'warn'}>{status}</Badge></td>
                          <td className="mono">{n.toLocaleString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <h3>Ontology loaded</h3>
                  <KeyValue
                    rows={[
                      ['Spaces', health.data.ontology.spaces],
                      ['Equipment', health.data.ontology.equipment],
                      ['Points', health.data.ontology.points],
                      ['Relationships', health.data.ontology.relations],
                    ]}
                  />
                </div>
                <div>
                  <h3>Devices not reporting</h3>
                  {health.data.devices_not_reporting.length === 0 ? (
                    <p className="state state-ok">Every device reported within the last three expected intervals.</p>
                  ) : (
                    <table className="table table-compact">
                      <thead><tr><th>Device</th><th>Last observation</th></tr></thead>
                      <tbody>
                        {health.data.devices_not_reporting.map((d) => (
                          <tr key={d.equipment_id}>
                            <td className="mono">{d.equipment_id}</td>
                            <td className="mono">{fmtTime(d.last_observed_at)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                  <h3>Evaluation</h3>
                  <table className="table table-compact">
                    <thead><tr><th>Rule</th><th>Last pass</th><th>Evaluation time</th><th>Opened</th><th>Closed</th></tr></thead>
                    <tbody>
                      {health.data.evaluation.map((row) => (
                        <tr key={row.rule_key}>
                          <td className="mono">{row.rule_key}</td>
                          <td className="mono">{fmtTime(row.last_run_at)}</td>
                          <td className="mono">{fmtTime(row.last_evaluation_time)}</td>
                          <td>{row.opened}</td><td>{row.closed}</td>
                        </tr>
                      ))}
                      {!health.data.evaluation.length && (
                        <tr><td colSpan={5} className="state state-empty">No evaluation passes yet.</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}
        </AsyncState>
      </Panel>

      <Panel title="Data quality"
             subtitle="Rejected, duplicated, late, missing and incomplete data is kept visible rather than dropped.">
        <AsyncState loading={quality.loading && !quality.data} error={quality.error} empty={!quality.data}>
          <div className="split">
            <div>
              <h3>Rejections and duplicates</h3>
              <div className="scroll-y">
                <table className="table table-compact">
                  <thead><tr><th>Reason</th><th>Source record</th><th>Device</th><th>Observed</th></tr></thead>
                  <tbody>
                    {quality.data?.recent_rejections.map((row, i) => (
                      <tr key={i}>
                        <td>
                          <Badge kind={row.reason_code === 'duplicate_event' ? 'warn' : 'critical'}>{row.reason_code}</Badge>
                          <div className="muted tiny">{REASON_HELP[row.reason_code] ?? ''}</div>
                        </td>
                        <td className="mono tiny">{row.source_record_id ?? '--'}</td>
                        <td className="mono tiny">{row.device_id ?? '--'}{row.point_ref ? ` / ${row.point_ref}` : ''}</td>
                        <td className="mono tiny">{fmtTime(row.observed_at)}</td>
                      </tr>
                    ))}
                    {!quality.data?.recent_rejections.length && (
                      <tr><td colSpan={4} className="state state-ok">Nothing rejected.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
            <div>
              <h3>Incomplete measurements</h3>
              <p className="muted">
                A blank value in the source is stored as a MISSING observation, not dropped, so the gap stays
                visible and the evaluator can refuse to run on it.
              </p>
              <div className="scroll-y">
                <table className="table table-compact">
                  <thead><tr><th>Equipment</th><th>Point</th><th>Quality</th><th>Count</th><th>From</th><th>To</th></tr></thead>
                  <tbody>
                    {quality.data?.non_good_observations.map((row, i) => (
                      <tr key={i}>
                        <td className="mono tiny">{row.equipment_id}</td>
                        <td className="mono tiny">{row.point_id}</td>
                        <td><Badge kind="warn">{row.quality}</Badge></td>
                        <td className="mono">{row.n}</td>
                        <td className="mono tiny">{fmtTime(row.first_at)}</td>
                        <td className="mono tiny">{fmtTime(row.last_at)}</td>
                      </tr>
                    ))}
                    {!quality.data?.non_good_observations.length && (
                      <tr><td colSpan={6} className="state state-ok">Every stored observation is good.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </AsyncState>
      </Panel>
    </div>
  )
}
