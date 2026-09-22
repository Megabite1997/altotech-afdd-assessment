# Source Data Guide

The files represent information that a software team might receive from project and IoT teams. They are deliberately source-oriented rather than application-oriented.

## Building and equipment files

### spaces.csv

- `space_id`: Stable source identifier.
- `space_type`: Building, Floor, HVAC Zone, or Room.
- `parent_space_id`: Source containment relationship.
- `usage_type`: Office, hotel, tenant, guest, lobby, plant-room, or occupied-room context.
- `property_type`: Populated on building rows.

### equipment.csv

- `installed_space_id`: Physical location of the equipment.
- `served_space_id`: HVAC zone served by an AHU.
- `measurement_scope_id`: Room or floor represented by a sensor or meter.

These are source facts. Decide how they should be represented with Brickschema and stored in your platform.

### datapoint-reference.csv

Defines the owner, meaning, expected data type, unit, and expected observation interval for each source point. It does not prescribe database columns or API fields.

## Telemetry files

The telemetry CSVs contain device snapshots. You decide whether one row becomes one device event, multiple point events, or another representation.

- `source_record_id` is an upstream record identifier. Decide whether and how it participates in event identity.
- `observed_at` is the timestamp recorded for the observation.
- File row order represents source delivery order and is not guaranteed to match timestamp order.
- A blank measurement means that the source did not report that value in that snapshot.
- An absent device row means that no observation was provided for that expected interval.

The files intentionally include a small number of the following conditions:

- A duplicate source record.
- A late or out-of-order observation.
- A short missing interval.
- A snapshot missing a required measurement.
- An unknown equipment identifier.
- A temperature deviation shorter than the AFDD duration.
- A sustained temperature deviation.
- A temperature deviation while an AHU is OFF.
- Recovery after a sustained deviation.
- A smaller deviation suitable for demonstrating a local rule override.

Your documentation should explain how your system detects, stores, rejects, or exposes these situations.
