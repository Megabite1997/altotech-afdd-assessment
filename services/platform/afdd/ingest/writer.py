"""Ingestion: validate, resolve identity, deduplicate, persist.

The contract this module keeps:

* A duplicate event has **no second business effect**. Identity is claimed with
  an insert on the event-id primary key inside the same transaction that writes
  the observations, so the claim and the effect succeed or fail together.
* An **older observation is still stored as history** but never replaces a
  newer current value.
* Nothing is silently dropped. Unknown devices, unknown points, blank values and
  invalid values all land in `ingest_reject` or as a non-GOOD observation, and
  are counted on the health endpoint.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from ..events import QUALITY_GOOD, parse_timestamp

log = logging.getLogger(__name__)

REQUIRED_FIELDS = ("event_id", "source_record_id", "device_id", "observed_at", "readings")


@dataclass
class IngestResult:
    status: str
    event_id: str | None = None
    device_id: str | None = None
    reason: str | None = None
    accepted_readings: int = 0
    total_readings: int = 0
    rejects: list[dict] = field(default_factory=list)
    lateness_seconds: float | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("accepted", "partial")


class PointResolver:
    """(device_id, source_name) -> canonical point identity, from the ontology."""

    def __init__(self, cursor) -> None:
        self._cur = cursor
        self._points: dict[tuple[str, str], str] = {}
        self._devices: set[str] = set()
        self.refresh()

    def refresh(self) -> None:
        self._cur.execute("SELECT point_id, equipment_id, source_name FROM point_registry")
        self._points = {(r["equipment_id"], r["source_name"]): r["point_id"] for r in self._cur.fetchall()}
        self._devices = {eq for eq, _ in self._points}

    def known_device(self, device_id: str) -> bool:
        return device_id in self._devices

    def point_id(self, device_id: str, source_name: str) -> str | None:
        return self._points.get((device_id, source_name))


def _reject(cur, *, event_id, source_record_id, device_id, point_ref, reason, detail, observed_at):
    cur.execute(
        """
        INSERT INTO ingest_reject (event_id, source_record_id, device_id, point_ref,
                                   reason_code, detail, observed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (event_id, source_record_id, device_id, point_ref, reason, Jsonb(detail), observed_at),
    )


def ingest_event(cur, payload: dict, resolver: PointResolver, received_at: datetime | None = None) -> IngestResult:
    """Persist one telemetry event. Caller owns the transaction boundary."""
    received_at = received_at or datetime.now(timezone.utc)

    missing = [f for f in REQUIRED_FIELDS if not payload.get(f)]
    if missing:
        _reject(
            cur,
            event_id=None,
            source_record_id=payload.get("source_record_id"),
            device_id=payload.get("device_id"),
            point_ref=None,
            reason="malformed_envelope",
            detail={"missing_fields": missing},
            observed_at=None,
        )
        return IngestResult("rejected", reason="malformed_envelope")

    event_id = payload["event_id"]
    device_id = payload["device_id"]
    source_record_id = payload["source_record_id"]
    try:
        observed_at = parse_timestamp(payload["observed_at"])
    except ValueError:
        _reject(
            cur,
            event_id=event_id,
            source_record_id=source_record_id,
            device_id=device_id,
            point_ref=None,
            reason="invalid_observed_at",
            detail={"observed_at": payload.get("observed_at")},
            observed_at=None,
        )
        return IngestResult("rejected", event_id, device_id, "invalid_observed_at")

    lateness = (received_at - observed_at).total_seconds()
    readings = payload.get("readings") or []

    # --- identity claim: this is what makes a duplicate a no-op --------------
    cur.execute(
        """
        INSERT INTO ingest_event (event_id, source_record_id, device_id, device_kind,
                                  observed_at, produced_at, ingested_at, status,
                                  readings_total, lateness_seconds, payload)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'accepted', %s, %s, %s)
        ON CONFLICT (event_id) DO NOTHING
        """,
        (
            event_id,
            source_record_id,
            device_id,
            payload.get("device_kind"),
            observed_at,
            payload.get("produced_at"),
            received_at,
            len(readings),
            lateness,
            Jsonb(payload),
        ),
    )
    if cur.rowcount == 0:
        # Already seen. Record the sighting for observability, change nothing else.
        cur.execute(
            """
            INSERT INTO ingest_reject (event_id, source_record_id, device_id, reason_code,
                                       detail, observed_at)
            VALUES (%s, %s, %s, 'duplicate_event', %s, %s)
            """,
            (event_id, source_record_id, device_id, Jsonb({"suppressed": True}), observed_at),
        )
        return IngestResult("duplicate", event_id, device_id, "duplicate_event")

    # --- device identity ----------------------------------------------------
    if not resolver.known_device(device_id):
        cur.execute(
            "UPDATE ingest_event SET status = 'rejected', reason_code = %s WHERE event_id = %s",
            ("unknown_device", event_id),
        )
        _reject(
            cur,
            event_id=event_id,
            source_record_id=source_record_id,
            device_id=device_id,
            point_ref=None,
            reason="unknown_device",
            detail={"hint": "device is not present in the ontology registry"},
            observed_at=observed_at,
        )
        return IngestResult("rejected", event_id, device_id, "unknown_device", 0, len(readings))

    # --- readings -----------------------------------------------------------
    rows: list[tuple] = []
    rejects: list[dict] = []
    for reading in readings:
        source_name = reading.get("source_name")
        point_id = resolver.point_id(device_id, source_name) if source_name else None
        if point_id is None:
            rejects.append({"source_name": source_name, "reason": "unknown_point"})
            _reject(
                cur,
                event_id=event_id,
                source_record_id=source_record_id,
                device_id=device_id,
                point_ref=source_name,
                reason="unknown_point",
                detail={"hint": "point is not registered for this device"},
                observed_at=observed_at,
            )
            continue
        quality = reading.get("quality", QUALITY_GOOD)
        value = reading.get("value")
        value_number = float(value) if isinstance(value, (int, float)) else None
        value_text = value if isinstance(value, str) else None
        rows.append((point_id, observed_at, value_number, value_text, quality, event_id, received_at))

    if rows:
        # History: an out-of-order arrival is still stored at its own timestamp.
        cur.executemany(
            """
            INSERT INTO observation (point_id, observed_at, value_number, value_text,
                                     quality, event_id, ingested_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (point_id, observed_at) DO NOTHING
            """,
            rows,
        )
        # Current value: guarded so a late arrival can never move the clock back.
        cur.executemany(
            """
            INSERT INTO point_current (point_id, observed_at, value_number, value_text,
                                       quality, event_id, ingested_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (point_id) DO UPDATE
               SET observed_at = EXCLUDED.observed_at,
                   value_number = EXCLUDED.value_number,
                   value_text = EXCLUDED.value_text,
                   quality = EXCLUDED.quality,
                   event_id = EXCLUDED.event_id,
                   ingested_at = EXCLUDED.ingested_at,
                   updated_at = now()
             WHERE EXCLUDED.observed_at > point_current.observed_at
            """,
            rows,
        )

    status = "partial" if rejects else "accepted"
    cur.execute(
        "UPDATE ingest_event SET status = %s, readings_accepted = %s WHERE event_id = %s",
        (status, len(rows), event_id),
    )
    return IngestResult(status, event_id, device_id, None, len(rows), len(readings), rejects, lateness)
