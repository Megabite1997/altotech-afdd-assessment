"""Event envelope and rule-schema behaviour."""

from __future__ import annotations

import pytest

from afdd.events import make_event_id, from_source_row
from afdd.rules.defaults import AHU_SAT_DEVIATION, definitions
from afdd.rules.schema import validate_definition


# --- event envelope ---------------------------------------------------------

def test_event_id_is_derived_and_stable():
    a = make_event_id("ahu-a-f01-east", "src-ahu-0000-000")
    b = make_event_id("ahu-a-f01-east", "src-ahu-0000-000")
    c = make_event_id("ahu-a-f01-west", "src-ahu-0000-000")
    assert a == b, "the same source record must always produce the same event id"
    assert a != c, "the same record id from a different device is a different event"


def test_blank_measurement_becomes_missing_not_dropped():
    row = {
        "source_record_id": "r1", "observed_at": "2026-01-15T08:00:00Z",
        "equipment_id": "ahu-a-f01-east", "run_status": "ON", "alarm_status": "NORMAL",
        "supply_air_temperature_c": "14.1", "return_air_temperature_c": "24.0",
        "supply_air_temperature_setpoint_c": "",
    }
    event = from_source_row(row, "AHU")
    setpoint = next(r for r in event.readings if r.source_name == "SAT_SP")
    assert setpoint.quality == "MISSING"
    assert setpoint.value is None
    assert setpoint.reason == "blank_in_source"
    assert len(event.readings) == 5, "a blank value still occupies its slot in the snapshot"


def test_unparseable_and_out_of_range_values_are_invalid_not_missing():
    row = {
        "source_record_id": "r2", "observed_at": "2026-01-15T08:00:00Z",
        "equipment_id": "ahu-a-f01-east", "run_status": "MAYBE", "alarm_status": "NORMAL",
        "supply_air_temperature_c": "abc", "return_air_temperature_c": "9999",
        "supply_air_temperature_setpoint_c": "14.0",
    }
    event = from_source_row(row, "AHU")
    by_name = {r.source_name: r for r in event.readings}
    assert by_name["SAT"].quality == "INVALID"
    assert by_name["RAT"].quality == "INVALID"
    assert by_name["RUN"].quality == "INVALID"
    assert by_name["SAT_SP"].quality == "GOOD"


# --- rule schema ------------------------------------------------------------

def test_default_rules_validate():
    assert len(definitions()) == 2


def test_logic_must_declare_the_points_it_reads():
    broken = {**AHU_SAT_DEVIATION}
    broken["target"] = {
        **AHU_SAT_DEVIATION["target"],
        "require_points": ["run_status"],  # drops the two temperature roles
    }
    with pytest.raises(ValueError, match="require_points"):
        validate_definition(broken)


def test_unknown_operator_is_rejected():
    broken = {**AHU_SAT_DEVIATION}
    broken["logic"] = {
        **AHU_SAT_DEVIATION["logic"],
        "condition": {**AHU_SAT_DEVIATION["logic"]["condition"], "operator": "vibes_gt"},
    }
    with pytest.raises(ValueError, match="unknown operator"):
        validate_definition(broken)


def test_unknown_point_role_is_rejected():
    broken = {**AHU_SAT_DEVIATION}
    broken["target"] = {**AHU_SAT_DEVIATION["target"],
                        "require_points": ["run_status", "vibe_sensor"]}
    with pytest.raises(ValueError, match="unknown point roles"):
        validate_definition(broken)


def test_unknown_field_is_rejected_rather_than_silently_ignored():
    broken = {**AHU_SAT_DEVIATION, "surprise": True}
    with pytest.raises(ValueError):
        validate_definition(broken)


# --- override resolution ----------------------------------------------------

def test_override_applies_only_inside_its_scope():
    rule = validate_definition(AHU_SAT_DEVIATION)
    in_scope = rule.effective_config(
        property_id="building-b", floor_id="building-b-f01", equipment_id="ahu-b-f01-west"
    )
    out_of_scope = rule.effective_config(
        property_id="building-a", floor_id="building-a-f02", equipment_id="ahu-a-f02-east"
    )
    assert in_scope["threshold"] == 2.0
    assert in_scope["source"] == "override:building-b-tighter-threshold"
    assert out_of_scope["threshold"] == 3.0
    assert out_of_scope["source"] == "rule_default"


def test_override_leaves_untouched_settings_at_their_defaults():
    rule = validate_definition(AHU_SAT_DEVIATION)
    config = rule.effective_config(
        property_id="building-b", floor_id="building-b-f01", equipment_id="ahu-b-f01-west"
    )
    assert config["duration_seconds"] == 900
    assert config["severity"] == "Critical"


def test_target_scope_and_fault_logic_are_independently_editable():
    """Changing the target must not touch the logic, and vice versa."""
    rule = validate_definition(AHU_SAT_DEVIATION)
    retargeted = rule.model_copy(deep=True)
    retargeted.target.include.served_floor_ids = ["building-a-f02"]
    assert retargeted.logic.model_dump() == rule.logic.model_dump()

    retuned = rule.model_copy(deep=True)
    retuned.logic.condition.threshold = 1.5
    assert retuned.target.model_dump() == rule.target.model_dump()


# --- selector semantics -----------------------------------------------------

def test_unset_selector_differs_from_an_explicitly_empty_one():
    """`null` means "not filtered". `[]` means "nothing selected".

    The distinction matters because it is how an agent says "the assets you
    named do not exist" without inventing an identifier to filter on.
    """
    unset = validate_definition(AHU_SAT_DEVIATION)
    assert unset.target.include.served_floor_ids is None

    empty = AHU_SAT_DEVIATION["target"]["include"] | {"served_floor_ids": []}
    definition = validate_definition(
        {**AHU_SAT_DEVIATION,
         "target": {**AHU_SAT_DEVIATION["target"], "include": empty}}
    )
    assert definition.target.include.served_floor_ids == []


def test_equipment_ids_selector_is_registered():
    from afdd.rules.schema import SELECTORS

    assert "equipment_ids" in SELECTORS
    definition = validate_definition(
        {**AHU_SAT_DEVIATION,
         "target": {**AHU_SAT_DEVIATION["target"],
                    "include": AHU_SAT_DEVIATION["target"]["include"] | {"equipment_ids": ["ahu-a-f02-east"]}}}
    )
    assert definition.target.include.equipment_ids == ["ahu-a-f02-east"]
