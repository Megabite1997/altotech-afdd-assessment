"""Scheduler + persistence around the pure evaluator.

Evaluation runs on a **data clock** by default: `evaluation_time` is the newest
observation time in the store, not wall-clock now. Replaying a six-hour fixture
at 60x therefore produces exactly the same issues, at exactly the same
observation timestamps, every run - which is what makes the demo and the tests
reproducible. Set `EVAL_CLOCK=wall` for a live deployment against live devices.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from .. import db
from ..config import settings
from ..ontology import queries as onto
from ..rules import scope as scope_module
from ..rules.schema import RuleDefinition, validate_definition
from . import samples as sample_builder
from .core import EvalConfig, EvalState, Operand, Sample, finalize, step

log = logging.getLogger(__name__)

ZONE_ROLE_PREFIX = "zone::"


# ---------------------------------------------------------------------------
# clocks
# ---------------------------------------------------------------------------

def data_clock() -> datetime | None:
    row = db.query_one("SELECT max(observed_at) AS t FROM observation")
    return row["t"] if row else None


def evaluation_time() -> datetime:
    if settings.eval_clock == "wall":
        return datetime.now(timezone.utc)
    return data_clock() or datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------

def active_rules() -> list[tuple[str, int, RuleDefinition]]:
    rows = db.query(
        """
        SELECT r.rule_key, r.active_version, v.definition
          FROM rule r
          JOIN rule_version v ON v.rule_key = r.rule_key AND v.version = r.active_version
         WHERE r.status = 'active' AND r.active_version IS NOT NULL
         ORDER BY r.rule_key
        """
    )
    out = []
    for row in rows:
        try:
            out.append((row["rule_key"], row["active_version"], validate_definition(row["definition"])))
        except Exception:  # noqa: BLE001 - a bad stored rule must not stop the worker
            log.exception("rule %s version %s failed validation; skipped", row["rule_key"], row["active_version"])
    return out


def _config_for(rule: RuleDefinition, match) -> EvalConfig:
    effective = match.effective_config
    cond = rule.logic.condition
    left = Operand(role=cond.left.role, constant=cond.left.constant, scope=cond.left.scope)
    right_role = cond.right.role
    if cond.right.scope == "served_zone_rooms" and right_role:
        right_role = ZONE_ROLE_PREFIX + right_role
    right = Operand(
        role=right_role,
        constant=cond.right.constant,
        scope=cond.right.scope,
        aggregate=cond.right.aggregate,
    )
    return EvalConfig(
        threshold=effective["threshold"],
        duration_seconds=effective["duration_seconds"],
        max_input_age_seconds=effective["max_input_age_seconds"],
        recovery_seconds=effective["recovery_seconds"],
        severity=effective["severity"],
        operator=cond.operator,
        unit=cond.unit,
        left=left,
        right=right,
        operating_role=rule.logic.operating_state.role if rule.logic.operating_state else None,
        operating_equals=rule.logic.operating_state.equals if rule.logic.operating_state else "ON",
        threshold_source=effective["source"],
    )


def _roles_needed(rule: RuleDefinition) -> list[str]:
    roles = set(rule.target.require_points)
    if rule.logic.operating_state:
        roles.add(rule.logic.operating_state.role)
    for operand in (rule.logic.condition.left, rule.logic.condition.right):
        if operand.role and operand.scope == "same_equipment":
            roles.add(operand.role)
    return sorted(roles)


# ---------------------------------------------------------------------------
# state persistence
# ---------------------------------------------------------------------------

def _load_states(rule_key: str) -> dict[str, dict]:
    rows = db.query("SELECT * FROM evaluator_state WHERE rule_key = %s", (rule_key,))
    return {r["equipment_id"]: r for r in rows}


def _save_state(cur, rule_key: str, equipment_id: str, state: EvalState, version: int, open_issue_id):
    cur.execute(
        """
        INSERT INTO evaluator_state (rule_key, equipment_id, mode, condition_since, normal_since,
                                     last_observed_at, open_issue_id, recurrence_index, detail, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (rule_key, equipment_id) DO UPDATE
           SET mode = EXCLUDED.mode, condition_since = EXCLUDED.condition_since,
               normal_since = EXCLUDED.normal_since, last_observed_at = EXCLUDED.last_observed_at,
               open_issue_id = EXCLUDED.open_issue_id,
               recurrence_index = EXCLUDED.recurrence_index,
               detail = EXCLUDED.detail, updated_at = now()
        """,
        (
            rule_key,
            equipment_id,
            state.mode,
            state.condition_since,
            state.normal_since,
            state.last_observed_at,
            open_issue_id,
            state.recurrence_index,
            Jsonb(
                {
                    "rule_version": version,
                    "last_difference": state.last_difference,
                    "last_reason": state.last_reason,
                }
            ),
        ),
    )


# ---------------------------------------------------------------------------
# issue persistence
# ---------------------------------------------------------------------------

def _affected(equipment_id: str) -> dict:
    spatial = onto.affected_spaces(equipment_id)
    return {
        "zone_id": (spatial["served_zone"] or {}).get("entity_id"),
        "room_ids": [r["entity_id"] for r in spatial["affected_rooms"]],
        "installed_space_id": (spatial["installed_space"] or {}).get("entity_id"),
    }


def _open_issue(cur, *, rule, version, match, cfg, event, previous_issue_id, backtest_run_id) -> str:
    issue_id = str(uuid.uuid4())
    affected = _affected(match.equipment_id)
    cur.execute(
        """
        INSERT INTO issue (issue_id, rule_key, rule_version, equipment_id, severity, state,
                           opened_at, trigger_started_at, trigger_observed_at, qualifying_seconds,
                           calculated_difference, threshold, threshold_source, unit, data_quality,
                           affected_zone_id, affected_room_ids, installed_space_id,
                           rule_snapshot, effective_config, backtest_run_id,
                           recurrence_of, recurrence_index)
        VALUES (%s, %s, %s, %s, %s, 'open', %s, %s, %s, %s, %s, %s, %s, %s, 'good',
                %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            issue_id,
            rule.key,
            version,
            match.equipment_id,
            cfg.severity,
            event.at,
            event.detail["trigger_started_at"],
            event.at,
            event.detail["qualifying_seconds"],
            event.detail["difference"],
            cfg.threshold,
            cfg.threshold_source,
            cfg.unit,
            affected["zone_id"],
            affected["room_ids"],
            affected["installed_space_id"],
            Jsonb(rule.model_dump(mode="json")),
            Jsonb(match.effective_config),
            backtest_run_id,
            previous_issue_id,
            event.detail.get("recurrence_index", 0),
        ),
    )
    cur.execute(
        "INSERT INTO issue_event (issue_id, at, type, detail) VALUES (%s, %s, 'opened', %s)",
        (issue_id, event.at, Jsonb({k: str(v) for k, v in event.detail.items()})),
    )
    return issue_id


