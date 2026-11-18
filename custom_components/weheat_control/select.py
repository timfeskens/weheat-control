"""App-derived quiet presets and DHW Intelligence comfort preference."""

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import WeheatConfigEntry
from .controls import (
    DAY_PRESETS,
    DHW_COMFORT_CODES,
    DHW_COMFORT_LEVELS,
    NIGHT_PRESETS,
    dhw_comfort_available,
    dhw_comfort_option,
)
from .coordinator import WeheatCoordinator
from .entity import WeheatEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: WeheatConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(
        [
            QuietPreset(
                entry.runtime_data, "PerformanceModePreset", "performanceModePreset", "Day quiet preset", DAY_PRESETS
            ),
            QuietPreset(
                entry.runtime_data,
                "SilentHoursScheduledPreset",
                "silentHoursScheduledPreset",
                "Scheduled-night quiet preset",
                NIGHT_PRESETS,
            ),
            DhwComfortPreference(entry.runtime_data),
        ]
    )


class QuietPreset(WeheatEntity, SelectEntity):
    def __init__(
        self, coordinator: WeheatCoordinator, write_key: str, read_key: str, name: str, presets: dict[str, int]
    ) -> None:
        super().__init__(coordinator, read_key)
        self._write_key, self._read_key, self._presets = write_key, read_key, presets
        self._attr_name = name
        self._attr_options = list(presets)
        self._attr_icon = "mdi:volume-low"

    @property
    def available(self) -> bool:
        return super().available and self.current_option is not None

    @property
    def current_option(self) -> str | None:
        value = self.coordinator.data.get(self._read_key)
        if type(value) is not int:
            return None
        return next((name for name, code in self._presets.items() if code == value), None)

    async def async_select_option(self, option: str) -> None:
        if option not in self._presets:
            raise HomeAssistantError("Unknown quiet preset")
        await self.coordinator.async_write({self._write_key: self._presets[option]})


class DhwComfortPreference(WeheatEntity, SelectEntity):
    """The app's Intelligence choice Normaal/Zuinig: safety temperature plus comfort code."""

    _attr_name = "Hot-water Intelligence comfort"
    _attr_icon = "mdi:water-boiler-auto"
    _attr_options = list(DHW_COMFORT_LEVELS)

    def __init__(self, coordinator: WeheatCoordinator) -> None:
        # Same unique ID as the former comfort-preference select, so the entity is kept.
        super().__init__(coordinator, "dhw_comfort_preference")

    @property
    def available(self) -> bool:
        return super().available and dhw_comfort_available(self.coordinator.data)

    @property
    def current_option(self) -> str | None:
        return dhw_comfort_option(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "safety_temperature": self.coordinator.data.get("dhwScheduleSafetyTemperature"),
            "comfort_code": self.coordinator.data.get("dhwComfortPreference"),
        }

    async def async_select_option(self, option: str) -> None:
        if option not in DHW_COMFORT_LEVELS:
            raise HomeAssistantError("Unknown hot-water Intelligence comfort setting")
        await self.coordinator.async_write(
            {"DhwScheduleSafetyTemperature": DHW_COMFORT_LEVELS[option]},
            properties={"DhwComfortPreference": DHW_COMFORT_CODES[option]},
        )
