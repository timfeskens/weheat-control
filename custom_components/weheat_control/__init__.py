"""Unofficial Weheat cloud integration for Home Assistant."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AuthenticationError, Tokens, WeheatApi
from .const import TOKENS
from .coordinator import WeheatCoordinator

PLATFORMS = [Platform.BUTTON, Platform.NUMBER, Platform.SELECT, Platform.SENSOR, Platform.SWITCH, Platform.TIME]
type WeheatConfigEntry = ConfigEntry[WeheatCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: WeheatConfigEntry) -> bool:
    async def save_tokens(tokens: Tokens) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, TOKENS: tokens.as_dict()})

    try:
        tokens = Tokens.from_saved(entry.data.get(TOKENS))
    except AuthenticationError as error:
        raise ConfigEntryAuthFailed(str(error)) from None
    api = WeheatApi(async_get_clientsession(hass), tokens, save_tokens)
    coordinator = WeheatCoordinator(hass, entry, api)
    await coordinator.async_load_operation()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WeheatConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
