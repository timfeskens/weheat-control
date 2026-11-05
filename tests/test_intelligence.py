"""DHW-only Intelligence payload, readback, persistence and gate coverage."""

import time
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientSession
from homeassistant.exceptions import HomeAssistantError
from test_controls import coordinator, settings
from test_weheat import config_entry

from custom_components.weheat_control.api import Installation, Tokens, WeheatApi, WeheatError
from custom_components.weheat_control.controls import (
    dhw_intelligence_available,
    dhw_intelligence_properties,
    dhw_target_available,
    validate_write,
)
from custom_components.weheat_control.coordinator import WeheatCoordinator
from custom_components.weheat_control.switch import DhwIntelligenceSwitch

pytest_plugins = ["test_weheat"]


@pytest.mark.parametrize("mode", [0, 2, 3])
async def test_switch_uses_confirmed_dhw_state_and_exact_commands(mode):
    data = {**settings(), "dhwScheduleEnabled": mode, "controlMethod": 4}
    current = coordinator(data)
    entity = DhwIntelligenceSwitch(current)
    assert entity.available
    assert entity.is_on is (mode == 3)
    assert entity.entity_registry_enabled_default
    await entity.async_turn_on()
    current.async_write.assert_awaited_with({"DhwScheduleEnabled": 3}, properties=dhw_intelligence_properties)
    assert entity.is_on is (mode == 3)  # no optimistic updates
    await entity.async_turn_off()
    current.async_write.assert_awaited_with({"DhwScheduleEnabled": 0})
    assert data["dhwTargetTemperature"] == 55
    assert data["controlMethod"] == 4
    data["dhwScheduleEnabled"] = 0
    assert entity.is_on is False
    assert dhw_target_available(data)


@pytest.mark.parametrize(
    "change",
    [
        {"dhwAvailable": 0},
        {"dhwAvailable": True},
        {"dhwScheduleEnabled": True},
        {"dhwScheduleEnabled": "3"},
        {"dhwScheduleEnabled": 99},
        {"dhwScheduleEnabled": None},
    ],
)
def test_unknown_capability_or_mode_is_unavailable(change):
    current = coordinator({**settings(), **change})
    entity = DhwIntelligenceSwitch(current)
    assert not dhw_intelligence_available(current.data)
    assert not entity.available
    assert entity.is_on is None
    for mode, properties in ((0, None), (3, {"DhwComfortPreference": 2})):
        with pytest.raises(WeheatError):
            validate_write({"DhwScheduleEnabled": mode}, current.data, properties)


@pytest.mark.parametrize("preference,expected", [(1, 1), (2, 2), (3, 3), (None, 2), (0, 2), (True, 2), (99, 2)])
def test_enable_preserves_valid_comfort_otherwise_uses_balanced(preference, expected):
    data = {**settings(), "dhwComfortPreference": preference}
    properties = dhw_intelligence_properties(data)
    assert properties == {"DhwComfortPreference": expected}
    validate_write({"DhwScheduleEnabled": 3}, data, properties)
    validate_write({"DhwScheduleEnabled": 0}, data)


@pytest.mark.parametrize(
    "command,properties",
    [
        ({"DhwScheduleEnabled": True}, None),
        ({"DhwScheduleEnabled": 2}, None),
        ({"DhwScheduleEnabled": 3}, None),
        ({"DhwScheduleEnabled": 3}, {"DhwComfortPreference": True}),
        ({"DhwScheduleEnabled": 3}, {"DhwComfortPreference": 99}),
        ({"DhwScheduleEnabled": 0}, {"DhwComfortPreference": 2}),
        ({"DhwScheduleEnabled": 3, "ControlMethod": 9}, {"DhwComfortPreference": 2}),
        ({"PerformanceModePreset": 2}, {"DhwComfortPreference": 2}),
        ({"DhwScheduleEnabled": 3}, {"DhwComfortPreference": 2, "Other": 1}),
    ],
)
def test_only_narrow_mode_property_contract_allowed(command, properties):
    with pytest.raises(WeheatError):
        validate_write(command, settings(), properties)


async def test_api_enable_body_includes_properties_without_retry():
    async with ClientSession() as session:
        api = WeheatApi(session, Tokens("fake", "fake-refresh", time.time() + 300))
        with patch.object(
            api, "_http", new_callable=AsyncMock, return_value={"settingsRequestId": "request-1"}
        ) as http:
            assert await api.patch("board-1", {"DhwScheduleEnabled": 3}, {"DhwComfortPreference": 2}) == "request-1"
            http.assert_awaited_once()
            assert http.call_args.kwargs["body"] == {
                "settings": {"DhwScheduleEnabled": 3},
                "properties": {"DhwComfortPreference": 2},
                "updateImmediately": False,
            }
            assert http.call_args.kwargs["headers"]["x-version"] == "app_consumer_v4.0.1"


