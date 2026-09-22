# System architecture

## Components

```mermaid
flowchart LR
  subgraph edge["Edge (simulated)"]
    SIM["Device simulator<br/>AHU · meter · IAQ<br/>interval + speedup"]
  end

  subgraph broker["Broker"]
    TOPIC[("telemetry.device.v1<br/>6 partitions, keyed by device")]
  end

  subgraph platform["Platform services"]
    ING["Ingestion consumer<br/>validate · resolve · dedupe"]
    EVAL["AFDD evaluator<br/>scheduler + state machine"]
    API["FastAPI<br/>REST + OpenAPI"]
    AGENT["Rule-authoring agent<br/>bounded tools"]
  end

  subgraph stores["Stores (one PostgreSQL instance)"]
    TS[("TimescaleDB hypertable<br/>observation")]
    CUR[("point_current")]
    ONTO[("ontology_entity<br/>ontology_relation<br/>space_closure")]
    APPDB[("rule · rule_version<br/>issue · issue_observation<br/>agent_request · agent_step")]
  end

  UI["React + TypeScript<br/>operations dashboard"]

  SIM -->|JSON envelope| TOPIC
  TOPIC --> ING
  ING -->|history| TS
  ING -->|guarded upsert| CUR
  ING -->|identity lookup| ONTO
  ING -->|rejects · duplicates| APPDB
  EVAL -->|read samples| TS
  EVAL -->|target scope| ONTO
  EVAL -->|issues · evidence| APPDB
  API --> TS
  API --> CUR
  API --> ONTO
  API --> APPDB
  AGENT -->|read-only tools| ONTO
  AGENT -->|draft + trace| APPDB
  API --> AGENT
  UI -->|/api| API
```

Every Python service runs from the same image; the compose command selects the
process. That removes the most common source of production drift, where the
worker and the API disagree about the version of the evaluation code.

## Trust boundaries

| Boundary | What crosses it | Control |
|---|---|---|
| Device → broker | Unauthenticated device claims: an id, a timestamp, values | Nothing is trusted. Identity is resolved against the ontology; unknown devices and points are rejected and counted. |
| Broker → platform | At-least-once delivery, possible replays and out-of-order arrival | Derived event ids + a primary-key claim make redelivery a no-op. Order is not assumed anywhere. |
| Model → platform | A proposed rule draft, possibly wrong or invented | The model can only call bounded read-only tools. Every draft is re-validated server-side. The agent cannot write a rule or activate one. |
| Operator → platform | Rule edits and activations | Versions are immutable and append-only; activation is a recorded action with an actor. |

The model is on the far side of the same boundary as a device: it is a source of
claims, not a source of authority.

## Telemetry ingestion flow

```mermaid
sequenceDiagram
  participant SIM as Simulator
  participant K as Broker
  participant ING as Ingestion consumer
  participant ONTO as Ontology registry
  participant TS as TimescaleDB
  participant CUR as point_current

  SIM->>K: publish(event_id = uuid5(device|source_record), observed_at, readings[])
  K->>ING: batch (at-least-once)
  ING->>ING: envelope validation
  ING->>TS: INSERT ingest_event ... ON CONFLICT (event_id) DO NOTHING
  alt row inserted
    ING->>ONTO: resolve device, then (device, source_name) per reading
    alt unknown device
      ING->>TS: status = rejected, reason = unknown_device
    else known
      ING->>TS: INSERT observation (history, ON CONFLICT DO NOTHING)
      ING->>CUR: UPSERT WHERE excluded.observed_at > current.observed_at
    end
  else conflict
    ING->>TS: record duplicate sighting, no further effect
  end
  ING->>K: commit offsets only after the batch is durable
```

Offsets are committed after the write, so a crash replays the batch. The replay
is harmless because the identity claim happens inside the same transaction as
the effect.

## AFDD evaluation flow

```mermaid
sequenceDiagram
  participant W as Evaluator worker
  participant R as rule / rule_version
  participant ONTO as Ontology
  participant TS as observation
  participant DB as issue / evaluator_state

  W->>R: load active rules (key, version, definition)
  W->>ONTO: resolve target scope (selectors → equipment + relationship paths)
  W->>DB: load per-equipment evaluator state
  W->>TS: fetch samples with observed_at > last_observed_at
  loop per equipment, per sample (ordered by observation time)
    W->>W: step(state, sample, effective config)
  end
  alt issue_opened
    W->>DB: INSERT issue + rule snapshot + effective config + affected spaces
    W->>DB: INSERT issue_observation evidence (window + leading context)
  end
  alt issue_closed
    W->>DB: UPDATE issue (recovered | operating_state_changed | rule_changed)
  end
  W->>DB: persist evaluator state + evaluation_run metrics
```

## Natural-language rule flow

```mermaid
sequenceDiagram
  participant U as Engineer
  participant API as API
  participant ORC as Orchestrator
  participant M as Model
  participant T as Bounded tools
  participant DB as Stores

  U->>API: POST /api/agent/requests {request}
  API->>ORC: run(request)
  ORC->>DB: persist request (status = running)
  loop bounded iterations, bounded wall clock
    ORC->>M: system prompt + history + tool specs
    M-->>ORC: tool_use
    ORC->>T: execute (server-implemented, read only)
    T-->>ORC: result
    ORC->>DB: persist step (input, output, ok, latency)
  end
  alt submit_draft and preview matched > 0
    ORC->>DB: status = awaiting_confirmation
  else ask_clarification / report_unsupported / no match
    ORC->>DB: status = stopped, stop_reason recorded
  end
  API-->>U: draft + preview + tool trace + stop reason
  U->>API: POST /confirm {actor}
  API->>DB: validate draft again → new rule version → activation with actor
```

The agent never reaches the activation path. Confirmation is a separate,
human-authenticated call, and it re-validates the draft rather than trusting
what was stored.

## Why these boundaries

- **Ingestion is separate from evaluation.** Ingestion must keep up with device
  throughput; evaluation is bursty and depends on rule count. Separating them
  means a slow rule cannot stall the data path.
- **The evaluator core is a pure function.** `evaluator/core.py` has no database,
  clock or I/O. Live evaluation, backtesting and the unit tests all drive the
  same function, so a test genuinely proves shipped behaviour.
- **The agent is a client of the platform, not a privileged component.** It uses
  the same scope resolver and the same validator as the API.

## Failure behaviour

| Failure | Behaviour |
|---|---|
| Broker unavailable | Simulator and consumer retry with backoff. No data is written, nothing is silently marked healthy; `/api/health/pipeline` shows the ingest lag growing. |
| Store write fails mid-batch | Transaction rolls back, offsets are not committed, batch is redelivered. Idempotent writes make the retry safe. |
| A rule fails to validate | The worker logs it and skips that rule; other rules keep evaluating. |
| A device stops reporting | Evaluation moves to `insufficient_data`, visible on the dashboard and in `/api/health/pipeline`; no issue is opened or closed on absent data. |
| Model provider errors | Retried twice, then the request stops with `stop_reason = provider_error`. Nothing is drafted or activated. |
| No API key configured | The agent falls back to a deterministic provider and says so in the response and the UI. |
