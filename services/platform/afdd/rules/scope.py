"""Ontology-based target-scope resolution with an explainable preview.

Every match and every exclusion carries the relationship path that produced it,
so a reviewer can see *why* an AHU is in scope rather than trusting a count.
Matching never parses identifiers; it follows brick:feeds, brick:hasPart and
brick:hasPoint edges.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import db
from ..ontology import brick
from .schema import RuleDefinition


@dataclass
class Match:
    equipment_id: str
    name: str
    brick_class: str
    property_id: str | None
    property_name: str | None
    property_type: str | None
    served_zone_id: str | None
    served_zone_name: str | None
    served_zone_usage: str | None
    served_floor_id: str | None
    served_floor_name: str | None
    installed_space_id: str | None
    points: dict[str, str] = field(default_factory=dict)
    missing_roles: list[str] = field(default_factory=list)
    effective_config: dict = field(default_factory=dict)
    path: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Exclusion:
    equipment_id: str
    name: str
    reason: str
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class ScopePreview:
    matched: list[Match]
    excluded: list[Exclusion]

    def as_dict(self) -> dict:
        return {
            "matched_count": len(self.matched),
            "excluded_count": len(self.excluded),
            "matched": [m.as_dict() for m in self.matched],
            "excluded": [e.as_dict() for e in self.excluded],
        }


_CANDIDATE_SQL = """
SELECT eq.entity_id,
       eq.name,
       eq.brick_class,
       eq.metadata ->> 'property_id'        AS property_id,
       eq.metadata ->> 'installed_space_id' AS installed_space_id,
       b.name                                AS property_name,
       b.metadata ->> 'property_type'        AS property_type,
       zone.entity_id                        AS served_zone_id,
       zone.name                             AS served_zone_name,
       zone.metadata ->> 'usage_type'        AS served_zone_usage,
       floor.entity_id                       AS served_floor_id,
       floor.name                            AS served_floor_name
  FROM ontology_entity eq
  LEFT JOIN ontology_entity b ON b.entity_id = eq.metadata ->> 'property_id'
  LEFT JOIN ontology_relation feeds
         ON feeds.subject_id = eq.entity_id AND feeds.predicate = %(feeds)s
  LEFT JOIN ontology_entity zone ON zone.entity_id = feeds.object_id
  LEFT JOIN ontology_relation zone_in
         ON zone_in.subject_id = zone.entity_id AND zone_in.predicate = %(is_part_of)s
  LEFT JOIN ontology_entity floor
         ON floor.entity_id = zone_in.object_id AND floor.brick_class = 'brick:Floor'
 WHERE eq.kind = 'equipment'
   AND eq.brick_class = ANY(%(classes)s)
 ORDER BY eq.entity_id
"""


def _points_by_role(equipment_ids: list[str]) -> dict[str, dict[str, str]]:
    if not equipment_ids:
        return {}
    rows = db.query(
        """
        SELECT p.equipment_id, e.metadata ->> 'role' AS role, p.point_id
          FROM point_registry p
          JOIN ontology_entity e ON e.entity_id = p.point_id
         WHERE p.equipment_id = ANY(%s)
        """,
        (equipment_ids,),
    )
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        out.setdefault(row["equipment_id"], {})[row["role"]] = row["point_id"]
    return out


def resolve(rule: RuleDefinition) -> ScopePreview:
    selector = rule.target.include
    candidates = db.query(
        _CANDIDATE_SQL,
        {
            "feeds": brick.FEEDS,
            "is_part_of": brick.IS_PART_OF,
            "classes": selector.equipment_classes or ["brick:AHU"],
        },
    )
    point_map = _points_by_role([c["entity_id"] for c in candidates])

    matched: list[Match] = []
    excluded: list[Exclusion] = []

    for row in candidates:
        eid = row["entity_id"]

        def drop(reason: str, **detail) -> None:
            excluded.append(Exclusion(eid, row["name"], reason, detail))

        if eid in rule.target.exclude_equipment_ids:
            drop("explicitly_excluded")
            continue

        # A selector left as null is unset and matches everything. A selector
        # set to an empty list is an explicit "nothing selected" and matches
        # nothing - which is how an agent expresses "the assets you named do not
        # exist" without inventing an identifier to exclude.
        checks = (
            ("equipment_not_selected", selector.equipment_ids, eid, "equipment_id"),
            ("property_type_not_selected", selector.property_types, row["property_type"], "property_type"),
            ("property_not_selected", selector.property_ids, row["property_id"], "property_id"),
            ("served_space_usage_not_selected", selector.served_space_usage,
             row["served_zone_usage"], "served_zone_usage"),
            ("floor_not_selected", selector.served_floor_ids, row["served_floor_id"], "served_floor_id"),
            ("served_space_not_selected", selector.served_space_ids, row["served_zone_id"], "served_zone_id"),
            ("installed_space_not_selected", selector.installed_space_ids,
             row["installed_space_id"], "installed_space_id"),
        )
        rejected = False
        for reason, allowed, value, detail_key in checks:
            if allowed is not None and value not in allowed:
                drop(reason, **{detail_key: value, "selected": allowed})
                rejected = True
                break
        if rejected:
            continue

        points = point_map.get(eid, {})
        missing = [role for role in rule.target.require_points if role not in points]
        if missing:
            # Visible exclusion, not a silent skip: the rule cannot be evaluated
            # honestly without every required input.
            drop("missing_required_point", missing_roles=missing, available_roles=sorted(points))
            continue

        match = Match(
            equipment_id=eid,
            name=row["name"],
            brick_class=row["brick_class"],
            property_id=row["property_id"],
            property_name=row["property_name"],
            property_type=row["property_type"],
            served_zone_id=row["served_zone_id"],
            served_zone_name=row["served_zone_name"],
            served_zone_usage=row["served_zone_usage"],
            served_floor_id=row["served_floor_id"],
            served_floor_name=row["served_floor_name"],
            installed_space_id=row["installed_space_id"],
            points={role: points[role] for role in rule.target.require_points},
            missing_roles=[],
        )
        match.effective_config = rule.effective_config(
            property_id=row["property_id"],
            floor_id=row["served_floor_id"],
            equipment_id=eid,
        )
        match.path = [
            f"{eid} {brick.FEEDS} {row['served_zone_id']}",
            f"{row['served_zone_id']} {brick.IS_PART_OF} {row['served_floor_id']}",
            f"{row['served_floor_id']} {brick.IS_PART_OF} {row['property_id']}",
        ]
        matched.append(match)

    return ScopePreview(matched=matched, excluded=excluded)


def zone_room_points(zone_id: str, role: str) -> list[str]:
    """Point ids of a given role for devices measuring rooms inside a zone.

    Backs the cross-entity operand (`scope: served_zone_rooms`) used by rules
    that compare equipment readings against the spaces they serve.
    """
    rows = db.query(
        """
        SELECT DISTINCT p.point_id
          FROM space_closure c
          JOIN ontology_relation meas
            ON meas.object_id = c.descendant_id AND meas.predicate = %(measures)s
          JOIN ontology_entity room ON room.entity_id = c.descendant_id
          JOIN point_registry p ON p.equipment_id = meas.subject_id
          JOIN ontology_entity pe ON pe.entity_id = p.point_id
         WHERE c.ancestor_id = %(zone)s
           AND room.brick_class = 'brick:Room'
           AND pe.metadata ->> 'role' = %(role)s
         ORDER BY p.point_id
        """,
        {"measures": brick.MEASURES, "zone": zone_id, "role": role},
    )
    return [r["point_id"] for r in rows]
