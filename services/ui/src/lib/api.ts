const BASE = '/api'

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = body.detail ?? detail
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail, response.status)
  }
  return response.json() as Promise<T>
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) }),
}

// --- shapes the UI relies on ------------------------------------------------

export type PointValue = {
  value: number | string | null
  quality: string
  observed_at: string | null
  age_seconds: number | null
  state: 'fresh' | 'stale' | 'empty'
}

export type Device = {
  equipment_id: string
  name: string
  brick_class: string
  points: Record<string, PointValue>
  open_issues: number
  evaluator_mode: string | null
}

export type Zone = {
  entity_id: string
  name: string
  usage_type: string | null
  ahu: Device | null
  rooms: { entity_id: string; name: string; usage_type: string | null; iaq: Device | null }[]
}

export type Floor = {
  entity_id: string
  name: string
  usage_type: string | null
  meter: Device | null
  zones: Zone[]
}

export type Property = {
  entity_id: string
  name: string
  property_type: string | null
  floors: Floor[]
  open_issues: number
}

export type Overview = {
  clocks: {
    data_time: string | null
    platform_time: string
    evaluation_clock: string
    note: string
  }
  properties: Property[]
  totals: { open_issues: number; equipment: number; insufficient_data: number }
}

export type IssueSummary = {
  issue_id: string
  rule_key: string
  rule_version: number
  equipment_id: string
  equipment_name: string
  property_id: string | null
  severity: string
  state: string
  opened_at: string
  closed_at: string | null
  close_reason: string | null
  calculated_difference: number | null
  threshold: number | null
  threshold_source: string | null
  unit: string | null
  data_quality: string
  affected_zone_id: string | null
  affected_room_ids: string[]
  recurrence_index: number
}

export type TrendPoint = Record<string, number | string | null> & { observed_at: string }

export type IssueDetail = IssueSummary & {
  trigger_started_at: string
  trigger_observed_at: string
  qualifying_seconds: number
  installed_space_id: string | null
  rule_snapshot: any
  effective_config: any
  evidence: {
    observed_at: string
    supply_air_temp: number | null
    setpoint: number | null
    difference: number | null
    run_status: string | null
    quality: string | null
    in_window: boolean
  }[]
  lifecycle: { at: string; type: string; detail: Record<string, string> }[]
  spatial: {
    installed_space: { entity_id: string; name: string } | null
    served_zone: { entity_id: string; name: string } | null
    served_floor: { entity_id: string; name: string } | null
    affected_rooms: { entity_id: string; name: string; usage_type: string | null }[]
    path: { relation: string | null; entity_id: string; role: string }[]
  }
  trend: TrendPoint[]
  room_context: {
    room_id: string
    room_name: string
    role: string
    value_number: number | null
    quality: string | null
    observed_at: string | null
  }[]
  floor_meter: { role: string; value_number: number | null; unit: string | null; observed_at: string | null }[]
}

export type RuleSummary = {
  rule_key: string
  name: string
  intent: string
  status: string
  active_version: number | null
  latest_version: number | null
  open_issues: number
  updated_at: string
}

export type ScopeMatch = {
  equipment_id: string
  name: string
  property_name: string | null
  property_type: string | null
  served_zone_name: string | null
  served_floor_name: string | null
  served_floor_id: string | null
  installed_space_id: string | null
  points: Record<string, string>
  effective_config: Record<string, any>
  path: string[]
}

export type ScopePreview = {
  rule_key: string
  version?: number
  matched_count: number
  excluded_count: number
  matched: ScopeMatch[]
  excluded: { equipment_id: string; name: string; reason: string; detail: Record<string, any> }[]
}

export type PipelineHealth = {
  clocks: {
    platform_time: string
    data_time: string | null
    evaluation_clock: string
    ingest_to_platform_lag_seconds: number | null
  }
  observations: number
  ingest_events: Record<string, number>
  rejections: { reason_code: string; n: number; last_at: string }[]
  out_of_order_arrivals: number
  stale_points: number
  devices_not_reporting: { equipment_id: string; name: string; last_observed_at: string }[]
  evaluation: {
    rule_key: string
    last_run_at: string
    last_evaluation_time: string
    opened: number
    closed: number
  }[]
  ontology: { spaces: number; equipment: number; points: number; relations: number }
}

export type AgentRun = {
  request_id: string
  status: string
  stop_reason: string
  draft: any
  preview: any
  clarification: { question: string; options: string[] } | null
  error: any
  iterations: number
  latency_ms: number
  provider: string
  model: string
  provider_fallback_reason: string | null
}

export type AgentRequestRow = {
  request_id: string
  request_text: string
  status: string
  stop_reason: string | null
  provider: string
  model: string
  iterations: number
  latency_ms: number | null
  activated_rule_key: string | null
  activated_version: number | null
  created_at: string
}

export type AgentDetail = AgentRequestRow & {
  draft: any
  preview: any
  clarification: any
  error: any
  confirmed_by: string | null
  confirmed_at: string | null
  steps: {
    seq: number
    kind: string
    name: string | null
    input: any
    output: any
    ok: boolean
    latency_ms: number | null
    at: string
  }[]
}
