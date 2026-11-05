"""Explicit expired-boost acknowledgement never replays a mutation."""

import time
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientSession
from homeassistant.exceptions import HomeAssistantError
from test_weheat import config_entry

from custom_components.weheat_control.api import WeheatApi, WeheatError
from custom_components.weheat_control.coordinator import WeheatCoordinator

pytest_plugins = ["test_weheat"]


def expired_operation():
    return {
        "state": "unknown",
        "terminal": False,
        "board_id": "board-1",
        "settings": {"DhwBoostEndTime": str(int(time.time()) - 1200)},
        "created_at": int(time.time()) - 3000,
    }


async def test_acknowledgement_is_durable_and_preserved_in_next_operation(hass):
    async with ClientSession() as session:
        api = WeheatApi(session)
        coordinator = WeheatCoordinator(hass, config_entry(), api)
        coordinator.operation = expired_operation()
        original = dict(coordinator.operation)
        with (
            patch.object(
                api, "settings", new_callable=AsyncMock, return_value={"dhwBoostEndTime": "1", "dhwAvailable": 1}
            ),
            patch.object(api, "request_queue_empty", new_callable=AsyncMock, return_value=True),
            patch.object(api, "patch", new_callable=AsyncMock) as mutation,
        ):
            await coordinator.async_acknowledge_expired_boost()
            mutation.assert_not_awaited()
            restored = WeheatCoordinator(hass, coordinator.entry, api)
            await restored.async_load_operation()
            assert restored.operation["terminal"] is True
            assert restored.operation["state"] == "expired_outcome_unknown"
            assert restored.operation["settings"] == original["settings"]
            from custom_components.weheat_control.api import Installation

            with (
                patch.object(
                    api,
                    "installations",
                    new_callable=AsyncMock,
                    return_value=[Installation("installation-1", "board-1")],
                ),
                patch.object(restored, "async_request_refresh", new_callable=AsyncMock),
            ):
                mutation.return_value = "request-2"
                await restored.async_write({"DhwBoostEndTime": str(int(time.time()) + 1800)})
                assert restored.operation["history"][0]["settings"] == original["settings"]
                mutation.assert_awaited_once()


@pytest.mark.parametrize(
    "change",
    [
        {"request_id": "request-1"},
        {"board_id": "other-board"},
        {"settings": {"DhwBoostEndTime": str(int(time.time()) + 1800)}},
        {"settings": {"PerformanceModePreset": 2}},
        {"created_at": 0},
        {"state": "pending"},
    ],
)
async def test_acknowledgement_rejects_other_or_unexpired_operations(hass, change):
    async with ClientSession() as session:
        api = WeheatApi(session)
        coordinator = WeheatCoordinator(hass, config_entry(), api)
        coordinator.operation = {**expired_operation(), **change}
        with patch.object(api, "request_queue_empty", new_callable=AsyncMock) as listing:
            with pytest.raises(HomeAssistantError):
                await coordinator.async_acknowledge_expired_boost()
            listing.assert_not_awaited()
            assert coordinator.operation["terminal"] is False


@pytest.mark.parametrize(
    "readback,empty", [(None, True), ("invalid", True), (str(int(time.time()) + 3600), True), ("1", False)]
)
async def test_acknowledgement_fails_closed_on_readback_or_queue(hass, readback, empty):
    async with ClientSession() as session:
        api = WeheatApi(session)
        coordinator = WeheatCoordinator(hass, config_entry(), api)
        coordinator.operation = expired_operation()
        with (
            patch.object(api, "settings", new_callable=AsyncMock, return_value={"dhwBoostEndTime": readback}),
            patch.object(api, "request_queue_empty", new_callable=AsyncMock, return_value=empty),
            patch.object(api, "patch", new_callable=AsyncMock) as mutation,
        ):
            with pytest.raises(HomeAssistantError):
                await coordinator.async_acknowledge_expired_boost()
            assert coordinator.operation["terminal"] is False
            mutation.assert_not_awaited()


async def test_acknowledgement_storage_failure_keeps_gate_closed(hass):
    async with ClientSession() as session:
        api = WeheatApi(session)
        coordinator = WeheatCoordinator(hass, config_entry(), api)
        coordinator.operation = expired_operation()
        with (
            patch.object(api, "settings", new_callable=AsyncMock, return_value={"dhwBoostEndTime": "1"}),
            patch.object(api, "request_queue_empty", new_callable=AsyncMock, return_value=True),
            patch.object(coordinator._store, "async_save", new_callable=AsyncMock, side_effect=OSError),
        ):
            with pytest.raises(HomeAssistantError, match="persist"):
                await coordinator.async_acknowledge_expired_boost()
            assert coordinator.operation["terminal"] is False


async def test_acknowledgement_cloud_failure_and_concurrent_write_keep_gate_closed(hass):
    async with ClientSession() as session:
        api = WeheatApi(session)
        coordinator = WeheatCoordinator(hass, config_entry(), api)
        coordinator.operation = expired_operation()
        with (
            patch.object(api, "settings", new_callable=AsyncMock, return_value={"dhwBoostEndTime": "1"}),
            patch.object(
                api, "request_queue_empty", new_callable=AsyncMock, side_effect=WeheatError("Cloud unavailable")
            ),
        ):
            with pytest.raises(HomeAssistantError, match="Cloud unavailable"):
                await coordinator.async_acknowledge_expired_boost()
            assert coordinator.operation["terminal"] is False
            async with coordinator._write_lock:
                with pytest.raises(HomeAssistantError, match="in progress"):
                    await coordinator.async_acknowledge_expired_boost()


async def test_expired_state_without_acknowledgement_marker_does_not_unlock_on_restart(hass):
    async with ClientSession() as session:
        api = WeheatApi(session)
        coordinator = WeheatCoordinator(hass, config_entry(), api)
        coordinator.operation = {**expired_operation(), "state": "expired_outcome_unknown", "terminal": True}
        await coordinator._save_operation()
        restored = WeheatCoordinator(hass, coordinator.entry, api)
        await restored.async_load_operation()
        assert restored.operation["terminal"] is False


@pytest.mark.parametrize(
    "response,expected",
    [
        ({"metadata": {"totalCount": 0, "currentPage": 1, "totalPages": 0}, "data": []}, True),
        ({"metadata": {"totalCount": 1, "currentPage": 1, "totalPages": 1}, "data": [{}]}, False),
        ({"metadata": {"totalCount": False, "currentPage": 1, "totalPages": 0}, "data": []}, None),
        ({"data": []}, None),
    ],
)
async def test_request_queue_requires_explicit_complete_empty_listing(response, expected):
    async with ClientSession() as session:
        api = WeheatApi(session)
        with patch.object(api, "request", new_callable=AsyncMock, return_value=response):
            if expected is None:
                with pytest.raises(WeheatError):
                    await api.request_queue_empty("board-1")
            else:
                assert await api.request_queue_empty("board-1") is expected