async def test_intelligence_properties_are_fresh_durable_and_readback_verified(hass):
    async with ClientSession() as session:
        api = WeheatApi(session)
        current = WeheatCoordinator(hass, config_entry(), api)
        data = {**settings(), "dhwComfortPreference": 3}
        with (
            patch.object(api, "settings", new_callable=AsyncMock, return_value=data),
            patch.object(
                api, "installations", new_callable=AsyncMock, return_value=[Installation("installation-1", "board-1")]
            ),
            patch.object(api, "patch", new_callable=AsyncMock, return_value="request-1") as submit,
            patch.object(current, "async_request_refresh", new_callable=AsyncMock),
        ):
            await DhwIntelligenceSwitch(current).async_turn_on()
            submit.assert_awaited_once_with("board-1", {"DhwScheduleEnabled": 3}, {"DhwComfortPreference": 3})
        restored = WeheatCoordinator(hass, current.entry, api)
        await restored.async_load_operation()
        assert restored.operation["properties"] == {"DhwComfortPreference": 3}
        with (
            patch.object(
                api, "settings", new_callable=AsyncMock, return_value={**data, "dhwScheduleEnabled": 3}
            ) as read,
            patch.object(api, "status", new_callable=AsyncMock, return_value=2),
        ):
            read.return_value["dhwComfortPreference"] = 2
            await restored._async_update_data()
            assert restored.operation["state"] == "succeeded_unverified"
            assert restored.operation["readback_matches"] is False
            read.return_value["dhwComfortPreference"] = 3
            await restored._async_update_data()
            assert restored.operation["state"] == "succeeded"
        assert data["dhwScheduleEnabled"] == 0
        assert data["dhwTargetTemperature"] == 55


async def test_intelligence_uses_existing_uncertain_request_gate(hass):
    async with ClientSession() as session:
        api = WeheatApi(session)
        current = WeheatCoordinator(hass, config_entry(), api)
        current.operation = {"state": "submission_uncertain", "terminal": False}
        with patch.object(api, "patch", new_callable=AsyncMock) as submit:
            for action in (DhwIntelligenceSwitch(current).async_turn_on, DhwIntelligenceSwitch(current).async_turn_off):
                with pytest.raises(HomeAssistantError, match="unresolved"):
                    await action()
            submit.assert_not_awaited()


async def test_failed_enable_keeps_properties_and_blocks_replay_after_restart(hass):
    async with ClientSession() as session:
        api = WeheatApi(session)
        current = WeheatCoordinator(hass, config_entry(), api)
        with (
            patch.object(api, "settings", new_callable=AsyncMock, return_value=settings()),
            patch.object(
                api, "installations", new_callable=AsyncMock, return_value=[Installation("installation-1", "board-1")]
            ),
            patch.object(
                api, "patch", new_callable=AsyncMock, side_effect=WeheatError("timeout", code="timeout")
            ) as submit,
        ):
            with pytest.raises(HomeAssistantError, match="uncertain"):
                await DhwIntelligenceSwitch(current).async_turn_on()
            restored = WeheatCoordinator(hass, current.entry, api)
            await restored.async_load_operation()
            assert restored.operation["properties"] == {"DhwComfortPreference": 2}
            assert restored.operation["settings"] == {"DhwScheduleEnabled": 3}
            assert restored.operation["terminal"] is False
            with pytest.raises(HomeAssistantError, match="unresolved"):
                await DhwIntelligenceSwitch(restored).async_turn_on()
            submit.assert_awaited_once()


async def test_disable_submits_only_fixed_mode_without_properties_or_target(hass):
    async with ClientSession() as session:
        api = WeheatApi(session)
        current = WeheatCoordinator(hass, config_entry(), api)
        with (
            patch.object(api, "settings", new_callable=AsyncMock, return_value={**settings(), "dhwScheduleEnabled": 3}),
            patch.object(
                api, "installations", new_callable=AsyncMock, return_value=[Installation("installation-1", "board-1")]
            ),
            patch.object(api, "patch", new_callable=AsyncMock, return_value="request-1") as submit,
            patch.object(current, "async_request_refresh", new_callable=AsyncMock),
        ):
            await DhwIntelligenceSwitch(current).async_turn_off()
            submit.assert_awaited_once_with("board-1", {"DhwScheduleEnabled": 0})
            assert "properties" not in current.operation
            assert current.operation["settings"] == {"DhwScheduleEnabled": 0}
