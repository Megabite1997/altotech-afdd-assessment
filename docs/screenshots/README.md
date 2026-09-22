# Screenshot walkthrough

The demonstration sequence, captured from a live run of the stack against the
supplied six-hour fixture. Step-by-step narration is in the
[demonstration guide](../06-demonstration-guide.md).

---

### 1. Portfolio overview — `01-portfolio.png`

Property → floor → zone → room. Both clocks are shown in the banner, with a note
that freshness is measured against **data time** because the evaluator runs on a
data clock. Every value carries its unit, observed time, freshness and quality.
Floor electricity meters sit on the floor row; IAQ devices sit in the rooms.

### 2. Pipeline health — `02-pipeline-health.png`

The answer to "is the data behind this dashboard real?". 30,235 accepted events,
**1 rejected**, **1 duplicate suppressed**, **1 out-of-order arrival**, and the
six `MISSING` setpoint observations on `ahu-a-f04-west` from 12:10 to 12:15 —
stored rather than dropped. The ontology counts match the supplied pack exactly:
93 spaces, 84 equipment, 288 points, 1,092 relationships.

### 3. Issue list — `03-issues.png`

Both seeded issues. Note the **threshold source** column: `ahu-a-f02-east`
triggered at the portfolio default of 3.00 °C, `ahu-b-f01-west` at 2.00 °C from
`override:building-b-tighter-threshold`. The same deviation size occurs nowhere
else in the portfolio; only Building B's local setting turns it into an issue.

### 4. Issue investigation — `04-issue-investigation.png`

The core explainability screen.

- **Why it triggered** — supply-air temperature against its setpoint, the ±3 °C
  tolerance band, the qualifying window shaded from 10:00, the trigger line at
  10:15 and the close marker at 10:26. The run-status strip along the bottom is
  green throughout: the unit was running. Data gaps are drawn as gaps, never
  interpolated.
- **Potentially affected spaces** — the relationship path rendered as chips:
  `brick:feeds` to the zone, `brick:hasPart` to the two tenant rooms, and
  `brick:hasLocation` to the plant room **struck through and labelled not
  affected**.
- **Context, not cause** — room IAQ and floor electricity, explicitly separated
  from the measurements that triggered the issue.
- Below the fold: 21 evidence observations, the lifecycle log, and the rule
  snapshot exactly as it was when the issue opened.

### 5. Rule detail and preview — `05-rule-detail.png`

Plain-language intent at the top, then **target scope and fault logic side by
side as separate configuration**, with the local override and its engineering
note. Below: the editable definition with quick-adjust buttons, the matched
assets with their relationship paths and effective thresholds, exclusions with
reasons, the version list, and per-equipment evaluator state.

### 6. AI-assisted rule authoring — `06-rule-authoring.png`

A natural-language request turned into a reviewable draft: stop reason
`draft_ready`, the provider and model named openly, the 16 assets it would
select, and the full tool trace with per-call latency. The only controls are
**Reject** and **Confirm & activate** — the agent cannot activate anything
itself.

---

Captured at 1440 px wide against a live stack. To reproduce:

```bash
docker compose up --build
open http://localhost:3000
```
