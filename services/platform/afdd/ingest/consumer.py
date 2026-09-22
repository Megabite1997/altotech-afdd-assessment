"""Kafka consumer: at-least-once delivery made safe by idempotent writes.

Offsets are committed only after the batch is durably written, so a crash
replays the batch. That is harmless because `ingest_event` claims the event id
first: the replayed events land as duplicates with no second effect.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError

from .. import db
from ..config import settings
from .writer import IngestResult, PointResolver, ingest_event

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 4
BACKOFF_SECONDS = [0.5, 2.0, 5.0]


class Ingestor:
    def __init__(self) -> None:
        self.counters = {
            "accepted": 0,
            "partial": 0,
            "duplicate": 0,
            "rejected": 0,
            "retried": 0,
            "dead_lettered": 0,
        }

    def handle_batch(self, payloads: list[dict]) -> list[IngestResult]:
        """One transaction per batch. Resolver is rebuilt per batch so ontology
        changes (a new building onboarded) take effect without a restart."""
        results: list[IngestResult] = []
        with db.connection() as conn:
            with conn.cursor() as cur:
                resolver = PointResolver(cur)
                for payload in payloads:
                    result = ingest_event(cur, payload, resolver)
                    self.counters[result.status] = self.counters.get(result.status, 0) + 1
                    results.append(result)
            conn.commit()
        return results

    async def run(self) -> None:
        consumer = AIOKafkaConsumer(
            settings.telemetry_topic,
            bootstrap_servers=settings.kafka_bootstrap,
            group_id=settings.consumer_group,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            value_deserializer=lambda v: json.loads(v.decode()),
            max_poll_records=500,
        )
        await consumer.start()
        log.info(
            "ingest consumer started topic=%s group=%s",
            settings.telemetry_topic,
            settings.consumer_group,
        )
        try:
            while True:
                batches = await consumer.getmany(timeout_ms=1000, max_records=500)
                payloads: list[dict] = []
                for records in batches.values():
                    payloads.extend(r.value for r in records)
                if not payloads:
                    continue
                started = time.monotonic()
                if await self._write_with_retry(payloads):
                    await consumer.commit()
                log.info(
                    "ingest batch size=%d ms=%.0f counters=%s",
                    len(payloads),
                    (time.monotonic() - started) * 1000,
                    self.counters,
                )
        finally:
            await consumer.stop()

    async def _write_with_retry(self, payloads: list[dict]) -> bool:
        for attempt in range(MAX_ATTEMPTS):
            try:
                self.handle_batch(payloads)
                return True
            except Exception as exc:  # noqa: BLE001 - retry any store failure
                if attempt == MAX_ATTEMPTS - 1:
                    log.exception("batch failed after %d attempts, not committing", MAX_ATTEMPTS)
                    self.counters["dead_lettered"] += len(payloads)
                    # Offsets stay uncommitted: the batch is redelivered rather
                    # than silently lost. Idempotent writes make that safe.
                    return False
                self.counters["retried"] += 1
                delay = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                log.warning("batch write failed (%s), retrying in %.1fs", exc, delay)
                await asyncio.sleep(delay)
        return False


async def main() -> None:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db.wait_for_db()
    await _wait_for_broker()
    await Ingestor().run()


async def _wait_for_broker(timeout: float = 120.0) -> None:
    from aiokafka.admin import AIOKafkaAdminClient
    from aiokafka.admin import NewTopic

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        admin = AIOKafkaAdminClient(bootstrap_servers=settings.kafka_bootstrap)
        try:
            await admin.start()
            try:
                await admin.create_topics(
                    [
                        NewTopic(settings.telemetry_topic, settings.topic_partitions, 1),
                        NewTopic(settings.dlq_topic, 1, 1),
                    ]
                )
                log.info("created topics")
            except KafkaError:
                log.info("topics already present")
            await admin.close()
            return
        except Exception as exc:  # pragma: no cover - startup race
            log.info("waiting for broker: %s", exc)
            await asyncio.sleep(2)
    raise RuntimeError("broker not reachable")


if __name__ == "__main__":
    asyncio.run(main())
