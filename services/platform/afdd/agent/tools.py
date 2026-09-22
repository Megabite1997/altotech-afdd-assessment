"""Bounded tools the rule-authoring agent may call.

Every tool is server-implemented and server-resolved. The model can only *ask*;
it can never write to a store, run code, query unrestricted data, or activate a
rule. Identities come back from the ontology, so an invented asset id simply
fails to resolve and the agent is told so.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .. import db
from ..rules import scope as scope_module
from ..rules.schema import OPERATORS, POINT_ROLES, SELECTORS, SEVERITIES, validate_definition

MAX_LIST = 40


class ToolError(Exception):
    """Raised for a bad tool call. Returned to the model, never to the user raw."""


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------

def discover_ontology(_: dict) -> dict:
    """What actually exists. The model selects from this; it never invents ids."""
    facets = db.query_one(
        """
        SELECT
          (SELECT array_agg(DISTINCT metadata ->> 'property_type')
             FROM ontology_entity
            WHERE brick_class = 'brick:Building')                       AS property_types,
          (SELECT array_agg(DISTINCT brick_class)
             FROM ontology_entity WHERE kind = 'equipment')             AS equipment_classes,
          (SELECT array_agg(DISTINCT metadata ->> 'usage_type')
             FROM ontology_entity WHERE brick_class = 'brick:HVAC_Zone') AS zone_usage_types
        """
    )
    properties = db.query(
        """
        SELECT entity_id, name, metadata ->> 'property_type' AS property_type
          FROM ontology_entity WHERE brick_class = 'brick:Building' ORDER BY entity_id
        """
    )
    floors = db.query(
        """
        SELECT entity_id, name, metadata ->> 'usage_type' AS usage_type
          FROM ontology_entity WHERE brick_class = 'brick:Floor' ORDER BY entity_id
        """
    )
    return {
        "brick_version": "1.3.0",
        "property_types": sorted(filter(None, facets["property_types"] or [])),
        "equipment_classes": sorted(facets["equipment_classes"] or []),
        "zone_usage_types": sorted(filter(None, facets["zone_usage_types"] or [])),
        "properties": properties,
        "floors": floors[:MAX_LIST],
        "point_roles": sorted(POINT_ROLES),
        "selectors": SELECTORS,
        "operators": OPERATORS,
        "severities": list(SEVERITIES),
    }


def resolve_entities(args: dict) -> dict:
    """Resolve free-text names or ids the request mentioned to real entities."""
    terms = args.get("terms") or []
    if not isinstance(terms, list) or not terms:
        raise ToolError("terms must be a non-empty list of strings")
    out: dict[str, Any] = {"resolved": [], "unresolved": []}
    for term in terms[:MAX_LIST]:
        rows = db.query(
            """
            SELECT entity_id, name, kind, brick_class
              FROM ontology_entity
             WHERE entity_id = %(t)s OR lower(name) = lower(%(t)s) OR lower(name) LIKE lower(%(like)s)
             ORDER BY (entity_id = %(t)s) DESC, entity_id
             LIMIT 5
            """,
            {"t": str(term), "like": f"%{term}%"},
        )
        if rows:
            out["resolved"].append({"term": term, "candidates": rows})
        else:
            out["unresolved"].append(term)
    return out


def preview_target(args: dict) -> dict:
    """Validate a draft and show which equipment it would actually select."""
    draft = args.get("rule")
    if not isinstance(draft, dict):
        raise ToolError("rule must be an object")
    try:
        definition = validate_definition(draft)
    except Exception as exc:  # noqa: BLE001 - surfaced to the model verbatim
        return {"valid": False, "errors": str(exc), "matched_count": 0}
    preview = scope_module.resolve(definition)
    reasons: dict[str, int] = {}
    for item in preview.excluded:
        reasons[item.reason] = reasons.get(item.reason, 0) + 1
    return {
        "valid": True,
        "matched_count": len(preview.matched),
        "matched_sample": [
            {
                "equipment_id": m.equipment_id,
                "property_id": m.property_id,
                "served_zone_id": m.served_zone_id,
                "served_floor_id": m.served_floor_id,
                "effective_threshold": m.effective_config.get("threshold"),
                "threshold_source": m.effective_config.get("source"),
            }
            for m in preview.matched[:MAX_LIST]
        ],
        "excluded_count": len(preview.excluded),
        "exclusion_reasons": reasons,
    }


def validate_rule(args: dict) -> dict:
    draft = args.get("rule")
    if not isinstance(draft, dict):
        raise ToolError("rule must be an object")
    try:
        definition = validate_definition(draft)
    except Exception as exc:  # noqa: BLE001
        return {"valid": False, "errors": str(exc)}
    return {"valid": True, "normalised": definition.model_dump(mode="json")}


# Terminal tools. They end the run; they do not change anything.
def ask_clarification(args: dict) -> dict:
    question = (args.get("question") or "").strip()
    if not question:
        raise ToolError("question is required")
    return {
        "terminal": "clarification_needed",
        "question": question,
        "options": (args.get("options") or [])[:6],
    }


def report_unsupported(args: dict) -> dict:
    reason = (args.get("reason") or "").strip()
    if not reason:
        raise ToolError("reason is required")
    return {"terminal": "unsupported", "reason": reason, "detail": args.get("detail", "")}


def submit_draft(args: dict) -> dict:
    """The model's proposed rule. Server-validated and previewed before it counts."""
    draft = args.get("rule")
    if not isinstance(draft, dict):
        raise ToolError("rule must be an object")
    result = preview_target({"rule": draft})
    if not result["valid"]:
        return {"terminal": None, **result}
    if result["matched_count"] == 0:
        return {"terminal": "no_match", **result}
    return {"terminal": "draft_ready", **result}


