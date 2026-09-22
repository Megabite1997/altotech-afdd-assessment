"""Rule, issue and backtest endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel

from .. import db
from ..evaluator import backtest as backtest_module
from ..evaluator import worker
from ..ontology import queries as onto
from ..rules import scope as scope_module
from ..rules import store as rule_store
from ..rules.schema import OPERATORS, POINT_ROLES, SELECTORS, SEVERITIES, validate_definition

router = APIRouter()


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------

@router.get("/rules/vocabulary", tags=["rules"], summary="Selectors, roles and operators a rule may use")
def vocabulary() -> dict:
    return {
        "selectors": SELECTORS,
        "point_roles": sorted(POINT_ROLES),
        "operators": OPERATORS,
        "severities": list(SEVERITIES),
    }


@router.get("/rules", tags=["rules"])
def list_rules() -> dict:
    return {"rules": rule_store.list_rules()}


@router.get("/rules/{rule_key}", tags=["rules"])
def get_rule(rule_key: str) -> dict:
    rule = rule_store.get_rule(rule_key)
    if rule is None:
        raise HTTPException(404, "unknown rule")
    rule["activations"] = rule_store.activation_history(rule_key)
    return rule


@router.get("/rules/{rule_key}/versions/{version}", tags=["rules"])
def get_rule_version(rule_key: str, version: int) -> dict:
    found = rule_store.get_definition(rule_key, version)
    if found is None:
        raise HTTPException(404, "unknown rule version")
    _, definition = found
    return {"rule_key": rule_key, "version": version, "definition": definition.model_dump(mode="json")}


class SaveRule(BaseModel):
    definition: dict
    notes: str = ""
    created_by: str = "operator"
    activate: bool = False


@router.post("/rules", tags=["rules"], summary="Append a new immutable rule version")
def save_rule(body: SaveRule) -> dict:
    try:
        definition = validate_definition(body.definition)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"invalid rule definition: {exc}") from exc
    version = rule_store.save_version(definition, notes=body.notes, created_by=body.created_by)
    result = {"rule_key": definition.key, "version": version, "activated": False}
    if body.activate:
        rule_store.activate(definition.key, version, actor=body.created_by)
        result["activated"] = True
    return result


@router.post("/rules/preview", tags=["rules"],
             summary="Resolve any draft against the ontology without saving it")
def preview_rule(definition: dict = Body(..., embed=False)) -> dict:
    try:
        parsed = validate_definition(definition)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"invalid rule definition: {exc}") from exc
    preview = scope_module.resolve(parsed)
    return {"rule_key": parsed.key, **preview.as_dict()}


@router.get("/rules/{rule_key}/preview", tags=["rules"], summary="Matched assets for a stored rule")
def preview_stored_rule(rule_key: str, version: int | None = None) -> dict:
    found = rule_store.get_definition(rule_key, version)
    if found is None:
        raise HTTPException(404, "unknown rule")
    resolved_version, definition = found
    preview = scope_module.resolve(definition)
    return {"rule_key": rule_key, "version": resolved_version, **preview.as_dict()}


class Activation(BaseModel):
    version: int
    actor: str = "operator"


@router.post("/rules/{rule_key}/activate", tags=["rules"])
def activate_rule(rule_key: str, body: Activation) -> dict:
    try:
        return rule_store.activate(rule_key, body.version, actor=body.actor)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/rules/{rule_key}/disable", tags=["rules"])
def disable_rule(rule_key: str, actor: str = "operator") -> dict:
    return rule_store.disable(rule_key, actor=actor)


@router.get("/rules/{rule_key}/state", tags=["rules"], summary="Per-equipment evaluator state")
def rule_state(rule_key: str) -> dict:
    return {
        "rule_key": rule_key,
        "state": db.query(
            """
            SELECT s.equipment_id, e.name, s.mode, s.condition_since, s.normal_since,
                   s.last_observed_at, s.open_issue_id, s.recurrence_index, s.detail, s.updated_at
              FROM evaluator_state s
              JOIN ontology_entity e ON e.entity_id = s.equipment_id
             WHERE s.rule_key = %s ORDER BY s.equipment_id
            """,
            (rule_key,),
        ),
        "runs": db.query(
            """
            SELECT run_id, evaluation_time, started_at, duration_ms, targets, evaluated,
                   excluded, insufficient, opened, closed, samples
              FROM evaluation_run WHERE rule_key = %s ORDER BY started_at DESC LIMIT 20
            """,
            (rule_key,),
        ),
    }


@router.post("/evaluate/run-once", tags=["rules"], summary="Trigger one evaluation tick (demo aid)")
def run_once() -> dict:
    return {"results": worker.run_once()}


# ---------------------------------------------------------------------------
# issues
# ---------------------------------------------------------------------------

@router.get("/issues", tags=["issues"])
def list_issues(
    state: str | None = Query(None, pattern="^(open|closed)$"),
    severity: str | None = None,
    equipment_id: str | None = None,
    property_id: str | None = None,
    rule_key: str | None = None,
    include_backtest: bool = False,
    limit: int = Query(100, le=1000),
) -> dict:
    sql = """
        SELECT i.issue_id, i.rule_key, i.rule_version, i.equipment_id, eq.name AS equipment_name,
               eq.metadata ->> 'property_id' AS property_id, i.severity, i.state, i.opened_at,
               i.closed_at, i.close_reason, i.calculated_difference, i.threshold, i.threshold_source,
               i.unit, i.data_quality, i.affected_zone_id, i.affected_room_ids, i.recurrence_of,
               i.recurrence_index, i.backtest_run_id
          FROM issue i JOIN ontology_entity eq ON eq.entity_id = i.equipment_id
         WHERE TRUE
    """
    params: dict = {}
    if not include_backtest:
        sql += " AND i.backtest_run_id IS NULL"
    if state:
        sql += " AND i.state = %(state)s"
        params["state"] = state
    if severity:
        sql += " AND i.severity = %(severity)s"
        params["severity"] = severity
    if equipment_id:
        sql += " AND i.equipment_id = %(equipment_id)s"
        params["equipment_id"] = equipment_id
    if property_id:
        sql += " AND eq.metadata ->> 'property_id' = %(property_id)s"
        params["property_id"] = property_id
    if rule_key:
        sql += " AND i.rule_key = %(rule_key)s"
        params["rule_key"] = rule_key
    sql += " ORDER BY i.opened_at DESC LIMIT %(limit)s"
    params["limit"] = limit
    return {"issues": db.query(sql, params)}


@router.get("/issues/{issue_id}", tags=["issues"],
            summary="Everything needed to reconstruct why and when an issue triggered")
def get_issue(issue_id: str, context_minutes: int = Query(45, le=360)) -> dict:
    issue = db.query_one(
        """
        SELECT i.*, eq.name AS equipment_name
          FROM issue i JOIN ontology_entity eq ON eq.entity_id = i.equipment_id
         WHERE i.issue_id = %s
        """,
        (issue_id,),
    )
    if issue is None:
        raise HTTPException(404, "unknown issue")

    issue["evidence"] = db.query(
        """
        SELECT observed_at, supply_air_temp, setpoint, difference, run_status, quality, in_window
          FROM issue_observation WHERE issue_id = %s ORDER BY observed_at
        """,
        (issue_id,),
    )
    issue["lifecycle"] = db.query(
        "SELECT at, type, detail FROM issue_event WHERE issue_id = %s ORDER BY at, id",
        (issue_id,),
    )
    issue["spatial"] = onto.affected_spaces(issue["equipment_id"])

    window_start = issue["trigger_started_at"]
    end = issue["closed_at"] or issue["trigger_observed_at"]
    context = db.query(
        """
        SELECT o.observed_at, e.metadata ->> 'role' AS role, o.value_number, o.value_text, o.quality
          FROM observation o
          JOIN point_registry pr ON pr.point_id = o.point_id
          JOIN ontology_entity e ON e.entity_id = o.point_id
         WHERE pr.equipment_id = %(eq)s
           AND o.observed_at BETWEEN %(start)s - (%(ctx)s * interval '1 minute')
                                 AND %(end)s + (%(ctx)s * interval '1 minute')
         ORDER BY o.observed_at
        """,
        {"eq": issue["equipment_id"], "start": window_start, "end": end, "ctx": context_minutes},
    )
    merged: dict[datetime, dict] = {}
    for row in context:
        point = merged.setdefault(row["observed_at"], {"observed_at": row["observed_at"]})
        point[row["role"]] = row["value_number"] if row["value_number"] is not None else row["value_text"]
        point[f"{row['role']}__quality"] = row["quality"]
    issue["trend"] = [merged[k] for k in sorted(merged)]

    # Contextual, explicitly-not-triggering data for the affected rooms.
    room_ids = issue["affected_room_ids"] or []
    issue["room_context"] = db.query(
        """
        SELECT sp.entity_id AS room_id, sp.name AS room_name, pr.equipment_id,
               e.metadata ->> 'role' AS role, pc.value_number, pc.quality, pc.observed_at
          FROM ontology_entity sp
          JOIN ontology_relation meas ON meas.object_id = sp.entity_id AND meas.predicate = 'app:measures'
          JOIN point_registry pr ON pr.equipment_id = meas.subject_id
          JOIN ontology_entity e ON e.entity_id = pr.point_id
          LEFT JOIN point_current pc ON pc.point_id = pr.point_id
         WHERE sp.entity_id = ANY(%s)
         ORDER BY sp.entity_id, role
        """,
        (room_ids,),
    ) if room_ids else []

    issue["floor_meter"] = db.query(
        """
        SELECT pr.equipment_id, e.metadata ->> 'role' AS role, pc.value_number, pc.unit,
               pc.quality, pc.observed_at
          FROM ontology_relation meas
          JOIN ontology_entity floor ON floor.entity_id = meas.object_id
          JOIN point_registry pr ON pr.equipment_id = meas.subject_id
          JOIN ontology_entity e ON e.entity_id = pr.point_id
          LEFT JOIN (SELECT pc.*, pr2.unit FROM point_current pc
                       JOIN point_registry pr2 ON pr2.point_id = pc.point_id) pc
                 ON pc.point_id = pr.point_id
         WHERE meas.predicate = 'app:measures'
           AND floor.entity_id = (
                 SELECT object_id FROM ontology_relation
                  WHERE subject_id = %s AND predicate = 'brick:isPartOf' LIMIT 1)
        """,
        (issue["affected_zone_id"],),
    ) if issue["affected_zone_id"] else []

    return issue


# ---------------------------------------------------------------------------
# backtest
# ---------------------------------------------------------------------------

class BacktestRequest(BaseModel):
    rule_key: str | None = None
    version: int | None = None
    definition: dict | None = None
    start: datetime
    end: datetime
    label: str = ""


@router.post("/backtests", tags=["backtest"], summary="Replay a draft rule over history, isolated from live")
def create_backtest(body: BacktestRequest) -> dict:
    if body.definition is not None:
        try:
            definition = validate_definition(body.definition)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, f"invalid rule definition: {exc}") from exc
    elif body.rule_key:
        found = rule_store.get_definition(body.rule_key, body.version)
        if found is None:
            raise HTTPException(404, "unknown rule")
        definition = found[1]
    else:
        raise HTTPException(422, "provide either rule_key or definition")
    return backtest_module.run(definition, body.start, body.end, body.label)


@router.get("/backtests", tags=["backtest"])
def list_backtests() -> dict:
    return {"runs": backtest_module.list_runs()}


@router.get("/backtests/{run_id}", tags=["backtest"])
def get_backtest(run_id: str) -> dict:
    run = backtest_module.get_run(run_id)
    if run is None:
        raise HTTPException(404, "unknown backtest run")
    return run
