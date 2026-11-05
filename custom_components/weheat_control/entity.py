"""Shared device identity and coordinator state."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, INSTALLATION_ID
from .coordinator import WeheatCoordinator


class WeheatEntity(CoordinatorEntity[WeheatCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: WeheatCoordinator, key: str) -> None:
        super().__init__(coordinator)
        installation_id = str(coordinator.entry.data[INSTALLATION_ID])
        self._attr_unique_id = f"{installation_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, installation_id)},
            manufacturer="Weheat",
            name=f"Weheat {installation_id}",
        )