REGISTRY: dict[str, Callable[[dict], dict]] = {
    "discover_ontology": discover_ontology,
    "resolve_entities": resolve_entities,
    "validate_rule": validate_rule,
    "preview_target": preview_target,
    "ask_clarification": ask_clarification,
    "report_unsupported": report_unsupported,
    "submit_draft": submit_draft,
}

TERMINAL_TOOLS = {"ask_clarification", "report_unsupported", "submit_draft"}


def call(name: str, args: dict) -> tuple[dict, float, bool]:
    started = time.monotonic()
    handler = REGISTRY.get(name)
    if handler is None:
        return ({"error": f"unknown tool {name!r}", "known_tools": sorted(REGISTRY)},
                (time.monotonic() - started) * 1000, False)
    try:
        return handler(args or {}), (time.monotonic() - started) * 1000, True
    except ToolError as exc:
        return {"error": str(exc)}, (time.monotonic() - started) * 1000, False
    except Exception as exc:  # noqa: BLE001 - never leak a stack trace to the model
        return ({"error": "tool failed", "detail": type(exc).__name__},
                (time.monotonic() - started) * 1000, False)


# --- JSON schema advertised to the model -----------------------------------

_RULE_SCHEMA = {
    "type": "object",
    "description": "An AFDD rule definition. target = which equipment, logic = how it is evaluated.",
    "properties": {
        "key": {"type": "string", "description": "kebab-case identifier"},
        "name": {"type": "string"},
        "intent": {"type": "string", "description": "plain-language restatement of the request"},
        "target": {
            "type": "object",
            "properties": {
                "include": {
                    "type": "object",
                    "properties": {
                        "property_types": {"type": "array", "items": {"type": "string"}},
                        "property_ids": {"type": "array", "items": {"type": "string"}},
                        "equipment_classes": {"type": "array", "items": {"type": "string"}},
                        "served_space_usage": {"type": "array", "items": {"type": "string"}},
                        "served_floor_ids": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "exclude_equipment_ids": {"type": "array", "items": {"type": "string"}},
                "require_points": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["include", "require_points"],
        },
        "logic": {
            "type": "object",
            "properties": {
                "operating_state": {
                    "type": "object",
                    "properties": {"role": {"type": "string"}, "equals": {"type": "string"}},
                },
                "condition": {
                    "type": "object",
                    "properties": {
                        "left": {"type": "object"},
                        "right": {"type": "object"},
                        "operator": {"type": "string", "enum": sorted(OPERATORS)},
                        "threshold": {"type": "number"},
                        "unit": {"type": "string"},
                    },
                    "required": ["left", "right", "operator", "threshold"],
                },
                "duration_seconds": {"type": "integer"},
                "max_input_age_seconds": {"type": "integer"},
                "recovery_seconds": {"type": "integer"},
                "severity": {"type": "string", "enum": list(SEVERITIES)},
            },
            "required": ["condition", "duration_seconds", "severity"],
        },
        "overrides": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["key", "name", "target", "logic"],
}

SPECS = [
    {
        "name": "discover_ontology",
        "description": "List the property types, equipment classes, zone usage types, floors, point roles, operators and severities that exist. Call this before writing any selector.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "resolve_entities",
        "description": "Resolve building/floor/zone/equipment names or ids mentioned in the request to real ontology entities. Anything returned as unresolved does not exist.",
        "input_schema": {
            "type": "object",
            "properties": {"terms": {"type": "array", "items": {"type": "string"}}},
            "required": ["terms"],
        },
    },
    {
        "name": "validate_rule",
        "description": "Validate a draft rule against the rule schema. Returns errors to fix.",
        "input_schema": {"type": "object", "properties": {"rule": _RULE_SCHEMA}, "required": ["rule"]},
    },
    {
        "name": "preview_target",
        "description": "Show which equipment a draft rule would select, with exclusion reasons and effective thresholds.",
        "input_schema": {"type": "object", "properties": {"rule": _RULE_SCHEMA}, "required": ["rule"]},
    },
    {
        "name": "ask_clarification",
        "description": "Stop and ask the engineer one question. Use when the request is missing a detail that changes the outcome (threshold, duration, severity, which spaces). Do not guess.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["question"],
        },
    },
    {
        "name": "report_unsupported",
        "description": "Stop and report that the request cannot be expressed with the available selectors, roles or operators. Never approximate an unsupported request.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}, "detail": {"type": "string"}},
            "required": ["reason"],
        },
    },
    {
        "name": "submit_draft",
        "description": "Submit the finished rule draft for human review. The draft is validated and previewed server-side. This does NOT activate anything.",
        "input_schema": {"type": "object", "properties": {"rule": _RULE_SCHEMA}, "required": ["rule"]},
    },
]

SYSTEM_PROMPT = """You turn a property engineer's plain-language request into a draft AFDD rule.

Rules of engagement:
- You cannot activate anything. A human reviews and confirms every draft.
- Never invent an entity id, property type, point role, operator or severity.
  Call discover_ontology first, and resolve_entities for anything the request names.
- A rule has two separate halves: `target` (which equipment, by ontology
  selectors) and `logic` (how the selected points are evaluated over time).
- `target.require_points` must declare every point role the logic reads.
- If a detail that changes the outcome is missing or ambiguous, call
  ask_clarification. Do not guess a threshold, duration or severity.
- If the request needs a selector, comparison or data source that does not
  exist, call report_unsupported. Do not approximate it with something else.
- Before submitting, call preview_target and check the matched equipment is what
  the engineer meant. If it matches nothing, say so rather than submitting.
- Finish by calling exactly one of: submit_draft, ask_clarification,
  report_unsupported.
"""
