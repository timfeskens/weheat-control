"""Intelligence comfort (Normaal/Zuinig) is the DHW safety temperature, fail-closed."""

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


def comfort_settings(level: int = 40, code: int | None = None):
    # Observed in the app: Normaal = 40 °C / code 6, Zuinig = 10 °C / code 5. The code comes from
    # installationSystem.dhwComfortPreference in the overview, which the coordinator overlays.
    data = {**settings(), "dhwScheduleEnabled": 3, "dhwScheduleSafetyTemperature": level}
    if code is not None:
        data["dhwComfortPreference"] = code
    return data


@pytest.mark.parametrize("option,value,code", [("Normal", 40, 6), ("Eco", 10, 5)])
async def test_exact_setting_command_and_confirmed_state(option, value, code):
    current = coordinator(comfort_settings(40, 6))
    entity = DhwComfortPreference(current)
    assert entity.available
    assert entity.unique_id == "installation-1_dhw_comfort_preference"
    assert entity.current_option == "Normal"
    assert entity.options == ["Normal", "Eco"]
    await entity.async_select_option(option)
    current.async_write.assert_awaited_once_with(
        {"DhwScheduleSafetyTemperature": value}, properties={"DhwComfortPreference": code}
    )
    validate_write({"DhwScheduleSafetyTemperature": value}, current.data, {"DhwComfortPreference": code})
    assert entity.current_option == "Normal"  # no optimistic state
    current.data.update(dhwScheduleSafetyTemperature=value, dhwComfortPreference=code)
    assert entity.current_option == option
    assert entity.extra_state_attributes == {"safety_temperature": value, "comfort_code": code}
    assert current.data["dhwTargetTemperature"] == 55
    assert current.data["dhwScheduleEnabled"] == 3


def test_app_label_follows_comfort_code_over_temperature():
    # Observed: HA wrote only 40 °C while the code stayed 5, and the app kept showing Zuinig.
    assert DhwComfortPreference(coordinator(comfort_settings(40, 5))).current_option == "Eco"
    assert DhwComfortPreference(coordinator(comfort_settings(10))).current_option == "Eco"  # no code: fallback
    assert DhwComfortPreference(coordinator(comfort_settings(40, 99))).current_option is None
    assert DhwComfortPreference(coordinator(comfort_settings(40, True))).current_option is None


def test_other_reported_level_is_shown_as_unknown_not_mapped():
    entity = DhwComfortPreference(coordinator(comfort_settings(35)))
    assert entity.available
    assert entity.current_option is None


@pytest.mark.parametrize(
    "change",
    [
        {"dhwAvailable": 0},
        {"dhwAvailable": True},
        {"dhwScheduleEnabled": 0},
        {"dhwScheduleEnabled": 2},
        {"dhwScheduleEnabled": "3"},
        {"dhwScheduleSafetyTemperature": None},
        {"dhwScheduleSafetyTemperature": True},
        {"dhwScheduleSafetyTemperature": 40.0},
        {"dhwScheduleSafetyTemperature": "40"},
    ],
)
def test_unavailable_without_intelligence_or_reported_level(change):
    data = {**comfort_settings(), **change}
    entity = DhwComfortPreference(coordinator(data))
    assert not dhw_comfort_available(data)
    assert not entity.available
    assert entity.current_option is None
    with pytest.raises(WeheatError):
        validate_write({"DhwScheduleSafetyTemperature": 10}, data, {"DhwComfortPreference": 5})


@pytest.mark.parametrize(
    "command,properties",
    [
        ({"DhwScheduleSafetyTemperature": 10}, None),  # temperature alone leaves the app on the old label
        ({"DhwScheduleSafetyTemperature": 10}, {"DhwComfortPreference": 6}),  # mismatched halves
        ({"DhwScheduleSafetyTemperature": 40}, {"DhwComfortPreference": 5}),
        ({"DhwScheduleSafetyTemperature": 20}, {"DhwComfortPreference": 5}),
        ({"DhwScheduleSafetyTemperature": 10.0}, {"DhwComfortPreference": 5}),
        ({"DhwScheduleSafetyTemperature": "10"}, {"DhwComfortPreference": 5}),
        ({"DhwScheduleSafetyTemperature": 10}, {"DhwComfortPreference": 3}),
        ({"DhwScheduleSafetyTemperature": 10}, {"DhwComfortPreference": 5, "Other": 1}),
        ({"DhwScheduleSafetyTemperature": 10, "DhwTargetTemperature": 50}, {"DhwComfortPreference": 5}),
        ({}, {"DhwComfortPreference": 3}),  # the old property-only command is no longer sent
        ({}, None),
    ],
)
def test_reject_other_levels_and_arbitrary_commands(command, properties):
    with pytest.raises(WeheatError):
        validate_write(command, comfort_settings(), properties)


