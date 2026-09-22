export function fmtNumber(value: unknown, digits = 2): string {
  if (value === null || value === undefined || value === '') return '--'
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? n.toFixed(digits) : String(value)
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '--'
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toISOString().replace('T', ' ').replace('.000Z', 'Z').slice(0, 19) + 'Z'
}

export function fmtClock(iso: string | null | undefined): string {
  if (!iso) return '--'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toISOString().slice(11, 19) + 'Z'
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '--'
  const s = Math.round(seconds)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${s % 60}s`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}

/** Freshness is judged against data time, not wall clock. See docs/03. */
export function freshnessLabel(state: string, age: number | null): string {
  if (state === 'empty') return 'no data'
  if (state === 'stale') return `stale (${fmtDuration(age)} old)`
  return `fresh (${fmtDuration(age)} old)`
}

export const MODE_LABEL: Record<string, string> = {
  normal: 'Normal',
  pending: 'Condition met, timing',
  fault: 'Issue open',
  insufficient_data: 'Insufficient data',
  off: 'Not running',
}

export const ROLE_LABEL: Record<string, string> = {
  run_status: 'Run status',
  alarm_status: 'Alarm',
  supply_air_temperature: 'Supply-air temp',
  return_air_temperature: 'Return-air temp',
  supply_air_temperature_setpoint: 'Supply-air setpoint',
  room_air_temperature: 'Room temp',
  relative_humidity: 'Humidity',
  co2_concentration: 'CO2',
  electrical_power: 'Power',
  electrical_energy: 'Energy',
}

export const ROLE_UNIT: Record<string, string> = {
  supply_air_temperature: '°C',
  return_air_temperature: '°C',
  supply_air_temperature_setpoint: '°C',
  room_air_temperature: '°C',
  relative_humidity: '%RH',
  co2_concentration: 'ppm',
  electrical_power: 'kW',
  electrical_energy: 'kWh',
}
