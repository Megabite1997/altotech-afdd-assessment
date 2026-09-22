"""Ontology traversal. Every answer follows explicit relationships - no id parsing."""

from __future__ import annotations

from .. import db
from . import brick


def entity(entity_id: str) -> dict | None:
    return db.query_one("SELECT * FROM ontology_entity WHERE entity_id = %s", (entity_id,))


def spaces_tree() -> list[dict]:
    """Full containment tree, buildings first, ordered for UI rendering."""
    return db.query(
        """
        SELECT e.entity_id, e.name, e.brick_class, e.metadata,
               e.metadata ->> 'parent_space_id' AS parent_id,
               e.metadata ->> 'space_type'      AS space_type,
               e.metadata ->> 'usage_type'      AS usage_type,
               e.metadata ->> 'property_type'   AS property_type
          FROM ontology_entity e
         WHERE e.kind = 'space'
         ORDER BY e.entity_id
        """
    )


def properties() -> list[dict]:
    return db.query(
        """
        SELECT entity_id, name, metadata ->> 'property_type' AS property_type
          FROM ontology_entity
         WHERE kind = 'space' AND brick_class = 'brick:Building'
         ORDER BY entity_id
        """
    )


def equipment_detail(equipment_id: str) -> dict | None:
    row = db.query_one(
        """
        SELECT e.entity_id, e.name, e.brick_class, e.metadata
          FROM ontology_entity e
         WHERE e.entity_id = %s AND e.kind = 'equipment'
        """,
        (equipment_id,),
    )
    if row is None:
        return None
    row["points"] = points_of(equipment_id)
    row["installed_space"] = installed_space(equipment_id)
    row["served_space"] = served_space(equipment_id)
    row["measurement_scope"] = measurement_scope(equipment_id)
    row["property"] = property_of_equipment(equipment_id)
    return row


def points_of(equipment_id: str) -> list[dict]:
    return db.query(
        """
        SELECT p.point_id, p.source_name, p.brick_class, p.unit, p.value_type,
               p.expected_interval_seconds,
               e.metadata ->> 'role' AS role, e.name AS description
          FROM point_registry p
          JOIN ontology_entity e ON e.entity_id = p.point_id
         WHERE p.equipment_id = %s
         ORDER BY p.source_name
        """,
        (equipment_id,),
    )


def _related(subject: str, predicate: str) -> list[dict]:
    return db.query(
        """
        SELECT o.entity_id, o.name, o.brick_class, o.metadata
          FROM ontology_relation r
          JOIN ontology_entity o ON o.entity_id = r.object_id
         WHERE r.subject_id = %s AND r.predicate = %s
         ORDER BY o.entity_id
        """,
        (subject, predicate),
    )


def installed_space(equipment_id: str) -> dict | None:
    rows = _related(equipment_id, brick.HAS_LOCATION)
    return rows[0] if rows else None


def served_space(equipment_id: str) -> dict | None:
    rows = _related(equipment_id, brick.FEEDS)
    return rows[0] if rows else None


def measurement_scope(equipment_id: str) -> dict | None:
    rows = _related(equipment_id, brick.MEASURES)
    return rows[0] if rows else None


def property_of_equipment(equipment_id: str) -> dict | None:
    return db.query_one(
        """
        SELECT b.entity_id, b.name, b.metadata ->> 'property_type' AS property_type
          FROM ontology_entity eq
          JOIN ontology_entity b
            ON b.entity_id = eq.metadata ->> 'property_id'
         WHERE eq.entity_id = %s
        """,
        (equipment_id,),
    )


