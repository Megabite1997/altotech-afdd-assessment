# Demonstration guide

The hero journey, end to end: an engineer describes a known problem, reviews how
the system interpreted it and which assets it matched, activates the rule,
watches telemetry replay, sees the seeded fault detected, and investigates its
evidence and affected tenant rooms.

Roughly 12 minutes at `SIM_SPEEDUP=60`.

---

## 0. Start

```bash
cp .env.example .env
docker compose up --build
```

Wait for `api` to become healthy, then open:

- Dashboard — <http://localhost:3000>
- API docs — <http://localhost:8000/docs>

`docker compose up` runs migrations, loads the ontology, installs the rules, and
starts the broker, simulator, ingestor, evaluator, API and UI. The simulator
replays six source hours in about six minutes.

To confirm the seed before going further:

```bash
docker compose exec api afdd verify
```

```
check                          actual   expected  result
spaces                             93         93  pass
equipment                          84         84  pass
points                            288        288  pass
```

---

## 1. Foundation — the pipeline is real

**Pipeline health** (<http://localhost:3000/health>).

| Look at | Expect |
|---|---|
| Data time vs platform time | Data time advances through 2026-01-15 08:00 → 14:00 as the replay runs; platform time is now. The panel states that freshness is measured against data time. |
| Ingestion outcomes | `accepted` climbing to **30,235**; `rejected` **1** |
| Rejections | `duplicate_event` **1**, `unknown_device` **1** |
| Incomplete measurements | `ahu-a-f04-west`, **6** `MISSING` observations, 12:10 → 12:15 |
| Devices not reporting | `ahu-c-f03-east` appears briefly around the 11:00–11:04 gap |
| Ontology | 93 spaces, 84 equipment, 288 points, 1,092 relationships |

Three things to say out loud here:

- **The duplicate had no second effect.** The same source record arrived twice;
  the second copy is recorded as a sighting and changed nothing.
- **The unknown device was rejected, not guessed at.** `unknown-ahu-999` is not
  in the ontology, so its reading has no identity and is not stored under one.
- **Missing data stays missing.** The blank setpoint is stored as a `MISSING`
  observation, not dropped and not back-filled.

**Portfolio** (<http://localhost:3000/portfolio>). Expand Building A → Floor 2 →
East Zone. Every value shows its unit, observed time, freshness and quality.
The zone shows its AHU, and the rooms show their IAQ devices; the floor shows
its electricity meter.

Check one device through the API:

```bash
curl -s localhost:8000/api/equipment/ahu-a-f02-east/current | jq
curl -s localhost:8000/api/ontology/equipment/ahu-a-f02-east/affected-spaces | jq
```

The second call is the spatial answer: it feeds `building-a-f02-east`, which has
two rooms, and it is installed in `building-a-plant-room` — returned separately
and labelled `installed_location_not_affected`.

---

## 2. The rule — configuration, not code

**Rules → AHU supply-air temperature deviation** (<http://localhost:3000/rules>).

The intent is shown in plain language. Below it, target scope and fault logic are
displayed as two separate blocks, and the local override is listed with its note.

**Matched assets** shows **16 matched, 8 excluded**. Every exclusion states a
reason: the 8 Building C AHUs are `property_type_not_selected` because Building C
is a hotel. Each matched row shows its relationship path
(`ahu-a-f02-east brick:feeds building-a-f02-east brick:isPartOf building-a-f02 …`)
and its **effective threshold**: 3.0 °C for Building A, **2.0 °C for Building B
via `override:building-b-tighter-threshold`**.

### Show target scope changing independently of fault logic

In the editor, click **target only Building A floor 2**, then **Validate &
preview**. Matched drops from 16 to 2 — and the fault logic is untouched. Click
**target all tenant floors** to restore.

### Show equipment missing a required point being excluded

```bash
docker compose exec api afdd remove-point ahu-b-f03-west-sat-sp
```

Re-run **Validate & preview**: 15 matched, 9 excluded, with a new exclusion:

```json
{"equipment_id": "ahu-b-f03-west", "reason": "missing_required_point",
 "detail": {"missing_roles": ["supply_air_temperature_setpoint"],
            "available_roles": ["alarm_status", "return_air_temperature",
                                "run_status", "supply_air_temperature"]}}
```

The rule refuses to evaluate equipment it cannot evaluate honestly, and says so.
Restore it with:

```bash
docker compose exec api afdd seed
```

---

## 3. The seeded fault

Once the replay passes data time 10:15, **two** issues appear. Both are in the
supplied fixture and both are expected:

| Equipment | Opens (observed) | Deviation | Threshold | Closes | Why it matters |
|---|---|---|---|---|---|
| `ahu-a-f02-east` | **10:15** | 4.40 °C | 3.0 (`rule_default`) | 10:26 `recovered` | The sustained fault |
| `ahu-b-f01-west` | **11:35** | 2.50 °C | 2.0 (`override:building-b-tighter-threshold`) | 11:46 `recovered` | The local override changing the result for its scope only |

**Investigate `ahu-a-f02-east`** (Issues → Investigate).

- **Why it triggered.** The chart shows supply-air temperature against its
  setpoint, the ±3 °C tolerance band, the qualifying window shaded from 10:00,
  the trigger line at 10:15, and the close marker at 10:26. The run-status strip
  along the bottom is green throughout — the unit was running.
- **The numbers.** Trigger window opened 10:00, qualified at 10:15, qualifying
  duration 15m 0s, difference 4.40 °C against a 3.00 °C threshold from
  `rule_default`, rule `ahu-supply-air-deviation` **version 1**.
- **Potentially affected spaces.** The path is rendered as
  `ahu-a-f02-east —brick:feeds→ building-a-f02-east —brick:hasPart→ r01, r02`,
  with `building-a-plant-room` shown struck through as the installed location.
- **Context, not cause.** Room IAQ and floor electricity are in their own panel,
  explicitly labelled as not having taken part in the trigger.
- **Evidence.** 21 observations, 16 of them in the window, each with supply-air
  temperature, setpoint, computed difference, run status and quality. The
  leading context rows make the window start checkable.
- **Rule snapshot.** The definition exactly as it was when the issue opened,
  plus the effective configuration after overrides.

Then open `ahu-b-f01-west` and point at `threshold 2.00 °C, source
override:building-b-tighter-threshold`. Same deviation size occurs nowhere else
in the portfolio; only Building B's local setting turns it into an issue.

### What deliberately did *not* create an issue

| Equipment | Condition | Why no issue |
|---|---|---|
| `ahu-a-f03-west` | 4.1 °C for **14 minutes** from 09:00 | One minute short of the 15-minute duration |
| `ahu-b-f04-east` | 4.5 °C for 26 minutes from 10:30, **AHU OFF** | Not running is not running badly |
| `ahu-a-f04-west` | setpoint **missing** 12:10–12:15 | Insufficient data cannot open an issue |
| `ahu-c-*` | hotel property | Out of target scope |

Check these on **Rules → Evaluator behaviour**, which shows per-equipment mode
(`normal`, `pending`, `off`, `insufficient_data`, `fault`) and the last
evaluation passes.

---

## 4. AI-assisted rule authoring

**Rule authoring** (<http://localhost:3000/agent>). Five example requests are
one click away.

### 4a. Supported

The pre-filled request. Click **Draft a rule**.

- Stop reason `draft_ready`, the tool trace shows `discover_ontology` →
  `preview_target` → `submit_draft`, each with its latency.
- The draft's target and logic are shown as JSON, plus the 16 assets it would
  select with their effective thresholds.
- **Nothing is active.** Click **Confirm & activate**, supply that it is you,
  and only then does a new rule version appear — authored `agent+operator`, with
  an activation row naming the actor and `source = agent-confirmation`.

### 4b. Ambiguous → it asks rather than guesses

Click the **ambiguous** example ("The tenant floors feel too warm lately"). Stop
reason `clarification_needed`, with the question and two suggested answers.
Nothing was drafted or saved.

### 4c. Unsupported → it refuses rather than approximates

Click **unsupported** (the weather-forecast condition). Stop reason
`unsupported`, with the reason: the ontology exposes no weather, forecast or
occupancy points.

### 4d. No match → it does not invent an asset

Click **no match** (Building Z). Stop reason `no_match`. Inspect the draft: it
contains **no** `building-z` identifier. The agent called `resolve_entities`,
got nothing back, and expressed that as an empty selector.

### 4e. The whole matrix

```bash
docker compose exec api afdd agent evaluate
```

Runs all eight categories — supported, paraphrased, ambiguous, unsupported,
no-match, invented-asset, changed-ontology, recoverable-failure — and prints
expected against actual. Add `--provider anthropic` to run it against a real
model (needs `ANTHROPIC_API_KEY` in `.env`).

---

## 5. Bonus: historical backtesting

Replay a draft over history using the same evaluator, isolated from live issues:

```bash
curl -s -X POST localhost:8000/api/backtests \
  -H 'content-type: application/json' \
  -d '{"rule_key":"ahu-supply-air-deviation",
       "start":"2026-01-15T08:00:00Z","end":"2026-01-15T14:00:00Z",
       "label":"seeded rule, full window"}' | jq
```

Returns the predicted issues (the same two, at the same observation timestamps)
and the insufficient-data intervals, including
`ahu-a-f04-west 12:10 → 12:16`. Live issues are untouched — compare
`GET /api/issues` before and after.

Compare configurations by posting a modified `definition` instead of a
`rule_key`: drop `overrides` to `[]` and the Building B issue disappears from
the prediction.

---

## 6. Tests

```bash
cd services/platform && pip install -e ".[dev]" && pytest -q
```

50 tests, no database or broker required. `tests/test_evaluator_core.py` covers
the timing semantics; `tests/test_fixture_scenarios.py` pins every seeded
condition in the supplied telemetry against the shipped evaluator.

---

## Running without Docker

Everything except the broker path runs against a plain PostgreSQL:

```bash
export DATABASE_URL=postgresql://afdd:afdd@localhost:5432/afdd
export SOURCE_DIR=$PWD/source-pack
cd services/platform && pip install -e ".[dev]"
afdd seed          # migrate + ontology + rules
afdd replay        # load the fixtures directly, same envelope and writer
afdd evaluate --once
afdd verify
uvicorn afdd.api.app:app --port 8000
```

`afdd replay` uses the same event envelope and the same ingestion writer as the
broker path, so duplicate suppression, unknown-device rejection and the guarded
current-value upsert all behave identically — it just skips the network. The
telemetry table falls back from a hypertable to a plain table with a BRIN index
if the TimescaleDB extension is absent, and the migration says so.

---

## AI assistance note

This project was built with Claude Code as a coding assistant. It was used for
scaffolding the service skeletons, the React components, the SQL schema and the
first drafts of these documents; every design decision in
[technical decisions](05-technical-decisions.md) was made and defended by me.

**Two suggestions corrected or rejected:**

1. **Rejected: closing an open issue when its input data disappears.** The first
   generated evaluator treated missing input as a recovery and closed the issue.
   That is exactly backwards for a diagnostics product — a failing sensor would
   silently clear a real fault. The shipped behaviour keeps the issue open and
   flags `data_quality = evidence_gap`; `test_an_open_issue_is_not_closed_by_losing_the_data`
   pins it.

2. **Corrected: an empty selector list meaning "match everything".** The first
   scope resolver used `if selector.property_ids and ...`, which makes `null`
   and `[]` behave identically. That surfaced through the agent case matrix: the
   only way for the agent to express "the building you named does not exist" was
   to write a fabricated id into the rule. `null` now means unset and `[]` means
   nothing-selected, which is what lets the `no_match` and `changed-ontology`
   cases pass with no invented identifiers in the draft.

A third correction worth noting: two of the generated timing tests asserted the
wrong expected trigger time after a window reset. The evaluator was right and
the tests were wrong; they now assert both the restarted window start *and* the
resulting trigger instant.
