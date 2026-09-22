# Candidate Starter Pack

This package contains source-style project information for the Senior Full Stack Engineer technical assessment. It describes what exists in the buildings and provides a six-hour telemetry sample. It does not prescribe the architecture of your solution.

## Start here

1. Read `domain-glossary.md`.
2. Review the three files under `building-and-equipment/`.
3. Trace one AHU from its installed location to its served zone and rooms.
4. Review `source-data-guide.md` and the three telemetry files.
5. Read `required-behaviors.md`.
6. Complete `understanding-checkpoint.md` before substantial implementation.

## Portfolio facts

- 3 buildings: Buildings A and B are offices; Building C is a hotel.
- 4 occupied floors per building.
- 2 HVAC zones per floor.
- 2 occupied rooms per zone.
- 1 AHU serves each zone.
- 1 IAQ sensor is installed in each occupied room.
- 1 electricity meter measures each floor.
- Each building also has one lobby and one plant room.

Expected inventory:

- 93 spaces, including buildings, floors, zones, occupied rooms, lobbies, and plant rooms.
- 24 AHUs.
- 48 IAQ sensors.
- 12 electricity meters.
- 288 source datapoints.

## Important boundary

The supplied CSV structure is not a prescribed application schema, streaming-event contract, API contract, or database design. You may normalize, transform, and reorganize it.

You own and should explain:

- The event envelope and event identity strategy.
- Broker topics, partitions, delivery behavior, and replay approach.
- TimescaleDB and application-data schemas.
- Current-state versus historical-data handling.
- Brickschema mapping and ontology storage.
- Simulator design and acceleration controls.
- AFDD rule representation and issue lifecycle.
- Tests, observability, APIs, and user experience.

Readable IDs are provided for debugging. Do not infer building relationships by parsing strings; use the supplied registers and the ontology representation you create.

## Source-data period

- Start: `2026-01-15T08:00:00Z`
- Duration: 6 hours
- Expected observation interval: 60 seconds
- Temperature unit: degrees Celsius
- Timestamps: UTC in RFC 3339 format

The source files contain a small number of deliberate operating and data-quality conditions. Their categories are documented, but their exact locations are not identified.
