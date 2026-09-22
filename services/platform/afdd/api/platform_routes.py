"""Health, ontology, telemetry and portfolio endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from .. import db
from ..config import settings
from ..evaluator import worker
from ..ontology import queries as onto

router = APIRouter()


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------

@router.get("/health", tags=["health"], summary="Liveness and store reachability")
def health() -> dict:
    try:
        db.query_one("SELECT 1 AS ok")
        store = "ok"
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"database unavailable: {exc}") from exc
    return {"status": "ok", "store": store, "eval_clock": settings.eval_clock}


@router.get("/health/pipeline", tags=["health"], summary="End-to-end pipeline health")
def pipeline_health() -> dict:
    """Everything an operator needs to answer 'is the data I am looking at real?'"""
    clocks = db.query_one(
        """
        SELECT max(observed_at) AS data_time,
               max(ingested_at) AS last_ingest_at,
               count(*)         AS observations
          FROM observation
        """
    ) or {}
    ingest = db.query(
        "SELECT status, count(*) AS n FROM ingest_event GROUP BY status ORDER BY status"
    )
    rejects = db.query(
        """
        SELECT reason_code, count(*) AS n, max(rejected_at) AS last_at
          FROM ingest_reject GROUP BY reason_code ORDER BY n DESC
        """
    )
    # "Late" must mean late *relative to the stream*, not old in wall-clock
    # terms. Replaying a historical fixture makes every event months old, which
    # would render an absolute-age metric meaningless. What matters is an
    # observation arriving after a newer one had already been ingested.
    out_of_order = db.query_one(
        """
        SELECT count(*) AS n FROM (
            SELECT observed_at,
                   max(observed_at) OVER (
                       ORDER BY ingested_at, event_id
                       ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                   ) AS prior_max
              FROM ingest_event
             WHERE status IN ('accepted', 'partial') AND observed_at IS NOT NULL
        ) t
        WHERE prior_max IS NOT NULL AND observed_at < prior_max - interval '60 seconds'
        """
    )
    runs = db.query(
        """
        SELECT rule_key, max(started_at) AS last_run_at, max(evaluation_time) AS last_evaluation_time,
               sum(opened) AS opened, sum(closed) AS closed
          FROM evaluation_run GROUP BY rule_key ORDER BY rule_key
        """
    )
    now = datetime.now(timezone.utc)
    data_time = clocks.get("data_time")
    stale_points = db.query_one(
        """
        SELECT count(*) AS n FROM point_current
         WHERE observed_at < (SELECT max(observed_at) FROM observation) - interval '180 seconds'
        """
    )
    gaps = db.query(
        """
        SELECT eq.entity_id AS equipment_id, eq.name,
               max(pc.observed_at) AS last_observed_at
          FROM point_current pc
          JOIN point_registry pr ON pr.point_id = pc.point_id
          JOIN ontology_entity eq ON eq.entity_id = pr.equipment_id
         GROUP BY eq.entity_id, eq.name
        HAVING max(pc.observed_at) < (SELECT max(observed_at) FROM observation) - interval '180 seconds'
         ORDER BY 3
         LIMIT 20
        """
    )
    return {
        "clocks": {
            "platform_time": now.isoformat(),
            "data_time": data_time.isoformat() if data_time else None,
            "evaluation_clock": settings.eval_clock,
            "ingest_to_platform_lag_seconds": (
                (now - clocks["last_ingest_at"]).total_seconds() if clocks.get("last_ingest_at") else None
            ),
        },
        "observations": clocks.get("observations", 0),
        "ingest_events": {r["status"]: r["n"] for r in ingest},
        "rejections": rejects,
        "out_of_order_arrivals": (out_of_order or {}).get("n", 0),
        "stale_points": (stale_points or {}).get("n", 0),
        "devices_not_reporting": gaps,
        "evaluation": runs,
        "ontology": onto.counts()["totals"],
    }


@router.get("/health/data-quality", tags=["health"], summary="Rejected, duplicate and incomplete data")
def data_quality(limit: int = Query(100, le=500)) -> dict:
    return {
        "recent_rejections": db.query(
            """
            SELECT reason_code, source_record_id, device_id, point_ref, observed_at, detail, rejected_at
              FROM ingest_reject ORDER BY rejected_at DESC, id DESC LIMIT %s
            """,
            (limit,),
        ),
        "non_good_observations": db.query(
            """
            SELECT pr.equipment_id, o.point_id, o.quality, count(*) AS n,
                   min(o.observed_at) AS first_at, max(o.observed_at) AS last_at
              FROM observation o JOIN point_registry pr ON pr.point_id = o.point_id
             WHERE o.quality <> 'GOOD'
             GROUP BY pr.equipment_id, o.point_id, o.quality
             ORDER BY n DESC LIMIT %s
            """,
            (limit,),
        ),
    }


# ---------------------------------------------------------------------------
# ontology
# ---------------------------------------------------------------------------

@router.get("/ontology/counts", tags=["ontology"])
def ontology_counts() -> dict:
    return onto.counts()


@router.get("/ontology/spaces", tags=["ontology"], summary="Full containment tree")
def spaces() -> dict:
    return {"spaces": onto.spaces_tree(), "properties": onto.properties()}


@router.get("/ontology/equipment", tags=["ontology"])
def equipment_list(brick_class: str | None = None, property_id: str | None = None) -> dict:
    sql = """
        SELECT entity_id, name, brick_class, metadata
          FROM ontology_entity WHERE kind = 'equipment'
    """
    params: list = []
    if brick_class:
        sql += " AND brick_class = %s"
        params.append(brick_class)
    if property_id:
        sql += " AND metadata ->> 'property_id' = %s"
        params.append(property_id)
    sql += " ORDER BY entity_id"
    return {"equipment": db.query(sql, params)}


@router.get("/ontology/equipment/{equipment_id}", tags=["ontology"])
def equipment_detail(equipment_id: str) -> dict:
    detail = onto.equipment_detail(equipment_id)
    if detail is None:
        raise HTTPException(404, "unknown equipment")
    return detail


@router.get("/ontology/equipment/{equipment_id}/affected-spaces", tags=["ontology"],
            summary="AHU -feeds-> zone -hasPart-> rooms; installation room kept separate")
def affected_spaces(equipment_id: str) -> dict:
    if onto.entity(equipment_id) is None:
        raise HTTPException(404, "unknown equipment")
    return onto.affected_spaces(equipment_id)


@router.get("/ontology/spaces/{space_id}/devices", tags=["ontology"])
def space_devices(space_id: str) -> dict:
    if onto.entity(space_id) is None:
        raise HTTPException(404, "unknown space")
    return {"space_id": space_id, "devices": onto.devices_in_space(space_id)}


# ---------------------------------------------------------------------------
# telemetry
# ---------------------------------------------------------------------------

@router.get("/points/{point_id}/current", tags=["telemetry"])
def point_current(point_id: str) -> dict:
    row = db.query_one(
        """
        SELECT pc.*, pr.equipment_id, pr.source_name, pr.unit, pr.value_type,
               pr.expected_interval_seconds, e.metadata ->> 'role' AS role,
               (SELECT max(observed_at) FROM observation) AS data_time
          FROM point_current pc
          JOIN point_registry pr ON pr.point_id = pc.point_id
          JOIN ontology_entity e ON e.entity_id = pc.point_id
         WHERE pc.point_id = %s
        """,
        (point_id,),
    )
    if row is None:
        raise HTTPException(404, "no current value for this point")
    if row["data_time"]:
        row["age_seconds"] = (row["data_time"] - row["observed_at"]).total_seconds()
        row["fresh"] = row["age_seconds"] <= 3 * row["expected_interval_seconds"]
    return row


@router.get("/points/{point_id}/history", tags=["telemetry"])
def point_history(
    point_id: str,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(2000, le=20000),
) -> dict:
    rows = db.query(
        """
        SELECT observed_at, value_number, value_text, quality, ingested_at
          FROM observation
         WHERE point_id = %(p)s
           AND (%(start)s::timestamptz IS NULL OR observed_at >= %(start)s)
           AND (%(end)s::timestamptz IS NULL OR observed_at <= %(end)s)
         ORDER BY observed_at DESC LIMIT %(limit)s
        """,
        {"p": point_id, "start": start, "end": end, "limit": limit},
    )
    return {"point_id": point_id, "count": len(rows), "observations": list(reversed(rows))}


@router.get("/equipment/{equipment_id}/current", tags=["telemetry"])
def equipment_current(equipment_id: str) -> dict:
    if onto.entity(equipment_id) is None:
        raise HTTPException(404, "unknown equipment")
    rows = db.query(
        """
        SELECT pr.point_id, pr.source_name, pr.unit, pr.expected_interval_seconds,
               e.metadata ->> 'role' AS role,
               pc.observed_at, pc.value_number, pc.value_text, pc.quality, pc.ingested_at,
               (SELECT max(observed_at) FROM observation) AS data_time
          FROM point_registry pr
          JOIN ontology_entity e ON e.entity_id = pr.point_id
          LEFT JOIN point_current pc ON pc.point_id = pr.point_id
         WHERE pr.equipment_id = %s
         ORDER BY pr.source_name
        """,
        (equipment_id,),
    )
    for row in rows:
        if row["observed_at"] and row["data_time"]:
            row["age_seconds"] = (row["data_time"] - row["observed_at"]).total_seconds()
            row["state"] = "fresh" if row["age_seconds"] <= 3 * row["expected_interval_seconds"] else "stale"
        else:
            row["age_seconds"] = None
            row["state"] = "empty"
    return {"equipment_id": equipment_id, "points": rows}


@router.get("/equipment/{equipment_id}/series", tags=["telemetry"])
def equipment_series(
    equipment_id: str,
    roles: str = Query("supply_air_temperature,supply_air_temperature_setpoint,run_status"),
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(5000, le=50000),
) -> dict:
    wanted = [r.strip() for r in roles.split(",") if r.strip()]
    rows = db.query(
        """
        SELECT o.observed_at, e.metadata ->> 'role' AS role, o.value_number, o.value_text, o.quality
          FROM observation o
          JOIN point_registry pr ON pr.point_id = o.point_id
          JOIN ontology_entity e ON e.entity_id = o.point_id
         WHERE pr.equipment_id = %(eq)s AND e.metadata ->> 'role' = ANY(%(roles)s)
           AND (%(start)s::timestamptz IS NULL OR o.observed_at >= %(start)s)
           AND (%(end)s::timestamptz IS NULL OR o.observed_at <= %(end)s)
         ORDER BY o.observed_at LIMIT %(limit)s
        """,
        {"eq": equipment_id, "roles": wanted, "start": start, "end": end, "limit": limit},
    )
    merged: dict[datetime, dict] = {}
    for row in rows:
        point = merged.setdefault(row["observed_at"], {"observed_at": row["observed_at"]})
        value = row["value_number"] if row["value_number"] is not None else row["value_text"]
        point[row["role"]] = value
        point[f"{row['role']}__quality"] = row["quality"]
    return {"equipment_id": equipment_id, "roles": wanted,
            "series": [merged[k] for k in sorted(merged)]}


# ---------------------------------------------------------------------------
# portfolio
# ---------------------------------------------------------------------------

_CURRENT_BY_EQUIPMENT_SQL = """
SELECT pr.equipment_id, e.metadata ->> 'role' AS role, pc.observed_at, pc.value_number,
       pc.value_text, pc.quality, pr.expected_interval_seconds
  FROM point_registry pr
  JOIN ontology_entity e ON e.entity_id = pr.point_id
  LEFT JOIN point_current pc ON pc.point_id = pr.point_id
