"""Property-only comfort writes retain the same fail-closed command contract."""

import time
from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiohttp import ClientSession
from homeassistant.exceptions import HomeAssistantError
from test_controls import coordinator, settings
from test_weheat import config_entry

from custom_components.weheat_control.api import Installation, Tokens, WeheatApi, WeheatError
from custom_components.weheat_control.controls import dhw_comfort_available, validate_write
from custom_components.weheat_control.coordinator import WeheatCoordinator
from custom_components.weheat_control.select import DhwComfortPreference
from custom_components.weheat_control.select import async_setup_entry as setup_selects

pytest_plugins = ["test_weheat"]


def comfort_settings():
    return {**settings(), "dhwScheduleEnabled": 3, "dhwComfortPreference": 2}


@pytest.mark.parametrize("option,value", [("Safe", 1), ("Balanced", 2), ("Eco", 3)])
async def test_exact_property_only_command_and_confirmed_state(option, value):
    current = coordinator(comfort_settings())
    entity = DhwComfortPreference(current)
    assert entity.available
    assert entity.current_option == "Balanced"
    assert entity.options == ["Safe", "Balanced", "Eco"]
    await entity.async_select_option(option)
    current.async_write.assert_awaited_once_with({}, properties={"DhwComfortPreference": value})
    assert entity.current_option == "Balanced"
    current.data["dhwComfortPreference"] = value
    assert entity.current_option == option
    assert current.data["dhwTargetTemperature"] == 55
    assert current.data["dhwScheduleEnabled"] == 3


@pytest.mark.parametrize(
    "change",
    [
        {"dhwAvailable": 0},
        {"dhwAvailable": True},
        {"dhwScheduleEnabled": 0},
        {"dhwScheduleEnabled": 2},
        {"dhwScheduleEnabled": "3"},
        {"dhwComfortPreference": None},
        {"dhwComfortPreference": True},
        {"dhwComfortPreference": 2.0},
        {"dhwComfortPreference": "2"},
        {"dhwComfortPreference": 99},
    ],
)
def test_unavailable_without_reported_preference_or_intelligence(change):
    data = {**comfort_settings(), **change}
    entity = DhwComfortPreference(coordinator(data))
    assert not dhw_comfort_available(data)
    assert not entity.available
    assert entity.current_option is None
    with pytest.raises(WeheatError):
        validate_write({}, data, {"DhwComfortPreference": 3})


@pytest.mark.parametrize(
    "properties",
    [
        None,
        {},
        {"Other": 2},
        {"DhwComfortPreference": True},
        {"DhwComfortPreference": 2.0},
        {"DhwComfortPreference": "2"},
        {"DhwComfortPreference": 0},
        {"DhwComfortPreference": 4},
        {"DhwComfortPreference": 2, "Other": 1},
    ],
)
def test_reject_empty_or_arbitrary_property_commands(properties):
    with pytest.raises(WeheatError):
        validate_write({}, comfort_settings(), properties)


async def test_unknown_option_and_failed_coordinator_availability():
    current = coordinator(comfort_settings())
    entity = DhwComfortPreference(current)
    with pytest.raises(HomeAssistantError):
        await entity.async_select_option("unknown")
    current.async_write.assert_not_awaited()
    current.last_update_success = False
    assert not entity.available


def test_missing_comfort_field_is_not_a_balanced_default():
    data = comfort_settings()
    del data["dhwComfortPreference"]
    entity = DhwComfortPreference(coordinator(data))
    assert not entity.available
    assert entity.current_option is None
    with pytest.raises(WeheatError):
        validate_write({}, data, {"DhwComfortPreference": 2})


async def test_select_setup_preserves_quiet_presets_and_adds_comfort():
    entry = Mock(runtime_data=coordinator(comfort_settings()))
    add = Mock()
    await setup_selects(Mock(), entry, add)
    entities = add.call_args.args[0]
    assert len(entities) == 3
    assert isinstance(entities[-1], DhwComfortPreference)


async def test_api_property_only_body():
    async with ClientSession() as session:
        api = WeheatApi(session, Tokens("fake", "fake-refresh", time.time() + 300))
        with patch.object(
            api, "_http", new_callable=AsyncMock, return_value={"settingsRequestId": "request-1"}
        ) as http:
            await api.patch("board-1", {}, {"DhwComfortPreference": 3})
            http.assert_awaited_once()
            assert http.call_args.kwargs["body"] == {
                "settings": {},
                "properties": {"DhwComfortPreference": 3},
                "updateImmediately": False,
            }