def _write_evidence(cur, issue_id: str, series: list[Sample], cfg: EvalConfig, start, trigger_at, context=timedelta(minutes=5)):
    """Persist the observations that produced the issue, plus leading context."""
    lo = start - context
    rows = []
    for sample in series:
        if not (lo <= sample.observed_at <= trigger_at):
            continue
        left = sample.values.get(cfg.left.role) if cfg.left.role else cfg.left.constant
        right = sample.values.get(cfg.right.role) if cfg.right.role else cfg.right.constant
        difference = None
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            difference = abs(left - right) if cfg.operator == "abs_difference_gt" else left - right
        quality = "GOOD"
        for role in filter(None, [cfg.left.role, cfg.right.role, cfg.operating_role]):
            if sample.quality(role) != "GOOD":
                quality = sample.quality(role)
                break
        rows.append(
            (
                issue_id,
                sample.observed_at,
                left if isinstance(left, (int, float)) else None,
                right if isinstance(right, (int, float)) else None,
                difference,
                sample.values.get(cfg.operating_role) if cfg.operating_role else None,
                quality,
                None,
                sample.observed_at >= start,
            )
        )
    if rows:
        cur.executemany(
            """
            INSERT INTO issue_observation (issue_id, observed_at, supply_air_temp, setpoint,
                                           difference, run_status, quality, freshness_seconds, in_window)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            rows,
        )


def _close_issue(cur, issue_id: str, at, reason: str, detail: dict) -> None:
    cur.execute(
        """
        UPDATE issue SET state = 'closed', closed_at = %s, close_reason = %s
         WHERE issue_id = %s AND state = 'open'
        """,
        (at, reason, issue_id),
    )
    cur.execute(
        "INSERT INTO issue_event (issue_id, at, type, detail) VALUES (%s, %s, 'closed', %s)",
        (issue_id, at, Jsonb({k: str(v) for k, v in detail.items()})),
    )


def _mark_evidence_gap(cur, issue_id: str, at, detail: dict) -> None:
    cur.execute(
        "UPDATE issue SET data_quality = 'evidence_gap' WHERE issue_id = %s AND state = 'open'",
        (issue_id,),
    )
    cur.execute(
        "INSERT INTO issue_event (issue_id, at, type, detail) VALUES (%s, %s, 'evidence_gap', %s)",
        (issue_id, at, Jsonb({k: str(v) for k, v in detail.items()})),
    )


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------

def evaluate_rule(rule_key: str, version: int, rule: RuleDefinition, now: datetime) -> dict:
    started = time.monotonic()
    run_id = str(uuid.uuid4())
    preview = scope_module.resolve(rule)
    states = _load_states(rule_key)
    roles = _roles_needed(rule)

    equipment_ids = [m.equipment_id for m in preview.matched]
    zone_by_equipment = {m.equipment_id: m.served_zone_id for m in preview.matched}

    # Incremental: each equipment resumes from its own last observation.
    earliest = None
    for eid in equipment_ids:
        last = (states.get(eid) or {}).get("last_observed_at")
        if last is None:
            earliest = None
            break
        earliest = last if earliest is None or last < earliest else earliest

    series = sample_builder.build(equipment_ids, roles, earliest, now)

    cond = rule.logic.condition
    if cond.right.scope == "served_zone_rooms" and cond.right.role:
        aggregate = sample_builder.ZoneAggregate(
            list(zone_by_equipment.values()),
            cond.right.role,
            earliest,
            now,
            cond.right.aggregate,
        )
        sample_builder.attach_zone_operand(
            series,
            zone_by_equipment,
            aggregate,
            ZONE_ROLE_PREFIX + cond.right.role,
            rule.logic.max_input_age_seconds,
        )

    opened = closed = evaluated = insufficient = total_samples = 0

    with db.connection() as conn:
        with conn.cursor() as cur:
            for match in preview.matched:
                eid = match.equipment_id
                cfg = _config_for(rule, match)
                stored = states.get(eid)
                state = EvalState()
                open_issue_id = None
                if stored:
                    stored_version = (stored.get("detail") or {}).get("rule_version")
                    if stored_version is not None and stored_version != version:
                        # A rule change starts a fresh evaluation. Issues opened
                        # under the previous version keep that version and close
                        # with an explicit reason rather than silently re-basing.
                        if stored["open_issue_id"]:
                            _close_issue(
                                cur,
                                stored["open_issue_id"],
                                now,
                                "rule_changed",
                                {"from_version": stored_version, "to_version": version},
                            )
                            closed += 1
                        stored = None
                        states.pop(eid, None)
                    else:
                        state = EvalState(
                            mode=stored["mode"],
                            condition_since=stored["condition_since"],
                            normal_since=stored["normal_since"],
                            last_observed_at=stored["last_observed_at"],
                            open_issue=stored["open_issue_id"] is not None,
                            recurrence_index=stored["recurrence_index"],
                        )
                        open_issue_id = stored["open_issue_id"]

                equipment_series = series.get(eid, [])
                # `build` starts from the global earliest cursor; skip anything
                # this equipment has already consumed.
                if state.last_observed_at:
                    equipment_series = [s for s in equipment_series if s.observed_at > state.last_observed_at]
                total_samples += len(equipment_series)

                previous_issue_id = open_issue_id
                for sample in equipment_series:
                    for event in step(state, sample, cfg):
                        if event.type == "issue_opened":
                            open_issue_id = _open_issue(
                                cur,
                                rule=rule,
                                version=version,
                                match=match,
                                cfg=cfg,
                                event=event,
                                previous_issue_id=previous_issue_id if state.recurrence_index else None,
                                backtest_run_id=None,
                            )
                            _write_evidence(
                                cur,
                                open_issue_id,
                                equipment_series,
                                cfg,
                                event.detail["trigger_started_at"],
                                event.at,
                            )
                            opened += 1
                        elif event.type == "issue_closed" and open_issue_id:
                            _close_issue(cur, open_issue_id, event.at, event.detail["reason"], event.detail)
                            previous_issue_id = open_issue_id
                            open_issue_id = None
                            closed += 1
                        elif event.type == "evidence_gap" and open_issue_id:
                            _mark_evidence_gap(cur, open_issue_id, sample.observed_at, event.detail)

                finalize(state, now, cfg)
                if state.mode == "insufficient_data":
                    insufficient += 1
                evaluated += 1
                _save_state(cur, rule_key, eid, state, version, open_issue_id)

            cur.execute(
                """
                INSERT INTO evaluation_run (run_id, rule_key, evaluation_time, duration_ms, targets,
                                            evaluated, excluded, insufficient, opened, closed, samples)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    rule_key,
                    now,
                    (time.monotonic() - started) * 1000,
                    len(preview.matched) + len(preview.excluded),
                    evaluated,
                    len(preview.excluded),
                    insufficient,
                    opened,
                    closed,
                    total_samples,
                ),
            )
        conn.commit()

    return {
        "run_id": run_id,
        "rule_key": rule_key,
        "rule_version": version,
        "evaluation_time": now.isoformat(),
        "matched": len(preview.matched),
        "excluded": len(preview.excluded),
        "evaluated": evaluated,
        "insufficient": insufficient,
        "opened": opened,
        "closed": closed,
        "samples": total_samples,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
    }


def run_once() -> list[dict]:
    if settings.eval_clock == "data" and data_clock() is None:
        # Nothing has been ingested yet. Falling back to wall clock here would
        # stamp a run with an evaluation time that has no relationship to the
        # data, so there is simply nothing to evaluate.
        return []
    now = evaluation_time()
    results = []
    for rule_key, version, rule in active_rules():
        try:
            results.append(evaluate_rule(rule_key, version, rule, now))
        except Exception:  # noqa: BLE001 - one bad rule must not stop the others
            log.exception("rule %s evaluation failed", rule_key)
    return results


def run_forever() -> None:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db.wait_for_db()
    log.info("afdd evaluator started clock=%s tick=%ss", settings.eval_clock, settings.eval_tick_seconds)
    while True:
        for result in run_once():
            if result["samples"] or result["opened"] or result["closed"]:
                log.info("evaluated %s", result)
        time.sleep(settings.eval_tick_seconds)


if __name__ == "__main__":
    run_forever()
