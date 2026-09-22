import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, ScopePreview } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { AsyncState, Badge, KeyValue, Panel } from '../components/common'
import { fmtDuration, fmtNumber, fmtTime } from '../lib/format'

type RuleDoc = {
  rule_key: string
  name: string
  intent: string
  status: string
  active_version: number | null
  definition: any
  versions: { version: number; notes: string; created_by: string; created_at: string }[]
  activations: { version: number; action: string; actor: string; source: string; at: string }[]
}

export default function RuleDetail() {
  const { ruleKey = '' } = useParams()
  const { data, loading, error, reload } = useAsync(() => api.get<RuleDoc>(`/rules/${ruleKey}`), [ruleKey])
  const [draft, setDraft] = useState('')
  const [preview, setPreview] = useState<ScopePreview | null>(null)
  const [message, setMessage] = useState<{ tone: string; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (data?.definition) setDraft(JSON.stringify(data.definition, null, 2))
  }, [data?.definition])

  const parsed = useMemo(() => {
    try {
      return { value: JSON.parse(draft), error: null as string | null }
    } catch (err) {
      return { value: null, error: err instanceof Error ? err.message : String(err) }
    }
  }, [draft])

  const act = async (fn: () => Promise<unknown>, success: string) => {
    setBusy(true)
    setMessage(null)
    try {
      await fn()
      setMessage({ tone: 'ok', text: success })
      reload()
    } catch (err) {
      setMessage({ tone: 'error', text: err instanceof Error ? err.message : String(err) })
    } finally {
      setBusy(false)
    }
  }

  const doPreview = () =>
    act(async () => {
      const result = await api.post<ScopePreview>('/rules/preview', parsed.value)
      setPreview(result)
    }, 'Draft validated and resolved against the ontology.')

  const doSave = (activate: boolean) =>
    act(async () => {
      const result = await api.post<{ version: number }>('/rules', {
        definition: parsed.value,
        notes: 'edited in the operations dashboard',
        created_by: 'operator',
        activate,
      })
      setMessage({ tone: 'ok', text: `Saved version ${result.version}${activate ? ' and activated it' : ''}.` })
    }, 'Saved.')

  const adjust = (mutate: (doc: any) => void) => {
    if (!parsed.value) return
    const next = JSON.parse(JSON.stringify(parsed.value))
    mutate(next)
    setDraft(JSON.stringify(next, null, 2))
  }

  return (
    <div className="page">
      <AsyncState loading={loading} error={error} empty={!data} emptyLabel="Rule not found.">
        {data && (
          <>
            <Panel
              title={data.name}
              subtitle={data.intent}
              actions={
                <>
                  <Badge kind={data.status === 'active' ? 'ok' : 'warn'}>{data.status}</Badge>
                  <button className="btn" disabled={busy} onClick={() =>
                    act(() => api.post(`/rules/${ruleKey}/disable`), 'Rule disabled.')}>Disable</button>
                  {data.active_version !== null && (
                    <button className="btn" disabled={busy} onClick={() =>
                      act(() => api.post(`/rules/${ruleKey}/activate`, { version: data.active_version, actor: 'operator' }),
                        'Rule re-activated.')}>Re-activate v{data.active_version}</button>
                  )}
                </>
              }
            >
              {message && <p className={`state state-${message.tone === 'ok' ? 'ok' : 'error'}`}>{message.text}</p>}
              <StructuredSummary definition={data.definition} activeVersion={data.active_version} />
            </Panel>

            <Panel
              title="Target scope and fault logic"
              subtitle="Edit the configuration, resolve it against the ontology, then save a new immutable version."
              actions={
                <>
                  <button className="btn" disabled={busy || !!parsed.error} onClick={doPreview}>Validate &amp; preview</button>
                  <button className="btn" disabled={busy || !!parsed.error} onClick={() => doSave(false)}>Save draft version</button>
                  <button className="btn btn-primary" disabled={busy || !!parsed.error} onClick={() => doSave(true)}>
                    Save &amp; activate
                  </button>
                </>
              }
            >
              <div className="quick-adjust">
                <span className="muted">quick adjust:</span>
                <button className="btn btn-small" onClick={() => adjust((d) => { d.logic.condition.threshold = 2.0 })}>threshold 2.0 °C</button>
                <button className="btn btn-small" onClick={() => adjust((d) => { d.logic.condition.threshold = 3.0 })}>threshold 3.0 °C</button>
                <button className="btn btn-small" onClick={() => adjust((d) => { d.logic.duration_seconds = 1800 })}>duration 30 min</button>
                <button className="btn btn-small" onClick={() => adjust((d) => { d.logic.duration_seconds = 900 })}>duration 15 min</button>
                <button className="btn btn-small" onClick={() => adjust((d) => { d.target.include.served_floor_ids = ['building-a-f02'] })}>
                  target only Building A floor 2
                </button>
                <button className="btn btn-small" onClick={() => adjust((d) => { d.target.include.served_floor_ids = null })}>
                  target all tenant floors
                </button>
                <button className="btn btn-small" onClick={() => adjust((d) => { d.overrides = [] })}>remove overrides</button>
              </div>
              <textarea className="editor" spellCheck={false} value={draft} onChange={(e) => setDraft(e.target.value)} />
              {parsed.error && <p className="state state-error">Invalid JSON: {parsed.error}</p>}
            </Panel>

            <Panel title="Matched assets"
                   subtitle="Resolved through explicit relationships. Every exclusion states its reason.">
              <PreviewPanel ruleKey={ruleKey} preview={preview} />
            </Panel>

            <Panel title="Versions and activation history"
                   subtitle="Issues keep the version they opened under, so history is never rewritten by an edit.">
              <div className="split">
                <div>
                  <h3>Versions</h3>
                  <table className="table table-compact">
                    <thead><tr><th>v</th><th>Notes</th><th>By</th><th>Created</th><th /></tr></thead>
                    <tbody>
                      {data.versions.map((v) => (
                        <tr key={v.version}>
                          <td className="mono">{v.version}{v.version === data.active_version && ' ●'}</td>
                          <td>{v.notes}</td>
                          <td className="mono">{v.created_by}</td>
                          <td className="mono">{fmtTime(v.created_at)}</td>
                          <td>
                            <button className="btn btn-small" disabled={busy} onClick={() =>
                              act(() => api.post(`/rules/${ruleKey}/activate`, { version: v.version, actor: 'operator' }),
                                `Activated version ${v.version}.`)}>Activate</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div>
                  <h3>Activation history</h3>
                  <ul className="list">
                    {data.activations.map((a, i) => (
                      <li key={i}>
                        <span className="mono">{fmtTime(a.at)}</span> — {a.action} v{a.version} by{' '}
                        <strong>{a.actor}</strong> <span className="muted">via {a.source}</span>
                      </li>
                    ))}
                    {!data.activations.length && <li className="muted">No activations recorded.</li>}
                  </ul>
                </div>
              </div>
            </Panel>

            <EvaluatorState ruleKey={ruleKey} />
          </>
        )}
      </AsyncState>
    </div>
  )
}

function StructuredSummary({ definition, activeVersion }: { definition: any; activeVersion: number | null }) {
  if (!definition) return <p className="state state-empty">No active version.</p>
  const include = definition.target?.include ?? {}
  const logic = definition.logic ?? {}
  return (
    <div className="split">
      <div>
        <h3>Target scope</h3>
        <KeyValue
          rows={[
            ['Property types', (include.property_types ?? ['any']).join(', ')],
            ['Equipment classes', (include.equipment_classes ?? []).join(', ')],
            ['Served space usage', (include.served_space_usage ?? ['any']).join(', ')],
            ['Served floors', include.served_floor_ids ? include.served_floor_ids.join(', ') : 'all'],
            ['Required points', (definition.target?.require_points ?? []).join(', ')],
            ['Explicit exclusions', (definition.target?.exclude_equipment_ids ?? []).join(', ') || 'none'],
          ]}
        />
      </div>
      <div>
        <h3>Fault logic (version {activeVersion ?? '--'})</h3>
        <KeyValue
          rows={[
            ['Operating state', logic.operating_state ? `${logic.operating_state.role} = ${logic.operating_state.equals}` : 'not gated'],
            ['Comparison', `${logic.condition?.operator} ${fmtNumber(logic.condition?.threshold)} ${logic.condition?.unit ?? ''}`],
            ['Left / right', `${logic.condition?.left?.role ?? logic.condition?.left?.constant} vs ${logic.condition?.right?.role ?? logic.condition?.right?.constant}${logic.condition?.right?.scope === 'served_zone_rooms' ? ' (zone rooms, ' + logic.condition.right.aggregate + ')' : ''}`],
            ['Must hold for', fmtDuration(logic.duration_seconds)],
            ['Input trusted for', fmtDuration(logic.max_input_age_seconds)],
            ['Recovery after', fmtDuration(logic.recovery_seconds)],
            ['Severity', logic.severity],
          ]}
        />
        <h3>Local overrides</h3>
        {(definition.overrides ?? []).length === 0 ? (
          <p className="muted">None.</p>
        ) : (
          <ul className="list">
            {definition.overrides.map((o: any) => (
              <li key={o.id}>
                <strong className="mono">{o.id}</strong>
                <div className="muted">{o.note}</div>
                <div className="mono">scope {JSON.stringify(o.scope)} → {JSON.stringify(o.values)}</div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

function PreviewPanel({ ruleKey, preview }: { ruleKey: string; preview: ScopePreview | null }) {
  const stored = useAsync(() => api.get<ScopePreview>(`/rules/${ruleKey}/preview`), [ruleKey])
  const shown = preview ?? stored.data
  return (
    <AsyncState loading={stored.loading && !shown} error={stored.error} empty={!shown}>
      {shown && (
        <>
          <p className="muted">
            {preview ? 'Preview of the edited draft.' : 'Preview of the stored rule.'}{' '}
            <strong>{shown.matched_count}</strong> matched, <strong>{shown.excluded_count}</strong> excluded.
          </p>
          <div className="scroll-y">
            <table className="table table-compact">
              <thead>
                <tr><th>Equipment</th><th>Property</th><th>Floor</th><th>Served zone</th><th>Effective threshold</th><th>Relationship path</th></tr>
              </thead>
              <tbody>
                {shown.matched.map((m) => (
                  <tr key={m.equipment_id}>
                    <td className="mono">{m.equipment_id}</td>
                    <td>{m.property_name} <span className="muted">{m.property_type}</span></td>
                    <td>{m.served_floor_name}</td>
                    <td>{m.served_zone_name}</td>
                    <td className="mono">
                      {fmtNumber(m.effective_config.threshold)} °C
                      <div className="muted">{m.effective_config.source}</div>
                    </td>
                    <td className="mono tiny">{m.path.join(' · ')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3>Exclusions</h3>
          <div className="scroll-y">
            <table className="table table-compact">
              <thead><tr><th>Equipment</th><th>Reason</th><th>Detail</th></tr></thead>
              <tbody>
                {shown.excluded.map((e) => (
                  <tr key={e.equipment_id}>
                    <td className="mono">{e.equipment_id}</td>
                    <td><Badge kind={e.reason === 'missing_required_point' ? 'critical' : 'neutral'}>{e.reason}</Badge></td>
                    <td className="mono tiny">{JSON.stringify(e.detail)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </AsyncState>
  )
}

function EvaluatorState({ ruleKey }: { ruleKey: string }) {
  const { data, loading, error } = useAsync(
    () => api.get<{ state: any[]; runs: any[] }>(`/rules/${ruleKey}/state`),
    [ruleKey],
    8000,
  )
  return (
    <Panel title="Evaluator behaviour" subtitle="Per-equipment state and the last evaluation passes.">
      <AsyncState loading={loading && !data} error={error} empty={!data?.state.length}
                  emptyLabel="This rule has not been evaluated yet.">
        <div className="split">
          <div>
            <h3>Per-equipment state</h3>
            <div className="scroll-y">
              <table className="table table-compact">
                <thead><tr><th>Equipment</th><th>Mode</th><th>Window since</th><th>Last observation</th><th>Last difference</th></tr></thead>
                <tbody>
                  {data?.state.map((s) => (
                    <tr key={s.equipment_id}>
                      <td className="mono">{s.equipment_id}</td>
                      <td><Badge kind={s.mode === 'fault' ? 'critical' : s.mode === 'normal' ? 'ok' : 'warn'}>{s.mode}</Badge></td>
                      <td className="mono">{fmtTime(s.condition_since)}</td>
                      <td className="mono">{fmtTime(s.last_observed_at)}</td>
                      <td className="mono">{fmtNumber(s.detail?.last_difference)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div>
            <h3>Recent evaluation passes</h3>
            <table className="table table-compact">
              <thead><tr><th>Evaluation time</th><th>Targets</th><th>Excluded</th><th>Insufficient</th><th>Opened</th><th>Closed</th><th>ms</th></tr></thead>
              <tbody>
                {data?.runs.map((r) => (
                  <tr key={r.run_id}>
                    <td className="mono">{fmtTime(r.evaluation_time)}</td>
                    <td>{r.targets}</td><td>{r.excluded}</td><td>{r.insufficient}</td>
                    <td>{r.opened}</td><td>{r.closed}</td>
                    <td className="mono">{fmtNumber(r.duration_ms, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </AsyncState>
    </Panel>
  )
}
