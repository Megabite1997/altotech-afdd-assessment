"""Rule definition schema.

A rule is configuration, never a code path. It has exactly two halves and they
are stored separately so either can change without touching the other:

    target  - which entities the rule applies to (ontology selectors)
    logic   - how the selected points are evaluated over time

Extending the platform means registering a new selector, a new point role, or a
new comparison operator - not editing the evaluator.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "afdd-rule/1.0"

# --- selector registry ------------------------------------------------------
# Adding a selector is a two-line change: declare it here and add its SQL in
# rules/scope.py. Nothing else in the platform needs to know.
SELECTORS = {
    "property_types": "Building property_type, e.g. Office / Hotel",
    "property_ids": "Explicit building entity ids",
    "equipment_classes": "Brick equipment classes, e.g. brick:AHU",
    "equipment_ids": "Explicit equipment entity ids, when a rule targets named units",
    "served_space_usage": "usage_type of the space an AHU feeds, e.g. Tenant Area",
    "served_floor_ids": "Floor entity ids, matched through the served zone",
    "served_space_ids": "Served zone entity ids",
    "installed_space_ids": "Where the equipment physically sits",
}

POINT_ROLES = {
    "run_status",
    "alarm_status",
    "supply_air_temperature",
    "return_air_temperature",
    "supply_air_temperature_setpoint",
    "electrical_power",
    "electrical_energy",
    "room_air_temperature",
    "relative_humidity",
    "co2_concentration",
}

OPERATORS = {
    "abs_difference_gt": "|left - right| > threshold",
    "difference_gt": "left - right > threshold",
    "difference_lt": "left - right < threshold",
    "value_gt": "left > threshold",
    "value_lt": "left < threshold",
}

SEVERITIES = ("Critical", "Warning", "Info")


class Selector(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_types: list[str] | None = None
    property_ids: list[str] | None = None
    equipment_classes: list[str] = Field(default_factory=lambda: ["brick:AHU"])
    equipment_ids: list[str] | None = None
    served_space_usage: list[str] | None = None
    served_floor_ids: list[str] | None = None
    served_space_ids: list[str] | None = None
    installed_space_ids: list[str] | None = None


class TargetScope(BaseModel):
    """Which equipment the rule applies to."""

    model_config = ConfigDict(extra="forbid")

    include: Selector = Field(default_factory=Selector)
    exclude_equipment_ids: list[str] = Field(default_factory=list)
    # Equipment that cannot supply every required role is excluded from
    # evaluation and reported as such - it is never evaluated on partial inputs.
    require_points: list[str] = Field(default_factory=list)

    @field_validator("require_points")
    @classmethod
    def _known_roles(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - POINT_ROLES)
        if unknown:
            raise ValueError(f"unknown point roles: {unknown}")
        return value


class Operand(BaseModel):
    """One side of a comparison.

    `scope` decides where the value comes from:
      same_equipment    - a point owned by the evaluated equipment
      served_zone_rooms - points of devices in the zone the equipment feeds,
                          reduced by `aggregate` (the cross-entity case)
    """

    model_config = ConfigDict(extra="forbid")

    role: str | None = None
    constant: float | None = None
    scope: Literal["same_equipment", "served_zone_rooms"] = "same_equipment"
    aggregate: Literal["mean", "max", "min"] = "mean"

    @model_validator(mode="after")
    def _one_of(self) -> "Operand":
        if (self.role is None) == (self.constant is None):
            raise ValueError("operand needs exactly one of role or constant")
        if self.role is not None and self.role not in POINT_ROLES:
            raise ValueError(f"unknown point role {self.role!r}")
        return self


class OperatingState(BaseModel):
    """Gate that must hold for the timing window to run at all."""

    model_config = ConfigDict(extra="forbid")

    role: str = "run_status"
    equals: str = "ON"


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left: Operand
    right: Operand
    operator: str = "abs_difference_gt"
    threshold: float
    unit: str = "degC"

    @field_validator("operator")
    @classmethod
    def _known_operator(cls, value: str) -> str:
        if value not in OPERATORS:
            raise ValueError(f"unknown operator {value!r}; known: {sorted(OPERATORS)}")
        return value


class FaultLogic(BaseModel):
    """How the selected points are evaluated over time."""

    model_config = ConfigDict(extra="forbid")

    operating_state: OperatingState | None = None
    condition: Condition
    duration_seconds: int = Field(900, ge=0)
    # How recent an input must be to participate. Default is three expected
    # 60-second intervals: tolerant of one dropped reading, not of a stale feed.
    max_input_age_seconds: int = Field(180, ge=1)
    # How long readings must stay normal before an open issue closes.
    recovery_seconds: int = Field(300, ge=0)
    severity: str = "Critical"

    @field_validator("severity")
    @classmethod
    def _known_severity(cls, value: str) -> str:
        if value not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}")
        return value


class OverrideScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_ids: list[str] | None = None
    served_floor_ids: list[str] | None = None
    equipment_ids: list[str] | None = None


class OverrideValues(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threshold: float | None = None
    duration_seconds: int | None = None
    severity: str | None = None


class Override(BaseModel):
    """A local setting that changes the result for its scope only."""

    model_config = ConfigDict(extra="forbid")

    id: str
    note: str = ""
    scope: OverrideScope
    values: OverrideValues


class RuleDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    key: str
    name: str
    intent: str = ""
    target: TargetScope
    logic: FaultLogic
    overrides: list[Override] = Field(default_factory=list)

    @model_validator(mode="after")
    def _roles_are_required(self) -> "RuleDefinition":
        """Every role the logic reads must be declared in require_points.

        This is what turns "equipment missing a required point" into a visible,
        configuration-level exclusion instead of a runtime surprise.
        """
        used: set[str] = set()
        if self.logic.operating_state:
            used.add(self.logic.operating_state.role)
        for operand in (self.logic.condition.left, self.logic.condition.right):
            if operand.role and operand.scope == "same_equipment":
                used.add(operand.role)
        undeclared = sorted(used - set(self.target.require_points))
        if undeclared:
            raise ValueError(
                f"logic reads {undeclared} but target.require_points does not declare them"
            )
        return self

    def effective_config(self, *, property_id: str | None, floor_id: str | None, equipment_id: str) -> dict:
        """Resolve overrides for one equipment. Last matching override wins."""
        config = {
            "threshold": self.logic.condition.threshold,
            "duration_seconds": self.logic.duration_seconds,
            "severity": self.logic.severity,
            "max_input_age_seconds": self.logic.max_input_age_seconds,
            "recovery_seconds": self.logic.recovery_seconds,
            "unit": self.logic.condition.unit,
            "operator": self.logic.condition.operator,
            "source": "rule_default",
            "applied_overrides": [],
        }
        for override in self.overrides:
            scope = override.scope
            matched = (
                (scope.equipment_ids and equipment_id in scope.equipment_ids)
                or (scope.property_ids and property_id in scope.property_ids)
                or (scope.served_floor_ids and floor_id in scope.served_floor_ids)
            )
            if not matched:
                continue
            for name in ("threshold", "duration_seconds", "severity"):
                value = getattr(override.values, name)
                if value is not None:
                    config[name] = value
            config["source"] = f"override:{override.id}"
            config["applied_overrides"].append(override.id)
        return config


def validate_definition(raw: dict) -> RuleDefinition:
    return RuleDefinition.model_validate(raw)
