# Technical decisions

Each entry: the decision, the alternatives considered, why this one, and what it
costs. The costs are real; none of them are hidden.

---

## 1. One device snapshot = one event

**Decision.** A source row becomes one event carrying every point that device
reported at that instant, with a `readings[]` array.

**Alternatives.** One event per point reading; a batched multi-device frame.

**Why.** The required rule compares supply-air temperature against its setpoint
*and* checks run status at the same observation instant. Per-point events would
turn that into a temporal join across partitions, with no guarantee the three
readings ever arrive together. A multi-device frame would couple unrelated
devices' delivery.

**Cost.** Consumers interested in one point receive the whole snapshot, and the
envelope is larger. At this data rate that is irrelevant; at a hundred sites,
adding a per-point projection topic is a straightforward addition.

---

## 2. Derived event identity (`uuid5(device | source_record_id)`)

**Decision.** Event ids are derived from the source record, not assigned by the
producer.

**Alternatives.** Producer-assigned UUIDv4; a broker offset; a natural key of
`(point, observed_at)`.

**Why.** Any producer replaying the same source record produces the same id, so
idempotency needs no coordination between producers, and a replayed file is
automatically deduplicated. A UUIDv4 would make a re-run of the simulator look
like brand-new data.

**Cost.** Two genuinely distinct observations that share a source record id
would collapse into one. That is a property of the source system's identifiers;
if it were untrue, the id would have to include `observed_at`.

---

## 3. Idempotency by primary-key claim, in the same transaction as the effect

**Decision.** `INSERT INTO ingest_event ... ON CONFLICT (event_id) DO NOTHING`
runs in the same transaction that writes the observations. Offsets are committed
only after the transaction.

**Alternatives.** A dedup cache in the consumer; exactly-once broker semantics;
a separate dedup table written before processing.

**Why.** At-least-once delivery plus an idempotent write is simpler and more
robust than pursuing exactly-once. Putting the claim in the same transaction as
the effect removes the window where a crash leaves an event marked-seen but
unwritten. A consumer-side cache would not survive a restart or a rebalance.

**Cost.** One extra row per event, and duplicate detection is per-event rather
than per-reading.

---

## 4. Guarded current-value upsert

**Decision.** `point_current` updates only `WHERE EXCLUDED.observed_at >
point_current.observed_at`. History always accepts the row.

**Alternatives.** Last-write-wins; a view computing `max(observed_at)` over the
hypertable.

**Why.** The source delivers a late observation out of order. Last-write-wins
would make the dashboard go backwards in time. A view would be correct but
expensive on every dashboard read.

**Cost.** A denormalised table to keep in step. The guard is one SQL clause and
is exercised by the fixture's out-of-order tail, which is delivered last.

---

## 5. Relational ontology with a materialised closure — not a graph database

**Decision.** `ontology_entity` + `ontology_relation` (both directions) +
`space_closure`, in PostgreSQL alongside the telemetry and the issues.

**Alternatives.** Neo4j; Apache AGE; an RDF triple store with a real Brick
reasoner.

**Why.** The queries the product actually runs are shallow, fixed-shape
traversals (AHU → zone → rooms; building → everything inside) over ~400 entities
and ~1,100 edges. Every one of them also needs to join to issues or rules, which
live in PostgreSQL. A second store would mean a second thing to operate, back
up, and keep consistent — for traversals that are already index lookups.

**Cost.** No inference over the Brick class hierarchy (asking for
`brick:Equipment` will not transitively match `brick:AHU`), and variable-depth
traversal means a recursive CTE or a closure rebuild. Both are stated
limitations, not oversights. Because every traversal goes through
`ontology/queries.py`, swapping the backing store is a change in one module.

**When I would change it.** Brick class-hierarchy inference becoming a product
requirement, or variable-depth path queries becoming common, would justify
Apache AGE (same PostgreSQL instance, so no second operational story) before
Neo4j.

---

## 6. Rules as versioned JSONB configuration, with target and logic separated

**Decision.** Rules are validated Pydantic models stored as JSONB in immutable
`rule_version` rows. `target` (ontology selectors) and `logic` (evaluation) are
separate objects.

**Alternatives.** Python classes per rule; a bespoke text DSL; a general
expression language.

