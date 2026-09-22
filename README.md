# Multi-site AFDD with Brickschema

Automatic Fault Detection and Diagnostics across a three-building portfolio,
built so a property engineer can turn recurring operational know-how into safe,
reusable monitoring rules — and so a technician can see **what needs attention,
why, and which occupied spaces may be affected**.

AltoTech Global — Senior Full Stack Engineer technical assessment.

---

## Quickstart

Prerequisites: Docker Desktop (running), ~4 GB free, ports 3000, 8000, 5432,
19092.

```bash
cp .env.example .env
docker compose up --build
```

That one command applies migrations, loads the Brickschema ontology from the
supplied source pack, installs the rules, and starts the broker, device
simulator, ingestion consumer, AFDD evaluator, API and dashboard. The simulator
replays six source hours in about six minutes (`SIM_SPEEDUP=60`).

| Surface | URL |
|---|---|
| Operations dashboard | <http://localhost:3000> |
| API documentation (OpenAPI) | <http://localhost:8000/docs> |
| OpenAPI schema | <http://localhost:8000/openapi.json> |

Confirm the seed:

```bash
docker compose exec api afdd verify
```

```
check                          actual   expected  result
spaces                             93         93  pass
equipment                          84         84  pass
points                            288        288  pass
```

Then follow the **[demonstration guide](docs/06-demonstration-guide.md)** for the
end-to-end journey.

---

## What is implemented

| Task | Status | Where |
|---|---|---|
| **1 — Foundation** | Complete | Configurable mock-device simulator (selectable interval and acceleration, per-building selection); Redpanda broker; ingestion consumer with ontology-resolved identity, derived event ids and idempotent writes; TimescaleDB history plus a guarded current-value projection; visible rejects, duplicates, lateness and gaps; discovery, relationship, latest-value, history and health APIs |
| **2 — Configurable AFDD engine** | Complete | Versioned rule schema with target scope and fault logic stored separately; ontology-based scope resolution with an explainable preview; deterministic evaluator on device observation time; documented OFF / missing / stale / recovery / recurrence / rule-change behaviour; issues preserving their opening rule version and evidence |
| **3 — Operation dashboard** | Complete | Portfolio and property overview, issue investigation, rule detail and preview, plus equipment detail and pipeline health. React + TypeScript against the Python API |
| **4 — AI-assisted rule creation** | Complete | Bounded-tool agent with clarification, safe rejection, target preview, retry and stop limits; full tool traces; human confirmation as the only path to activation; repeatable 8-case evaluation matrix |
| **Bonus — Historical backtesting** | Complete | Same evaluator over a chosen window, isolated from live issues, reporting predicted issues and insufficient-data intervals |
| **Bonus — MCP server** | Not implemented | Design position stated in [AI rule-authoring design](docs/04-ai-rule-authoring-design.md) |

### Known limitations

No authentication or authorisation — every API call is anonymous and `actor` is
a supplied string, not an authenticated identity. This is the largest gap
between this and a production system. Single-process evaluator; no alert
delivery; no retention or downsampling policy on the hypertable; no
accessibility audit. Full list and reasoning in
[technical decisions](docs/05-technical-decisions.md).

---

## Documents

| Document | What it covers |
|---|---|
| [Understanding checkpoint](docs/understanding-checkpoint.md) | Problem framing, system boundary, domain interpretation, assumptions and open questions |
| [System architecture](docs/01-system-architecture.md) | Component diagram, trust boundaries, the three flow diagrams, failure behaviour |
| [Brickschema model](docs/02-brickschema-model.md) | Classes, relationships, where we leave Brick and why, storage, onboarding a new building |
| [AFDD behaviour](docs/03-afdd-behavior.md) | Rule schema, timing semantics with reasoning, issue lifecycle, evidence, limitations, test mapping |
| [AI rule-authoring design](docs/04-ai-rule-authoring-design.md) | Agent, tools, state and stopping, persistence, evaluation matrix, evolution plan |
| [Technical decisions](docs/05-technical-decisions.md) | Fourteen decisions with alternatives, reasoning, costs; performance numbers and scaling limits |
| [Demonstration guide](docs/06-demonstration-guide.md) | Step-by-step journey, expected seeded outcomes, AI-assistance note |
| [Screenshot walkthrough](docs/screenshots/README.md) | The sequence captured from a live run |

---

## Repository layout

```
docker-compose.yml           one command starts the whole stack
.env.example                 every knob, documented
source-pack/                 the supplied ontology and telemetry, unmodified
docs/                        the seven documents above
services/
  platform/                  Python: API, simulator, ingestion, evaluator, agent
    afdd/
      config.py              environment-driven settings
      db.py                  connection pool and migrations
      events.py              telemetry event envelope and quality coercion
      migrations/            SQL schema
      ontology/              Brick vocabulary, loader, traversal queries
      simulator/             mock-device replay
      ingest/                consumer and idempotent writer
      rules/                 schema, scope resolution, store, shipped rules
      evaluator/             pure state machine, worker, backtester
      agent/                 bounded tools, providers, orchestrator, case matrix
      api/                   FastAPI routes
      cli.py                 the afdd command
    tests/                   50 tests, no database or broker needed
    requirements.lock        pinned dependencies
  ui/                        React + TypeScript operations dashboard
```

