# Understanding checkpoint

Written before implementation, kept as a record of the interpretation the build
followed. Where the build later refined a position, the refinement is noted.

## 1. User problem

A property engineer already knows their buildings' recurring problems. What they
lack is a way to turn that knowledge into monitoring that is **safe** (it does
not cry wolf on bad data), **reusable** (one rule, many buildings, local
settings where needed), and **explainable** (a technician can see why it fired
and which tenant spaces are involved).

The product answers two questions and supports a third:

- *What equipment needs attention, and why?* — an issue with preserved evidence.
- *Which occupied spaces may be affected?* — the served-space path, not the
  installation room.
- *How can the rule be improved?* — preview, local overrides, versions, and
  backtesting against history.

A technician's real cost is a wasted site visit. Every design decision that
looked like a trade-off between "detect more" and "be sure" resolved toward
being sure, and toward making uncertainty visible rather than hiding it.

## 2. System boundary

**In scope.** Mock-device simulator; broker; ingestion with ontology-resolved
identity and idempotency; TimescaleDB history plus current values; Brickschema
ontology registry and spatial traversal; configurable versioned AFDD rules with
a deterministic evaluator; issue lifecycle with evidence; REST API with OpenAPI;
React operations dashboard; an agentic natural-language rule-authoring workflow
with human confirmation; historical backtesting; Docker Compose for all of it.

**Deliberately out of scope.** Physical gateways and real protocols (BACnet,
Modbus); production cloud deployment; equipment control of any kind; work-order
integration; 3D visualisation; autonomous rule activation; authentication and
authorisation; alert delivery.

The authentication gap is the largest distance between this and production, and
is recorded as such in [technical decisions](05-technical-decisions.md).

## 3. Domain interpretation

**Installed location vs served space.** An AHU sits in the plant room and serves
an HVAC zone. These are different relationships (`brick:hasLocation` versus
`brick:feeds`) and they answer different questions: where to send the technician
to inspect the unit, versus which tenants are affected. Conflating them would
send someone to the wrong room. The affected scope of an issue never includes
the installation room, and the UI shows it struck through.

**HVAC zone vs the rooms it contains.** A zone is the unit of air distribution;
rooms are the occupied spaces inside it. An AHU serves a *zone*; the rooms are
affected by consequence, through `brick:hasPart`. Keeping the zone in the middle
is what makes the affected-room list derived rather than hard-coded.

**Device vs telemetry point.** A device is a thing that owns measurements; a
point is one measurable property with a type, a unit and an expected interval.
Identity resolution is `(device, source_name) → point_id`, which is why renaming
a source point is an ontology change rather than a data migration. Rules refer
to point *roles*, never to point ids, which is why one rule works across three
buildings.

**Missing data vs normal operating data.** A blank in the source means "not
reported", which is not the same as a valid reading, and neither is the same as
an out-of-range value. The envelope distinguishes `GOOD`, `MISSING` and
`INVALID`, and a fourth state — *stale* — is derived at evaluation time from
`observed_at` against the evaluation clock. Missing data is stored, not dropped,
so its absence is itself visible evidence.

## 4. Proposed telemetry event

One device snapshot becomes one event:

```json
{
  "schema_version": "1.0",
  "event_id": "uuid5(NAMESPACE, 'ahu-a-f02-east|src-ahu-0120-004')",
  "source_record_id": "src-ahu-0120-004",
  "device_id": "ahu-a-f02-east",
  "device_kind": "AHU",
  "observed_at": "2026-01-15T10:00:00Z",
  "produced_at": "2026-09-22T17:45:03.221Z",
  "producer": "device-simulator",
  "readings": [
    {"source_name": "RUN",    "value": "ON",  "value_type": "enum",   "unit": null,   "quality": "GOOD"},
    {"source_name": "SAT",    "value": 18.4,  "value_type": "number", "unit": "degC", "quality": "GOOD"},
    {"source_name": "SAT_SP", "value": null,  "value_type": "number", "unit": "degC", "quality": "MISSING", "reason": "blank_in_source"}
  ]
}
```

- **Identity.** Derived, not assigned: `uuid5(namespace, device|source_record)`.
  Any producer replaying the same source record produces the same id, so
  idempotency needs no coordination.
- **Observation time.** `observed_at` is the device's, never rewritten.
  `produced_at` is when the simulator emitted it; `ingested_at` is stamped by
  the consumer. All three are kept.
- **Units.** Carried per reading and also held in the point registry, which is
  authoritative. The UI never renders a number without its unit.
- **Quality.** `GOOD` / `MISSING` / `INVALID` assigned at envelope construction,
  from blankness, parseability, enum domain and plausibility bounds.
- **Idempotency.** The consumer claims `event_id` as a primary key in the same
  transaction that writes the observations. A duplicate therefore has no second
  effect, and a redelivered batch after a crash is harmless.

## 5. Proposed ontology representation

| Fact | Representation | Standard or application |
|---|---|---|
| Containment | `brick:hasPart` / `brick:isPartOf`, Building → Floor → Zone → Room | Brick |
| Installation | `brick:hasLocation` / `brick:isLocationOf` | Brick |
| AHU service | `brick:feeds` / `brick:isFedBy`, AHU → HVAC_Zone | Brick |
| Point ownership | `brick:hasPoint` / `brick:isPointOf` | Brick |
| Measurement scope | `app:measures` / `app:isMeasuredBy` | **Application** |
| IAQ device | `app:IAQ_Device` | **Application** |
| `property_type`, `usage_type`, `unit`, `value_type`, `expected_interval_seconds`, point `role` | JSONB on the entity | **Application** |

