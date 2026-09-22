"""Model providers.

`AnthropicProvider` makes the real tool-use calls. `StubProvider` is a
deterministic double used by tests and by anyone running the stack without an
API key: it plans over the *same* tool surface, so the orchestrator, tool
contracts, persistence and case matrix are exercised identically either way.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Protocol

from ..config import settings

log = logging.getLogger(__name__)


@dataclass
class ToolCall:
    name: str
    arguments: dict
    call_id: str = "call"
    text: str = ""


@dataclass
class ProviderResult:
    tool_calls: list[ToolCall] = field(default_factory=list)
    text: str = ""
    raw_stop_reason: str = ""


class Provider(Protocol):
    name: str
    model: str

    def step(self, system: str, messages: list[dict], tools: list[dict]) -> ProviderResult: ...


class ProviderError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.model = model or settings.llm_model
        key = api_key or settings.anthropic_api_key
        if not key:
            raise ProviderError("ANTHROPIC_API_KEY is not set")
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("anthropic sdk not installed") from exc
        self._client = anthropic.Anthropic(api_key=key, max_retries=2, timeout=45.0)

    def step(self, system: str, messages: list[dict], tools: list[dict]) -> ProviderResult:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system,
                tools=tools,
                messages=messages,
            )
        except Exception as exc:  # noqa: BLE001 - normalised for the orchestrator
            raise ProviderError(f"{type(exc).__name__}: {exc}") from exc

        calls, text = [], ""
        for block in response.content:
            if block.type == "text":
                text += block.text
            elif block.type == "tool_use":
                calls.append(ToolCall(name=block.name, arguments=dict(block.input or {}), call_id=block.id))
        return ProviderResult(tool_calls=calls, text=text, raw_stop_reason=response.stop_reason or "")


# ---------------------------------------------------------------------------
# Deterministic stub
# ---------------------------------------------------------------------------

_BASE_DRAFT = {
    "key": "ahu-supply-air-deviation-draft",
    "name": "AHU supply-air temperature deviation",
    "intent": "",
    "target": {
        "include": {
            "property_types": ["Office"],
            "equipment_classes": ["brick:AHU"],
            "served_space_usage": ["Tenant Area"],
        },
        "exclude_equipment_ids": [],
        "require_points": ["run_status", "supply_air_temperature", "supply_air_temperature_setpoint"],
    },
    "logic": {
        "operating_state": {"role": "run_status", "equals": "ON"},
        "condition": {
            "left": {"role": "supply_air_temperature"},
            "right": {"role": "supply_air_temperature_setpoint"},
            "operator": "abs_difference_gt",
            "threshold": 3.0,
            "unit": "degC",
        },
        "duration_seconds": 900,
        "max_input_age_seconds": 180,
        "recovery_seconds": 300,
        "severity": "Critical",
    },
    "overrides": [],
}

_VAGUE = re.compile(
    r"\b(too warm|too cold|too hot|uncomfortable|not right|feels? off|something wrong|drifting)\b"
)
_WORDED_DURATION = {
    "quarter of an hour": 900,
    "half an hour": 1800,
    "an hour": 3600,
}


class StubProvider:
    """Keyword planner over the real tool surface. Deterministic, offline.

    It is a test double, not a model: it exists so the harness, tool contracts,
    persistence and case matrix can be exercised without a network call. It
    follows the same discipline the system prompt demands of a real model -
    discover the ontology, resolve every named entity, never invent an id.
    """

    name = "stub"
    model = "deterministic-stub/1"

    def __init__(self, failures_before_success: int = 0) -> None:
        self._failures_left = failures_before_success

    def step(self, system: str, messages: list[dict], tools: list[dict]) -> ProviderResult:
        if self._failures_left > 0:
            self._failures_left -= 1
            raise ProviderError("simulated transient provider failure")

        raw = self._first_user_text(messages)
        request = raw.lower()
        turn = sum(1 for m in messages if m["role"] == "assistant")

        if turn == 0:
            return ProviderResult(tool_calls=[ToolCall("discover_ontology", {}, "c0")])

        # Unsupported: a signal the platform has no data source for.
        if any(k in request for k in ("weather", "forecast", "occupancy schedule", "predict")):
            return ProviderResult(
                tool_calls=[
                    ToolCall(
                        "report_unsupported",
                        {
                            "reason": "no data source for the requested signal",
                            "detail": "The ontology exposes no weather, forecast or occupancy points.",
                        },
                        "c1",
                    )
                ]
            )

        # Ambiguous: a comparison with no stated threshold or duration.
        if self._is_ambiguous(request):
            return ProviderResult(
                tool_calls=[
                    ToolCall(
                        "ask_clarification",
                        {
                            "question": "How large must the deviation be, and how long must it last, before an issue is created?",
                            "options": ["more than 3 degC for 15 minutes", "more than 2 degC for 30 minutes"],
                        },
                        "c1",
                    )
                ]
            )

        named = self._named_terms(request)
        if named and turn == 1:
            # Never construct an identifier from prose. Ask the server which of
            # the named things exist before putting anything in a selector.
            return ProviderResult(tool_calls=[ToolCall("resolve_entities", {"terms": named}, "c1")])

        resolved = self._resolved_ids(messages)
        draft = self._build_draft(raw, request, named, resolved)

        # Check who the draft would select before submitting it; one preview,
        # then submit. The extra turn when entities were named is the resolve.
        preview_turn = 2 if named else 1
        if turn == preview_turn:
            return ProviderResult(tool_calls=[ToolCall("preview_target", {"rule": draft}, "c2")])
        return ProviderResult(tool_calls=[ToolCall("submit_draft", {"rule": draft}, "c3")])

    # -- planning helpers ---------------------------------------------------

    def _build_draft(self, raw: str, request: str, named: list[str], resolved: dict[str, list[str]]) -> dict:
        draft = json.loads(json.dumps(_BASE_DRAFT))
        draft["intent"] = raw.strip()

        if "hotel" in request and "office" not in request:
            draft["target"]["include"]["property_types"] = ["Hotel"]
            draft["target"]["include"]["served_space_usage"] = ["Guest Area"]
        if "guest" in request:
            draft["target"]["include"]["served_space_usage"] = ["Guest Area"]

        if named:
            # Only ids the server confirmed. An empty list is an explicit
            # "nothing selected", which resolves to no match rather than to a
            # fabricated selector.
            floors = sorted(resolved.get("brick:Floor", []))
            buildings = sorted(resolved.get("brick:Building", []))
            equipment = sorted(resolved.get("equipment", []))
            if any(self._looks_like_floor(term) for term in named):
                draft["target"]["include"]["served_floor_ids"] = floors
            if any(self._looks_like_building(term) for term in named):
                draft["target"]["include"]["property_ids"] = buildings
                draft["target"]["include"].pop("property_types", None)
            if any(term.startswith("ahu-") for term in named):
                draft["target"]["include"]["equipment_ids"] = equipment
                draft["target"]["include"]["property_types"] = None
                draft["target"]["include"]["served_space_usage"] = None

        threshold = re.search(r"(\d+(?:\.\d+)?)\s*(?:°|deg(?:rees)?\s*)?c\b", request)
        if threshold:
            draft["logic"]["condition"]["threshold"] = float(threshold.group(1))
        minutes = re.search(r"(\d+)\s*minute", request)
        if minutes:
            draft["logic"]["duration_seconds"] = int(minutes.group(1)) * 60
        else:
            for phrase, seconds in _WORDED_DURATION.items():
                if phrase in request:
                    draft["logic"]["duration_seconds"] = seconds
                    break
        if "warning" in request:
            draft["logic"]["severity"] = "Warning"

        if "return-air" in request or "return air" in request:
            draft["key"] = "ahu-return-vs-room-draft"
            draft["logic"]["condition"]["left"] = {"role": "return_air_temperature"}
            draft["logic"]["condition"]["right"] = {
                "role": "room_air_temperature",
                "scope": "served_zone_rooms",
                "aggregate": "mean",
            }
            draft["target"]["require_points"] = ["run_status", "return_air_temperature"]

        # Drop selectors that were never set; keep the ones deliberately set to
        # an empty list, because "nothing matched what you named" is a result.
        explicit = {"served_floor_ids", "property_ids", "equipment_ids"}
        draft["target"]["include"] = {
            k: v for k, v in draft["target"]["include"].items() if v is not None or k in explicit
        }
        return draft

    @staticmethod
    def _looks_like_floor(term: str) -> bool:
        return term.lower().startswith("floor")

    @staticmethod
    def _looks_like_building(term: str) -> bool:
        return term.lower().startswith("building")

    @staticmethod
    def _named_terms(request: str) -> list[str]:
        terms: list[str] = []
        for match in re.finditer(r"building\s+([a-z])\b", request):
            terms.append(f"Building {match.group(1).upper()}")
        for match in re.finditer(r"floor\s+(\d+)", request):
            terms.append(f"Floor {match.group(1)}")
        terms.extend(re.findall(r"\bahu-[a-z0-9-]+", request))
        seen, unique = set(), []
        for term in terms:
            if term not in seen:
                seen.add(term)
                unique.append(term)
        return unique

    @staticmethod
    def _resolved_ids(messages: list[dict]) -> dict[str, list[str]]:
        """Read back what resolve_entities actually confirmed exists."""
        out: dict[str, list[str]] = {}
        for message in messages:
            content = message.get("content")
            if not isinstance(content, str):
                continue
            if "resolve_entities" not in content:
                continue
            try:
                payload = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                continue
            for entry in payload if isinstance(payload, list) else []:
                result = entry.get("result") if isinstance(entry, dict) else None
                if not isinstance(result, dict):
                    continue
                for item in result.get("resolved", []):
                    for candidate in item.get("candidates", []):
                        bucket = candidate["brick_class"] if candidate["kind"] == "space" else "equipment"
                        out.setdefault(bucket, []).append(candidate["entity_id"])
        return out

    @staticmethod
    def _is_ambiguous(request: str) -> bool:
        wants_rule = any(k in request for k in ("monitor", "issue", "alert", "detect", "flag"))
        has_threshold = re.search(r"\d+(\.\d+)?\s*(°|deg)", request) is not None
        has_duration = (
            re.search(r"\d+\s*(minute|min\b|hour)", request) is not None
            or any(phrase in request for phrase in _WORDED_DURATION)
        )
        return wants_rule and bool(_VAGUE.search(request)) and not (has_threshold and has_duration)

    @staticmethod
    def _first_user_text(messages: list[dict]) -> str:
        for message in messages:
            if message["role"] == "user":
                content = message["content"]
                if isinstance(content, str):
                    return content
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        return block["text"]
        return ""


def build_provider(name: str | None = None, **kwargs) -> Provider:
    name = (name or settings.llm_provider or "stub").lower()
    if name == "anthropic":
        return AnthropicProvider(**kwargs)
    if name == "stub":
        return StubProvider(**kwargs)
    raise ProviderError(f"unknown provider {name!r}")


def build_provider_with_fallback(name: str | None = None) -> tuple[Provider, str | None]:
    """Use the configured provider, falling back to the stub with a reason."""
    try:
        return build_provider(name), None
    except ProviderError as exc:
        log.warning("falling back to stub provider: %s", exc)
        return StubProvider(), str(exc)
