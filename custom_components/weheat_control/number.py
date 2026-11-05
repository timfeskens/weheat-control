"""DHW target and capability-gated heating-curve points."""

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import WeheatConfigEntry
from .api import Settings, WeheatError
from .controls import curve_available, curve_point, dhw_target_available
from .coordinator import WeheatCoordinator
from .entity import WeheatEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: WeheatConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[NumberEntity] = [DhwTarget(coordinator)]
    async_add_entities(entities)
    registered: set[int] = set()

    @callback
    def add_curve_points() -> None:
        if not curve_available(coordinator.data):
            return
        curve = coordinator.data.get("heatingCurveY")
        if isinstance(curve, list):
            missing = [index for index in range(len(curve)) if index not in registered]
            registered.update(missing)
            if missing:
                async_add_entities([HeatingCurvePoint(coordinator, index) for index in missing])

    entry.async_on_unload(coordinator.async_add_listener(add_curve_points))
    add_curve_points()


class DhwTarget(WeheatEntity, NumberEntity):
    _attr_name = "Hot-water target temperature"
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = 40
    _attr_native_max_value = 60
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: WeheatCoordinator) -> None:
        super().__init__(coordinator, "dhw_target")

    @property
    def available(self) -> bool:
        return super().available and dhw_target_available(self.coordinator.data)

    @property
    def native_value(self) -> float | None:
        value = self.coordinator.data.get("dhwTargetTemperature")
        return float(value) if type(value) in (int, float) else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_write({"DhwTargetTemperature": value})


class HeatingCurvePoint(WeheatEntity, NumberEntity):
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = 18
    _attr_native_max_value = 60
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: WeheatCoordinator, index: int) -> None:
        super().__init__(coordinator, f"heating_curve_{index}")
        self._index = index
        self._original_x = coordinator.data.get("heatingCurveX")
        self._attr_name = f"Heating curve point {index + 1}"
        # These are advanced controls, not a pretend room thermostat.

    @property
    def available(self) -> bool:
        return (
            super().available
            and curve_available(self.coordinator.data)
            and self.coordinator.data.get("heatingCurveX") == self._original_x
        )

    @property
    def native_value(self) -> float | None:
        values = self.coordinator.data.get("heatingCurveY")
        if isinstance(values, list) and self._index < len(values):
            return float(values[self._index])
        return None

    async def async_set_native_value(self, value: float) -> None:
        def build(current: Settings) -> Settings:
            if current.get("heatingCurveX") != self._original_x:
                raise WeheatError("Heating-curve axes changed; reload the integration")
            return curve_point(current, self._index, value)

        await self.coordinator.async_write(build)
