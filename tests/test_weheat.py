"""Offline tests; no real credentials, network, or device writes."""

import asyncio
import base64
import hashlib
import time
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from aiohttp import ClientSession, web
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.weheat_control import async_setup_entry, async_unload_entry
from custom_components.weheat_control.api import (
    AuthenticationError,
    Installation,
    Tokens,
    WeheatApi,
    WeheatError,
    callback_code,
    login_request,
)
from custom_components.weheat_control.config_flow import WeheatConfigFlow
from custom_components.weheat_control.const import BOARD_ID, DOMAIN, INSTALLATION_ID, TOKENS
from custom_components.weheat_control.coordinator import WeheatCoordinator, readback_matches


def config_entry(legacy_options: bool = False) -> ConfigEntry:
    return ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Weheat",
        source="user",
        unique_id="installation-1",
        data={
            INSTALLATION_ID: "installation-1",
            BOARD_ID: "board-1",
            TOKENS: Tokens("fake", "fake-refresh", time.time() + 300).as_dict(),
        },
        options={"allow_writes": False} if legacy_options else {},
        discovery_keys=MappingProxyType({}),
        subentries_data=[],
    )


@pytest.fixture
async def hass(tmp_path: Path):
    instance = HomeAssistant(str(tmp_path))
    yield instance
    await instance.async_stop()


@pytest.fixture
def current_settings():
    with patch.object(WeheatApi, "settings", new_callable=AsyncMock, return_value={"dhwAvailable": 1}):
        yield


def test_pkce_and_callback() -> None:
    url, verifier, state = login_request()
    query = parse_qs(urlsplit(url).query)
    assert query["code_challenge"] == [
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    ]
    assert query["redirect_uri"] == ["weheat://auth"]
    assert callback_code(f"weheat://auth?state={state}&code=test-code", state) == "test-code"
    for value in [
        "https://evil.test?state=x&code=c",
        "weheat://auth?state=x&code=c&code=d",
        "weheat://auth?state=x&code=c#x",
        "weheat://auth?state=wrong&code=c",
    ]:
        with pytest.raises(AuthenticationError):
            callback_code(value, "x")


async def test_transport_patch_no_retry_and_version_headers() -> None:
    calls: list[tuple[str, str, str | None]] = []

    async def handler(request: web.Request) -> web.Response:
        calls.append((request.method, request.path, request.headers.get("x-version")))
        if request.method == "PATCH":
            assert await request.json() == {"settings": {"DhwBoostEndTime": "123"}, "updateImmediately": False}
            return web.json_response({"sensitive": "never-echo"}, status=503)
        return web.json_response({"data": [{"id": "installation-1", "heatPump": {"controlBoardId": "board-1"}}]})

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    try:
        async with ClientSession() as session:
            api = WeheatApi(session, Tokens("fake", "fake-refresh", time.time() + 300))
            with patch("custom_components.weheat_control.api.API", f"http://127.0.0.1:{port}"):
                assert await api.installations() == [Installation("installation-1", "board-1")]
                with pytest.raises(WeheatError, match="503") as error:
                    await api.patch("board-1", {"DhwBoostEndTime": "123"})
                assert "never-echo" not in str(error.value)
        assert calls == [
            ("GET", "/installations/fully-expanded", None),
            ("PATCH", "/control-boards/board-1/settings", "4.5.14"),
        ]
    finally:
        await runner.cleanup()


async def test_refresh_is_serialized_and_rotated_token_is_saved() -> None:
    async with ClientSession() as session:
        save = AsyncMock()
        api = WeheatApi(session, Tokens("old", "old-refresh", 0), save)
        calls = 0

        async def http(method, url, **kwargs):
            nonlocal calls
            if method == "POST":
                calls += 1
                await asyncio.sleep(0)
                return {"access_token": "new", "refresh_token": "rotated", "expires_in": 300}
            return {"data": []}

        with patch.object(api, "_http", side_effect=http):
            await asyncio.gather(api.installations(), api.installations())
        assert calls == 1
        assert api.tokens and api.tokens.refresh_token == "rotated"
        save.assert_awaited_once()


