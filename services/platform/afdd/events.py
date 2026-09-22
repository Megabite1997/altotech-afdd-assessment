"""Telemetry event envelope.

One source device snapshot becomes one event carrying every point the device
reported at that instant. Keeping the snapshot whole matters: the AFDD rule has
to compare supply-air temperature against its setpoint *and* check run status
from the same observation instant, and splitting a snapshot into per-point
messages would make that a join across partitions.

Identity is derived, not assigned: `event_id = uuid5(NAMESPACE, device|record)`.
Any producer replaying the same source record therefore produces the same id,
which is what makes the consumer idempotent without coordination.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "1.0"
EVENT_NAMESPACE = uuid.UUID("7f9a5c2e-0d1b-4e63-9a3b-2c7f6e9d4a10")

QUALITY_GOOD = "GOOD"
QUALITY_MISSING = "MISSING"
QUALITY_INVALID = "INVALID"

# Column -> canonical source point name, per source file.
AHU_POINTS = {
    "run_status": "RUN",
    "alarm_status": "ALARM",
    "supply_air_temperature_c": "SAT",
    "return_air_temperature_c": "RAT",
    "supply_air_temperature_setpoint_c": "SAT_SP",
}
METER_POINTS = {
    "active_power_kw": "POWER_KW",
    "cumulative_energy_kwh": "ENERGY_KWH",
}
IAQ_POINTS = {
    "room_temperature_c": "ROOM_TEMP",
    "relative_humidity_pct": "RH",
    "co2_ppm": "CO2",
}

VALUE_TYPE_BY_POINT = {
    "RUN": "enum",
    "ALARM": "enum",
    "SAT": "number",
    "RAT": "number",
    "SAT_SP": "number",
    "POWER_KW": "number",
    "ENERGY_KWH": "number",
    "ROOM_TEMP": "number",
    "RH": "number",
    "CO2": "number",
}

UNIT_BY_POINT = {
    "SAT": "degC",
    "RAT": "degC",
    "SAT_SP": "degC",
    "ROOM_TEMP": "degC",
    "RH": "%RH",
    "CO2": "ppm",
    "POWER_KW": "kW",
    "ENERGY_KWH": "kWh",
}

ENUM_DOMAIN = {"RUN": {"ON", "OFF"}, "ALARM": {"NORMAL", "ALARM"}}

# Plausibility bounds. A value outside these is INVALID, not MISSING: the device
# said something, it just cannot be true.
NUMERIC_BOUNDS = {
    "SAT": (-40.0, 80.0),
    "RAT": (-40.0, 80.0),
    "SAT_SP": (-40.0, 80.0),
    "ROOM_TEMP": (-40.0, 80.0),
    "RH": (0.0, 100.0),
    "CO2": (0.0, 50000.0),
    "POWER_KW": (0.0, 100000.0),
    "ENERGY_KWH": (0.0, 1e12),
}


@dataclass
class Reading:
    source_name: str
    value: Any
    value_type: str
    unit: str | None
    quality: str
    reason: str | None = None


@dataclass
class TelemetryEvent:
    event_id: str
    source_record_id: str
    device_id: str
    device_kind: str
    observed_at: str
    produced_at: str
    readings: list[Reading] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    producer: str = "device-simulator"

    def to_dict(self) -> dict:
        return asdict(self)


def make_event_id(device_id: str, source_record_id: str) -> str:
    return str(uuid.uuid5(EVENT_NAMESPACE, f"{device_id}|{source_record_id}"))


def _coerce(source_name: str, raw: str) -> Reading:
    value_type = VALUE_TYPE_BY_POINT[source_name]
    unit = UNIT_BY_POINT.get(source_name)
    text = (raw or "").strip()
    if text == "":
        # The source did not report this value in this snapshot. It is carried
        # as a first-class MISSING reading rather than dropped, so downstream
        # consumers can tell "not reported" from "never existed".
        return Reading(source_name, None, value_type, unit, QUALITY_MISSING, "blank_in_source")
    if value_type == "enum":
        upper = text.upper()
        domain = ENUM_DOMAIN.get(source_name)
        if domain and upper not in domain:
            return Reading(source_name, text, value_type, unit, QUALITY_INVALID, "value_out_of_domain")
        return Reading(source_name, upper, value_type, unit, QUALITY_GOOD)
    try:
        number = float(text)
    except ValueError:
        return Reading(source_name, text, value_type, unit, QUALITY_INVALID, "not_a_number")
    lo, hi = NUMERIC_BOUNDS.get(source_name, (float("-inf"), float("inf")))
    if not (lo <= number <= hi):
        return Reading(source_name, number, value_type, unit, QUALITY_INVALID, "out_of_plausible_range")
    return Reading(source_name, number, value_type, unit, QUALITY_GOOD)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def from_source_row(row: dict, device_kind: str, produced_at: str | None = None) -> TelemetryEvent:
    """Translate one CSV snapshot row into an event envelope."""
    if device_kind == "AHU":
        device_id, mapping = row["equipment_id"], AHU_POINTS
    elif device_kind == "METER":
        device_id, mapping = row["meter_id"], METER_POINTS
    elif device_kind == "IAQ":
        device_id, mapping = row["device_id"], IAQ_POINTS
    else:  # pragma: no cover - guarded by caller
        raise ValueError(f"unknown device kind {device_kind}")

    readings = [_coerce(name, row.get(column, "")) for column, name in mapping.items()]
    record_id = row["source_record_id"]
    return TelemetryEvent(
        event_id=make_event_id(device_id, record_id),
        source_record_id=record_id,
        device_id=device_id,
        device_kind=device_kind,
        observed_at=row["observed_at"],
        produced_at=produced_at or _now_iso(),
        readings=readings,
    )


def parse_timestamp(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
