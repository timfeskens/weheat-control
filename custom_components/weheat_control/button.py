"""Explicit timed boost commands; never imply a persistent power switch."""

import time

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import WeheatConfigEntry
from .coordinator import WeheatCoordinator
from .entity import WeheatEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: WeheatConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([BoostButton(entry.runtime_data, minutes) for minutes in (30, 60, 0)])


class BoostButton(WeheatEntity, ButtonEntity):
    def __init__(self, coordinator: WeheatCoordinator, minutes: int) -> None:
        super().__init__(coordinator, f"boost_{minutes}")
        self._minutes = minutes
        self._attr_name = f"Hot-water boost {minutes} minutes" if minutes else "Stop hot-water boost"
        self._attr_icon = "mdi:water-boiler"

    @property
    def available(self) -> bool:
        value = self.coordinator.data.get("dhwAvailable")
        return super().available and type(value) is int and value == 1

    async def async_press(self) -> None:
        await self.coordinator.async_write({"DhwBoostEndTime": str(int(time.time()) + self._minutes * 60)})
