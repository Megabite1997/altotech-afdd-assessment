"""Load the supplied source CSVs into the ontology registry.

The source files are *facts*, not a schema. This module is the single place
where source shape is translated into Brickschema entities and relationships.
Onboarding a new building means providing the same three files.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

from psycopg.types.json import Jsonb

from .. import db
from . import brick

log = logging.getLogger(__name__)


@dataclass
class LoadReport:
    spaces: int = 0
    equipment: int = 0
    points: int = 0
    relations: int = 0
    closure_rows: int = 0
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "spaces": self.spaces,
            "equipment": self.equipment,
            "points": self.points,
            "relations": self.relations,
            "closure_rows": self.closure_rows,
            "problems": self.problems,
        }


def _read(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(fh)]


def load(source_dir: Path, reset: bool = True) -> LoadReport:
    spaces = _read(source_dir / "building-and-equipment" / "spaces.csv")
    equipment = _read(source_dir / "building-and-equipment" / "equipment.csv")
    points = _read(source_dir / "building-and-equipment" / "datapoint-reference.csv")

    report = LoadReport()
    entities: list[tuple] = []
    relations: set[tuple[str, str, str]] = set()
    registry: list[tuple] = []

    def relate(subject: str, predicate: str, obj: str) -> None:
        relations.add((subject, predicate, obj))
        inverse = brick.INVERSE.get(predicate)
        if inverse:
            relations.add((obj, inverse, subject))

    # --- spaces -------------------------------------------------------------
    space_ids = {r["space_id"] for r in spaces}
    property_of: dict[str, str] = {}
    for row in spaces:
        klass = brick.SPACE_CLASS_BY_SOURCE_TYPE.get(row["space_type"])
        if klass is None:
            report.problems.append(f"unknown space_type {row['space_type']!r} for {row['space_id']}")
            continue
        entities.append(
            (
                row["space_id"],
                "space",
                klass,
                row["name"],
                row["space_id"],
                Jsonb(
                    {
                        "space_type": row["space_type"],
                        "usage_type": row["usage_type"] or None,
                        "property_type": row["property_type"] or None,
                        "parent_space_id": row["parent_space_id"] or None,
                    }
                ),
            )
        )
        report.spaces += 1

    for row in spaces:
        parent = row["parent_space_id"]
        if not parent:
            continue
        if parent not in space_ids:
            report.problems.append(f"space {row['space_id']} references unknown parent {parent}")
            continue
        relate(parent, brick.HAS_PART, row["space_id"])

    # Resolve the owning building for every space (used for property filters).
    parent_map = {r["space_id"]: r["parent_space_id"] for r in spaces}
    type_map = {r["space_id"]: r["space_type"] for r in spaces}

    def building_of(space_id: str) -> str | None:
        seen, cur = set(), space_id
        while cur and cur not in seen:
            if type_map.get(cur) == "Building":
                return cur
            seen.add(cur)
            cur = parent_map.get(cur) or ""
        return None

    for row in spaces:
        property_of[row["space_id"]] = building_of(row["space_id"]) or ""

    # --- equipment ----------------------------------------------------------
    for row in equipment:
        klass = brick.EQUIPMENT_CLASS_BY_SOURCE_TYPE.get(row["equipment_type"])
        if klass is None:
            report.problems.append(
                f"unknown equipment_type {row['equipment_type']!r} for {row['equipment_id']}"
            )
            continue
        entities.append(
            (
                row["equipment_id"],
                "equipment",
                klass,
                row["name"],
                row["equipment_id"],
                Jsonb(
                    {
                        "equipment_type": row["equipment_type"],
                        "property_id": row["property_id"] or None,
                        "installed_space_id": row["installed_space_id"] or None,
                        "served_space_id": row["served_space_id"] or None,
                        "measurement_scope_id": row["measurement_scope_id"] or None,
                    }
                ),
            )
        )
        report.equipment += 1

        if row["installed_space_id"]:
            if row["installed_space_id"] in space_ids:
                relate(row["equipment_id"], brick.HAS_LOCATION, row["installed_space_id"])
            else:
                report.problems.append(
                    f"equipment {row['equipment_id']} installed in unknown space "
                    f"{row['installed_space_id']}"
                )
        if row["served_space_id"]:
            if row["served_space_id"] in space_ids:
                relate(row["equipment_id"], brick.FEEDS, row["served_space_id"])
            else:
                report.problems.append(
                    f"equipment {row['equipment_id']} serves unknown space {row['served_space_id']}"
                )
        if row["measurement_scope_id"]:
            if row["measurement_scope_id"] in space_ids:
                relate(row["equipment_id"], brick.MEASURES, row["measurement_scope_id"])
            else:
                report.problems.append(
                    f"equipment {row['equipment_id']} measures unknown space "
                    f"{row['measurement_scope_id']}"
                )

    equipment_ids = {r["equipment_id"] for r in equipment}

    # --- points -------------------------------------------------------------
    for row in points:
        if row["equipment_id"] not in equipment_ids:
            report.problems.append(
                f"point {row['source_point_id']} owned by unknown equipment {row['equipment_id']}"
            )
            continue
        klass = brick.POINT_CLASS_BY_SOURCE_NAME.get(row["source_name"])
        if klass is None:
            report.problems.append(f"unknown source_name {row['source_name']!r}")
            continue
        role = brick.POINT_ROLE_BY_SOURCE_NAME[row["source_name"]]
        interval = int(row["expected_interval_seconds"] or 60)
        entities.append(
            (
                row["source_point_id"],
                "point",
                klass,
                row["description"] or row["source_name"],
                row["source_point_id"],
                Jsonb(
                    {
                        "equipment_id": row["equipment_id"],
                        "source_name": row["source_name"],
                        "role": role,
                        "unit": row["unit"] or None,
                        "value_type": row["value_type"],
                        "expected_interval_seconds": interval,
                    }
                ),
            )
        )
        report.points += 1
        relate(row["equipment_id"], brick.HAS_POINT, row["source_point_id"])
        registry.append(
            (
                row["source_point_id"],
                row["equipment_id"],
                row["source_name"],
                klass,
                row["value_type"],
                row["unit"] or None,
                interval,
            )
        )

    # --- write --------------------------------------------------------------
    with db.cursor() as cur:
        if reset:
            cur.execute("TRUNCATE point_registry, ontology_relation, space_closure CASCADE")
            cur.execute("TRUNCATE ontology_entity CASCADE")
        cur.executemany(
            """
            INSERT INTO ontology_entity (entity_id, kind, brick_class, name, source_id, metadata)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (entity_id) DO UPDATE
              SET kind = EXCLUDED.kind, brick_class = EXCLUDED.brick_class,
                  name = EXCLUDED.name, metadata = EXCLUDED.metadata
            """,
            entities,
        )
        cur.executemany(
            """
            INSERT INTO ontology_relation (subject_id, predicate, object_id)
            VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
            """,
            sorted(relations),
        )
        cur.executemany(
            """
            INSERT INTO point_registry (point_id, equipment_id, source_name, brick_class,
                                        value_type, unit, expected_interval_seconds)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (point_id) DO UPDATE
              SET equipment_id = EXCLUDED.equipment_id, source_name = EXCLUDED.source_name,
                  brick_class = EXCLUDED.brick_class, value_type = EXCLUDED.value_type,
                  unit = EXCLUDED.unit,
                  expected_interval_seconds = EXCLUDED.expected_interval_seconds
            """,
            registry,
        )
    report.relations = len(relations)
    report.closure_rows = rebuild_closure()
    log.info("ontology loaded: %s", report.as_dict())
    return report


def rebuild_closure() -> int:
    """Materialise the transitive brick:hasPart closure over spaces."""
    with db.cursor() as cur:
        cur.execute("TRUNCATE space_closure")
        cur.execute(
            """
            WITH RECURSIVE parts AS (
                SELECT e.entity_id AS ancestor_id, e.entity_id AS descendant_id, 0 AS depth
                  FROM ontology_entity e
                 WHERE e.kind = 'space'
                UNION ALL
                SELECT p.ancestor_id, r.object_id, p.depth + 1
                  FROM parts p
                  JOIN ontology_relation r
                    ON r.subject_id = p.descendant_id AND r.predicate = %s
                 WHERE p.depth < 16
            )
            INSERT INTO space_closure (ancestor_id, descendant_id, depth)
            SELECT ancestor_id, descendant_id, MIN(depth)
              FROM parts
             GROUP BY ancestor_id, descendant_id
            """,
            (brick.HAS_PART,),
        )
        cur.execute("SELECT count(*) AS n FROM space_closure")
        return cur.fetchone()["n"]
