-- AltoTech AFDD platform - baseline schema
-- Applied by `afdd db migrate`. Idempotent: safe to re-run.

-- TimescaleDB is the production store for telemetry history. The guard keeps a
-- plain PostgreSQL usable for local unit runs; docker-compose always ships
-- TimescaleDB, and `afdd verify` reports which one is in use.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS timescaledb;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'timescaledb extension unavailable (%); observation stays a plain table', SQLERRM;
END
$$;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ---------------------------------------------------------------------------
-- Ontology registry (Brickschema semantic model + application metadata)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ontology_entity (
    entity_id    text PRIMARY KEY,
    kind         text NOT NULL CHECK (kind IN ('space', 'equipment', 'point')),
    brick_class  text NOT NULL,
    name         text NOT NULL,
    source_id    text,
    metadata     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ontology_entity_kind_idx ON ontology_entity (kind);
CREATE INDEX IF NOT EXISTS ontology_entity_class_idx ON ontology_entity (brick_class);
CREATE INDEX IF NOT EXISTS ontology_entity_meta_idx ON ontology_entity USING gin (metadata);

-- Typed, directed edges. Inverse edges are materialised so traversal in either
-- direction is a single index lookup.
CREATE TABLE IF NOT EXISTS ontology_relation (
    subject_id  text NOT NULL REFERENCES ontology_entity (entity_id) ON DELETE CASCADE,
    predicate   text NOT NULL,
    object_id   text NOT NULL REFERENCES ontology_entity (entity_id) ON DELETE CASCADE,
    PRIMARY KEY (subject_id, predicate, object_id)
);
CREATE INDEX IF NOT EXISTS ontology_relation_obj_idx ON ontology_relation (object_id, predicate);
CREATE INDEX IF NOT EXISTS ontology_relation_pred_idx ON ontology_relation (predicate);

-- Transitive containment closure over brick:hasPart, refreshed by the loader.
CREATE TABLE IF NOT EXISTS space_closure (
    ancestor_id   text NOT NULL,
    descendant_id text NOT NULL,
    depth         int  NOT NULL,
    PRIMARY KEY (ancestor_id, descendant_id)
);
CREATE INDEX IF NOT EXISTS space_closure_desc_idx ON space_closure (descendant_id);

-- Resolution index: (device_id, source_name) -> canonical point identity.
CREATE TABLE IF NOT EXISTS point_registry (
    point_id                  text PRIMARY KEY REFERENCES ontology_entity (entity_id) ON DELETE CASCADE,
    equipment_id              text NOT NULL,
    source_name               text NOT NULL,
    brick_class               text NOT NULL,
    value_type                text NOT NULL,
    unit                      text,
    expected_interval_seconds int NOT NULL DEFAULT 60,
    UNIQUE (equipment_id, source_name)
);

-- ---------------------------------------------------------------------------
-- Telemetry: history (hypertable) + current value projection
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS observation (
    point_id     text        NOT NULL,
    observed_at  timestamptz NOT NULL,
    value_number double precision,
    value_text   text,
    quality      text        NOT NULL CHECK (quality IN ('GOOD', 'MISSING', 'INVALID')),
    event_id     uuid        NOT NULL,
    ingested_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (point_id, observed_at)
);

DO $$
BEGIN
    PERFORM create_hypertable('observation', 'observed_at',
                              chunk_time_interval => INTERVAL '1 day',
                              if_not_exists => TRUE);
EXCEPTION WHEN undefined_function THEN
    RAISE WARNING 'create_hypertable unavailable; falling back to a BRIN index';
    CREATE INDEX IF NOT EXISTS observation_time_brin ON observation USING brin (observed_at);
END
$$;

CREATE INDEX IF NOT EXISTS observation_ingested_idx ON observation (ingested_at DESC);

-- Latest accepted observation per point. Guarded so an older observation can
-- never overwrite a newer current value (see ingest/writer.py).
CREATE TABLE IF NOT EXISTS point_current (
    point_id     text PRIMARY KEY,
    observed_at  timestamptz NOT NULL,
    value_number double precision,
    value_text   text,
    quality      text NOT NULL,
    event_id     uuid NOT NULL,
    ingested_at  timestamptz NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Ingestion bookkeeping - duplicates, rejects and lateness stay visible
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ingest_event (
    event_id          uuid PRIMARY KEY,
    source_record_id  text NOT NULL,
    device_id         text NOT NULL,
    device_kind       text,
    observed_at       timestamptz,
    produced_at       timestamptz,
    ingested_at       timestamptz NOT NULL DEFAULT now(),
    status            text NOT NULL CHECK (status IN ('accepted', 'duplicate', 'rejected', 'partial')),
    reason_code       text,
    readings_total    int NOT NULL DEFAULT 0,
    readings_accepted int NOT NULL DEFAULT 0,
    lateness_seconds  double precision,
    payload           jsonb
);
CREATE INDEX IF NOT EXISTS ingest_event_status_idx ON ingest_event (status, ingested_at DESC);
CREATE INDEX IF NOT EXISTS ingest_event_device_idx ON ingest_event (device_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS ingest_reject (
    id               bigserial PRIMARY KEY,
    event_id         uuid,
    source_record_id text,
    device_id        text,
    point_ref        text,
    reason_code      text NOT NULL,
    detail           jsonb NOT NULL DEFAULT '{}'::jsonb,
    observed_at      timestamptz,
    rejected_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ingest_reject_reason_idx ON ingest_reject (reason_code, rejected_at DESC);

-- ---------------------------------------------------------------------------
-- Rules: target scope and fault logic stored as versioned configuration
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS rule (
    rule_key       text PRIMARY KEY,
    name           text NOT NULL,
    intent         text NOT NULL DEFAULT '',
    status         text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'disabled')),
    active_version int,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rule_version (
    rule_key   text NOT NULL REFERENCES rule (rule_key) ON DELETE CASCADE,
    version    int  NOT NULL,
    definition jsonb NOT NULL,
    notes      text,
    created_by text NOT NULL DEFAULT 'system',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (rule_key, version)
);

CREATE TABLE IF NOT EXISTS rule_activation (
    id           bigserial PRIMARY KEY,
    rule_key     text NOT NULL,
    version      int  NOT NULL,
    action       text NOT NULL CHECK (action IN ('activated', 'disabled')),
    actor        text NOT NULL DEFAULT 'operator',
    source       text NOT NULL DEFAULT 'api',
    at           timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Issues and evidence
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS issue (
    issue_id              uuid PRIMARY KEY,
    rule_key              text NOT NULL,
    rule_version          int  NOT NULL,
    equipment_id          text NOT NULL,
    severity              text NOT NULL,
    state                 text NOT NULL CHECK (state IN ('open', 'closed')),
    opened_at             timestamptz NOT NULL,
    closed_at             timestamptz,
    close_reason          text,
    trigger_started_at    timestamptz NOT NULL,
    trigger_observed_at   timestamptz NOT NULL,
    qualifying_seconds    double precision NOT NULL,
    calculated_difference double precision,
    threshold             double precision,
    threshold_source      text,
    unit                  text,
    data_quality          text NOT NULL DEFAULT 'good',
    affected_zone_id      text,
    affected_room_ids     text[] NOT NULL DEFAULT '{}',
    installed_space_id    text,
    rule_snapshot         jsonb NOT NULL,
    effective_config      jsonb NOT NULL,
    backtest_run_id       uuid,
    recurrence_of         uuid,
    recurrence_index      int NOT NULL DEFAULT 0,
    created_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS issue_state_idx ON issue (state, opened_at DESC);
CREATE INDEX IF NOT EXISTS issue_equipment_idx ON issue (equipment_id, opened_at DESC);
CREATE INDEX IF NOT EXISTS issue_backtest_idx ON issue (backtest_run_id);
-- Only one live issue per (rule, equipment); backtest issues are excluded.
CREATE UNIQUE INDEX IF NOT EXISTS issue_one_open_live_idx
    ON issue (rule_key, equipment_id)
    WHERE state = 'open' AND backtest_run_id IS NULL;

CREATE TABLE IF NOT EXISTS issue_observation (
    issue_id          uuid NOT NULL REFERENCES issue (issue_id) ON DELETE CASCADE,
    observed_at       timestamptz NOT NULL,
    supply_air_temp   double precision,
    setpoint          double precision,
    difference        double precision,
    run_status        text,
    quality           text,
    freshness_seconds double precision,
    in_window         boolean NOT NULL DEFAULT true,
    PRIMARY KEY (issue_id, observed_at)
);

CREATE TABLE IF NOT EXISTS issue_event (
    id       bigserial PRIMARY KEY,
    issue_id uuid NOT NULL REFERENCES issue (issue_id) ON DELETE CASCADE,
    at       timestamptz NOT NULL,
    type     text NOT NULL,
    detail   jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS issue_event_issue_idx ON issue_event (issue_id, at);

-- ---------------------------------------------------------------------------
-- Evaluator state and run log
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS evaluator_state (
    rule_key        text NOT NULL,
    equipment_id    text NOT NULL,
    mode            text NOT NULL DEFAULT 'normal',
    condition_since timestamptz,
    normal_since    timestamptz,
    last_observed_at timestamptz,
    open_issue_id   uuid,
    recurrence_index int NOT NULL DEFAULT 0,
    detail          jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (rule_key, equipment_id)
);

CREATE TABLE IF NOT EXISTS evaluation_run (
    run_id          uuid PRIMARY KEY,
    rule_key        text NOT NULL,
    evaluation_time timestamptz NOT NULL,
    started_at      timestamptz NOT NULL DEFAULT now(),
    duration_ms     double precision,
    targets         int NOT NULL DEFAULT 0,
    evaluated       int NOT NULL DEFAULT 0,
    excluded        int NOT NULL DEFAULT 0,
    insufficient    int NOT NULL DEFAULT 0,
    opened          int NOT NULL DEFAULT 0,
    closed          int NOT NULL DEFAULT 0,
    samples         int NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS evaluation_run_time_idx ON evaluation_run (started_at DESC);

-- ---------------------------------------------------------------------------
-- Backtesting (isolated from live issues)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS backtest_run (
    run_id      uuid PRIMARY KEY,
    rule_key    text NOT NULL,
    label       text,
    definition  jsonb NOT NULL,
    range_start timestamptz NOT NULL,
    range_end   timestamptz NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    summary     jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- ---------------------------------------------------------------------------
-- AI rule-authoring agent
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS agent_request (
    request_id     uuid PRIMARY KEY,
    request_text   text NOT NULL,
    status         text NOT NULL,
    outcome        text,
    stop_reason    text,
    draft          jsonb,
    preview        jsonb,
    clarification  jsonb,
    error          jsonb,
    model          text,
    provider       text,
    schema_version text,
    iterations     int NOT NULL DEFAULT 0,
    latency_ms     double precision,
    confirmed_by   text,
    confirmed_at   timestamptz,
    activated_rule_key text,
    activated_version  int,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_step (
    request_id uuid NOT NULL REFERENCES agent_request (request_id) ON DELETE CASCADE,
    seq        int  NOT NULL,
    kind       text NOT NULL,
    name       text,
    input      jsonb,
    output     jsonb,
    ok         boolean NOT NULL DEFAULT true,
    latency_ms double precision,
    at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (request_id, seq)
);
