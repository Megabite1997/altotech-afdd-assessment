import { useState } from 'react'
import { Link } from 'react-router-dom'
import { AgentDetail, AgentRequestRow, AgentRun, api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { AsyncState, Badge, KeyValue, Panel } from '../components/common'
import { fmtNumber, fmtTime } from '../lib/format'

const EXAMPLES = [
  'For all office properties, monitor AHUs serving tenant areas. While an AHU is ON, if supply-air temperature differs from its setpoint by more than 3 degC continuously for 15 minutes, create a Critical issue.',
  'The tenant floors feel too warm lately. Can you set up monitoring for that?',
  'Monitor AHUs and raise an issue when supply-air temperature is more than 3 degC off setpoint for 15 minutes AND tomorrow’s weather forecast is above 35 degC.',
  'In Building Z, monitor AHUs serving tenant areas: while ON, more than 3 degC from setpoint for 15 minutes is Critical.',
  'For Floor 2 AHUs in Buildings B and C, compare return-air temperature with the room temperature in the same zone while ON. Above 5 degC for 15 minutes creates a Warning.',
]

const STOP_TONE: Record<string, string> = {
  draft_ready: 'ok',
  clarification_needed: 'warn',
  unsupported: 'warn',
  no_match: 'warn',
  max_iterations: 'critical',
  timeout: 'critical',
  provider_error: 'critical',
}

export default function Agent() {
  const [text, setText] = useState(EXAMPLES[0])
  const [run, setRun] = useState<AgentRun | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ tone: string; text: string } | null>(null)
  const history = useAsync(() => api.get<{ requests: AgentRequestRow[] }>('/agent/requests'), [run])
  const [openRequest, setOpenRequest] = useState<string | null>(null)

  const submit = async () => {
    setBusy(true)
    setMessage(null)
    setRun(null)
    try {
      const result = await api.post<AgentRun>('/agent/requests', { request: text })
      setRun(result)
      setOpenRequest(result.request_id)
    } catch (err) {
      setMessage({ tone: 'error', text: err instanceof Error ? err.message : String(err) })
    } finally {
      setBusy(false)
    }
  }

  const confirm = async (requestId: string) => {
    setBusy(true)
    try {
      const result = await api.post<{ rule_key: string; version: number; activated: boolean }>(
        `/agent/requests/${requestId}/confirm`,
        { actor: 'operator', activate: true },
      )
      setMessage({
        tone: 'ok',
        text: `Confirmed. Saved ${result.rule_key} version ${result.version}${result.activated ? ' and activated it' : ''}.`,
      })
      setRun(null)
      history.reload()
    } catch (err) {
      setMessage({ tone: 'error', text: err instanceof Error ? err.message : String(err) })
    } finally {
      setBusy(false)
    }
  }

  const reject = async (requestId: string) => {
    await api.post(`/agent/requests/${requestId}/reject`, { actor: 'operator', note: 'rejected in review' })
    setRun(null)
    history.reload()
  }

  return (
    <div className="page">
      <Panel
        title="AI-assisted rule authoring"
        subtitle="The agent plans, calls bounded tools and returns a draft. It cannot activate anything: only a human confirmation creates and activates a rule version."
        actions={<button className="btn btn-primary" disabled={busy || text.trim().length < 4} onClick={submit}>
          {busy ? 'Working…' : 'Draft a rule'}
        </button>}
      >
        {message && <p className={`state state-${message.tone === 'ok' ? 'ok' : 'error'}`}>{message.text}</p>}
        <textarea className="editor editor-short" value={text} onChange={(e) => setText(e.target.value)}
                  placeholder="Describe the problem in your own words…" />
        <div className="examples">
          <span className="muted">try:</span>
          {EXAMPLES.map((example, i) => (
            <button key={i} className="btn btn-small" onClick={() => setText(example)}>
              {['supported', 'ambiguous', 'unsupported', 'no match', 'cross-entity'][i]}
            </button>
          ))}
        </div>
      </Panel>

      {run && (
        <Panel
          title="Draft under review"
          subtitle={`Stopped as "${run.stop_reason}" after ${run.iterations} iteration(s) in ${fmtNumber(run.latency_ms, 0)} ms.`}
          actions={
            run.stop_reason === 'draft_ready' ? (
              <>
                <button className="btn" disabled={busy} onClick={() => reject(run.request_id)}>Reject</button>
                <button className="btn btn-primary" disabled={busy} onClick={() => confirm(run.request_id)}>
                  Confirm &amp; activate
                </button>
              </>
            ) : null
          }
        >
          <KeyValue
            rows={[
              ['Outcome', <Badge kind={STOP_TONE[run.stop_reason] ?? 'neutral'}>{run.stop_reason}</Badge>],
              ['Provider', `${run.provider} / ${run.model}`],
              ['Matched equipment', run.preview?.matched_count ?? '--'],
              ...(run.provider_fallback_reason
                ? [['Provider note', <span className="warn-text">{run.provider_fallback_reason}</span>] as [string, any]]
                : []),
            ]}
          />

          {run.clarification && (
            <div className="callout callout-warn">
              <h3>The agent stopped to ask</h3>
              <p>{run.clarification.question}</p>
              {run.clarification.options?.length > 0 && (
                <ul className="list">{run.clarification.options.map((o) => <li key={o}>{o}</li>)}</ul>
              )}
              <p className="muted">Answer it in the request text and run again. Nothing was saved.</p>
            </div>
          )}

          {run.error && (
            <div className="callout callout-warn">
              <h3>Stopped safely</h3>
              <pre className="code">{JSON.stringify(run.error, null, 2)}</pre>
            </div>
          )}

          {run.draft && (
            <>
              <h3>Proposed rule</h3>
              <pre className="code scroll-y">{JSON.stringify(run.draft, null, 2)}</pre>
            </>
          )}
          {run.preview?.matched_sample && (
            <>
              <h3>It would select</h3>
              <table className="table table-compact">
                <thead><tr><th>Equipment</th><th>Property</th><th>Floor</th><th>Effective threshold</th></tr></thead>
                <tbody>
                  {run.preview.matched_sample.map((m: any) => (
                    <tr key={m.equipment_id}>
                      <td className="mono">{m.equipment_id}</td>
                      <td className="mono">{m.property_id}</td>
                      <td className="mono">{m.served_floor_id}</td>
                      <td className="mono">{fmtNumber(m.effective_threshold)} ({m.threshold_source})</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </Panel>
      )}

      <Panel title="Request history" subtitle="Every request, tool trace, stop reason and confirmation is retained.">
        <AsyncState loading={history.loading && !history.data} error={history.error}
                    empty={!history.data?.requests.length} emptyLabel="No requests yet.">
          <table className="table">
            <thead>
              <tr><th>Created</th><th>Request</th><th>Status</th><th>Stop reason</th><th>Model</th><th>ms</th><th>Activated</th><th /></tr>
            </thead>
            <tbody>
              {history.data?.requests.map((row) => (
                <tr key={row.request_id}>
                  <td className="mono">{fmtTime(row.created_at)}</td>
                  <td className="clamp">{row.request_text}</td>
                  <td><Badge kind={row.status === 'confirmed' ? 'ok' : row.status === 'awaiting_confirmation' ? 'warn' : 'neutral'}>{row.status}</Badge></td>
                  <td><Badge kind={STOP_TONE[row.stop_reason ?? ''] ?? 'neutral'}>{row.stop_reason}</Badge></td>
                  <td className="mono tiny">{row.model}</td>
                  <td className="mono">{fmtNumber(row.latency_ms, 0)}</td>
                  <td className="mono">
                    {row.activated_rule_key
                      ? <Link className="link" to={`/rules/${row.activated_rule_key}`}>{row.activated_rule_key} v{row.activated_version}</Link>
                      : '--'}
                  </td>
                  <td>
                    <button className="btn btn-small"
                            onClick={() => setOpenRequest(openRequest === row.request_id ? null : row.request_id)}>
                      {openRequest === row.request_id ? 'Hide trace' : 'Trace'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {openRequest && <ToolTrace requestId={openRequest} />}
        </AsyncState>
      </Panel>
    </div>
  )
}

function ToolTrace({ requestId }: { requestId: string }) {
  const { data, loading, error } = useAsync(() => api.get<AgentDetail>(`/agent/requests/${requestId}`), [requestId])
  return (
    <div className="trace">
      <h3>Tool trace</h3>
      <AsyncState loading={loading} error={error} empty={!data?.steps.length} emptyLabel="No steps recorded.">
        <ol className="timeline">
          {data?.steps.map((step) => (
            <li key={step.seq}>
              <span className="mono">#{step.seq}</span>
              <Badge kind={step.ok ? 'ok' : 'critical'}>{step.name ?? step.kind}</Badge>
              <span className="muted mono">{fmtNumber(step.latency_ms, 0)} ms</span>
              <details>
                <summary>input / output</summary>
                <pre className="code">{JSON.stringify({ input: step.input, output: step.output }, null, 2)}</pre>
              </details>
            </li>
          ))}
        </ol>
        {data?.confirmed_by && (
          <p className="muted">Confirmed by <strong>{data.confirmed_by}</strong> at {fmtTime(data.confirmed_at)}.</p>
        )}
      </AsyncState>
    </div>
  )
}
