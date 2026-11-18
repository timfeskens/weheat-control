"""Manual DHW schedule: slot encoding, gates, mode select, sensors and service."""

import json
from datetime import time as clock
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
import yaml
from aiohttp import ClientSession
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from test_controls import coordinator, settings
from test_weheat import config_entry

from custom_components.weheat_control import async_setup
from custom_components.weheat_control.api import Installation, WeheatApi, WeheatError
from custom_components.weheat_control.const import DOMAIN
from custom_components.weheat_control.controls import (
    DAYS,
    DHW_MODES,
    dhw_full_schedule,
    dhw_intelligence_properties,
    dhw_schedule_available,
    dhw_schedule_periods,
    dhw_schedule_slots,
    validate_write,
)
from custom_components.weheat_control.coordinator import WeheatCoordinator, readback_matches
from custom_components.weheat_control.select import DhwMode
from custom_components.weheat_control.sensor import DhwScheduleSensor
from custom_components.weheat_control.services import SERVICE_SET_DHW_SCHEDULE, schedule_command

pytest_plugins = ["test_weheat"]


def day(start_slot: int, end_slot: int, value: int) -> list[int]:
    return [value if start_slot <= slot < end_slot else 0 for slot in range(96)]


def week(data: dict, **days: list[int]) -> dict:
    """The complete week as submitted: readback with optional replaced days."""
    return {f"DhwSchedule{name}Target": days.get(name, data[f"dhwSchedule{name}Target"]) for name in DAYS}


def live_settings(mode: int = 3) -> dict:
    """Shape of the reported live settings: weekdays 11–16 at 50 °C, weekend at 55 °C."""
    data = {**settings(), "dhwScheduleEnabled": mode, "dhwScheduleSafetyTemperature": 10}
    for name in DAYS[:5]:
        data[f"dhwSchedule{name}Target"] = day(44, 64, 100)
    data["dhwScheduleSaturdayTarget"] = day(40, 64, 110)
    data["dhwScheduleSundayTarget"] = day(44, 64, 110)
    return data


def test_slots_are_quarter_hours_in_half_degrees():
    data = live_settings()
    assert dhw_schedule_available(data)
    assert dhw_schedule_periods(data["dhwScheduleMondayTarget"]) == [
        {"start": "11:00", "end": "16:00", "temperature": 50.0}
    ]
    assert dhw_schedule_periods(data["dhwScheduleSaturdayTarget"]) == [
        {"start": "10:00", "end": "16:00", "temperature": 55.0}
    ]
    assert dhw_schedule_slots([(660, 960, 50)]) == data["dhwScheduleMondayTarget"]
    assert dhw_schedule_slots([]) == [0] * 96
    evening = dhw_schedule_slots([(6 * 60, 7 * 60 + 30, 52.5), (22 * 60, 0, 45)])
    assert evening[24:30] == [105] * 6 and evening[88:] == [90] * 8
    assert dhw_schedule_periods(evening) == [
        {"start": "06:00", "end": "07:30", "temperature": 52.5},
        {"start": "22:00", "end": "00:00", "temperature": 45.0},
    ]
    adjacent = dhw_schedule_slots([(0, 60, 40), (60, 120, 60)])
    assert len(dhw_schedule_periods(adjacent)) == 2


@pytest.mark.parametrize(
    "periods",
    [
        [(600, 610, 50)],  # not a quarter hour
        [(960, 660, 50)],  # ends before it starts
        [(660, 660, 50)],  # empty
        [(660, 960, 39.5)],
        [(660, 960, 60.5)],
        [(660, 960, 50.2)],
        [(660, 960, float("nan"))],
        [(600, 720, 50), (690, 800, 50)],  # overlap
    ],
)
def test_invalid_periods_are_rejected_not_rounded(periods):
    with pytest.raises(WeheatError):
        dhw_schedule_slots(periods)