async def test_controls_submit_without_opt_in_even_with_legacy_false_option(hass, current_settings) -> None:
    async with ClientSession() as session:
        api = WeheatApi(session)
        for legacy in (False, True):
            coordinator = WeheatCoordinator(hass, config_entry(legacy), api)
            with (
                patch.object(
                    api,
                    "installations",
                    new_callable=AsyncMock,
                    return_value=[Installation("installation-1", "board-1")],
                ),
                patch.object(api, "patch", new_callable=AsyncMock, return_value="request-1") as submission,
                patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock),
            ):
                await coordinator.async_write({"DhwBoostEndTime": str(int(time.time()) + 1800)})
                submission.assert_awaited_once()
                assert coordinator.operation["state"] == "pending"


async def test_unknown_submission_blocks_writes_across_restart(hass, current_settings) -> None:
    async with ClientSession() as session:
        api = WeheatApi(session)
        entry = config_entry(True)
        coordinator = WeheatCoordinator(hass, entry, api)
        with (
            patch.object(
                api, "installations", new_callable=AsyncMock, return_value=[Installation("installation-1", "board-1")]
            ),
            patch.object(api, "patch", new_callable=AsyncMock, side_effect=WeheatError("timeout")) as mutation,
        ):
            with pytest.raises(HomeAssistantError, match="uncertain"):
                await coordinator.async_write({"DhwBoostEndTime": str(int(time.time()) + 1800)})
            assert coordinator.operation["state"] == "unknown"
            restarted = WeheatCoordinator(hass, entry, api)
            await restarted.async_load_operation()
            with pytest.raises(HomeAssistantError, match="unresolved"):
                await restarted.async_write({"DhwBoostEndTime": str(int(time.time()) + 1800)})
            mutation.assert_awaited_once()


async def test_request_id_polling_and_mismatch_preserved(hass, current_settings) -> None:
    async with ClientSession() as session:
        api = WeheatApi(session)
        coordinator = WeheatCoordinator(hass, config_entry(True), api)
        with (
            patch.object(
                api, "installations", new_callable=AsyncMock, return_value=[Installation("installation-1", "board-1")]
            ),
            patch.object(api, "patch", new_callable=AsyncMock, return_value="request-123"),
            patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock),
        ):
            await coordinator.async_write({"DhwBoostEndTime": str(int(time.time()) + 1800)})
        assert coordinator.operation["request_id"] == "request-123"
        with patch.object(api, "status", new_callable=AsyncMock, return_value=5) as status:
            await coordinator._check_operation()
        status.assert_awaited_once_with("board-1", "request-123")
        assert coordinator.operation["state"] == "mismatch"
        assert coordinator.operation["terminal"] is True


async def test_coordinator_rejects_changed_board(hass, current_settings) -> None:
    async with ClientSession() as session:
        api = WeheatApi(session)
        coordinator = WeheatCoordinator(hass, config_entry(True), api)
        with (
            patch.object(
                api, "installations", new_callable=AsyncMock, return_value=[Installation("installation-1", "new-board")]
            ),
            patch.object(api, "patch", new_callable=AsyncMock) as mutation,
        ):
            with pytest.raises(HomeAssistantError, match="changed"):
                await coordinator.async_write({"DhwBoostEndTime": str(int(time.time()) + 1800)})
            mutation.assert_not_awaited()


def test_saved_token_validation() -> None:
    tokens = Tokens("access", "refresh", datetime.now(UTC).timestamp())
    assert Tokens.from_saved(tokens.as_dict()) == tokens
    with pytest.raises(AuthenticationError):
        Tokens.from_saved({"access_token": "secret", "refresh_token": None, "expires_at": 10})


def test_iso_boost_readback_is_not_unix_string() -> None:
    epoch = str(int(datetime(2026, 7, 26, 12, 28, 27, tzinfo=UTC).timestamp()))
    assert readback_matches({"dhwBoostEndTime": "2026-07-26T12:28:27Z"}, {"DhwBoostEndTime": epoch})
    assert not readback_matches({"dhwBoostEndTime": "2026-07-26T12:28:27"}, {"DhwBoostEndTime": epoch})
    assert not readback_matches({}, {"DhwBoostEndTime": epoch})


async def test_login_form_and_failed_discovery_retry_without_exchange(hass) -> None:
    flow = WeheatConfigFlow()
    flow.hass = hass
    with patch.object(flow, "async_show_form", side_effect=lambda **kwargs: kwargs):
        form = await flow.async_step_user()
        assert form["step_id"] == "login"
        assert "code_challenge=" in form["description_placeholders"]["login_url"]
        # Build the callback for exactly this flow, not a different login attempt.
        with (
            patch("custom_components.weheat_control.config_flow.async_get_clientsession"),
            patch.object(WeheatApi, "exchange", new_callable=AsyncMock) as exchange,
            patch.object(WeheatApi, "installations", new_callable=AsyncMock, side_effect=WeheatError("offline")),
        ):
            form = await flow.async_step_login({"callback_url": f"weheat://auth?code=c&state={flow._state}"})
            assert form["step_id"] == "installation"
            assert form["errors"]["base"] == "cannot_connect"
            await flow.async_step_installation({})
            exchange.assert_awaited_once()


