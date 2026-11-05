"""Polling and durable serialized settings requests."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AuthenticationError, Json, Settings, WeheatApi, WeheatError, json_value, object_value
from .const import BOARD_ID, DOMAIN, INSTALLATION_ID
from .controls import validate_write
from .journal import OperationJournal

_LOGGER = logging.getLogger(__name__)


def readback_matches(settings: Settings, requested: dict[str, Json]) -> bool:
    for key, value in requested.items():
        actual = settings.get(key[0].lower() + key[1:])
        if key == "DhwComfortPreference" and (type(actual) is not int or type(value) is not int):
            return False
        if key == "DhwBoostEndTime" and isinstance(actual, str) and not actual.isdigit():
            try:
                date = datetime.fromisoformat(actual.replace("Z", "+00:00"))
                if date.tzinfo is None:
                    return False
                actual = str(int(date.timestamp()))
            except ValueError:
                return False
        if actual != value and str(actual) != str(value):
            return False
    return True


def boost_timestamp(value: Json) -> int | None:
    """Parse a positive, timezone-qualified or Unix-seconds boost deadline."""
    try:
        if isinstance(value, str) and value.isdigit():
            return int(value)
        if isinstance(value, str):
            date = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return int(date.timestamp()) if date.tzinfo else None
        if type(value) is int:
            return value
    except ValueError, OverflowError, OSError:
        pass
    return None


class WeheatCoordinator(DataUpdateCoordinator[Settings]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: WeheatApi) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=timedelta(seconds=30))
        self.api = api
        self.entry = entry
        self.operation: dict[str, Json] = {}
        self._store = OperationJournal(hass, entry.entry_id)
        self._write_lock = asyncio.Lock()

    async def async_load_operation(self) -> None:
        saved = await self._store.async_load()
        if saved is not None:
            self.operation = object_value(json_value(saved))
            # Only our verified terminal records can release the mutation gate.
            status = self.operation.get("status")
            if self.operation.get("state") == "expired_outcome_unknown" and self._expired_boost_intent():
                # Explicit local acknowledgement, not an invented upstream result.
                self.operation["terminal"] = self.operation.get("recovery") == "expired_boost_acknowledged"
            elif self.operation.get("state") == "cancelled_before_submission":
                self.operation["terminal"] = True
            elif type(status) is not int or not 2 <= status <= 11:
                self.operation["terminal"] = False

    async def _save_operation(self) -> None:
        try:
            await self._store.async_save(self.operation)
            self.async_update_listeners()
        except OSError, ValueError:
            raise HomeAssistantError(
                "Could not persist the settings journal. Device writes are blocked; inspect storage."
            ) from None

    def _expired_boost_intent(self) -> bool:
        operation = self.operation
        settings = operation.get("settings")
        if (
            operation.get("request_id") is not None
            or operation.get("board_id") != self.entry.data[BOARD_ID]
            or not isinstance(settings, dict)
            or set(settings) != {"DhwBoostEndTime"}
        ):
            return False
        end = boost_timestamp(settings["DhwBoostEndTime"])
        created = operation.get("created_at")
        return (
            type(created) is int
            and end is not None
            and 0 < created <= end <= created + 3600
            and end + 600 <= time.time()
        )

    async def async_acknowledge_expired_boost(self) -> None:
        """Release only an expired ID-less boost; never replay or claim success."""
        if self._write_lock.locked():
            raise HomeAssistantError("A settings request is already in progress")
        async with self._write_lock:
            if (
                self.operation.get("terminal") is True
                or self.operation.get("state") not in ("unknown", "submission_uncertain", "submitting")
                or not self._expired_boost_intent()
            ):
                raise HomeAssistantError("Only an uncertain, expired boost without a request ID can be acknowledged")
            try:
                current = await self.api.settings(str(self.entry.data[INSTALLATION_ID]))
                end = boost_timestamp(current.get("dhwBoostEndTime"))
                if end is None or end > time.time():
                    raise HomeAssistantError("Boost readback is unknown or still active; writes remain blocked")
                if not await self.api.request_queue_empty(str(self.entry.data[BOARD_ID])):
                    raise HomeAssistantError("Weheat still lists settings requests; writes remain blocked")
            except WeheatError as error:
                raise HomeAssistantError(str(error)) from None
            previous = self.operation
            self.operation = {
                **previous,
                "state": "expired_outcome_unknown",
                "terminal": True,
                "recovery": "expired_boost_acknowledged",
                "acknowledged_at": int(time.time()),
            }
            try:
                await self._save_operation()
            except BaseException:
                self.operation = previous
                raise

    async def _check_operation(self) -> None:
        if not self.operation or self.operation.get("terminal") is True:
            return
        request_id = self.operation.get("request_id")
        board = self.operation.get("board_id")
        if not isinstance(request_id, str) or not isinstance(board, str):
            return  # Ambiguous submission: keep blocking writes across restarts.
        status = await self.api.status(board, request_id)
        states = {
            0: "pending",
            1: "executing",
            2: "succeeded_unverified",
            3: "cancelled",
            4: "failed",
            5: "mismatch",
            6: "failed_device_wrong_state",
            7: "failed_wrong_execution_order",
            8: "failed_device_disconnected",
            9: "failed_settings_request_mismatch",
            10: "failed_settings_key_not_found",
            11: "failed_another_request_executing",
        }
        self.operation.update(status=status, state=states.get(status, "failed"), terminal=status >= 2)
        await self._save_operation()

    async def _async_update_data(self) -> Settings:
        try:
            # Polling must not race an operation record being submitted/saved.
            async with self._write_lock:
                await self._check_operation()
                settings = await self.api.settings(str(self.entry.data[INSTALLATION_ID]))
                if self.operation.get("state") in ("succeeded_unverified", "mismatch"):
                    requested = self.operation.get("settings")
                    properties = self.operation.get("properties")
                    if isinstance(requested, dict) and (requested or isinstance(properties, dict) and properties):
                        matches = readback_matches(settings, requested)
                        if isinstance(properties, dict) and properties:
                            matches = matches and readback_matches(settings, properties)
                        self.operation["readback_matches"] = matches
                        if matches and self.operation.get("status") == 2:
                            self.operation["state"] = "succeeded"
                        await self._save_operation()
                return settings
        except AuthenticationError as error:
            raise ConfigEntryAuthFailed(str(error)) from None
        except WeheatError as error:
            raise UpdateFailed(str(error)) from None

    async def async_write(
        self,
        command: Settings | Callable[[Settings], Settings],
        *,
        properties: Settings | Callable[[Settings], Settings] | None = None,
    ) -> None:
        """Save intent before sending. Never resubmit after an uncertain outcome."""
        if self._write_lock.locked():
            raise HomeAssistantError("A settings request is already in progress")
        async with self._write_lock:
            if self.operation and self.operation.get("terminal") is not True:
                raise HomeAssistantError(
                    "Previous settings request is unresolved. Check request status; do not retry blindly."
                )
            try:
                current = await self.api.settings(str(self.entry.data[INSTALLATION_ID]))
                settings = command(current) if callable(command) else command
                resolved_properties = properties(current) if callable(properties) else properties
                validate_write(settings, current, resolved_properties)
                selected = next(
                    (item for item in await self.api.installations() if item.id == self.entry.data[INSTALLATION_ID]),
                    None,
                )
                if not selected or selected.board_id != self.entry.data[BOARD_ID]:
                    raise HomeAssistantError(
                        "Selected control board is no longer accessible or has changed; set up again"
                    )
                history = self.operation.get("history", [])
                if self.operation.get("recovery") == "expired_boost_acknowledged":
                    history = [*history, {key: value for key, value in self.operation.items() if key != "history"}]
                self.operation = {
                    "state": "submitting",
                    "terminal": False,
                    "board_id": selected.board_id,
                    "settings": dict(settings),
                    "created_at": int(time.time()),
                    "history": history,
                }
                if resolved_properties:
                    self.operation["properties"] = dict(resolved_properties)
                await self._save_operation()
                try:
                    if resolved_properties:
                        request_id = await self.api.patch(selected.board_id, settings, resolved_properties)
                    else:
                        request_id = await self.api.patch(selected.board_id, settings)
                    self.operation.update(request_id=request_id, state="pending")
                except (Exception, asyncio.CancelledError) as error:
                    # Only controlled metadata is retained, never exception text,
                    # URLs, response bodies, credentials, or tracebacks.
                    code = "cancelled" if isinstance(error, asyncio.CancelledError) else "unexpected_error"
                    http_status = None
                    if isinstance(error, WeheatError):
                        code = error.code
                        http_status = error.http_status
                    self.operation.update(state="submission_uncertain", error_code=code, http_status=http_status)
                    _LOGGER.warning(
                        "Settings submission outcome uncertain (error_code=%s, http_status=%s); "
                        "further writes blocked, no retry performed",
                        code,
                        http_status,
                    )
                    await self._save_operation()
                    # The intent remains durable even if cancellation prevents cleanup.
                    if isinstance(error, asyncio.CancelledError):
                        raise
                    raise HomeAssistantError(
                        "Submission outcome is uncertain. Do not retry; check the official app and request state."
                    ) from None
                await self._save_operation()
            except AuthenticationError as error:
                self.entry.async_start_reauth(self.hass)
                raise HomeAssistantError(str(error)) from None
            except WeheatError as error:
                raise HomeAssistantError(str(error)) from None
        # This is a request, not an optimistic assertion that the setting changed.
        await self.async_request_refresh()
