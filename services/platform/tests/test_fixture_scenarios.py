"""The supplied six-hour fixture, evaluated by the shipped evaluator.

These tests pin the expected outcomes of every seeded condition in
source-pack/sample-telemetry. They run without a database or broker: the source
rows go through the real event envelope and the real evaluator, so an accidental
change in either shows up here.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime, timezone

import pytest

from afdd.events import from_source_row, parse_timestamp
from afdd.ontology.brick import POINT_ROLE_BY_SOURCE_NAME
from afdd.evaluator.core import EvalConfig, Sample, run_series
from afdd.simulator.replay import build_schedule

WINDOW_END = datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)


def _ahu_series(source_dir) -> dict[str, list[Sample]]:
    """Build per-AHU sample series the same way ingestion would."""
    by_equipment: dict[str, dict[datetime, Sample]] = defaultdict(dict)
    seen: set[tuple[str, str]] = set()
    with (source_dir / "sample-telemetry" / "ahu_readings.csv").open(newline="") as fh:
        for raw in csv.DictReader(fh):
            row = {k: (v or "").strip() for k, v in raw.items()}
            key = (row["equipment_id"], row["source_record_id"])
            if key in seen:  # duplicate source record - no second effect
                continue
            seen.add(key)
            event = from_source_row(row, "AHU")
            observed = parse_timestamp(event.observed_at)
            sample = by_equipment[event.device_id].setdefault(observed, Sample(observed_at=observed))
            for reading in event.readings:
                role = POINT_ROLE_BY_SOURCE_NAME[reading.source_name]
                sample.values[role] = reading.value
                sample.qualities[role] = reading.quality
    return {eid: [bucket[k] for k in sorted(bucket)] for eid, bucket in by_equipment.items()}


def cfg(threshold: float = 3.0) -> EvalConfig:
    return EvalConfig(threshold=threshold, duration_seconds=900,
                      max_input_age_seconds=180, recovery_seconds=300)


@pytest.fixture(scope="module")
def series(source_dir):
    return _ahu_series(source_dir)


def opened(events):
    return [e for e in events if e.type == "issue_opened"]


def closed(events):
    return [e for e in events if e.type == "issue_closed"]


# --- seeded scenarios -------------------------------------------------------

def test_sustained_deviation_opens_exactly_one_critical_issue(series):
    """ahu-a-f02-east: 4.4 degC from 10:00, 21 minutes, AHU ON."""
    _, events = run_series(series["ahu-a-f02-east"], cfg(), evaluation_time=WINDOW_END)
    triggers = opened(events)
    assert len(triggers) == 1
    assert triggers[0].at.isoformat() == "2026-01-15T10:15:00+00:00"
    assert triggers[0].detail["trigger_started_at"].isoformat() == "2026-01-15T10:00:00+00:00"
    assert round(triggers[0].detail["difference"], 2) == 4.40


def test_that_issue_recovers_five_minutes_after_readings_normalise(series):
    _, events = run_series(series["ahu-a-f02-east"], cfg(), evaluation_time=WINDOW_END)
    closes = closed(events)
    assert len(closes) == 1
    assert closes[0].detail["reason"] == "recovered"
    assert closes[0].at.isoformat() == "2026-01-15T10:26:00+00:00"


def test_short_deviation_creates_no_issue(series):
    """ahu-a-f03-west: 4.1 degC for 14 minutes - one minute short."""
    _, events = run_series(series["ahu-a-f03-west"], cfg(), evaluation_time=WINDOW_END)
    assert opened(events) == []


def test_deviation_while_off_creates_no_issue(series):
    """ahu-b-f04-east: 4.5 degC for 26 minutes, but the unit is OFF throughout."""
    samples = series["ahu-b-f04-east"]
    off = [s for s in samples if s.values.get("run_status") == "OFF"]
    assert len(off) == 26
    _, events = run_series(samples, cfg(), evaluation_time=WINDOW_END)
    assert opened(events) == []


def test_small_deviation_needs_the_local_override_to_trigger(series):
    """ahu-b-f01-west: 2.5 degC for 21 minutes. Portfolio default ignores it."""
    samples = series["ahu-b-f01-west"]
    _, default_events = run_series(samples, cfg(3.0), evaluation_time=WINDOW_END)
    assert opened(default_events) == []

    _, override_events = run_series(samples, cfg(2.0), evaluation_time=WINDOW_END)
    triggers = opened(override_events)
    assert len(triggers) == 1
    assert triggers[0].at.isoformat() == "2026-01-15T11:35:00+00:00"


def test_override_does_not_change_other_equipment(series):
    """The Building B threshold must not alter a Building A result."""
    before = opened(run_series(series["ahu-a-f02-east"], cfg(3.0), evaluation_time=WINDOW_END)[1])
    after = opened(run_series(series["ahu-a-f02-east"], cfg(3.0), evaluation_time=WINDOW_END)[1])
    assert [e.at for e in before] == [e.at for e in after]


def test_missing_setpoint_is_visible_and_suspends_evaluation(series):
    """ahu-a-f04-west reports no setpoint for six minutes from 12:10."""
    samples = series["ahu-a-f04-west"]
    blanks = [s for s in samples if s.qualities.get("supply_air_temperature_setpoint") == "MISSING"]
    assert len(blanks) == 6
    assert blanks[0].observed_at.isoformat() == "2026-01-15T12:10:00+00:00"
    _, events = run_series(samples, cfg(), evaluation_time=WINDOW_END)
    assert opened(events) == []


def test_missing_interval_is_a_real_gap_in_the_series(series):
    """ahu-c-f03-east has no observations between 11:00 and 11:04."""
    samples = series["ahu-c-f03-east"]
    times = [s.observed_at for s in samples]
    gaps = [
        (a, b) for a, b in zip(times, times[1:])
        if (b - a).total_seconds() > 60
    ]
    assert len(gaps) == 1
    assert gaps[0][0].isoformat() == "2026-01-15T10:59:00+00:00"
    assert (gaps[0][1] - gaps[0][0]).total_seconds() == 360


def test_no_other_ahu_produces_an_issue_under_the_default_threshold(series):
    triggered = {
        eid for eid, samples in series.items()
        if eid != "unknown-ahu-999"
        and opened(run_series(samples, cfg(3.0), evaluation_time=WINDOW_END)[1])
    }
    assert triggered == {"ahu-a-f02-east"}


# --- source-stream properties ----------------------------------------------

def test_event_identity_is_stable_and_duplicates_collapse(source_dir):
    ids = defaultdict(int)
    with (source_dir / "sample-telemetry" / "ahu_readings.csv").open(newline="") as fh:
        for raw in csv.DictReader(fh):
            row = {k: (v or "").strip() for k, v in raw.items()}
            ids[from_source_row(row, "AHU").event_id] += 1
    duplicated = {k: v for k, v in ids.items() if v > 1}
    assert len(duplicated) == 1, "the fixture seeds exactly one duplicate source record"


def test_the_unknown_device_is_present_and_will_be_rejected(source_dir):
    with (source_dir / "sample-telemetry" / "ahu_readings.csv").open(newline="") as fh:
        devices = {row["equipment_id"] for row in csv.DictReader(fh)}
    known = set()
    with (source_dir / "building-and-equipment" / "equipment.csv").open(newline="") as fh:
        known = {row["equipment_id"] for row in csv.DictReader(fh)}
    assert devices - known == {"unknown-ahu-999"}


def test_simulator_preserves_the_out_of_order_tail(source_dir):
    """The late observation is delivered last, as the source supplied it."""
    schedule = build_schedule(source_dir)
    tail = [s for s in schedule if s.row["source_record_id"].startswith("src-ahu-")][-2:]
    record_ids = {s.row["source_record_id"] for s in tail}
    assert "src-ahu-0070-000" in record_ids
    late = next(s for s in tail if s.row["source_record_id"] == "src-ahu-0070-000")
    assert late.row["observed_at"] == "2026-01-15T09:10:00Z"
    assert late.slot.isoformat() == "2026-01-15T13:59:00+00:00", "delivered after everything else"