The two application predicates are justified in
[the Brickschema model](02-brickschema-model.md): Brick's `brick:meters` fits
the electricity meter but not an IAQ device that represents a room without
metering it, and Brick has no single class for a multi-sensor room device.
Stretching a Brick term across two meanings would be a false claim about the
standard.

Stored relationally (`ontology_entity`, `ontology_relation` both directions,
`space_closure`, `point_registry`) in the same PostgreSQL instance as the
telemetry and issues, so an issue can join to its ontology path and its
observations in one transaction.

## 6. AFDD interpretation

Stated as a state machine over device observation time. Full detail and the
reasoning behind each choice is in [AFDD behaviour](03-afdd-behavior.md).

- **Begins** at the first observation where the AHU is ON, all required inputs
  are `GOOD`, and `|SAT − setpoint| > threshold`.
- **Continues** while every subsequent observation satisfies the same test.
- **Resets** on any observation where the condition is false, on OFF, on missing
  or invalid input, and on a gap longer than `max_input_age_seconds` (180 s,
  three expected intervals). Old readings cannot be stitched across a hole.
- **Triggers** when `latest_observed_at − window_start ≥ 900 s`. The issue's
  `opened_at` is the observation instant that qualified it, not the wall-clock
  time the worker noticed.
- **Recovers** when the condition stays false for `recovery_seconds` (300 s).
  The issue closes with reason `recovered`.
- **Pauses visibly** — `insufficient_data` — when inputs are absent, invalid or
  stale. This state can never open an issue, and never closes one: an already
  open issue stays open, flagged `evidence_gap`.
- **Recurs** as a *new* issue linked by `recurrence_of` with an incremented
  `recurrence_index`. Closed issues are never reopened.

## 7. Architecture and risks

The component diagram, the three sequence diagrams and the failure table are in
[system architecture](01-system-architecture.md).

**The three risks that mattered most, and how each was handled.**

1. **Silent wrongness in timing.** An AFDD product that opens issues on stale or
   partial data destroys its own credibility, and the failure is invisible — it
   looks like it is working. *Handled by* making the evaluator a pure function
   with no I/O, driving live evaluation, backtesting and tests from the same
   `step`, and pinning every seeded fixture condition to an expected outcome. 50
   tests run with no database.

2. **The AI path becoming a way to bypass validation.** A natural-language
   front door is only safe if it cannot reach anything the API would not allow.
   *Handled by* giving the model seven read-only server-implemented tools and no
   others, re-validating every draft server-side at confirmation rather than
   trusting the stored one, and making activation a separate human-authenticated
   call. The case matrix asserts that no draft contains an identifier that is
   not in the ontology. This is where the one genuine safety bug was found — see
   the empty-selector correction in the
   [demonstration guide](06-demonstration-guide.md).

3. **Ontology drift between buildings.** A model that works for the supplied
   three buildings but requires code changes for the fourth has not solved the
   multi-site problem. *Handled by* rules selecting on roles and relationships
   rather than ids or names, a single idempotent loader that reports integrity
   problems rather than swallowing them, and scope resolution running on every
   evaluation pass so an ontology change shows up in the next preview.

## 8. Assumptions and clarification questions

**Assumptions that materially affect behaviour.** Each is configurable and
visible in the UI, so a reviewer can disagree with the default and change it
without a code change.

1. Input freshness of **180 s** (three expected intervals) for an input to be
   trusted. Tolerates one dropped reading, not a dead feed.
2. Recovery requires **300 s** of normal readings, not a single sample.
3. **OFF closes** an open issue rather than suspending it.
4. **Insufficient data never closes** an open issue.
5. A **rule version change closes** open issues under the old version rather
   than re-basing them.
6. "Selected office-building floors" is implemented as *all* tenant floors by
   default, with `served_floor_ids` available to narrow it. The selector exists
   precisely so the choice is the engineer's, not the developer's.
7. One open issue per (rule, equipment), enforced by a partial unique index.
8. The demonstration override is **Building B at 2.0 °C**, chosen because the
   fixture seeds a sustained 2.5 °C deviation on `ahu-b-f01-west` that the
   portfolio default correctly ignores — so the override's effect is provable
   rather than asserted.

**Questions I would ask before this went to a real site.**

1. When an AHU stops mid-fault, should the issue close or suspend? I chose
   close. In a real portfolio, a unit that is tripping off *because* of the fault
   is a different story from one that was scheduled off, and the distinction
   needs a maintenance-schedule input this dataset does not have.
2. Should an issue on a dead feed escalate after some period? Today it stays
   open and flagged. A real operations team probably wants "unmonitored for
   4 hours" to become its own alert.
3. Who is allowed to confirm an AI-drafted rule, and does it differ from who may
   edit one by hand? The platform records an actor but authenticates nobody;
   this is the first thing I would build next.
4. Is a tenant-facing view in scope later? That would change the affected-space
   model from "rooms" to "tenancies", which is a different relationship and
   worth knowing before the ontology hardens.
