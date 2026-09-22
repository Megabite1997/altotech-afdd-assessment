"""Historical backtesting.

Runs a draft rule over a past window using the *same* evaluator as live
monitoring, so a backtest cannot quietly disagree with production. Results are
tagged with a `backtest_run_id`, which every live query filters out - a backtest
never touches live issues, evaluator state, or activation.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from psycopg.types.json import Jsonb

from .. import db
from ..rules import scope as scope_module
from ..rules.schema import RuleDefinition
from . import samples as sample_builder
from .worker import ZONE_ROLE_PREFIX, _affected, _config_for, _roles_needed
from .core import EvalState, finalize, step


def run(rule: RuleDefinition, start: datetime, end: datetime, label: str = "") -> dict:
    run_id = str(uuid.uuid4())
    preview = scope_module.resolve(rule)
    roles = _roles_needed(rule)
    equipment_ids = [m.equipment_id for m in preview.matched]
    zone_by_equipment = {m.equipment_id: m.served_zone_id for m in preview.matched}

    series = sample_builder.build(equipment_ids, roles, start, end)

    cond = rule.logic.condition
    if cond.right.scope == "served_zone_rooms" and cond.right.role:
        aggregate = sample_builder.ZoneAggregate(
            list(zone_by_equipment.values()), cond.right.role, start, end, cond.right.aggregate
        )
        sample_builder.attach_zone_operand(
            series, zone_by_equipment, aggregate, ZONE_ROLE_PREFIX + cond.right.role,
            rule.logic.max_input_age_seconds,
        )

    predicted: list[dict] = []
    insufficient_intervals: list[dict] = []
    totals = {"opened": 0, "closed": 0, "evaluated": 0, "samples": 0}

    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO backtest_run (run_id, rule_key, label, definition, range_start, range_end)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (run_id, rule.key, label, Jsonb(rule.model_dump(mode="json")), start, end),
            )
            for match in preview.matched:
                cfg = _config_for(rule, match)
                state = EvalState()
                equipment_series = series.get(match.equipment_id, [])
                totals["samples"] += len(equipment_series)
                totals["evaluated"] += 1
                open_row: dict | None = None
                gap_from: datetime | None = None
                affected = _affected(match.equipment_id)

                for sample in equipment_series:
                    for event in step(state, sample, cfg):
                        if event.type == "issue_opened":
                            issue_id = str(uuid.uuid4())
                            open_row = {
                                "issue_id": issue_id,
                                "equipment_id": match.equipment_id,
                                "opened_at": event.at,
                                "trigger_started_at": event.detail["trigger_started_at"],
                                "difference": event.detail["difference"],
                                "threshold": cfg.threshold,
                                "threshold_source": cfg.threshold_source,
                                "severity": cfg.severity,
                                "closed_at": None,
                                "close_reason": None,
                            }
                            cur.execute(
                                """
                                INSERT INTO issue (issue_id, rule_key, rule_version, equipment_id,
                                                   severity, state, opened_at, trigger_started_at,
                                                   trigger_observed_at, qualifying_seconds,
                                                   calculated_difference, threshold, threshold_source,
                                                   unit, affected_zone_id, affected_room_ids,
                                                   installed_space_id, rule_snapshot, effective_config,
                                                   backtest_run_id)
                                VALUES (%s, %s, 0, %s, %s, 'open', %s, %s, %s, %s, %s, %s, %s, %s,
                                        %s, %s, %s, %s, %s, %s)
                                """,
                                (
                                    issue_id, rule.key, match.equipment_id, cfg.severity, event.at,
                                    event.detail["trigger_started_at"], event.at,
                                    event.detail["qualifying_seconds"], event.detail["difference"],
                                    cfg.threshold, cfg.threshold_source, cfg.unit,
                                    affected["zone_id"], affected["room_ids"],
                                    affected["installed_space_id"],
                                    Jsonb(rule.model_dump(mode="json")),
                                    Jsonb(match.effective_config), run_id,
                                ),
                            )
                            totals["opened"] += 1
                        elif event.type == "issue_closed" and open_row:
                            cur.execute(
                                "UPDATE issue SET state = 'closed', closed_at = %s, close_reason = %s WHERE issue_id = %s",
                                (event.at, event.detail["reason"], open_row["issue_id"]),
                            )
                            open_row["closed_at"] = event.at
                            open_row["close_reason"] = event.detail["reason"]
                            predicted.append(open_row)
                            open_row = None
                            totals["closed"] += 1
                    # Track contiguous insufficient-data intervals for the report.
                    if state.mode == "insufficient_data":
                        gap_from = gap_from or sample.observed_at
                    elif gap_from is not None:
                        insufficient_intervals.append(
                            {
                                "equipment_id": match.equipment_id,
                                "from": gap_from.isoformat(),
                                "to": sample.observed_at.isoformat(),
                            }
                        )
                        gap_from = None

                if open_row:
                    predicted.append(open_row)
                if gap_from is not None and equipment_series:
                    insufficient_intervals.append(
                        {
                            "equipment_id": match.equipment_id,
                            "from": gap_from.isoformat(),
                            "to": equipment_series[-1].observed_at.isoformat(),
                        }
                    )
                finalize(state, end, cfg)

            summary = {
                **totals,
                "matched": len(preview.matched),
                "excluded": len(preview.excluded),
                "predicted_issues": [
                    {**p, "opened_at": p["opened_at"].isoformat(),
                     "trigger_started_at": p["trigger_started_at"].isoformat(),
                     "closed_at": p["closed_at"].isoformat() if p["closed_at"] else None}
                    for p in predicted
                ],
                "insufficient_data_intervals": insufficient_intervals,
            }
            cur.execute("UPDATE backtest_run SET summary = %s WHERE run_id = %s", (Jsonb(summary), run_id))
        conn.commit()

    return {"run_id": run_id, "rule_key": rule.key, "label": label,
            "range": {"start": start.isoformat(), "end": end.isoformat()}, **summary}


def list_runs(limit: int = 25) -> list[dict]:
    return db.query(
        """
        SELECT run_id, rule_key, label, range_start, range_end, created_at, summary
          FROM backtest_run ORDER BY created_at DESC LIMIT %s
        """,
        (limit,),
    )


def get_run(run_id: str) -> dict | None:
    run = db.query_one("SELECT * FROM backtest_run WHERE run_id = %s", (run_id,))
    if run is None:
        return None
    run["issues"] = db.query(
        """
        SELECT issue_id, equipment_id, severity, state, opened_at, closed_at, close_reason,
               calculated_difference, threshold, threshold_source, affected_zone_id, affected_room_ids
          FROM issue WHERE backtest_run_id = %s ORDER BY opened_at
        """,
        (run_id,),
    )
    return run
