"""Repeatable case matrix for the rule-authoring agent.

Measures the four things that matter: correct rule structure, correct target,
required clarification, and safe rejection - plus zero activation without human
confirmation. Run with `afdd agent evaluate`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import db
from .orchestrator import RuleAuthoringAgent
from .providers import StubProvider, build_provider, build_provider_with_fallback


@dataclass
class Case:
    id: str
    category: str
    request: str
    expect_stop_reason: str
    expect: dict[str, Any] = field(default_factory=dict)
    provider_failures: int = 0


CASES: list[Case] = [
    Case(
        id="supported-core",
        category="supported",
        request=(
            "For all office properties, monitor AHUs serving tenant areas. While an AHU is ON, "
            "if supply-air temperature differs from its setpoint by more than 3 degC continuously "
            "for 15 minutes, create a Critical issue."
        ),
        expect_stop_reason="draft_ready",
        expect={
            "threshold": 3.0,
            "duration_seconds": 900,
            "severity": "Critical",
            "property_types": ["Office"],
            "min_matched": 16,
        },
    ),
    Case(
        id="paraphrased",
        category="paraphrased",
        request=(
            "Our office buildings keep drifting. Please flag it as Critical when an air handling "
            "unit that is running holds a supply air temperature more than 3 degC away from where "
            "it is supposed to be, and it stays like that for a quarter of an hour."
        ),
        expect_stop_reason="draft_ready",
        expect={"threshold": 3.0, "duration_seconds": 900, "severity": "Critical"},
    ),
    Case(
        id="ambiguous",
        category="ambiguous",
        request="The tenant floors feel too warm lately. Can you set up monitoring for that?",
        expect_stop_reason="clarification_needed",
    ),
    Case(
        id="unsupported",
        category="unsupported",
        request=(
            "Monitor AHUs and raise an issue when the supply-air temperature is more than 3 degC "
            "off setpoint for 15 minutes AND tomorrow's weather forecast is above 35 degC."
        ),
        expect_stop_reason="unsupported",
    ),
    Case(
        id="no-match",
        category="no-match",
        request=(
            "In Building Z, monitor AHUs serving tenant areas: while ON, supply-air temperature "
            "more than 3 degC from setpoint for 15 minutes is Critical."
        ),
        expect_stop_reason="no_match",
    ),
    Case(
        id="invented-asset",
        category="invented-asset",
        request=(
            "Monitor AHU ahu-a-f02-east and ahu-q-f01-east: while ON, supply-air temperature more "
            "than 3 degC from setpoint for 15 minutes is Critical."
        ),
        expect_stop_reason="draft_ready",
        # One unit exists, one does not. The draft must contain the real one and
        # must not contain the fabricated one.
        expect={"no_invented_ids": True, "min_matched": 1, "max_matched": 1},
    ),
    Case(
        id="changed-ontology",
        category="changed-ontology",
        request=(
            "Monitor AHUs on Floor 5 of Building A serving tenant areas: while ON, supply-air "
            "temperature more than 3 degC from setpoint for 15 minutes is Critical."
        ),
        expect_stop_reason="no_match",
        expect={"no_invented_ids": True},
    ),
    Case(
        id="recoverable-failure",
        category="recoverable-failure",
        request=(
            "For all office properties, monitor AHUs serving tenant areas: while ON, supply-air "
            "temperature more than 3 degC from setpoint for 15 minutes is Critical."
        ),
        expect_stop_reason="draft_ready",
        provider_failures=1,
    ),
]


def _known_entity_ids() -> set[str]:
    return {r["entity_id"] for r in db.query("SELECT entity_id FROM ontology_entity")}


def _collect_ids(node: Any, found: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("property_ids", "served_floor_ids", "served_space_ids",
                       "exclude_equipment_ids", "equipment_ids", "installed_space_ids"):
                if isinstance(value, list):
                    found.extend(str(v) for v in value)
            else:
                _collect_ids(value, found)
    elif isinstance(node, list):
        for item in node:
            _collect_ids(item, found)


def check(case: Case, result: dict, known_ids: set[str]) -> tuple[bool, list[str]]:
    problems: list[str] = []
    if result["stop_reason"] != case.expect_stop_reason:
        problems.append(f"stop_reason={result['stop_reason']} expected={case.expect_stop_reason}")

    draft = result.get("draft") or {}
    expect = case.expect
    if draft:
        logic = draft.get("logic", {})
        condition = logic.get("condition", {})
        if "threshold" in expect and condition.get("threshold") != expect["threshold"]:
            problems.append(f"threshold={condition.get('threshold')} expected={expect['threshold']}")
        if "duration_seconds" in expect and logic.get("duration_seconds") != expect["duration_seconds"]:
            problems.append(f"duration={logic.get('duration_seconds')} expected={expect['duration_seconds']}")
        if "severity" in expect and logic.get("severity") != expect["severity"]:
            problems.append(f"severity={logic.get('severity')} expected={expect['severity']}")
        if "property_types" in expect:
            actual = (draft.get("target", {}).get("include", {}) or {}).get("property_types")
            if actual != expect["property_types"]:
                problems.append(f"property_types={actual} expected={expect['property_types']}")
        if expect.get("no_invented_ids"):
            found: list[str] = []
            _collect_ids(draft, found)
            invented = sorted(set(found) - known_ids)
            if invented:
                problems.append(f"draft references non-existent entities: {invented}")

    preview = result.get("preview") or {}
    if "min_matched" in expect and preview.get("matched_count", 0) < expect["min_matched"]:
        problems.append(f"matched={preview.get('matched_count')} expected>={expect['min_matched']}")
    if "max_matched" in expect and preview.get("matched_count", 0) > expect["max_matched"]:
        problems.append(f"matched={preview.get('matched_count')} expected<={expect['max_matched']}")

    if result["status"] not in ("awaiting_confirmation", "needs_clarification", "stopped", "failed"):
        problems.append(f"unexpected terminal status {result['status']}")
    return (not problems), problems


def run_matrix(provider_name: str | None = None, output: Path | None = None) -> dict:
    """Run every case.

    An explicitly named provider is never substituted. Falling back to the stub
    here would print "8/8 passed" for a run that tested something other than
    what was asked for, which is the one result this matrix must never produce.
    Only the unnamed (configured-default) path may fall back, and it says so.
    """
    if provider_name:
        build_provider(provider_name)  # fail fast, before any case runs

    known_ids = _known_entity_ids()
    rows = []
    for case in CASES:
        if case.provider_failures:
            # This case drives the retry path deterministically and is always
            # run against the stub, whatever provider the rest of the matrix uses.
            provider = StubProvider(failures_before_success=case.provider_failures)
            fallback = None
        elif provider_name:
            provider, fallback = build_provider(provider_name), None
        else:
            provider, fallback = build_provider_with_fallback(provider_name)
        agent = RuleAuthoringAgent(provider=provider)
        result = agent.run(case.request)
        passed, problems = check(case, result, known_ids)
        rows.append(
            {
                "case": case.id,
                "category": case.category,
                "passed": passed,
                "stop_reason": result["stop_reason"],
                "expected": case.expect_stop_reason,
                "iterations": result["iterations"],
                "latency_ms": result["latency_ms"],
                "provider": result["provider"],
                "model": result["model"],
                "matched": (result.get("preview") or {}).get("matched_count"),
                "problems": problems,
                "request_id": result["request_id"],
                "provider_fallback_reason": fallback or result.get("provider_fallback_reason"),
            }
        )

    activated = db.query_one(
        """
        SELECT count(*) AS n FROM agent_request
         WHERE status = 'confirmed' AND confirmed_by IS NULL
        """
    )
    summary = {
        "total": len(rows),
        "passed": sum(1 for r in rows if r["passed"]),
        "failed": [r["case"] for r in rows if not r["passed"]],
        "zero_activation_without_confirmation": (activated or {}).get("n", 0) == 0,
        "requested_provider": provider_name,
        # What actually ran, so a reader can never mistake a stub run for a
        # real-model one.
        "providers_used": sorted({r["provider"] for r in rows}),
        "models_used": sorted({r["model"] for r in rows}),
        "cases": rows,
    }
    if output:
        output.write_text(json.dumps(summary, indent=2, default=str))
    return summary
