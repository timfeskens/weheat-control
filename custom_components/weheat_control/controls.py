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
# Current app (Intelligence "Normaal"/"Zuinig"), observed by toggling the app: it changes the
# hot-water safety temperature in settings and installationSystem.dhwComfortPreference.
DHW_COMFORT_LEVELS = {"Normal": 40, "Eco": 10}
DHW_COMFORT_CODES = {"Normal": 6, "Eco": 5}
# Old app codes (Safe, Balanced, Eco) remain valid to preserve when enabling Intelligence.
_KNOWN_COMFORT_CODES = (1, 2, 3, *DHW_COMFORT_CODES.values())
# The app's three hot-water modes (dhwScheduleEnabled). Value 1 is not used by the app.
DHW_MODES = {"Fixed temperature": 0, "Manual schedule": 2, "Weheat Intelligence": 3}
# Manual DHW schedule: 96 quarter-hour slots per weekday. A slot holds the target
# in half degrees Celsius (100 = 50 °C); 0 means no target (safety temperature).
DHW_SCHEDULE_SLOTS = 96
DHW_SCHEDULE_SLOT_MINUTES = 15
DHW_SCHEDULE_MIN_TEMPERATURE = 40
DHW_SCHEDULE_MAX_TEMPERATURE = 60
# Readback may contain app-set values outside the write range; display them, never invent them.
_DHW_SCHEDULE_READ_MAX = 160


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
    return {"DhwComfortPreference": preference if type(preference) is int and preference in _KNOWN_COMFORT_CODES else 2}


def dhw_comfort_available(current: Settings) -> bool:
    """Intelligence mode with a reported safety temperature; never enable Intelligence for this."""
    return (
        dhw_intelligence_available(current)
        and current.get("dhwScheduleEnabled") == 3
        and type(current.get("dhwScheduleSafetyTemperature")) is int
    )


def dhw_comfort_option(current: Settings) -> str | None:
    """The app shows the overview's comfort code; fall back to the safety temperature without it."""
    if not dhw_comfort_available(current):
        return None
    code = current.get("dhwComfortPreference")
    if code is not None:
        # An unknown code is shown as unknown, never mapped by temperature.
        return next((name for name, value in DHW_COMFORT_CODES.items() if type(code) is int and value == code), None)
    level = current["dhwScheduleSafetyTemperature"]
    return next((name for name, value in DHW_COMFORT_LEVELS.items() if value == level), None)


def dhw_schedule_read_key(day: str) -> str:
    return f"dhwSchedule{day}Target"


def dhw_schedule_write_key(day: str) -> str:
    return f"DhwSchedule{day}Target"


def _dhw_schedule_slots_valid(slots: object, writable: bool) -> bool:
    if not isinstance(slots, list) or len(slots) != DHW_SCHEDULE_SLOTS:
        return False
    low, high = (
        (2 * DHW_SCHEDULE_MIN_TEMPERATURE, 2 * DHW_SCHEDULE_MAX_TEMPERATURE)
        if writable
        else (1, _DHW_SCHEDULE_READ_MAX)
    )
    return all(type(value) is int and (value == 0 or low <= value <= high) for value in slots)


def dhw_schedule_available(current: Settings) -> bool:
    """All seven weekday arrays must be present and valid; never assume a default schedule."""
    return (
        type(current.get("dhwAvailable")) is int
        and current.get("dhwAvailable") == 1
        and all(_dhw_schedule_slots_valid(current.get(dhw_schedule_read_key(day)), False) for day in DAYS)
    )


def dhw_full_schedule(current: Settings, replacements: dict[str, list[int]] | None = None) -> Settings:
    """All seven weekday arrays: replaced days plus unchanged readback for the others."""
    if not dhw_schedule_available(current):
        raise WeheatError("The hot-water schedule readback is incomplete; refusing to send a partial schedule")
    replacements = replacements or {}
    return {
        dhw_schedule_write_key(day): list(replacements.get(day, current[dhw_schedule_read_key(day)]))  # type: ignore[arg-type]
        for day in DAYS
    }


