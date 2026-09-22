"""Brickschema vocabulary pinned for this platform.

Only the classes and relationships listed here are used. Anything outside the
Brick namespace is prefixed `app:` so the boundary between the standard
ontology and our application metadata stays explicit and auditable.

Brickschema version pinned: 1.3.0 (https://brickschema.org/schema/1.3/Brick)
"""

from __future__ import annotations

BRICK_VERSION = "1.3.0"
BRICK_NAMESPACE = "https://brickschema.org/schema/1.3/Brick#"
APP_NAMESPACE = "https://altotech.example/afdd/app#"

# --- classes ---------------------------------------------------------------

SPACE_CLASS_BY_SOURCE_TYPE = {
    "Building": "brick:Building",
    "Floor": "brick:Floor",
    "HVAC Zone": "brick:HVAC_Zone",
    "Room": "brick:Room",
}

EQUIPMENT_CLASS_BY_SOURCE_TYPE = {
    "AHU": "brick:AHU",
    "Electricity Meter": "brick:Electrical_Meter",
    # An IAQ sensor is a multi-point device. Brick models the individual
    # measurements as points; the device itself is application metadata.
    "IAQ Sensor": "app:IAQ_Device",
}

# (equipment source type, source point name) -> Brick point class
POINT_CLASS_BY_SOURCE_NAME = {
    "RUN": "brick:Run_Status",
    "ALARM": "brick:Alarm",
    "SAT": "brick:Supply_Air_Temperature_Sensor",
    "RAT": "brick:Return_Air_Temperature_Sensor",
    "SAT_SP": "brick:Supply_Air_Temperature_Setpoint",
    "POWER_KW": "brick:Electrical_Power_Sensor",
    "ENERGY_KWH": "brick:Electrical_Energy_Sensor",
    "ROOM_TEMP": "brick:Zone_Air_Temperature_Sensor",
    "RH": "brick:Humidity_Sensor",
    "CO2": "brick:CO2_Sensor",
}

# Stable application aliases used by rule configuration so a rule never has to
# name a site-specific point id or a raw source name.
POINT_ROLE_BY_SOURCE_NAME = {
    "RUN": "run_status",
    "ALARM": "alarm_status",
    "SAT": "supply_air_temperature",
    "RAT": "return_air_temperature",
    "SAT_SP": "supply_air_temperature_setpoint",
    "POWER_KW": "electrical_power",
    "ENERGY_KWH": "electrical_energy",
    "ROOM_TEMP": "room_air_temperature",
    "RH": "relative_humidity",
    "CO2": "co2_concentration",
}

ROLE_BY_BRICK_CLASS = {v: POINT_ROLE_BY_SOURCE_NAME[k] for k, v in POINT_CLASS_BY_SOURCE_NAME.items()}

# --- relationships ---------------------------------------------------------
# Brick relationships used, with their inverses. Traversal writes both
# directions so neither query path needs a recursive reverse scan.

HAS_PART = "brick:hasPart"
IS_PART_OF = "brick:isPartOf"
FEEDS = "brick:feeds"
IS_FED_BY = "brick:isFedBy"
HAS_LOCATION = "brick:hasLocation"
IS_LOCATION_OF = "brick:isLocationOf"
HAS_POINT = "brick:hasPoint"
IS_POINT_OF = "brick:isPointOf"

# Application metadata: the floor/room a meter or IAQ device *represents*.
# Brick's closest relationship is brick:meters / brick:isMeteredBy, which is
# defined for metering equipment only; we use an explicit app predicate so the
# semantics stay honest for IAQ devices too. See docs/02-brickschema-model.md.
MEASURES = "app:measures"
IS_MEASURED_BY = "app:isMeasuredBy"

INVERSE = {
    HAS_PART: IS_PART_OF,
    IS_PART_OF: HAS_PART,
    FEEDS: IS_FED_BY,
    IS_FED_BY: FEEDS,
    HAS_LOCATION: IS_LOCATION_OF,
    IS_LOCATION_OF: HAS_LOCATION,
    HAS_POINT: IS_POINT_OF,
    IS_POINT_OF: HAS_POINT,
    MEASURES: IS_MEASURED_BY,
    IS_MEASURED_BY: MEASURES,
}

BRICK_PREDICATES = {HAS_PART, IS_PART_OF, FEEDS, IS_FED_BY, HAS_LOCATION, IS_LOCATION_OF, HAS_POINT, IS_POINT_OF}
APP_PREDICATES = {MEASURES, IS_MEASURED_BY}


def expand(term: str) -> str:
    """Expand a `brick:`/`app:` prefixed term into a full IRI."""
    if term.startswith("brick:"):
        return BRICK_NAMESPACE + term.split(":", 1)[1]
    if term.startswith("app:"):
        return APP_NAMESPACE + term.split(":", 1)[1]
    return term