def affected_spaces(equipment_id: str) -> dict:
    """The served-space path for an AHU: AHU -feeds-> HVAC_Zone -hasPart-> Rooms.

    The installed location (plant room) is returned separately and is never part
    of the affected scope: it is where the unit sits, not who it serves.
    """
    zone = served_space(equipment_id)
    installed = installed_space(equipment_id)
    rooms: list[dict] = []
    if zone:
        rooms = db.query(
            """
            SELECT e.entity_id, e.name, e.brick_class,
                   e.metadata ->> 'usage_type' AS usage_type, c.depth
              FROM space_closure c
              JOIN ontology_entity e ON e.entity_id = c.descendant_id
             WHERE c.ancestor_id = %s AND c.depth > 0 AND e.brick_class = 'brick:Room'
             ORDER BY e.entity_id
            """,
            (zone["entity_id"],),
        )
    floor = None
    if zone:
        floor = db.query_one(
            """
            SELECT e.entity_id, e.name, e.brick_class
              FROM ontology_relation r
              JOIN ontology_entity e ON e.entity_id = r.object_id
             WHERE r.subject_id = %s AND r.predicate = %s AND e.brick_class = 'brick:Floor'
            """,
            (zone["entity_id"], brick.IS_PART_OF),
        )
    return {
        "equipment_id": equipment_id,
        "installed_space": installed,
        "served_zone": zone,
        "served_floor": floor,
        "affected_rooms": rooms,
        "path": _path_labels(equipment_id, installed, zone, rooms),
    }


def _path_labels(equipment_id: str, installed, zone, rooms) -> list[dict]:
    path = [{"relation": None, "entity_id": equipment_id, "role": "equipment"}]
    if zone:
        path.append({"relation": brick.FEEDS, "entity_id": zone["entity_id"], "role": "served_zone"})
    for room in rooms:
        path.append(
            {"relation": brick.HAS_PART, "entity_id": room["entity_id"], "role": "affected_room"}
        )
    if installed:
        path.append(
            {
                "relation": brick.HAS_LOCATION,
                "entity_id": installed["entity_id"],
                "role": "installed_location_not_affected",
            }
        )
    return path


def devices_in_space(space_id: str, kinds: list[str] | None = None) -> list[dict]:
    """Equipment whose measurement scope or location falls inside a space."""
    sql = """
        SELECT DISTINCT eq.entity_id, eq.name, eq.brick_class, eq.metadata
          FROM space_closure c
          JOIN ontology_relation r
            ON r.object_id = c.descendant_id
           AND r.predicate IN (%s, %s)
          JOIN ontology_entity eq ON eq.entity_id = r.subject_id
         WHERE c.ancestor_id = %s AND eq.kind = 'equipment'
    """
    params: list = [brick.MEASURES, brick.HAS_LOCATION, space_id]
    if kinds:
        sql += " AND eq.brick_class = ANY(%s)"
        params.append(kinds)
    sql += " ORDER BY eq.entity_id"
    return db.query(sql, params)


def resolve_point(equipment_id: str, source_name: str) -> dict | None:
    return db.query_one(
        """
        SELECT point_id, equipment_id, source_name, brick_class, value_type, unit,
               expected_interval_seconds
          FROM point_registry
         WHERE equipment_id = %s AND source_name = %s
        """,
        (equipment_id, source_name),
    )


def point_index() -> dict[tuple[str, str], dict]:
    """(equipment_id, source_name) -> point row. Cached by callers per run."""
    rows = db.query(
        """
        SELECT point_id, equipment_id, source_name, value_type, unit,
               expected_interval_seconds
          FROM point_registry
        """
    )
    return {(r["equipment_id"], r["source_name"]): r for r in rows}


def counts() -> dict:
    rows = db.query(
        """
        SELECT kind, brick_class, count(*) AS n
          FROM ontology_entity GROUP BY kind, brick_class ORDER BY kind, brick_class
        """
    )
    totals = db.query_one(
        """
        SELECT
          (SELECT count(*) FROM ontology_entity WHERE kind = 'space')     AS spaces,
          (SELECT count(*) FROM ontology_entity WHERE kind = 'equipment') AS equipment,
          (SELECT count(*) FROM ontology_entity WHERE kind = 'point')     AS points,
          (SELECT count(*) FROM ontology_relation)                        AS relations
        """
    )
    return {"totals": totals, "by_class": rows}