async def test_single_installation_auto_selected_and_multiple_installations_prompt(hass) -> None:
    flow = WeheatConfigFlow()
    flow.hass = hass
    async with ClientSession() as session:
        flow._api = WeheatApi(session, Tokens("access", "refresh", time.time() + 300))
        with (
            patch.object(
                flow._api,
                "installations",
                new_callable=AsyncMock,
                return_value=[Installation("installation-1", "board-1")],
            ),
            patch.object(flow, "_finish", new_callable=AsyncMock, return_value={"type": "create_entry"}) as finish,
        ):
            assert await flow.async_step_installation() == {"type": "create_entry"}
            finish.assert_awaited_once()
        with (
            patch.object(
                flow._api,
                "installations",
                new_callable=AsyncMock,
                return_value=[Installation("a", "b"), Installation("c", "d")],
            ),
            patch.object(flow, "async_show_form", side_effect=lambda **kwargs: kwargs),
            patch.object(flow, "_finish", new_callable=AsyncMock) as finish,
        ):
            form = await flow.async_step_installation()
            assert form["step_id"] == "installation"
            finish.assert_not_awaited()
            await flow.async_step_installation({INSTALLATION_ID: "c"})
            finish.assert_awaited_once_with(Installation("c", "d"), flow._api.tokens)


async def test_invalid_callback_does_not_exchange(hass) -> None:
    flow = WeheatConfigFlow()
    flow.hass = hass
    flow._url, flow._verifier, flow._state = login_request()
    with (
        patch.object(flow, "async_show_form", side_effect=lambda **kwargs: kwargs),
        patch.object(WeheatApi, "exchange", new_callable=AsyncMock) as exchange,
    ):
        form = await flow.async_step_login({"callback_url": "weheat://auth?code=c&state=wrong"})
        assert form["errors"]["base"] == "invalid_auth"
        exchange.assert_not_awaited()


async def test_intent_is_durable_before_patch_and_concurrent_submission_is_rejected(hass, current_settings) -> None:
    async with ClientSession() as session:
        api = WeheatApi(session)
        entry = config_entry(True)
        coordinator = WeheatCoordinator(hass, entry, api)
        started, finish = asyncio.Event(), asyncio.Event()

        async def mutation(board, settings):
            # Recreate the reader to avoid relying on the original store's cache.
            restored = WeheatCoordinator(hass, entry, api)
            await restored.async_load_operation()
            assert restored.operation["state"] == "submitting"
            assert restored.operation["terminal"] is False
            started.set()
            await finish.wait()
            return "request-1"

        with (
            patch.object(
                api, "installations", new_callable=AsyncMock, return_value=[Installation("installation-1", "board-1")]
            ),
            patch.object(api, "patch", side_effect=mutation) as submission,
            patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock),
        ):
            first = asyncio.create_task(coordinator.async_write({"DhwBoostEndTime": str(int(time.time()) + 1800)}))
            await started.wait()
            try:
                with pytest.raises(HomeAssistantError, match="already in progress"):
                    await coordinator.async_write({"DhwBoostEndTime": str(int(time.time()) + 3600)})
            finally:
                finish.set()
                await first
            submission.assert_awaited_once()


async def test_fresh_mode_validation_blocks_target_before_record_or_mutation(hass) -> None:
    async with ClientSession() as session:
        api = WeheatApi(session)
        coordinator = WeheatCoordinator(hass, config_entry(True), api)
        # A UI value may be stale. The freshly read Intelligence mode wins.
        with (
            patch.object(
                api,
                "settings",
                new_callable=AsyncMock,
                return_value={"dhwAvailable": 1, "dhwTargetTemperature": 55, "dhwScheduleEnabled": 3},
            ),
            patch.object(api, "patch", new_callable=AsyncMock) as mutation,
        ):
            with pytest.raises(HomeAssistantError, match="Unsupported"):
                await coordinator.async_write({"DhwTargetTemperature": 56})
            assert coordinator.operation == {}
            mutation.assert_not_awaited()