@pytest.mark.parametrize(
    "change",
    [
        {"dhwAvailable": 0},
        {"dhwScheduleTuesdayTarget": None},
        {"dhwScheduleTuesdayTarget": [0] * 95},
        {"dhwScheduleTuesdayTarget": [0.5] + [0] * 95},
        {"dhwScheduleTuesdayTarget": [-2] + [0] * 95},
        {"dhwScheduleTuesdayTarget": [999] + [0] * 95},
    ],
)
def test_incomplete_or_unknown_schedule_is_unavailable(change):
    data = {**live_settings(), **change}
    assert not dhw_schedule_available(data)
    current = coordinator(data)
    assert not DhwScheduleSensor(current, "Monday").available
    assert DhwScheduleSensor(current, "Monday").native_value is None
    with pytest.raises(WeheatError, match="partial"):
        dhw_full_schedule(data)
    with pytest.raises(WeheatError):
        validate_write(week(live_settings()), data)


def test_write_contract_always_sends_the_complete_week():
    data = live_settings()
    assert dhw_full_schedule(data) == week(data)
    assert dhw_full_schedule(data, {"Monday": day(0, 8, 120)}) == week(data, Monday=day(0, 8, 120))
    validate_write(week(data, Monday=day(0, 8, 120)), data)
    validate_write({f"DhwSchedule{name}Target": [0] * 96 for name in DAYS}, data)
    data["dhwScheduleSundayTarget"] = day(0, 8, 130)  # 65 °C set in the app
    validate_write(week(data, Monday=[0] * 96), data)  # unchanged day may keep it
    for command in (
        {"DhwScheduleMondayTarget": day(0, 8, 100)},  # a single day is never sent
        week(data, Monday=day(0, 8, 130)),  # changed day outside the write range
        week(data, Monday=day(0, 8, 79)),
        week(data, Monday=[0] * 95),
        week(data, Monday=[0.0] * 96),
        {**week(data), "DhwTargetTemperature": 50},
        {**week(data), "DhwScheduleEnabled": 3},
        {**week(data), "DhwScheduleEnabled": 0},
        {**week(data), "DhwScheduleFundayTarget": [0] * 96},
        {"DhwScheduleSafetyTemperature": 20},
        {"DhwScheduleEnabled": 2},  # mode 2 only together with the full schedule
    ):
        with pytest.raises(WeheatError):
            validate_write(command, data)
    with pytest.raises(WeheatError):
        validate_write(week(data), data, {"DhwComfortPreference": 2})


def test_enabling_requires_a_non_empty_schedule():
    data = live_settings(0)
    validate_write({"DhwScheduleEnabled": 2, **week(data)}, data)
    with pytest.raises(WeheatError):
        validate_write({"DhwScheduleEnabled": 2, **week(data)}, data, {"DhwComfortPreference": 2})
    empty = {f"DhwSchedule{name}Target": [0] * 96 for name in DAYS}
    validate_write(empty, data)  # clearing is allowed while not enabling
    with pytest.raises(WeheatError):
        validate_write({"DhwScheduleEnabled": 2, **empty}, data)
    with pytest.raises(WeheatError):
        validate_write({"DhwScheduleEnabled": 2, **week(data)}, settings())  # arrays absent


def test_readback_of_full_arrays_is_exact():
    data = live_settings()
    assert readback_matches(data, {"DhwScheduleMondayTarget": day(44, 64, 100)})
    assert not readback_matches(data, {"DhwScheduleMondayTarget": day(44, 64, 110)})


def test_sensors_summarise_each_weekday():
    data = live_settings(2)
    current = coordinator(data)
    monday = DhwScheduleSensor(current, "Monday")
    assert monday.available
    assert monday.unique_id == "installation-1_dhw_schedule_monday"
    assert monday.native_value == "11:00–16:00 50 °C"
    assert monday.extra_state_attributes == {
        "periods": [{"start": "11:00", "end": "16:00", "temperature": 50.0}],
        "schedule_active": True,
        "safety_temperature": 10,
    }
    assert DhwScheduleSensor(current, "Saturday").native_value == "10:00–16:00 55 °C"
    data["dhwScheduleSundayTarget"] = [0] * 96
    assert DhwScheduleSensor(current, "Sunday").native_value == "No periods"
    data["dhwScheduleSundayTarget"] = [80, 0] * 48
    assert DhwScheduleSensor(current, "Sunday").native_value == "48 periods"


