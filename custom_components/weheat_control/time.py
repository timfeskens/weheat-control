"""Per-day quiet schedule; preserves unequal weekday intervals."""

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import WeheatConfigEntry
from .controls import DAYS, schedule_available
from .coordinator import WeheatCoordinator
from .entity import WeheatEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: WeheatConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([QuietTime(entry.runtime_data, day, edge) for day in DAYS for edge in ("Start", "Stop")])


class QuietTime(WeheatEntity, TimeEntity):
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator: WeheatCoordinator, day: str, edge: str) -> None:
        super().__init__(coordinator, f"silent_{day}_{edge}")
        self._suffix = f"{day}{edge}"
        self._attr_name = f"Quiet schedule {day} {edge.lower()}"

    @property
    def available(self) -> bool:
        return super().available and schedule_available(self.coordinator.data)

    @property
    def native_value(self) -> time | None:
        hour = self.coordinator.data.get(f"silentHours{self._suffix}")
        minute = self.coordinator.data.get(f"silentMinutes{self._suffix}")
        if schedule_available(self.coordinator.data) and type(hour) is int and type(minute) is int:
            return time(hour, minute)
        return None

    async def async_set_value(self, value: time) -> None:
        if value.second or value.microsecond or value.tzinfo:
            raise HomeAssistantError("Use device-local hours and minutes only; timezone conversion is not established")
        await self.coordinator.async_write(
            {f"SilentHours{self._suffix}": value.hour, f"SilentMinutes{self._suffix}": value.minute}
        )
