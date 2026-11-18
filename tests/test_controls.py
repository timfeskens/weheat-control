"""Setting contracts and actual HA entities, without device traffic."""

import importlib
import json
from datetime import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.weheat_control import PLATFORMS
from custom_components.weheat_control.api import Settings, WeheatError
from custom_components.weheat_control.const import INSTALLATION_ID
from custom_components.weheat_control.controls import (
    CONTROL_METHODS,
    DAY_PRESETS,
    DAYS,
    NIGHT_PRESETS,
    curve_available,
    curve_point,
    dhw_target_available,
    schedule_available,
    validate_write,
)
from custom_components.weheat_control.diagnostics import async_get_config_entry_diagnostics
from custom_components.weheat_control.number import DhwTarget, HeatingCurvePoint
from custom_components.weheat_control.number import async_setup_entry as setup_numbers
from custom_components.weheat_control.select import QuietPreset
from custom_components.weheat_control.sensor import BoostEndSensor, ControlMethodSensor, DhwTargetSensor
from custom_components.weheat_control.time import QuietTime


def settings() -> Settings:
    result: Settings = {
        "dhwAvailable": 1,
        "dhwTargetTemperature": 55,
        "dhwScheduleEnabled": 0,
        "performanceModePreset": 1,
        "silentHoursScheduledPreset": 3,
        "controlMethod": 1,
        "heatingCurveX": [-20, 9, 15, 20, 25],
        "heatingCurveY": [42, 27, 23, 20, 18],
    }
    for day in DAYS:
        for edge, hour in (("Start", 19), ("Stop", 7)):
            result[f"silentHours{day}{edge}"] = hour
            result[f"silentMinutes{day}{edge}"] = 0
    return result


def coordinator(data: Settings):
    current = Mock()
    current.data = data
    current.entry.data = {INSTALLATION_ID: "installation-1"}
    current.last_update_success = True
    current.async_write = AsyncMock()
    return current


def test_dhw_bounds_and_modes_fail_closed() -> None:
    data = settings()
    assert dhw_target_available(data)
    for value in (40, 55, 60):
        validate_write({"DhwTargetTemperature": value}, data)
    for value in (39, 61, 50.5, True, float("nan"), "55"):
        with pytest.raises(WeheatError):
            validate_write({"DhwTargetTemperature": value}, data)
    for mode in (2, 3, True, "0", -1):
        data["dhwScheduleEnabled"] = mode
        assert not dhw_target_available(data)
        with pytest.raises(WeheatError):
            validate_write({"DhwTargetTemperature": 55}, data)


def test_quiet_presets_not_interchangeable_and_unknowns_not_defaulted() -> None:
    assert DAY_PRESETS["Background chatter"] == 1
    assert NIGHT_PRESETS["Background chatter"] == 2
    assert NIGHT_PRESETS["Schedule disabled"] == 0
    assert NIGHT_PRESETS["Off during scheduled hours"] == 5
    assert 7 not in NIGHT_PRESETS.values()
    for value in DAY_PRESETS.values():
        validate_write({"PerformanceModePreset": value}, settings())
    for value in NIGHT_PRESETS.values():
        validate_write({"SilentHoursScheduledPreset": value}, settings())
    for command in (
        {"PerformanceModePreset": 5},
        {"SilentHoursScheduledPreset": 7},
        {"SilentHoursScheduledPreset": True},
    ):
        with pytest.raises(WeheatError):
            validate_write(command, settings())
    data = settings()
    data["performanceModePreset"] = 99
    with pytest.raises(WeheatError):
        validate_write({"PerformanceModePreset": 1}, data)


def test_schedule_requires_all_fields_and_only_edits_one_pair() -> None:
    data = settings()
    assert schedule_available(data)
    command = {"SilentHoursTuesdayStart": 22, "SilentMinutesTuesdayStart": 30}
    validate_write(command, data)
    for invalid in (
        {"SilentHoursTuesdayStart": 22},
        {**command, "SilentMinutesTuesdayStart": 60},
        {**command, "SilentHoursTuesdayStart": -1},
    ):
        with pytest.raises(WeheatError):
            validate_write(invalid, data)
    del data["silentMinutesSundayStop"]
    assert not schedule_available(data)
    with pytest.raises(WeheatError):
        validate_write(command, data)