@pytest.mark.parametrize("mode", [0, 2, 3])
async def test_mode_select_has_the_three_app_choices_and_follows_readback(mode):
    data = live_settings(mode)
    current = coordinator(data)
    select = DhwMode(current)
    assert select.available
    assert select.options == ["Fixed temperature", "Manual schedule", "Weheat Intelligence"]
    assert DHW_MODES[select.current_option] == mode
    for option in select.options:
        current.async_write.reset_mock()
        await select.async_select_option(option)
        if DHW_MODES[option] == mode:
            current.async_write.assert_not_awaited()  # already active: nothing sent
            continue
        call = current.async_write.await_args
        if option == "Fixed temperature":
            assert call.args == ({"DhwScheduleEnabled": 0},) and not call.kwargs
        elif option == "Weheat Intelligence":
            assert call.args == ({"DhwScheduleEnabled": 3},)
            assert call.kwargs == {"properties": dhw_intelligence_properties}
        else:
            build = call.args[0]
            assert build(data) == {"DhwScheduleEnabled": 2, **week(data)}
            with pytest.raises(WeheatError, match="no periods"):
                build({**data, **{f"dhwSchedule{name}Target": [0] * 96 for name in DAYS}})
            with pytest.raises(WeheatError, match="partial"):
                build(settings())
    assert DHW_MODES[select.current_option] == mode  # no optimistic state
    with pytest.raises(HomeAssistantError):
        await select.async_select_option("Off")


@pytest.mark.parametrize("change", [{"dhwScheduleEnabled": 1}, {"dhwScheduleEnabled": None}, {"dhwAvailable": 0}])
def test_mode_select_unavailable_for_unknown_mode(change):
    select = DhwMode(coordinator({**live_settings(), **change}))
    assert not select.available
    assert select.current_option is None


def test_service_command_replaces_selected_days_in_the_full_week():
    data = live_settings()
    periods = [{"start": clock(6), "end": clock(7, 30), "temperature": 52.5}]
    build = schedule_command(["saturday", "monday"], periods)
    assert build(data) == week(data, Monday=day(24, 30, 105), Saturday=day(24, 30, 105))
    assert schedule_command(["sunday"], [])(data) == week(data, Sunday=[0] * 96)
    with pytest.raises(WeheatError, match="partial"):
        build(settings())
    with pytest.raises(ServiceValidationError):
        schedule_command([], periods)
    with pytest.raises(ServiceValidationError):
        schedule_command(["monday"], [{"start": clock(11, 5), "end": clock(16), "temperature": 50}])
    with pytest.raises(ServiceValidationError):
        schedule_command(["monday"], [{"start": clock(11, 0, 30), "end": clock(16), "temperature": 50}])


async def test_service_validates_and_submits_through_coordinator(hass):
    assert await async_setup(hass, {})
    assert hass.services.has_service(DOMAIN, SERVICE_SET_DHW_SCHEDULE)
    entry = Mock(domain=DOMAIN, state=ConfigEntryState.LOADED)
    entry.runtime_data.async_write = AsyncMock()
    hass.config_entries = Mock()
    hass.config_entries.async_get_entry = Mock(return_value=entry)
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_DHW_SCHEDULE,
        {
            "config_entry_id": "entry-1",
            "days": ["tuesday"],
            "periods": [{"start": "06:00", "end": "07:30", "temperature": 52.5}],
        },
        blocking=True,
    )
    build = entry.runtime_data.async_write.await_args.args[0]
    data = live_settings()
    assert build(data) == week(data, Tuesday=day(24, 30, 105))
    for data in (
        {"config_entry_id": "entry-1", "days": ["funday"]},
        {"config_entry_id": "entry-1", "days": []},
        {"config_entry_id": "entry-1", "days": ["monday"], "periods": [{"start": "06:00", "end": "07:00"}]},
    ):
        with pytest.raises(Exception):
            await hass.services.async_call(DOMAIN, SERVICE_SET_DHW_SCHEDULE, data, blocking=True)
    entry.state = ConfigEntryState.NOT_LOADED
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, SERVICE_SET_DHW_SCHEDULE, {"config_entry_id": "entry-1", "days": ["monday"]}, blocking=True
        )
    assert entry.runtime_data.async_write.await_count == 1


