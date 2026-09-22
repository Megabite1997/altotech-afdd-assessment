"""The behaviours the product promises, tested against the shipped evaluator.

Each test maps to a line in source-pack/required-behaviors.md.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from afdd.evaluator.core import (
    MODE_FAULT,
    MODE_INSUFFICIENT,
    MODE_NORMAL,
    MODE_OFF,
    MODE_PENDING,
    EvalConfig,
    EvalState,
    Sample,
    run_series,
)

T0 = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)


def cfg(**overrides) -> EvalConfig:
    base = dict(threshold=3.0, duration_seconds=900, max_input_age_seconds=180, recovery_seconds=300)
    base.update(overrides)
    return EvalConfig(**base)


def sample(minute: int, sat: float | None, setpoint: float | None = 14.0, run: str | None = "ON",
           sat_quality="GOOD", sp_quality="GOOD", run_quality="GOOD") -> Sample:
    values, qualities = {}, {}
    if run is not None:
        values["run_status"], qualities["run_status"] = run, run_quality
    if sat is not None:
        values["supply_air_temperature"], qualities["supply_air_temperature"] = sat, sat_quality
    else:
        qualities["supply_air_temperature"] = sat_quality if sat_quality != "GOOD" else "MISSING"
    if setpoint is not None:
        values["supply_air_temperature_setpoint"] = setpoint
        qualities["supply_air_temperature_setpoint"] = sp_quality
    else:
        qualities["supply_air_temperature_setpoint"] = sp_quality if sp_quality != "GOOD" else "MISSING"
    return Sample(observed_at=T0 + timedelta(minutes=minute), values=values, qualities=qualities)


def opened(events):
    return [e for e in events if e.type == "issue_opened"]


def closed(events):
    return [e for e in events if e.type == "issue_closed"]


# --- 1. normal data ---------------------------------------------------------

def test_normal_data_creates_no_issue():
    series = [sample(m, 14.0 + (m % 3) * 0.1) for m in range(60)]
    state, events = run_series(series, cfg())
    assert opened(events) == []
    assert state.mode == MODE_NORMAL


# --- 2. short deviation -----------------------------------------------------

def test_deviation_shorter_than_duration_creates_no_issue():
    """14 minutes of 4.1 degC deviation: one minute short of qualifying."""
    series = [sample(m, 18.1) for m in range(14)] + [sample(m, 14.0) for m in range(14, 25)]
    _, events = run_series(series, cfg())
    assert opened(events) == []


def test_deviation_exactly_at_duration_opens_one_issue():
    series = [sample(m, 18.4) for m in range(21)]
    state, events = run_series(series, cfg())
    triggers = opened(events)
    assert len(triggers) == 1
    assert triggers[0].at == T0 + timedelta(minutes=15)
    assert triggers[0].detail["trigger_started_at"] == T0
    assert triggers[0].detail["qualifying_seconds"] == 900
    assert state.mode == MODE_FAULT


# --- 3. sustained deviation and recovery ------------------------------------

def test_recovery_closes_the_issue_after_the_configured_normal_period():
    series = [sample(m, 18.4) for m in range(21)] + [sample(m, 14.0) for m in range(21, 30)]
    state, events = run_series(series, cfg())
    assert len(opened(events)) == 1
    closes = closed(events)
    assert len(closes) == 1
    assert closes[0].detail["reason"] == "recovered"
    # Deviation ends at minute 20, normal resumes at 21, recovery is 5 minutes.
    assert closes[0].at == T0 + timedelta(minutes=26)
    assert state.open_issue is False


def test_recurrence_is_a_new_issue_not_a_reopen():
    series = (
        [sample(m, 18.4) for m in range(21)]
        + [sample(m, 14.0) for m in range(21, 40)]
        + [sample(m, 18.4) for m in range(40, 61)]
    )
    state, events = run_series(series, cfg())
    triggers = opened(events)
    assert len(triggers) == 2
    assert triggers[0].detail["recurrence_index"] == 0
    assert triggers[1].detail["recurrence_index"] == 1
    assert len(closed(events)) == 1


# --- 4. OFF, missing and outdated data --------------------------------------

def test_deviation_while_off_never_opens_an_issue():
    series = [sample(m, 18.5, run="OFF") for m in range(40)]
    state, events = run_series(series, cfg())
    assert opened(events) == []
    assert state.mode == MODE_OFF


def test_turning_off_mid_window_resets_the_timer():
    series = (
        [sample(m, 18.4) for m in range(10)]
        + [sample(10, 18.4, run="OFF")]
        + [sample(m, 18.4) for m in range(11, 30)]
    )
    _, events = run_series(series, cfg())
    triggers = opened(events)
    assert len(triggers) == 1
    # The window restarts at minute 11, so the trigger lands at minute 26, not 15.
    assert triggers[0].detail["trigger_started_at"] == T0 + timedelta(minutes=11)
    assert triggers[0].at == T0 + timedelta(minutes=26)


def test_turning_off_closes_an_open_issue():
    series = [sample(m, 18.4) for m in range(20)] + [sample(20, 18.4, run="OFF")]
    _, events = run_series(series, cfg())
    closes = closed(events)
    assert len(closes) == 1
    assert closes[0].detail["reason"] == "operating_state_changed"


def test_missing_setpoint_suspends_evaluation_and_cannot_open_an_issue():
    series = [sample(m, 18.4, setpoint=None) for m in range(40)]
    state, events = run_series(series, cfg())
    assert opened(events) == []
    assert state.mode == MODE_INSUFFICIENT


def test_missing_input_mid_window_resets_the_timer():
    series = (
        [sample(m, 18.4) for m in range(10)]
        + [sample(10, 18.4, setpoint=None)]
        + [sample(m, 18.4) for m in range(11, 30)]
    )
    _, events = run_series(series, cfg())
    assert opened(events)[0].detail["trigger_started_at"] == T0 + timedelta(minutes=11)
    assert opened(events)[0].at == T0 + timedelta(minutes=26)


def test_a_gap_longer_than_max_input_age_breaks_the_window():
    """Old readings cannot be stitched across a hole to manufacture 15 minutes."""
    series = [sample(m, 18.4) for m in range(8)] + [sample(m, 18.4) for m in range(18, 26)]
    _, events = run_series(series, cfg())
    assert opened(events) == []
    resets = [e for e in events if e.type == "window_reset" and e.detail.get("reason") == "observation_gap"]
    assert len(resets) == 1


def test_invalid_reading_is_treated_as_insufficient_not_as_a_value():
    series = [sample(m, 18.4, sat_quality="INVALID") for m in range(40)]
    state, events = run_series(series, cfg())
    assert opened(events) == []
    assert state.mode == MODE_INSUFFICIENT


def test_stale_feed_at_evaluation_time_reports_insufficient_data():
    series = [sample(m, 14.0) for m in range(10)]
    state, events = run_series(series, cfg(), evaluation_time=T0 + timedelta(minutes=30))
    assert state.mode == MODE_INSUFFICIENT
    assert any(e.type == "stale_data" for e in events)


def test_an_open_issue_is_not_closed_by_losing_the_data():
    """Losing sight of a fault is not the same as the fault clearing."""
    series = [sample(m, 18.4) for m in range(21)] + [sample(m, 18.4, setpoint=None) for m in range(21, 30)]
    state, events = run_series(series, cfg())
    assert len(opened(events)) == 1
    assert closed(events) == []
    assert state.open_issue is True
    assert any(e.type == "evidence_gap" for e in events)


# --- 5. thresholds and overrides --------------------------------------------

def test_deviation_below_threshold_does_not_trigger():
    series = [sample(m, 16.5) for m in range(40)]  # 2.5 degC against a 3 degC limit
    _, events = run_series(series, cfg())
    assert opened(events) == []


def test_the_same_series_triggers_under_a_tighter_override_threshold():
    series = [sample(m, 16.5) for m in range(40)]
    _, events = run_series(series, cfg(threshold=2.0, threshold_source="override:building-b"))
    triggers = opened(events)
    assert len(triggers) == 1
    assert triggers[0].at == T0 + timedelta(minutes=15)


def test_duration_override_changes_when_the_issue_opens():
    series = [sample(m, 18.4) for m in range(40)]
    _, events = run_series(series, cfg(duration_seconds=1800))
    assert opened(events)[0].at == T0 + timedelta(minutes=30)


def test_pending_state_is_visible_before_the_issue_opens():
    series = [sample(m, 18.4) for m in range(10)]
    state, _ = run_series(series, cfg())
    assert state.mode == MODE_PENDING
    assert state.open_issue is False


# --- 6. determinism ---------------------------------------------------------

def test_evaluation_is_deterministic_over_repeated_runs():
    series = [sample(m, 18.4) for m in range(21)] + [sample(m, 14.0) for m in range(21, 30)]
    first = [(e.type, e.at) for _, e in [(0, e) for e in run_series(series, cfg())[1]]]
    second = [(e.type, e.at) for e in run_series(series, cfg())[1]]
    assert first == second


def test_resuming_from_stored_state_matches_a_single_pass():
    series = [sample(m, 18.4) for m in range(21)]
    whole_state, whole_events = run_series(series, cfg())

    resumed = EvalState()
    split_events = []
    for chunk in (series[:9], series[9:]):
        _, events = run_series(chunk, cfg(), state=resumed)
        split_events.extend(events)

    assert [(e.type, e.at) for e in whole_events] == [(e.type, e.at) for e in split_events]
    assert resumed.mode == whole_state.mode
    assert resumed.condition_since == whole_state.condition_since


@pytest.mark.parametrize(
    "operator,left,right,threshold,expected",
    [
        ("abs_difference_gt", 10.0, 14.0, 3.0, True),
        ("abs_difference_gt", 13.0, 14.0, 3.0, False),
        ("difference_gt", 18.0, 14.0, 3.0, True),
        ("difference_gt", 10.0, 14.0, 3.0, False),
        ("value_gt", 1200.0, None, 1000.0, True),
    ],
)
def test_operators(operator, left, right, threshold, expected):
    from afdd.evaluator.core import Operand

    config = cfg(
        operator=operator,
        threshold=threshold,
        duration_seconds=0,
        left=Operand(role="supply_air_temperature"),
        right=Operand(role="supply_air_temperature_setpoint") if right is not None else Operand(constant=0.0),
        operating_role=None,
    )
    series = [Sample(T0, {"supply_air_temperature": left,
                          "supply_air_temperature_setpoint": right},
                     {"supply_air_temperature": "GOOD", "supply_air_temperature_setpoint": "GOOD"})]
    _, events = run_series(series, config)
    assert bool(opened(events)) is expected