---

## The `afdd` command

```bash
docker compose exec api afdd <command>
```

| Command | Purpose |
|---|---|
| `seed` | Migrate, load the ontology, install and activate the default rule |
| `verify` | Check inventory counts and print the issues and data-quality outcomes |
| `simulate --interval 15 --speedup 120 --buildings building-a,building-b` | Stream source readings to the broker |
| `ingest` | Run the broker consumer |
| `replay` | Load the fixtures straight into the store, no broker |
| `evaluate [--once]` | Run the AFDD evaluator |
| `backtest --rule <key> --start <iso> --end <iso>` | Replay a rule over history |
| `agent run "<request>"` | Draft a rule from natural language |
| `agent evaluate [--provider anthropic]` | Run the repeatable case matrix |
| `remove-point <point_id>` | Demo aid: show visible exclusion of equipment missing a required point |
| `db migrate` / `db reset [--all]` | Database maintenance |

---

## Configuration

Every setting is environment-driven; see [`.env.example`](.env.example). The
ones that change behaviour:

| Variable | Default | Effect |
|---|---|---|
| `SIM_INTERVAL_SECONDS` | `60` | Publish cadence in seconds (15 or 60). This is the emission cadence, not a device sampling rate: observation timestamps are device facts and are never rewritten or resampled. |
| `SIM_SPEEDUP` | `60` | Source seconds per wall-clock second. `60` replays six hours in six minutes. |
| `SIM_BUILDINGS` | `*` | Stream all buildings, or a subset. |
| `EVAL_CLOCK` | `data` | `data` sets evaluation time to the newest observation time, making a replay byte-reproducible. `wall` uses wall-clock time for live deployments. |
| `LLM_PROVIDER` / `ANTHROPIC_API_KEY` | `anthropic` / empty | With a key, the rule-authoring agent makes real Anthropic tool-use calls. Without one it falls back to a deterministic stub provider and says so in the API response and the dashboard. |

---

## Tests

```bash
cd services/platform
pip install -e ".[dev]"
pytest -q
```

50 tests, no database or broker required.

- `tests/test_evaluator_core.py` — the timing semantics: duration, OFF, missing,
  invalid, stale, gaps, recovery, recurrence, overrides, determinism, and that
  resuming from stored state matches a single pass.
- `tests/test_fixture_scenarios.py` — every seeded condition in the supplied
  telemetry, pinned to an expected outcome through the real event envelope and
  the real evaluator.
- `tests/test_events_and_rules.py` — event identity, quality coercion, rule
  schema validation and override resolution.

Agent behaviour has its own repeatable matrix:

```bash
docker compose exec api afdd agent evaluate
```

---

## Expected outcomes from the supplied fixture

Six source hours, 30,237 events, 103,655 observations.

| Outcome | Expected |
|---|---|
| Inventory | 93 spaces, 84 equipment, 288 points, 1,092 relationships |
| Ingestion | 30,235 accepted, 1 duplicate suppressed, 1 unknown device rejected |
| Rule target | 16 matched (offices, tenant zones), 8 excluded (hotel) |
| Issue 1 | `ahu-a-f02-east` opens **10:15**, 4.40 °C against 3.0 °C (`rule_default`), closes 10:26 `recovered` |
| Issue 2 | `ahu-b-f01-west` opens **11:35**, 2.50 °C against 2.0 °C (`override:building-b-tighter-threshold`), closes 11:46 `recovered` |
| No issue | `ahu-a-f03-west` (14 min, one short), `ahu-b-f04-east` (deviating while OFF), `ahu-a-f04-west` (setpoint missing 12:10–12:15), all Building C AHUs (hotel, out of scope) |
| Data quality | 6 `MISSING` observations stored, a 5-minute gap on `ahu-c-f03-east` from 11:00, one late observation delivered last |

---

## Running without Docker

The broker path needs Redpanda, but everything else runs against a plain
PostgreSQL — useful for tests and for a quick look:

```bash
export DATABASE_URL=postgresql://afdd:afdd@localhost:5432/afdd
export SOURCE_DIR=$PWD/source-pack
cd services/platform && pip install -e ".[dev]"
afdd seed && afdd replay && afdd evaluate --once && afdd verify
uvicorn afdd.api.app:app --port 8000
```

`afdd replay` uses the same envelope and the same ingestion writer as the broker
path, so duplicate suppression, unknown-device rejection and the guarded
current-value upsert behave identically. If the TimescaleDB extension is absent
the telemetry table falls back to a plain table with a BRIN index, and the
migration says so.