"""


@router.get("/portfolio/overview", tags=["portfolio"],
            summary="Property -> floor -> zone -> room with fresh values and open issues")
def portfolio_overview() -> dict:
    data_time = worker.data_clock()
    now = datetime.now(timezone.utc)

    spaces_rows = onto.spaces_tree()
    children: dict[str, list[dict]] = {}
    for space in spaces_rows:
        children.setdefault(space["parent_id"] or "", []).append(space)

    equipment_rows = db.query(
        """
        SELECT entity_id, name, brick_class, metadata ->> 'property_id' AS property_id,
               metadata ->> 'installed_space_id' AS installed_space_id,
               metadata ->> 'served_space_id' AS served_space_id,
               metadata ->> 'measurement_scope_id' AS measurement_scope_id
          FROM ontology_entity WHERE kind = 'equipment' ORDER BY entity_id
        """
    )
    current: dict[str, dict] = {}
    for row in db.query(_CURRENT_BY_EQUIPMENT_SQL):
        bucket = current.setdefault(row["equipment_id"], {})
        value = row["value_number"] if row["value_number"] is not None else row["value_text"]
        age = (data_time - row["observed_at"]).total_seconds() if (data_time and row["observed_at"]) else None
        bucket[row["role"]] = {
            "value": value,
            "quality": row["quality"] or "EMPTY",
            "observed_at": row["observed_at"],
            "age_seconds": age,
            "state": "empty" if row["observed_at"] is None
                     else ("fresh" if age is not None and age <= 3 * row["expected_interval_seconds"] else "stale"),
        }

    issue_counts = {
        r["equipment_id"]: r["n"]
        for r in db.query(
            """
            SELECT equipment_id, count(*) AS n FROM issue
             WHERE state = 'open' AND backtest_run_id IS NULL GROUP BY equipment_id
            """
        )
    }
    modes = {
        (r["equipment_id"]): r["mode"]
        for r in db.query("SELECT equipment_id, mode FROM evaluator_state")
    }

    ahu_by_zone = {e["served_space_id"]: e for e in equipment_rows if e["served_space_id"]}
    meter_by_scope = {
        e["measurement_scope_id"]: e for e in equipment_rows
        if e["brick_class"] == "brick:Electrical_Meter" and e["measurement_scope_id"]
    }
    iaq_by_room = {
        e["measurement_scope_id"]: e for e in equipment_rows
        if e["brick_class"] == "app:IAQ_Device" and e["measurement_scope_id"]
    }

    def device_block(equipment: dict | None) -> dict | None:
        if not equipment:
            return None
        return {
            "equipment_id": equipment["entity_id"],
            "name": equipment["name"],
            "brick_class": equipment["brick_class"],
            "points": current.get(equipment["entity_id"], {}),
            "open_issues": issue_counts.get(equipment["entity_id"], 0),
            "evaluator_mode": modes.get(equipment["entity_id"]),
        }

    properties = []
    for building in [s for s in spaces_rows if s["space_type"] == "Building"]:
        floors = []
        building_issues = 0
        for floor in sorted(
            [c for c in children.get(building["entity_id"], []) if c["space_type"] == "Floor"],
            key=lambda s: s["entity_id"],
        ):
            zones = []
            for zone in sorted(
                [c for c in children.get(floor["entity_id"], []) if c["space_type"] == "HVAC Zone"],
                key=lambda s: s["entity_id"],
            ):
                rooms = [
                    {
                        "entity_id": room["entity_id"],
                        "name": room["name"],
                        "usage_type": room["usage_type"],
                        "iaq": device_block(iaq_by_room.get(room["entity_id"])),
                    }
                    for room in sorted(
                        [c for c in children.get(zone["entity_id"], []) if c["space_type"] == "Room"],
                        key=lambda s: s["entity_id"],
                    )
                ]
                ahu = device_block(ahu_by_zone.get(zone["entity_id"]))
                building_issues += (ahu or {}).get("open_issues", 0)
                zones.append(
                    {
                        "entity_id": zone["entity_id"],
                        "name": zone["name"],
                        "usage_type": zone["usage_type"],
                        "ahu": ahu,
                        "rooms": rooms,
                    }
                )
            floors.append(
                {
                    "entity_id": floor["entity_id"],
                    "name": floor["name"],
                    "usage_type": floor["usage_type"],
                    "meter": device_block(meter_by_scope.get(floor["entity_id"])),
                    "zones": zones,
                }
            )
        other_rooms = [
            {"entity_id": r["entity_id"], "name": r["name"], "usage_type": r["usage_type"]}
            for r in children.get(building["entity_id"], [])
            if r["space_type"] == "Room"
        ]
        properties.append(
            {
                "entity_id": building["entity_id"],
                "name": building["name"],
                "property_type": building["property_type"],
                "floors": floors,
                "non_conditioned_rooms": other_rooms,
                "open_issues": building_issues,
            }
        )

    return {
        "clocks": {
            "data_time": data_time.isoformat() if data_time else None,
            "platform_time": now.isoformat(),
            "evaluation_clock": settings.eval_clock,
            "note": "Freshness is measured against data time so a replayed fixture is judged on its own clock.",
        },
        "properties": properties,
        "totals": {
            "open_issues": sum(issue_counts.values()),
            "equipment": len(equipment_rows),
            "insufficient_data": sum(1 for m in modes.values() if m == "insufficient_data"),
        },
    }