async def test_property_journal_and_strict_readback_after_restart(hass):
    async with ClientSession() as session:
        api = WeheatApi(session)
        current = WeheatCoordinator(hass, config_entry(), api)
        with (
            patch.object(api, "settings", new_callable=AsyncMock, return_value=comfort_settings()),
            patch.object(
                api, "installations", new_callable=AsyncMock, return_value=[Installation("installation-1", "board-1")]
            ),
            patch.object(api, "patch", new_callable=AsyncMock, return_value="request-1") as submit,
            patch.object(current, "async_request_refresh", new_callable=AsyncMock),
        ):
            await DhwComfortPreference(current).async_select_option("Safe")
            submit.assert_awaited_once_with("board-1", {}, {"DhwComfortPreference": 1})
        restored = WeheatCoordinator(hass, current.entry, api)
        await restored.async_load_operation()
        assert restored.operation["settings"] == {}
        assert restored.operation["properties"] == {"DhwComfortPreference": 1}
        with (
            patch.object(api, "settings", new_callable=AsyncMock) as read,
            patch.object(api, "status", new_callable=AsyncMock, return_value=2),
        ):
            for preference in (None, True, "1", 1.0, 2):
                read.return_value = {**comfort_settings(), "dhwComfortPreference": preference}
                await restored._async_update_data()
                assert restored.operation["state"] == "succeeded_unverified"
                assert restored.operation["readback_matches"] is False
            read.return_value = {**comfort_settings(), "dhwComfortPreference": 1}
            await restored._async_update_data()
            assert restored.operation["state"] == "succeeded"
            assert restored.operation["readback_matches"] is True


async def test_property_only_upstream_mismatch_is_not_clean_success(hass):
    async with ClientSession() as session:
        api = WeheatApi(session)
        current = WeheatCoordinator(hass, config_entry(), api)
        current.operation = {
            "state": "pending",
            "terminal": False,
            "request_id": "request-1",
            "board_id": "board-1",
            "settings": {},
            "properties": {"DhwComfortPreference": 2},
        }
        with (
            patch.object(api, "settings", new_callable=AsyncMock, return_value=comfort_settings()),
            patch.object(api, "status", new_callable=AsyncMock, return_value=5),
        ):
            await current._async_update_data()
            assert current.operation["readback_matches"] is True
            assert current.operation["state"] == "mismatch"


async def test_fresh_mode_validation_prevents_stale_entity_write(hass):
    async with ClientSession() as session:
        api = WeheatApi(session)
        current = WeheatCoordinator(hass, config_entry(), api)
        with (
            patch.object(
                api, "settings", new_callable=AsyncMock, return_value={**comfort_settings(), "dhwScheduleEnabled": 0}
            ),
            patch.object(api, "patch", new_callable=AsyncMock) as submit,
        ):
            with pytest.raises(HomeAssistantError, match="unavailable"):
                await DhwComfortPreference(current).async_select_option("Eco")
            submit.assert_not_awaited()
            assert not current.operation


@pytest.mark.parametrize("failure", [WeheatError("timeout", code="timeout"), OSError("disk full")])
async def test_uncertain_command_blocks_replay_and_storage_failure_prevents_send(hass, failure):
    async with ClientSession() as session:
        api = WeheatApi(session)
        current = WeheatCoordinator(hass, config_entry(), api)
        with (
            patch.object(api, "settings", new_callable=AsyncMock, return_value=comfort_settings()),
            patch.object(
                api, "installations", new_callable=AsyncMock, return_value=[Installation("installation-1", "board-1")]
            ),
            patch.object(api, "patch", new_callable=AsyncMock, side_effect=failure) as submit,
            patch.object(current._store, "async_save", wraps=current._store.async_save) as save,
        ):
            if isinstance(failure, OSError):
                save.side_effect = failure
            with pytest.raises(HomeAssistantError):
                await DhwComfortPreference(current).async_select_option("Eco")
            if isinstance(failure, OSError):
                submit.assert_not_awaited()
            else:
                restored = WeheatCoordinator(hass, current.entry, api)
                await restored.async_load_operation()
                assert restored.operation["properties"] == {"DhwComfortPreference": 3}
                assert restored.operation["state"] == "submission_uncertain"
                with pytest.raises(HomeAssistantError, match="unresolved"):
                    await DhwComfortPreference(restored).async_select_option("Eco")
                submit.assert_awaited_once()
