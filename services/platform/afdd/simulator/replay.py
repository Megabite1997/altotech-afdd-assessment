"""Mock building-device simulator.

Streams the supplied source readings to the broker the way field devices would:
in source delivery order, at a configurable cadence, with device-recorded
observation timestamps left untouched.

Two independent knobs:

* ``interval``  - wall-clock spacing between publish batches (15s or 60s).
* ``speedup``   - how much *source* time advances per second of wall clock.
                  ``speedup=60`` replays six source hours in six minutes.

Observation timestamps are never rewritten. They are device facts, and the
whole point of separating observed-at from received-at is that the platform
must cope with the difference. The evaluator therefore runs on a data clock
(see evaluator/worker.py), not on wall-clock time.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..config import settings
from ..events import TelemetryEvent, from_source_row, parse_timestamp

log = logging.getLogger(__name__)

FILES = [
    ("sample-telemetry/ahu_readings.csv", "AHU", "equipment_id"),
    ("sample-telemetry/power_readings.csv", "METER", "meter_id"),
    ("sample-telemetry/iaq_readings.csv", "IAQ", "device_id"),
]


@dataclass(order=True)
class Scheduled:
    """A source row with the delivery slot it should be published in."""

    slot: datetime
    file_order: int
    row: dict = None  # type: ignore[assignment]
    kind: str = ""


def _building_of(device_id: str) -> str:
    # Used only for the operator-facing `--buildings` selector, never for
    # semantics. Ontology relationships decide meaning everywhere else.
    parts = device_id.split("-")
    return f"building-{parts[1]}" if len(parts) > 2 else ""


def build_schedule(source_dir: Path, buildings: list[str] | None = None) -> list[Scheduled]:
    """Merge the three source files into one delivery-ordered stream.

    A row's delivery slot is the running maximum observation time within its own
    file. That preserves the source's out-of-order tail (a late observation and
    an unknown-device record are delivered last, exactly as supplied) while
    normal rows stay in timestamp order.
    """
    scheduled: list[Scheduled] = []
    order = 0
    for rel, kind, id_column in FILES:
        path = source_dir / rel
        running: datetime | None = None
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                row = {k: (v or "").strip() for k, v in row.items()}
                if buildings and _building_of(row[id_column]) not in buildings:
                    continue
                observed = parse_timestamp(row["observed_at"])
                running = observed if running is None or observed > running else running
                scheduled.append(Scheduled(slot=running, file_order=order, row=row, kind=kind))
                order += 1
    scheduled.sort()
    return scheduled


def events(source_dir: Path, buildings: list[str] | None = None):
    """Yield (slot, event) pairs in delivery order. Used by replay and tests."""
    for item in build_schedule(source_dir, buildings):
        yield item.slot, from_source_row(item.row, item.kind)


class Simulator:
    def __init__(
        self,
        source_dir: Path | None = None,
        interval: int | None = None,
        speedup: float | None = None,
        buildings: list[str] | None = None,
        loop: bool | None = None,
    ) -> None:
        self.source_dir = source_dir or settings.source_dir
        self.interval = interval or settings.sim_interval_seconds
        self.speedup = speedup or settings.sim_speedup
        self.buildings = buildings if buildings is not None else settings.sim_building_filter
        self.loop = settings.sim_loop if loop is None else loop
        self.published = 0
        self.batches = 0

    async def run(self, producer=None) -> int:
        from aiokafka import AIOKafkaProducer

        own = producer is None
        if own:
            producer = AIOKafkaProducer(
                bootstrap_servers=settings.kafka_bootstrap,
                value_serializer=lambda v: json.dumps(v).encode(),
                key_serializer=lambda k: k.encode(),
                acks="all",
                enable_idempotence=True,
                linger_ms=20,
            )
            await producer.start()
        try:
            while True:
                await self._run_once(producer)
                if not self.loop:
                    break
                log.info("simulator looping over source window again")
        finally:
            if own:
                await producer.stop()
        return self.published

    async def _run_once(self, producer) -> None:
        schedule = build_schedule(self.source_dir, self.buildings)
        if not schedule:
            log.warning("no source rows matched the selection")
            return
        log.info(
            "simulator starting: %d rows, interval=%ss speedup=%sx buildings=%s",
            len(schedule),
            self.interval,
            self.speedup,
            self.buildings or "all",
        )
        origin = schedule[0].slot
        wall_start = time.monotonic()
        batch: list[Scheduled] = []
        batch_slot = origin

        async def flush() -> None:
            nonlocal batch
            if not batch:
                return
            for item in batch:
                event = from_source_row(item.row, item.kind)
                await self._publish(producer, event)
            self.batches += 1
            batch = []

        for item in schedule:
            source_elapsed = (item.slot - origin).total_seconds()
            # Batch everything that belongs in the same emission window.
            if (item.slot - batch_slot).total_seconds() >= self.interval:
                await flush()
                batch_slot = item.slot
            batch.append(item)
            target_wall = wall_start + source_elapsed / max(self.speedup, 0.001)
            drift = target_wall - time.monotonic()
            if drift > 0:
                await flush()
                batch_slot = item.slot
                await asyncio.sleep(min(drift, 5.0))
        await flush()
        log.info("simulator finished: published %d events in %d batches", self.published, self.batches)

    async def _publish(self, producer, event: TelemetryEvent) -> None:
        # Keyed by device so a device's observations keep their relative order
        # inside one partition. Ordering across devices is not required.
        await producer.send_and_wait(
            settings.telemetry_topic,
            value=event.to_dict(),
            key=event.device_id,
        )
        self.published += 1
        if self.published % 2000 == 0:
            log.info("published %d events", self.published)