def test_curve_preserves_whole_array_and_control_method() -> None:
    data = settings()
    command = curve_point(data, 1, 28)
    assert command == {"HeatingCurveY": [42, 28, 23, 20, 18], "ControlMethod": 1}
    assert data["heatingCurveY"] == [42, 27, 23, 20, 18]
    validate_write(command, data)
    data["controlMethod"] = 4
    assert CONTROL_METHODS[4] == "Smart OpenTherm"
    assert not curve_available(data)
    with pytest.raises(WeheatError):
        curve_point(data, 0, 43)
    with pytest.raises(WeheatError):
        validate_write(command, data)
    for command in ({"ControlMethod": 1}, {"ConsumerCoolingSetpoint": 18}, {"DhwLegionellaPrevention": 0}):
        with pytest.raises(WeheatError):
            validate_write(command, data)


async def test_native_quiet_and_schedule_entities_build_exact_payloads() -> None:
    current = coordinator(settings())
    day = QuietPreset(current, "PerformanceModePreset", "performanceModePreset", "Day", DAY_PRESETS)
    night = QuietPreset(current, "SilentHoursScheduledPreset", "silentHoursScheduledPreset", "Night", NIGHT_PRESETS)
    assert day.current_option == "Background chatter"
    assert night.current_option == "Light rain"
    await day.async_select_option("Whisper")
    current.async_write.assert_awaited_with({"PerformanceModePreset": 4})
    await night.async_select_option("Off during scheduled hours")
    current.async_write.assert_awaited_with({"SilentHoursScheduledPreset": 5})
    with pytest.raises(HomeAssistantError):
        await day.async_select_option("not-a-preset")
    schedule = QuietTime(current, "Tuesday", "Start")
    assert schedule.entity_registry_enabled_default
    assert schedule.native_value == time(19)
    await schedule.async_set_value(time(22, 30))
    current.async_write.assert_awaited_with({"SilentHoursTuesdayStart": 22, "SilentMinutesTuesdayStart": 30})
    with pytest.raises(HomeAssistantError):
        await schedule.async_set_value(time(22, 30, 1))


async def test_native_number_entities_and_actual_p60_modes() -> None:
    data = settings()
    current = coordinator(data)
    target = DhwTarget(current)
    assert target.available
    assert target.native_value == 55
    await target.async_set_native_value(56)
    current.async_write.assert_awaited_with({"DhwTargetTemperature": 56})
    point = HeatingCurvePoint(current, 1)
    assert point.entity_registry_enabled_default
    assert point.available
    data["controlMethod"] = 4
    data["dhwScheduleEnabled"] = 3
    assert not target.available
    assert not point.available
    assert DhwTargetSensor(current).native_value == 55
    assert ControlMethodSensor(current).native_value == "Smart OpenTherm"
    data["dhwBoostEndTime"] = "2026-07-26T12:28:27Z"
    assert BoostEndSensor(current).native_value.isoformat() == "2026-07-26T12:28:27+00:00"
    assert len(PLATFORMS) == 5


def test_packaging_and_platform_imports() -> None:
    root = Path(__file__).parents[1]
    component = root / "custom_components" / "weheat_control"
    manifest = json.loads((component / "manifest.json").read_text())
    assert manifest["domain"] == "weheat_control"
    assert manifest["config_flow"] is True
    assert manifest["iot_class"] == "cloud_polling"
    assert "options" not in json.loads((component / "strings.json").read_text())
    assert json.loads((root / "hacs.json").read_text())["homeassistant"] == "2026.10.0"
    assert json.loads((component / "strings.json").read_text()) == json.loads(
        (component / "translations" / "en.json").read_text()
    )
    for platform in PLATFORMS:
        module = importlib.import_module(f"custom_components.weheat_control.{platform}")
        assert callable(module.async_setup_entry)


async def test_diagnostics_does_not_export_tokens_or_ids() -> None:
    current = coordinator(settings())
    current.operation = {"state": "unknown", "board_id": "secret-board", "request_id": "private-request"}
    entry = Mock()
    entry.runtime_data = current
    entry.options = {}
    entry.data = {"tokens": {"access_token": "secret-access", "refresh_token": "secret-refresh"}}
    result = await async_get_config_entry_diagnostics(Mock(), entry)
    text = json.dumps(result)
    for secret in ("secret-access", "secret-refresh", "secret-board", "private-request"):
        assert secret not in text
    assert result["operation_state"] == "unknown"


async def test_curve_entities_appear_when_official_app_changes_to_supported_method() -> None:
    data = settings()
    data["controlMethod"] = 4
    current = coordinator(data)
    entry = Mock()
    entry.runtime_data = current
    add = Mock()
    await setup_numbers(Mock(), entry, add)
    assert len(add.call_args_list) == 1  # DHW number only under Smart OpenTherm.
    listener = current.async_add_listener.call_args.args[0]
    data["controlMethod"] = 1
    listener()
    assert len(add.call_args_list[1].args[0]) == 5
    listener()
    assert len(add.call_args_list) == 2  # no duplicate entities
