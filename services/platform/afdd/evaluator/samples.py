"""Turn stored observations into role-keyed sample series for the evaluator."""

from __future__ import annotations

import statistics
from bisect import bisect_right
from datetime import datetime

from .. import db
from ..rules import scope as scope_module
from .core import Sample

QUALITY_GOOD = "GOOD"


def _fetch(equipment_ids: list[str], roles: list[str], start: datetime | None, end: datetime):
    if not equipment_ids or not roles:
        return []
    return db.query(
        """
        SELECT pr.equipment_id,
               o.observed_at,
               pe.metadata ->> 'role' AS role,
               o.value_number,
               o.value_text,
               o.quality
          FROM observation o
          JOIN point_registry pr  ON pr.point_id = o.point_id
          JOIN ontology_entity pe ON pe.entity_id = o.point_id
         WHERE pr.equipment_id = ANY(%(eq)s)
           AND pe.metadata ->> 'role' = ANY(%(roles)s)
           AND (%(start)s::timestamptz IS NULL OR o.observed_at > %(start)s)
           AND o.observed_at <= %(end)s
         ORDER BY pr.equipment_id, o.observed_at
        """,
        {"eq": equipment_ids, "roles": roles, "start": start, "end": end},
    )


def build(
    equipment_ids: list[str],
    roles: list[str],
    start: datetime | None,
    end: datetime,
) -> dict[str, list[Sample]]:
    """Group observations into one ordered Sample list per equipment."""
    grouped: dict[str, dict[datetime, Sample]] = {eid: {} for eid in equipment_ids}
    for row in _fetch(equipment_ids, roles, start, end):
        bucket = grouped.setdefault(row["equipment_id"], {})
        sample = bucket.get(row["observed_at"])
        if sample is None:
            sample = Sample(observed_at=row["observed_at"])
            bucket[row["observed_at"]] = sample
        value = row["value_number"] if row["value_number"] is not None else row["value_text"]
        sample.values[row["role"]] = value
        sample.qualities[row["role"]] = row["quality"]
    return {eid: [bucket[k] for k in sorted(bucket)] for eid, bucket in grouped.items()}


class ZoneAggregate:
    """Aggregated room readings per HVAC zone, for cross-entity operands.

    Backs rules of the shape "compare return-air temperature with the room
    temperature of the spaces this AHU serves".
    """

    def __init__(self, zone_ids: list[str], role: str, start: datetime | None, end: datetime, aggregate: str = "mean"):
        self.role = role
        self.aggregate = aggregate
        self._series: dict[str, tuple[list[datetime], list[float]]] = {}
        reducer = {"mean": statistics.fmean, "max": max, "min": min}[aggregate]
        for zone_id in set(filter(None, zone_ids)):
            point_ids = scope_module.zone_room_points(zone_id, role)
            if not point_ids:
                continue
            rows = db.query(
                """
                SELECT observed_at, value_number
                  FROM observation
                 WHERE point_id = ANY(%(points)s)
                   AND quality = 'GOOD' AND value_number IS NOT NULL
                   AND (%(start)s::timestamptz IS NULL OR observed_at > %(start)s)
                   AND observed_at <= %(end)s
                 ORDER BY observed_at
                """,
                {"points": point_ids, "start": start, "end": end},
            )
            by_time: dict[datetime, list[float]] = {}
            for row in rows:
                by_time.setdefault(row["observed_at"], []).append(row["value_number"])
            times = sorted(by_time)
            self._series[zone_id] = (times, [reducer(by_time[t]) for t in times])

    def at(self, zone_id: str | None, when: datetime, max_age_seconds: float) -> tuple[float | None, str]:
        """Most recent aggregate at or before `when`, if it is fresh enough."""
        if not zone_id or zone_id not in self._series:
            return None, "ABSENT"
        times, values = self._series[zone_id]
        idx = bisect_right(times, when) - 1
        if idx < 0:
            return None, "ABSENT"
        if (when - times[idx]).total_seconds() > max_age_seconds:
            return None, "STALE"
        return values[idx], QUALITY_GOOD


def attach_zone_operand(
    samples_by_equipment: dict[str, list[Sample]],
    zone_by_equipment: dict[str, str | None],
    aggregate: ZoneAggregate,
    role_key: str,
    max_age_seconds: float,
) -> None:
    """Inject the zone aggregate into each sample under a synthetic role key."""
    for equipment_id, samples in samples_by_equipment.items():
        zone_id = zone_by_equipment.get(equipment_id)
        for sample in samples:
            value, quality = aggregate.at(zone_id, sample.observed_at, max_age_seconds)
            sample.values[role_key] = value
            sample.qualities[role_key] = quality
