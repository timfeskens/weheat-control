"""Supported app-derived settings only; no generic arbitrary settings service.

Bounds/enums are recovered app UI rules, not a manufacturer safety guarantee.
See docs/control-evidence.md for sources and unresolved device capabilities.
"""

import math
import time

from .api import Settings, WeheatError

DAY_PRESETS = {"Normal": 0, "Background chatter": 1, "Light rain": 2, "Library": 3, "Whisper": 4}
# Minimum (7) is model-specific. Don't expose it without verified model identity.
NIGHT_PRESETS = {
    "Schedule disabled": 0,
    "Normal": 1,
    "Background chatter": 2,
    "Light rain": 3,
    "Library": 4,
    "Off during scheduled hours": 5,
    "Whisper": 6,
}
CONTROL_METHODS = {
    0: "None",
    1: "Heating curve",
    2: "On/off thermostat",
    3: "Base OpenTherm",
    4: "Smart OpenTherm",
    5: "Cloud thermostat",
    6: "Manual power",
    7: "Auto input select",
    8: "Manual setpoint",
    9: "Weheat Intelligence",
}
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
DHW_COMFORT_PREFERENCES = {"Safe": 1, "Balanced": 2, "Eco": 3}


def integer(value: object, minimum: int, maximum: int) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value == int(value) and minimum <= value <= maximum


def dhw_target_available(current: Settings) -> bool:
    # Mode is an enum, not a boolean: 0 fixed, 2 manual schedule, 3 intelligence.
    return (
        type(current.get("dhwAvailable")) is int
        and current.get("dhwAvailable") == 1
        and type(current.get("dhwScheduleEnabled")) is int
        and current.get("dhwScheduleEnabled") == 0
        and integer(current.get("dhwTargetTemperature"), 40, 60)
    )


def dhw_intelligence_available(current: Settings) -> bool:
    return (
        type(current.get("dhwAvailable")) is int
        and current.get("dhwAvailable") == 1
        and type(current.get("dhwScheduleEnabled")) is int
        and current.get("dhwScheduleEnabled") in (0, 2, 3)
    )


def dhw_intelligence_properties(current: Settings) -> Settings:
    """Preserve a known comfort preference; otherwise use the app's Balanced default."""
    preference = current.get("dhwComfortPreference")
    return {"DhwComfortPreference": preference if type(preference) is int and preference in (1, 2, 3) else 2}


def dhw_comfort_available(current: Settings) -> bool:
    """Never invent a preference or enable Intelligence to expose this control."""
    return (
        dhw_intelligence_available(current)
        and current.get("dhwScheduleEnabled") == 3
        and type(current.get("dhwComfortPreference")) is int
        and current.get("dhwComfortPreference") in DHW_COMFORT_PREFERENCES.values()
    )


def schedule_available(current: Settings) -> bool:
    return all(
        integer(current.get(f"silent{part}{day}{edge}"), 0, maximum)
        for day in DAYS
        for edge in ("Start", "Stop")
        for part, maximum in (("Hours", 23), ("Minutes", 59))
    )


def curve_available(current: Settings) -> bool:
    x, y = current.get("heatingCurveX"), current.get("heatingCurveY")
    return (
        type(current.get("controlMethod")) is int
        and current.get("controlMethod") in (1, 2)
        and isinstance(x, list)
        and isinstance(y, list)
        and 1 <= len(x) == len(y) <= 20
        and all(type(value) in (int, float) and math.isfinite(value) for value in x)
        and all(integer(value, 18, 60) for value in y)
    )


def curve_point(current: Settings, index: int, value: float) -> Settings:
    if not curve_available(current) or not integer(value, 18, 60):
        raise WeheatError("Heating-curve editing is unavailable for the current control method or range")
    curve = current.get("heatingCurveY")
    if not isinstance(curve, list) or not 0 <= index < len(curve):
        raise WeheatError("Heating-curve point no longer exists")
    updated = list(curve)
    updated[index] = int(value)
    method = current.get("controlMethod")
    if type(method) is not int:
        raise WeheatError("Unknown control method")
    return {"HeatingCurveY": updated, "ControlMethod": method}


def validate_write(settings: Settings, current: Settings, properties: Settings | None = None) -> None:
    """Validate against freshly read state immediately before submission."""
    if not settings and properties and set(properties) == {"DhwComfortPreference"}:
        preference = properties["DhwComfortPreference"]
        if (
            dhw_comfort_available(current)
            and type(preference) is int
            and preference in DHW_COMFORT_PREFERENCES.values()
        ):
            return
        raise WeheatError("Hot-water Intelligence comfort preference is unavailable or invalid")
    if set(settings) == {"DhwScheduleEnabled"}:
        mode = settings["DhwScheduleEnabled"]
        if dhw_intelligence_available(current) and type(mode) is int:
            if mode == 0 and not properties:
                return
            if mode == 3 and properties == dhw_intelligence_properties(current):
                if properties and type(properties.get("DhwComfortPreference")) is int:
                    return
        raise WeheatError("Hot-water Intelligence is unavailable or its mode/comfort payload is invalid")
    if properties:
        raise WeheatError("Properties are only supported for hot-water Intelligence and its comfort preference")
    if set(settings) == {"DhwBoostEndTime"}:
        end = settings["DhwBoostEndTime"]
        if (
            isinstance(end, str)
            and end.isdigit()
            and len(end) <= 12
            and -60 <= int(end) - int(time.time()) <= 7200
            and type(current.get("dhwAvailable")) is int
            and current.get("dhwAvailable") == 1
        ):
            return
    elif set(settings) == {"DhwTargetTemperature"}:
        if dhw_target_available(current) and integer(settings["DhwTargetTemperature"], 40, 60):
            return
    elif len(settings) == 1:
        for write_key, read_key, presets in (
            ("PerformanceModePreset", "performanceModePreset", DAY_PRESETS),
            ("SilentHoursScheduledPreset", "silentHoursScheduledPreset", NIGHT_PRESETS),
        ):
            if write_key in settings and type(current.get(read_key)) is int and current[read_key] in presets.values():
                if type(settings[write_key]) is int and settings[write_key] in presets.values():
                    return
    elif set(settings) == {"HeatingCurveY", "ControlMethod"}:
        curve = settings["HeatingCurveY"]
        old = current.get("heatingCurveY")
        if (
            curve_available(current)
            and settings["ControlMethod"] == current["controlMethod"]
            and isinstance(curve, list)
            and isinstance(old, list)
            and len(curve) == len(old)
            and all(integer(value, 18, 60) for value in curve)
            and sum(left != right for left, right in zip(curve, old, strict=True)) <= 1
        ):
            return
    if schedule_available(current):
        for day in DAYS:
            for edge in ("Start", "Stop"):
                hour, minute = f"SilentHours{day}{edge}", f"SilentMinutes{day}{edge}"
                if (
                    set(settings) == {hour, minute}
                    and integer(settings[hour], 0, 23)
                    and integer(settings[minute], 0, 59)
                ):
                    return
    raise WeheatError("Unsupported setting, unavailable capability/mode, or value outside the recovered app range")
