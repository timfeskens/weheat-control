"""Edit the manual hot-water schedule; always submits the complete week, never the DHW mode."""

from __future__ import annotations

from collections.abc import Callable
from datetime import time

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .api import Settings, WeheatError
from .const import DOMAIN
from .controls import DAYS, dhw_full_schedule, dhw_schedule_slots

SERVICE_SET_DHW_SCHEDULE = "set_dhw_schedule"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_DAYS = "days"
ATTR_PERIODS = "periods"

PERIOD_SCHEMA = vol.Schema(
    {
        vol.Required("start"): cv.time,
        vol.Required("end"): cv.time,
        vol.Required("temperature"): vol.Coerce(float),
    }
)
SET_DHW_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_DAYS): vol.All(cv.ensure_list, [vol.In([day.lower() for day in DAYS])], vol.Length(min=1)),
        vol.Optional(ATTR_PERIODS, default=[]): vol.All(cv.ensure_list, [PERIOD_SCHEMA]),
    }
)


def _minutes(value: time) -> int:
    if value.second or value.microsecond or value.tzinfo:
        raise ServiceValidationError("Use device-local hours and minutes only")
    return value.hour * 60 + value.minute


def schedule_command(days: list[str], periods: list[dict[str, object]]) -> Callable[[Settings], Settings]:
    """Replace the selected weekdays; the others are re-sent from fresh readback."""
    try:
        slots = dhw_schedule_slots(
            [(_minutes(p["start"]), _minutes(p["end"]), float(p["temperature"])) for p in periods]  # type: ignore[arg-type]
        )
    except WeheatError as error:
        raise ServiceValidationError(str(error)) from None
    replacements = {day: list(slots) for day in DAYS if day.lower() in days}
    if not replacements:
        raise ServiceValidationError("No weekday selected")

    def build(current: Settings) -> Settings:
        return dhw_full_schedule(current, replacements)

    return build


async def _set_dhw_schedule(call: ServiceCall) -> None:
    entry = call.hass.config_entries.async_get_entry(call.data[ATTR_CONFIG_ENTRY_ID])
    if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError("Select a loaded Weheat Control entry")
    await entry.runtime_data.async_write(schedule_command(call.data[ATTR_DAYS], call.data[ATTR_PERIODS]))


def async_register_services(hass: HomeAssistant) -> None:
    hass.services.async_register(DOMAIN, SERVICE_SET_DHW_SCHEDULE, _set_dhw_schedule, SET_DHW_SCHEDULE_SCHEMA)