async def test_unknown_option_and_failed_coordinator_availability():
    current = coordinator(comfort_settings())
    entity = DhwComfortPreference(current)
    with pytest.raises(HomeAssistantError):
        await entity.async_select_option("Balanced")
    current.async_write.assert_not_awaited()
    current.last_update_success = False
    assert not entity.available


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


async def test_journal_and_strict_readback_after_restart(hass):
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
            await DhwComfortPreference(current).async_select_option("Eco")
            submit.assert_awaited_once_with(
                "board-1", {"DhwScheduleSafetyTemperature": 10}, {"DhwComfortPreference": 5}
            )
        restored = WeheatCoordinator(hass, current.entry, api)
        await restored.async_load_operation()
        assert restored.operation["settings"] == {"DhwScheduleSafetyTemperature": 10}
        assert restored.operation["properties"] == {"DhwComfortPreference": 5}
        with (
            patch.object(api, "settings", new_callable=AsyncMock) as read,
            patch.object(api, "installations", new_callable=AsyncMock) as overview,
            patch.object(api, "status", new_callable=AsyncMock, return_value=2),
        ):
            for level, code in ((40, 6), (10, 6), (40, 5)):  # either half still old: not verified
                read.return_value = comfort_settings(level)
                overview.return_value = [Installation("installation-1", "board-1", code)]
                await restored._async_update_data()
                assert restored.operation["state"] == "succeeded_unverified"
                assert restored.operation["readback_matches"] is False
            read.return_value = comfort_settings(10)
            overview.return_value = [Installation("installation-1", "board-1", 5)]
            data = await restored._async_update_data()
            assert data["dhwComfortPreference"] == 5
            assert restored.operation["state"] == "succeeded"


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


async def test_uncertain_command_blocks_replay(hass):
    async with ClientSession() as session:
        api = WeheatApi(session)
        current = WeheatCoordinator(hass, config_entry(), api)
        with (
            patch.object(api, "settings", new_callable=AsyncMock, return_value=comfort_settings()),
            patch.object(
                api, "installations", new_callable=AsyncMock, return_value=[Installation("installation-1", "board-1")]
            ),
            patch.object(
                api, "patch", new_callable=AsyncMock, side_effect=WeheatError("timeout", code="timeout")
            ) as submit,
        ):
            with pytest.raises(HomeAssistantError, match="uncertain"):
                await DhwComfortPreference(current).async_select_option("Eco")
            restored = WeheatCoordinator(hass, current.entry, api)
            await restored.async_load_operation()
            assert restored.operation["state"] == "submission_uncertain"
            with pytest.raises(HomeAssistantError, match="unresolved"):
                await DhwComfortPreference(restored).async_select_option("Normal")
            submit.assert_awaited_once()


async def test_overview_comfort_code_is_parsed_and_only_fetched_in_intelligence(hass):
    async with ClientSession() as session:
        api = WeheatApi(session, Tokens("fake", "fake-refresh", time.time() + 300))
        overview = {
            "data": [
                {
                    "id": "installation-1",
                    "heatPump": {"controlBoardId": "board-1"},
                    "installationSystem": {"systemControl": None, "dhwComfortPreference": 6},
                }
            ]
        }
        with patch.object(api, "_http", new_callable=AsyncMock, return_value=overview):
            assert await api.installations() == [Installation("installation-1", "board-1", 6)]
        overview["data"][0]["installationSystem"]["dhwComfortPreference"] = "6"
        with patch.object(api, "_http", new_callable=AsyncMock, return_value=overview):
            assert await api.installations() == [Installation("installation-1", "board-1", None)]
        current = WeheatCoordinator(hass, config_entry(), api)
        with (
            patch.object(api, "settings", new_callable=AsyncMock, return_value=comfort_settings()) as read,
            patch.object(api, "installations", new_callable=AsyncMock) as listing,
        ):
            listing.side_effect = WeheatError("down")
            assert "dhwComfortPreference" not in await current._async_update_data()  # optional readback
            read.return_value = {**comfort_settings(), "dhwScheduleEnabled": 0}
            listing.reset_mock()
            await current._async_update_data()
            listing.assert_not_awaited()