async def test_failure_status_retains_specific_device_reason(hass) -> None:
    async with ClientSession() as session:
        api = WeheatApi(session)
        coordinator = WeheatCoordinator(hass, config_entry(True), api)
        coordinator.operation = {"request_id": "request-1", "board_id": "board-1", "terminal": False}
        with patch.object(api, "status", new_callable=AsyncMock, return_value=8):
            await coordinator._check_operation()
        assert coordinator.operation["state"] == "failed_device_disconnected"
        assert coordinator.operation["terminal"] is True
        restored = WeheatCoordinator(hass, coordinator.entry, api)
        await restored.async_load_operation()
        assert restored.operation["terminal"] is True


async def test_entry_setup_and_unload_wire_runtime(hass) -> None:
    entry = config_entry()
    hass.config_entries = Mock()
    with (
        patch("custom_components.weheat_control.async_get_clientsession"),
        patch.object(WeheatCoordinator, "async_config_entry_first_refresh", new_callable=AsyncMock),
        patch.object(hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock) as platforms,
        patch.object(hass.config_entries, "async_unload_platforms", new_callable=AsyncMock, return_value=True),
    ):
        assert await async_setup_entry(hass, entry)
        assert isinstance(entry.runtime_data, WeheatCoordinator)
        platforms.assert_awaited_once()
        assert await async_unload_entry(hass, entry)


async def test_completed_config_entry_needs_no_write_option(hass) -> None:
    flow = WeheatConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    tokens = Tokens("fake-access", "fake-refresh", time.time() + 300)
    with (
        patch.object(flow, "async_set_unique_id", new_callable=AsyncMock),
        patch.object(flow, "_abort_if_unique_id_configured"),
        patch.object(flow, "async_create_entry", side_effect=lambda **kwargs: kwargs),
    ):
        result = await flow._finish(Installation("installation-1", "board-1"), tokens)
        assert "options" not in result
        assert result["data"][TOKENS] == tokens.as_dict()


async def test_reauth_refuses_different_installation(hass) -> None:
    flow = WeheatConfigFlow()
    flow.hass = hass
    flow.context = {"source": "reauth"}
    with (
        patch.object(flow, "async_set_unique_id", new_callable=AsyncMock),
        patch.object(flow, "_get_reauth_entry", return_value=config_entry()),
        patch.object(flow, "async_abort", side_effect=lambda **kwargs: kwargs),
    ):
        result = await flow._finish(Installation("other-installation", "other-board"), Tokens("fake", "fake", 123))
        assert result == {"reason": "wrong_account"}


async def test_success_only_verified_when_settings_readback_matches(hass) -> None:
    async with ClientSession() as session:
        api = WeheatApi(session)
        coordinator = WeheatCoordinator(hass, config_entry(True), api)
        coordinator.operation = {
            "state": "pending",
            "request_id": "request-1",
            "board_id": "board-1",
            "terminal": False,
            "settings": {"PerformanceModePreset": 3},
        }
        with (
            patch.object(api, "status", new_callable=AsyncMock, return_value=2),
            patch.object(api, "settings", new_callable=AsyncMock, return_value={"performanceModePreset": 1}),
        ):
            await coordinator._async_update_data()
            assert coordinator.operation["state"] == "succeeded_unverified"
            assert coordinator.operation["readback_matches"] is False
        with patch.object(api, "settings", new_callable=AsyncMock, return_value={"performanceModePreset": 3}):
            await coordinator._async_update_data()
            assert coordinator.operation["state"] == "succeeded"
            assert coordinator.operation["readback_matches"] is True


async def test_actual_journal_disk_failure_prevents_patch(hass, current_settings) -> None:
    async with ClientSession() as session:
        api = WeheatApi(session)
        coordinator = WeheatCoordinator(hass, config_entry(True), api)
        with (
            patch.object(
                api, "installations", new_callable=AsyncMock, return_value=[Installation("installation-1", "board-1")]
            ),
            patch.object(api, "patch", new_callable=AsyncMock) as mutation,
            patch("custom_components.weheat_control.journal.os.replace", side_effect=OSError("disk full")),
        ):
            with pytest.raises(HomeAssistantError, match="persist"):
                await coordinator.async_write({"DhwBoostEndTime": str(int(time.time()) + 1800)})
            mutation.assert_not_awaited()
            assert not coordinator._store.path.exists()