**Why.** The requirement is that the case be configuration, not a code path, and
that target scope be changeable independently of fault logic. A validated schema
gives type-checked configuration, a machine-readable contract for the AI agent,
and a diffable audit trail — without the cost of building and documenting a
parser. A general expression language would have made the safety analysis of the
AI path much harder: `preview_target` can enumerate exactly what a selector
matches, which is not true of arbitrary expressions.

**Cost.** Less expressive than a full DSL. Genuinely new logic shapes — a rate
of change, a multi-stage condition — need an operator or an operand scope added
in code. The extension points are enumerated in
[AFDD behaviour](03-afdd-behavior.md).

---

## 7. The evaluator core is a pure function

**Decision.** `evaluator/core.py` has no database, clock, or I/O. `step(state,
sample, config) -> events`. The worker, the backtester and the tests all call it.

**Alternatives.** Evaluate inline with SQL window functions; a streaming
framework (Flink, Bytewax).

**Why.** Timing semantics are the subtlest part of this product and the part a
reviewer will interrogate hardest. A pure function makes every edge case a
three-line test, and guarantees the backtest cannot silently disagree with live
evaluation. SQL window functions would have expressed the 15-minute condition
compactly, but "what happens to the window when the unit turns OFF mid-fault"
becomes very hard to read, and impossible to unit test without a database.

**Cost.** Samples must be loaded into memory per equipment per pass. At 24 AHUs
× 360 samples that is nothing; at portfolio scale the incremental cursor keeps
each pass to the new samples only.

---

## 8. Evaluation on a data clock by default

**Decision.** `EVAL_CLOCK=data` — evaluation time is the newest observation time
in the store. `EVAL_CLOCK=wall` for live deployments.

**Alternatives.** Always wall clock; rewrite observation timestamps to "now" on
ingest.

**Why.** The fixture is dated 2026-01-15. Against a wall clock every reading is
months stale and nothing evaluates. Rewriting timestamps would destroy the
observed-versus-received distinction the whole design rests on — and the brief
explicitly requires preserving device-recorded time. The data clock makes a
replay at any speed produce byte-identical issues, which is what makes the demo
and the tests reproducible.

**Cost.** In `data` mode a completely dead pipeline looks frozen rather than
stale, because the clock stops with the data. The dashboard mitigates this by
showing both clocks side by side and labelling which one freshness is measured
against, and `/api/health/pipeline` reports the platform-time ingest lag.

---

## 9. An open issue survives losing its data

**Decision.** Missing, invalid or stale input moves evaluation to
`insufficient_data`, resets the timing window, and **cannot open an issue**. If
an issue is already open it **stays open**, flagged `data_quality =
evidence_gap`.

**Alternatives.** Close the issue when evidence is lost; suspend it into a third
state.

**Why.** Losing sight of a fault is not the fault clearing. Auto-closing would
mean a failing sensor silently clears a real problem — the worst possible
failure mode for a diagnostics product.

**Cost.** An issue can stay open on a dead feed. That is why `evidence_gap` is
surfaced on the issue, in the list, and on the health page, rather than being an
internal flag.

---

## 10. A rule change closes open issues under the old version

**Decision.** When a rule's active version changes, open issues close with
reason `rule_changed`, keeping their original version and snapshot; evaluation
restarts under the new version.

**Alternatives.** Keep evaluating open issues under their opening version until
they close naturally; re-base them onto the new version.

**Why.** Re-basing would make the stored evidence inconsistent with the stated
threshold — an audit trail that lies. Keeping old versions alive indefinitely
means an unbounded set of concurrently active rule versions to reason about and
explain.

**Cost.** A trivial rule edit closes open issues. They remain fully readable,
and a genuine recurrence reopens as a new issue on the next pass. For a product
with long-lived critical issues this would deserve a "carry over" option.

---

## 11. One PostgreSQL instance for telemetry, ontology, rules and issues

**Decision.** TimescaleDB (which is PostgreSQL) hosts the hypertable and the
application schema.

**Alternatives.** Separate application PostgreSQL and TimescaleDB instances;
Supabase for application data.

**Why.** Every interesting query crosses the boundary — an issue joins to its
equipment, its ontology path and its observations. One instance makes those
single transactional queries instead of cross-store joins in application code,
and one thing to run for a reviewer.

