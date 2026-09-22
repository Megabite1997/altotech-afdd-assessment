"""Deterministic AFDD evaluation.

A pure state machine over a time-ordered sample series. No database, no clock,
no I/O - the same function backs live evaluation, backtesting and unit tests,
so a test proves the behaviour the product actually ships.

Timing decisions implemented here (all documented in docs/03-afdd-behavior.md):

* The window advances on **device-recorded observation time**, never on platform
  receipt time.
* The condition must hold **continuously**. Any sample where it is false resets
  the window, and so does a gap longer than `max_input_age_seconds` - old
  readings cannot be stitched across a hole to manufacture 15 minutes.
* An AHU turning **OFF** resets the window and closes an open issue: a unit that
  is not running is not running badly.
* **Missing, invalid or stale** input suspends evaluation into
  `insufficient_data`. It never opens an issue, and it never closes one either -
  losing sight of a fault is not the same as the fault clearing.
* **Recovery** requires the condition to stay false for `recovery_seconds`.
* A **recurrence** is a new issue with a link back, never a reopened one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

QUALITY_GOOD = "GOOD"

MODE_NORMAL = "normal"
MODE_PENDING = "pending"
MODE_FAULT = "fault"
MODE_INSUFFICIENT = "insufficient_data"
MODE_OFF = "off"


@dataclass
class Operand:
    role: str | None = None
    constant: float | None = None
    scope: str = "same_equipment"
    aggregate: str = "mean"


@dataclass
class EvalConfig:
    threshold: float
    duration_seconds: int = 900
    max_input_age_seconds: int = 180
    recovery_seconds: int = 300
    severity: str = "Critical"
    operator: str = "abs_difference_gt"
    unit: str = "degC"
    left: Operand = field(default_factory=lambda: Operand(role="supply_air_temperature"))
    right: Operand = field(default_factory=lambda: Operand(role="supply_air_temperature_setpoint"))
    operating_role: str | None = "run_status"
    operating_equals: str = "ON"
    threshold_source: str = "rule_default"


@dataclass
class Sample:
    """One observation instant for one equipment, already resolved by role."""

    observed_at: datetime
    values: dict[str, Any] = field(default_factory=dict)
    qualities: dict[str, str] = field(default_factory=dict)

    def quality(self, role: str) -> str:
        return self.qualities.get(role, "ABSENT")

    def good(self, role: str) -> bool:
        return self.quality(role) == QUALITY_GOOD and self.values.get(role) is not None


@dataclass
class EvalState:
    mode: str = MODE_NORMAL
    condition_since: datetime | None = None
    normal_since: datetime | None = None
    last_observed_at: datetime | None = None
    open_issue: bool = False
    recurrence_index: int = 0
    last_difference: float | None = None
    last_reason: str | None = None

    def copy(self) -> "EvalState":
        return EvalState(**self.__dict__)


@dataclass
class Transition:
    type: str
    at: datetime
    detail: dict = field(default_factory=dict)


def _metric(cfg: EvalConfig, left: float, right: float | None) -> float:
    if cfg.operator == "abs_difference_gt":
        return abs(left - (right or 0.0))
    if cfg.operator in ("difference_gt", "difference_lt"):
        return left - (right or 0.0)
    return left


def _compare(cfg: EvalConfig, metric: float) -> bool:
    if cfg.operator in ("abs_difference_gt", "difference_gt", "value_gt"):
        return metric > cfg.threshold
    if cfg.operator in ("difference_lt", "value_lt"):
        return metric < cfg.threshold
    raise ValueError(f"unsupported operator {cfg.operator!r}")


def _operand_value(sample: Sample, operand: Operand) -> tuple[float | None, str]:
    if operand.constant is not None:
        return operand.constant, QUALITY_GOOD
    role = operand.role or ""
    if not sample.good(role):
        return None, sample.quality(role)
    try:
        return float(sample.values[role]), QUALITY_GOOD
    except (TypeError, ValueError):
        return None, "INVALID"


def step(state: EvalState, sample: Sample, cfg: EvalConfig) -> list[Transition]:
    """Advance the state machine by one observation. Mutates and returns events."""
    events: list[Transition] = []
    t = sample.observed_at

    gap_seconds = (
        (t - state.last_observed_at).total_seconds() if state.last_observed_at else 0.0
    )
    window_broken_by_gap = bool(state.last_observed_at) and gap_seconds > cfg.max_input_age_seconds

    left, left_quality = _operand_value(sample, cfg.left)
    right, right_quality = _operand_value(sample, cfg.right)

    missing: list[str] = []
    if left is None:
        missing.append(f"{cfg.left.role or 'left'}:{left_quality}")
    if right is None:
        missing.append(f"{cfg.right.role or 'right'}:{right_quality}")

    operating_value = None
    if cfg.operating_role:
        if not sample.good(cfg.operating_role):
            missing.append(f"{cfg.operating_role}:{sample.quality(cfg.operating_role)}")
        else:
            operating_value = sample.values[cfg.operating_role]

    if missing:
        if state.condition_since is not None:
            events.append(
                Transition("window_reset", t, {"reason": "insufficient_data", "inputs": missing})
            )
        state.condition_since = None
        state.normal_since = None
        state.mode = MODE_INSUFFICIENT
        state.last_reason = "insufficient_data"
        state.last_difference = None
        if state.open_issue:
            # The fault is not proven gone; it is merely unobservable.
            events.append(Transition("evidence_gap", t, {"inputs": missing}))
        state.last_observed_at = t
        return events

    if cfg.operating_role and operating_value != cfg.operating_equals:
        if state.condition_since is not None:
            events.append(
                Transition(
                    "window_reset",
                    t,
                    {"reason": "operating_state_changed", "value": operating_value},
                )
            )
        state.condition_since = None
        state.normal_since = None
        state.mode = MODE_OFF
        state.last_reason = "not_in_operating_state"
        state.last_difference = None
        if state.open_issue:
            events.append(
                Transition("issue_closed", t, {"reason": "operating_state_changed", "value": operating_value})
            )
            state.open_issue = False
            state.recurrence_index += 1
        state.last_observed_at = t
        return events

    metric = _metric(cfg, left, right)
    state.last_difference = metric
    condition = _compare(cfg, metric)

    if condition:
        state.normal_since = None
        if state.condition_since is None:
            state.condition_since = t
            events.append(Transition("window_started", t, {"difference": metric}))
        elif window_broken_by_gap:
            events.append(
                Transition(
                    "window_reset",
                    t,
                    {"reason": "observation_gap", "gap_seconds": gap_seconds},
                )
            )
            state.condition_since = t
        elapsed = (t - state.condition_since).total_seconds()
        if not state.open_issue and elapsed >= cfg.duration_seconds:
            events.append(
                Transition(
                    "issue_opened",
                    t,
                    {
                        "trigger_started_at": state.condition_since,
                        "qualifying_seconds": elapsed,
                        "difference": metric,
                        "threshold": cfg.threshold,
                        "severity": cfg.severity,
                        "recurrence_index": state.recurrence_index,
                    },
                )
            )
            state.open_issue = True
        state.mode = MODE_FAULT if state.open_issue else MODE_PENDING
        state.last_reason = "condition_true"
    else:
        if state.condition_since is not None:
            events.append(Transition("window_reset", t, {"reason": "condition_false"}))
        state.condition_since = None
        state.mode = MODE_NORMAL
        state.last_reason = "condition_false"
        if state.open_issue:
            if state.normal_since is None:
                state.normal_since = t
            if (t - state.normal_since).total_seconds() >= cfg.recovery_seconds:
                events.append(
                    Transition(
                        "issue_closed",
                        t,
                        {
                            "reason": "recovered",
                            "normal_since": state.normal_since,
                            "difference": metric,
                        },
                    )
                )
                state.open_issue = False
                state.recurrence_index += 1
                state.normal_since = None

    state.last_observed_at = t
    return events


def finalize(state: EvalState, evaluation_time: datetime, cfg: EvalConfig) -> list[Transition]:
    """Apply freshness at evaluation time.

    A series can end with a perfectly healthy last sample and still be stale: the
    device stopped reporting. That must show as insufficient data, not as normal.
    """
    if state.last_observed_at is None:
        state.mode = MODE_INSUFFICIENT
        state.last_reason = "no_data"
        return [Transition("no_data", evaluation_time, {})]
    age = (evaluation_time - state.last_observed_at).total_seconds()
    if age > cfg.max_input_age_seconds:
        state.condition_since = None
        state.mode = MODE_INSUFFICIENT
        state.last_reason = "stale_data"
        return [
            Transition(
                "stale_data",
                evaluation_time,
                {"age_seconds": age, "max_input_age_seconds": cfg.max_input_age_seconds},
            )
        ]
    return []


def run_series(
    samples: list[Sample],
    cfg: EvalConfig,
    state: EvalState | None = None,
    evaluation_time: datetime | None = None,
) -> tuple[EvalState, list[Transition]]:
    """Convenience wrapper: fold `step` over a series, then `finalize`."""
    state = state or EvalState()
    events: list[Transition] = []
    for sample in samples:
        events.extend(step(state, sample, cfg))
    if evaluation_time is not None:
        events.extend(finalize(state, evaluation_time, cfg))
    return state, events
