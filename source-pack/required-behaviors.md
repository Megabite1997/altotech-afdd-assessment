# Required Behaviors

This file describes observable outcomes. It does not prescribe an implementation.

## Telemetry foundation

- Stream the source readings through a configurable mock-device simulator.
- Allow a selectable source interval, such as 15 or 60 seconds, or an acceleration factor for demonstrations.
- Preserve source observation time separately from platform receipt or processing time.
- Store historical readings and maintain a current value for each recognized point.
- Resolve recognized equipment and points through the building model you create.
- Make rejected, duplicated, late, missing, and incomplete data visible.
- A duplicate must not produce a second business effect.
- An older observation may be retained as history but must not replace a newer current value.

## Core AFDD rule

Target AHUs serving tenant areas on selected floors of office buildings.

While an AHU is ON and required readings are recent enough to trust, open one Critical issue when the absolute difference between supply-air temperature and its setpoint is greater than 3°C continuously for 15 minutes.

The implementation must support one property-specific threshold or duration override. Equipment without a required point must be excluded from evaluation.

## Decisions you must make and document

- How recent an input must be to participate in evaluation, using the 60-second expected interval as context.
- What happens to an in-progress timing window when an AHU turns OFF.
- What happens when an input is absent, blank, invalid, or too old.
- How an issue recovers or closes when readings return to normal.
- How a later recurrence is represented.
- How active issues retain the rule version and evidence used when they opened.

Reasonable choices are acceptable when they are consistent, testable, visible to users, and documented.

## Candidate-created verification

Create your own test fixtures and automated tests to demonstrate at least:

- Normal data creates no issue.
- A short deviation creates no issue.
- A sustained deviation opens one issue after the configured duration.
- OFF, missing, or outdated data cannot create a misleading issue.
- Recovery and later recurrence follow the documented lifecycle.
- A local override changes the result for its intended scope only.
- Equipment missing a required point is excluded visibly.