**Cost.** Telemetry write load and application query load share resources, and
they scale differently. The separation point is visible: `afdd/db.py` is the only
module that opens connections, and the ontology, rule and issue queries are
already isolated in their own modules. Splitting is a connection-routing change.

---

## 12. Redpanda for the broker

**Decision.** Redpanda, Kafka API, topic keyed by `device_id` across 6
partitions.

**Alternatives.** Kafka with ZooKeeper/KRaft; NATS JetStream; Redis Streams;
RabbitMQ.

**Why.** Kafka semantics — partitioned ordering, consumer groups, offset-based
replay — are what this workload needs, and keying by device gives per-device
ordering without a global order the system does not require. Redpanda is a
single binary with no coordination service, which matters for a stack a reviewer
must start with one command.

**Cost.** Ordering is guaranteed per device only. Nothing in the design depends
on cross-device ordering; the evaluator sorts by observation time regardless.

---

## 13. Simulator preserves source delivery order

**Decision.** A row's delivery slot is the running maximum observation time
within its own file, so the fixture's out-of-order tail is delivered last, as
supplied. Observation timestamps are never rewritten.

**Alternatives.** Sort strictly by `observed_at`; emit in raw file order.

**Why.** The fixture deliberately seeds a late observation and an unknown-device
record at the end of the file. Sorting by timestamp would destroy the very
condition the platform is supposed to handle visibly.

**Cost.** `interval` is an emission cadence, not a device sampling rate: at
`--interval 15` the same source stream is delivered in 15-second batches rather
than being resampled. Resampling would mean inventing readings, which would be
worse. This is stated in the README rather than glossed over.

---

## 14. React, TypeScript, no component or chart library

**Decision.** React + TypeScript + Vite, hand-written CSS, a hand-written SVG
chart. Served by nginx which also proxies `/api`, so the browser is single-origin.

**Alternatives.** A component library (MUI, shadcn); a chart library (Recharts,
ECharts).

**Why.** The investigation chart has unusual requirements: it must draw the
tolerance band around a *moving* setpoint, shade the qualifying window, mark the
trigger instant, show run status as a separate strip, and — most importantly —
**draw a data gap as a gap rather than interpolating across it**. Most chart
libraries connect across nulls by default, which would make missing data look
like healthy data in the one view where that matters most.

**Cost.** No zoom, pan, or tooltips on the chart, and no accessibility work
beyond semantic markup and an `aria-label`. Both are real gaps for a production
product.

---

## Performance and scale

Measured on the supplied fixture (3 buildings, 24 AHUs, 288 points, 6 hours):

| Operation | Measurement |
|---|---|
| Direct fixture load (30,237 events → 103,655 observations) | ~22 s single-process, unbatched |
| Full evaluation pass, 16 targets, 5,760 samples | ~120 ms |
| Target scope resolution, 24 candidates | single query + one point lookup |
| Issue detail with evidence, trend, room context and meter | 5 queries |

Where it breaks first, and the intended fix:

1. **Evaluator is single-process.** At ~100 buildings the pass time grows
   linearly. Fix: partition by rule or by property and run several workers;
   evaluator state is already keyed `(rule_key, equipment_id)`, so the work
   shards cleanly with no shared state.
2. **Scope resolution filters in Python.** Deliberate, so every exclusion can
   carry a reason. Above a few thousand assets this pushes into SQL, keeping the
   Python path for the explainability preview only.
3. **`discover_ontology` returns the whole facet set.** This is the first thing
   to break the AI path at portfolio scale. Fix: scoped, paginated discovery.
4. **Portfolio overview assembles the whole tree per request.** Fix: a cached
   projection refreshed on ingest, or per-property lazy loading.

## Known limitations

- No authentication or authorisation. Every API call is anonymous; `actor` is a
  supplied string, not an authenticated identity. This is the largest gap
  between this and a production system.
- No TLS, secret management, or rate limiting.
- Single-process evaluator, single broker partition consumer.
- No alert delivery, notification or work-order integration.
- No data retention, downsampling or continuous aggregates configured on the
  hypertable.
- The UI is not internationalised and has had no accessibility audit.
- The simulator's `--buildings` selector parses device ids for operator
  convenience. It is the one place ids are parsed, it affects only which rows are
  published, and no semantics depend on it.
