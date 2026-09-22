"""The rule shipped with the seed: the required AHU supply-air deviation case.

It is expressed entirely as configuration. Nothing about "3 degrees", "15
minutes", "tenant areas" or "office buildings" exists as a code path.
"""

from __future__ import annotations

from .schema import RuleDefinition, validate_definition

AHU_SAT_DEVIATION = {
    "schema_version": "afdd-rule/1.0",
    "key": "ahu-supply-air-deviation",
    "name": "AHU supply-air temperature deviation",
    "intent": (
        "For office properties, monitor AHUs serving tenant areas. While an AHU is ON and its "
        "readings are recent enough to trust, open one Critical issue when supply-air temperature "
        "differs from its setpoint by more than 3 degrees C continuously for 15 minutes."
    ),
    "target": {
        "include": {
            "property_types": ["Office"],
            "equipment_classes": ["brick:AHU"],
            "served_space_usage": ["Tenant Area"],
            # Left unset = every tenant floor. Narrow it to demonstrate target
            # scope changing independently of fault logic.
            "served_floor_ids": None,
        },
        "exclude_equipment_ids": [],
        "require_points": [
            "run_status",
            "supply_air_temperature",
            "supply_air_temperature_setpoint",
        ],
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
    "overrides": [
        {
            "id": "building-b-tighter-threshold",
            "note": (
                "Building B tenants report comfort complaints below the portfolio threshold, so "
                "the local engineer runs a tighter 2 degrees C limit."
            ),
            "scope": {"property_ids": ["building-b"]},
            "values": {"threshold": 2.0},
        }
    ],
}

# A second, deliberately unactivated rule showing the cross-entity operand the
# schema supports: compare an AHU's return-air temperature against the room
# temperature of the zone it serves.
AHU_RETURN_VS_ROOM = {
    "schema_version": "afdd-rule/1.0",
    "key": "ahu-return-vs-room",
    "name": "Return-air vs served-room temperature",
    "intent": (
        "While an AHU is ON, compare return-air temperature against the mean room temperature of "
        "the rooms in the zone it serves. A gap above 5 degrees C for 15 minutes is a Warning."
    ),
    "target": {
        "include": {
            "property_types": ["Office", "Hotel"],
            "equipment_classes": ["brick:AHU"],
        },
        "exclude_equipment_ids": [],
        "require_points": ["run_status", "return_air_temperature"],
    },
    "logic": {
        "operating_state": {"role": "run_status", "equals": "ON"},
        "condition": {
            "left": {"role": "return_air_temperature"},
            "right": {"role": "room_air_temperature", "scope": "served_zone_rooms", "aggregate": "mean"},
            "operator": "abs_difference_gt",
            "threshold": 5.0,
            "unit": "degC",
        },
        "duration_seconds": 900,
        "max_input_age_seconds": 180,
        "recovery_seconds": 300,
        "severity": "Warning",
    },
    "overrides": [],
}

DEFAULT_RULES: list[dict] = [AHU_SAT_DEVIATION, AHU_RETURN_VS_ROOM]


def definitions() -> list[RuleDefinition]:
    return [validate_definition(raw) for raw in DEFAULT_RULES]
