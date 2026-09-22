import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, Overview, Zone } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { AsyncState, Badge, Measurement, Panel } from '../components/common'
import { fmtDuration, fmtTime, MODE_LABEL } from '../lib/format'

export default function Portfolio() {
  const { data, loading, error, reload } = useAsync(
    () => api.get<Overview>('/portfolio/overview'),
    [],
    5000,
  )
  const [openProperty, setOpenProperty] = useState<string | null>(null)

  return (
    <div className="page">
      <Clocks data={data} />

      <Panel
        title="Portfolio"
        subtitle="Property, floor and zone. Every value shows its unit, observed time and freshness."
        actions={<button className="btn" onClick={reload}>Refresh</button>}
      >
        <AsyncState loading={loading && !data} error={error} empty={!data?.properties.length}
                    emptyLabel="No properties loaded. Run `afdd seed`.">
          <div className="totals">
            <Total label="Open issues" value={data?.totals.open_issues ?? 0} tone={data?.totals.open_issues ? 'critical' : 'ok'} />
            <Total label="Equipment" value={data?.totals.equipment ?? 0} />
            <Total label="Insufficient data" value={data?.totals.insufficient_data ?? 0}
                   tone={data?.totals.insufficient_data ? 'warn' : 'ok'} />
          </div>

          {data?.properties.map((property) => {
            const open = openProperty === property.entity_id
            return (
              <article key={property.entity_id} className="property">
                <button className="property-head" onClick={() => setOpenProperty(open ? null : property.entity_id)}>
                  <span className="chev">{open ? '▾' : '▸'}</span>
                  <strong>{property.name}</strong>
                  <Badge kind="neutral">{property.property_type ?? 'unknown type'}</Badge>
                  <span className="grow" />
                  {property.open_issues > 0
                    ? <Badge kind="critical">{property.open_issues} open</Badge>
                    : <Badge kind="ok">healthy</Badge>}
                </button>
                {open && (
                  <div className="property-body">
                    {property.floors.map((floor) => (
                      <div key={floor.entity_id} className="floor">
                        <div className="floor-head">
                          <strong>{floor.name}</strong>
                          <span className="muted">{floor.usage_type}</span>
                          <span className="grow" />
                          {floor.meter ? (
                            <span className="meter">
                              <span className="muted">floor meter</span>
                              <Measurement role="electrical_power" point={floor.meter.points.electrical_power} />
                              <Measurement role="electrical_energy" point={floor.meter.points.electrical_energy} />
                            </span>
                          ) : <span className="muted">no floor meter</span>}
                        </div>
                        <div className="zones">
                          {floor.zones.map((zone) => <ZoneCard key={zone.entity_id} zone={zone} />)}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </article>
            )
          })}
        </AsyncState>
      </Panel>
    </div>
  )
}

function ZoneCard({ zone }: { zone: Zone }) {
  const ahu = zone.ahu
  const mode = ahu?.evaluator_mode ?? null
  const tone = ahu?.open_issues ? 'critical' : mode === 'insufficient_data' ? 'warn'
    : mode === 'pending' ? 'warn' : mode === 'off' ? 'neutral' : 'ok'
  return (
    <div className={`zone zone-${tone}`}>
      <div className="zone-head">
        <strong>{zone.name}</strong>
        <span className="muted">{zone.usage_type}</span>
        <span className="grow" />
        {mode && <Badge kind={tone}>{MODE_LABEL[mode] ?? mode}</Badge>}
      </div>

      {ahu ? (
        <>
          <div className="zone-row">
            <Link to={`/equipment/${ahu.equipment_id}`} className="link">{ahu.name}</Link>
            {ahu.open_issues > 0 && (
              <Link to={`/issues?equipment_id=${ahu.equipment_id}`} className="badge badge-critical">
                {ahu.open_issues} open issue{ahu.open_issues > 1 ? 's' : ''}
              </Link>
            )}
          </div>
          <div className="measures">
            <span><span className="muted">run</span> <Measurement role="run_status" point={ahu.points.run_status} /></span>
            <span><span className="muted">SAT</span> <Measurement role="supply_air_temperature" point={ahu.points.supply_air_temperature} /></span>
            <span><span className="muted">setpoint</span> <Measurement role="supply_air_temperature_setpoint" point={ahu.points.supply_air_temperature_setpoint} /></span>
            <span><span className="muted">RAT</span> <Measurement role="return_air_temperature" point={ahu.points.return_air_temperature} /></span>
          </div>
        </>
      ) : (
        <p className="state state-empty">No AHU serves this zone.</p>
      )}

      <details className="rooms">
        <summary>{zone.rooms.length} occupied room{zone.rooms.length === 1 ? '' : 's'}</summary>
        {zone.rooms.map((room) => (
          <div key={room.entity_id} className="room">
            <span className="room-name">{room.name}</span>
            {room.iaq ? (
              <span className="measures">
                <Measurement role="room_air_temperature" point={room.iaq.points.room_air_temperature} />
                <Measurement role="relative_humidity" point={room.iaq.points.relative_humidity} />
                <Measurement role="co2_concentration" point={room.iaq.points.co2_concentration} />
              </span>
            ) : <span className="muted">no IAQ device</span>}
          </div>
        ))}
      </details>
    </div>
  )
}

function Total({ label, value, tone = 'neutral' }: { label: string; value: number; tone?: string }) {
  return (
    <div className={`total total-${tone}`}>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  )
}

function Clocks({ data }: { data: Overview | null }) {
  if (!data) return null
  const { data_time, platform_time, evaluation_clock } = data.clocks
  const lag = data_time ? (new Date(platform_time).getTime() - new Date(data_time).getTime()) / 1000 : null
  return (
    <div className="clocks" title={data.clocks.note}>
      <span><span className="muted">data time</span> <strong>{fmtTime(data_time)}</strong></span>
      <span><span className="muted">platform time</span> <strong>{fmtTime(platform_time)}</strong></span>
      <span><span className="muted">evaluation clock</span> <Badge kind="neutral">{evaluation_clock}</Badge></span>
      <span className="muted">
        {evaluation_clock === 'data'
          ? `Freshness is measured against data time (fixture offset ${fmtDuration(lag)}).`
          : 'Freshness is measured against wall-clock time.'}
      </span>
    </div>
  )
}
