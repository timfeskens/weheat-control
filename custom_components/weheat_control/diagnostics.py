"""Deliberately minimal diagnostics: never export tokens or device identifiers."""

from homeassistant.core import HomeAssistant

from . import WeheatConfigEntry


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: WeheatConfigEntry) -> dict[str, object]:
    coordinator = entry.runtime_data
    return {
        "last_update_success": coordinator.last_update_success,
        "settings_fields": sorted(coordinator.data or {}),
        "operation_state": coordinator.operation.get("state", "idle"),
        "operation_terminal": coordinator.operation.get("terminal"),
        "upstream_status": coordinator.operation.get("status"),
        "readback_matches": coordinator.operation.get("readback_matches"),
        "error_code": coordinator.operation.get("error_code"),
        "http_status": coordinator.operation.get("http_status"),
    }
