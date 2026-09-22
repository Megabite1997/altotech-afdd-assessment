# Understanding Checkpoint

Complete this short document before substantial implementation. Concise diagrams and bullet points are welcome.

## 1. User problem

What problem is the product solving for property engineers and technicians?

## 2. System boundary

What will you implement, and what will you intentionally leave outside the assessment scope?

## 3. Domain interpretation

Explain the difference between:

- An AHU's installed location and served space.
- An HVAC zone and the rooms it contains.
- A device and its telemetry points.
- Missing data and normal operating data.

## 4. Proposed telemetry event

Show your proposed event fields and explain identity, observation time, units, quality, and idempotency.

## 5. Proposed ontology representation

Show how you plan to represent containment, installation, AHU service, measurement scope, and point ownership. Identify which parts follow Brickschema and which parts are application metadata.

## 6. AFDD interpretation

Explain how your 15-minute timing window begins, continues, pauses or resets, triggers, recovers, and recurs.

## 7. Architecture and risks

Provide a high-level component diagram and identify the three most important technical or product risks.

## 8. Assumptions and clarification questions

List assumptions that materially affect behavior. Ask rather than silently guessing when an unanswered question would change the user outcome.