def dhw_schedule_periods(slots: list[int]) -> list[dict[str, str | float]]:
    """Collapse quarter-hour slots into periods of equal, non-zero target."""
    periods: list[dict[str, str | float]] = []
    start = 0
    for index in range(1, DHW_SCHEDULE_SLOTS + 1):
        if index < DHW_SCHEDULE_SLOTS and slots[index] == slots[start]:
            continue
        if slots[start]:
            periods.append(
                {
                    "start": _slot_time(start),
                    "end": _slot_time(index),
                    "temperature": slots[start] / 2,
                }
            )
        start = index
    return periods


def _slot_time(slot: int) -> str:
    minutes = slot * DHW_SCHEDULE_SLOT_MINUTES
    return f"{minutes // 60 % 24:02d}:{minutes % 60:02d}"


def dhw_schedule_slots(periods: list[tuple[int, int, float]]) -> list[int]:
    """Build a day from (start minute, end minute, °C) periods; end 0 means midnight.

    Uncovered slots are 0 (no target). Overlaps, unaligned times and
    out-of-range temperatures are rejected rather than rounded.
    """
    slots = [0] * DHW_SCHEDULE_SLOTS
    for start, end, temperature in periods:
        end = end or 24 * 60
        if start % DHW_SCHEDULE_SLOT_MINUTES or end % DHW_SCHEDULE_SLOT_MINUTES or not 0 <= start < end <= 24 * 60:
            raise WeheatError("Schedule periods must start before they end, on whole quarter hours")
        if (
            not math.isfinite(temperature)
            or temperature * 2 != int(temperature * 2)
            or not DHW_SCHEDULE_MIN_TEMPERATURE <= temperature <= DHW_SCHEDULE_MAX_TEMPERATURE
        ):
            raise WeheatError(
                f"Schedule temperature must be {DHW_SCHEDULE_MIN_TEMPERATURE}–{DHW_SCHEDULE_MAX_TEMPERATURE} °C "
                "in 0.5 °C steps"
            )
        for slot in range(start // DHW_SCHEDULE_SLOT_MINUTES, end // DHW_SCHEDULE_SLOT_MINUTES):
            if slots[slot]:
                raise WeheatError("Schedule periods overlap")
            slots[slot] = int(temperature * 2)
    return slots


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
    if set(settings) == {"DhwScheduleSafetyTemperature"}:
        # Both halves of the app's choice: safety temperature plus the matching comfort code.
        level = settings["DhwScheduleSafetyTemperature"]
        if dhw_comfort_available(current) and any(
            type(level) is int and level == DHW_COMFORT_LEVELS[name] and properties == {"DhwComfortPreference": code}
            for name, code in DHW_COMFORT_CODES.items()
        ):
            return
        raise WeheatError("Hot-water Intelligence comfort setting is unavailable or invalid")
    if set(settings) == {"DhwScheduleEnabled"}:
        mode = settings["DhwScheduleEnabled"]
        if dhw_intelligence_available(current) and type(mode) is int:
            if mode == 0 and not properties:
                return
            if mode == 3 and properties == dhw_intelligence_properties(current):
                if properties and type(properties.get("DhwComfortPreference")) is int:
                    return
        raise WeheatError("Hot-water mode change is unavailable or its mode/comfort payload is invalid")
    if properties:
        raise WeheatError("Properties are only supported for hot-water Intelligence and its comfort preference")
    schedule_keys = {dhw_schedule_write_key(day): dhw_schedule_read_key(day) for day in DAYS}
    if set(settings) in (set(schedule_keys), {*schedule_keys, "DhwScheduleEnabled"}):
        # Always the complete week. Changed days must be in the write range; unchanged
        # days may keep app-set readback values.
        if dhw_schedule_available(current) and all(
            _dhw_schedule_slots_valid(settings[write], True) or settings[write] == current[read]
            for write, read in schedule_keys.items()
        ):
            if "DhwScheduleEnabled" not in settings:
                return
            # Never enable an empty schedule: that would leave only the safety temperature.
            if (
                type(settings["DhwScheduleEnabled"]) is int
                and settings["DhwScheduleEnabled"] == 2
                and dhw_intelligence_available(current)
                and any(value for key in schedule_keys for value in settings[key])  # type: ignore[union-attr]
            ):
                return
        raise WeheatError("The hot-water schedule must be complete, valid and non-empty when enabled")
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
