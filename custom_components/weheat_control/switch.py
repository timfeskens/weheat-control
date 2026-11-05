"""Hot-water Intelligence only; never change space-heating control."""

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import WeheatConfigEntry
from .controls import dhw_intelligence_available, dhw_intelligence_properties
from .coordinator import WeheatCoordinator
from .entity import WeheatEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: WeheatConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([DhwIntelligenceSwitch(entry.runtime_data)])


class DhwIntelligenceSwitch(WeheatEntity, SwitchEntity):
    _attr_name = "Hot-water Weheat Intelligence"
    _attr_icon = "mdi:water-boiler-auto"

    def __init__(self, coordinator: WeheatCoordinator) -> None:
        super().__init__(coordinator, "dhw_intelligence")

    @property
    def available(self) -> bool:
        return super().available and dhw_intelligence_available(self.coordinator.data)

    @property
    def is_on(self) -> bool | None:
        if not dhw_intelligence_available(self.coordinator.data):
            return None
        return self.coordinator.data["dhwScheduleEnabled"] == 3

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_write({"DhwScheduleEnabled": 3}, properties=dhw_intelligence_properties)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_write({"DhwScheduleEnabled": 0})