async def test_schedule_write_is_fresh_durable_and_readback_verified(hass):
    async with ClientSession() as session:
        api = WeheatApi(session)
        current = WeheatCoordinator(hass, config_entry(), api)
        data = live_settings(3)
        command = week(data, Monday=day(24, 30, 105))
        with (
            patch.object(api, "settings", new_callable=AsyncMock, return_value=data),
            patch.object(
                api, "installations", new_callable=AsyncMock, return_value=[Installation("installation-1", "board-1")]
            ),
            patch.object(api, "patch", new_callable=AsyncMock, return_value="request-1") as submit,
            patch.object(current, "async_request_refresh", new_callable=AsyncMock),
        ):
            await current.async_write(
                schedule_command(["monday"], [{"start": clock(6), "end": clock(7, 30), "temperature": 52.5}])
            )
            submit.assert_awaited_once_with("board-1", command)
        restored = WeheatCoordinator(hass, current.entry, api)
        await restored.async_load_operation()
        assert restored.operation["settings"] == command
        with (
            patch.object(api, "settings", new_callable=AsyncMock, return_value=data) as read,
            patch.object(
                api, "installations", new_callable=AsyncMock, return_value=[Installation("installation-1", "board-1")]
            ),
            patch.object(api, "status", new_callable=AsyncMock, return_value=2),
        ):
            await restored._async_update_data()
            assert restored.operation["state"] == "succeeded_unverified"
            read.return_value = {**data, "dhwScheduleMondayTarget": day(24, 30, 105)}
            await restored._async_update_data()
            assert restored.operation["state"] == "succeeded"
        assert data["dhwScheduleEnabled"] == 3  # the schedule edit never changes the mode


async def test_uncertain_schedule_write_blocks_replay(hass):
    async with ClientSession() as session:
        api = WeheatApi(session)
        current = WeheatCoordinator(hass, config_entry(), api)
        with (
            patch.object(api, "settings", new_callable=AsyncMock, return_value=live_settings(0)),
            patch.object(
                api, "installations", new_callable=AsyncMock, return_value=[Installation("installation-1", "board-1")]
            ),
            patch.object(
                api, "patch", new_callable=AsyncMock, side_effect=WeheatError("timeout", code="timeout")
            ) as submit,
        ):
            with pytest.raises(HomeAssistantError, match="uncertain"):
                await DhwMode(current).async_select_option("Manual schedule")
            with pytest.raises(HomeAssistantError, match="unresolved"):
                await current.async_write(schedule_command(["monday"], []))
            submit.assert_awaited_once_with("board-1", {"DhwScheduleEnabled": 2, **week(live_settings())})


def test_service_definition_matches_translations():
    component = Path(__file__).parents[1] / "custom_components" / "weheat_control"
    services = yaml.safe_load((component / "services.yaml").read_text())
    strings = json.loads((component / "strings.json").read_text())
    assert set(services) == set(strings["services"]) == {SERVICE_SET_DHW_SCHEDULE}
    assert set(services[SERVICE_SET_DHW_SCHEDULE]["fields"]) == set(
        strings["services"][SERVICE_SET_DHW_SCHEDULE]["fields"]
    )
    options = services[SERVICE_SET_DHW_SCHEDULE]["fields"]["days"]["selector"]["select"]["options"]
    assert options == [name.lower() for name in DAYS] == list(strings["selector"]["weekday"]["options"])
