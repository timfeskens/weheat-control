"""Readback and durable settings request status."""

from datetime import UTC, datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import WeheatConfigEntry
from .controls import CONTROL_METHODS
from .coordinator import WeheatCoordinator
from .entity import WeheatEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: WeheatConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(
        [
            RequestSensor(entry.runtime_data),
            BoostEndSensor(entry.runtime_data),
            ControlMethodSensor(entry.runtime_data),
            CurveSensor(entry.runtime_data),
            DhwTargetSensor(entry.runtime_data),
        ]
    )


class DhwTargetSensor(WeheatEntity, SensorEntity):
    _attr_name = "Hot-water target readback"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: WeheatCoordinator) -> None:
        super().__init__(coordinator, "dhw_target_readback")

    @property
    def native_value(self) -> float | None:
        value = self.coordinator.data.get("dhwTargetTemperature")
        return float(value) if type(value) in (int, float) else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "dhw_schedule_mode": self.coordinator.data.get("dhwScheduleEnabled"),
            "fixed_target_write_requires_mode": 0,
            "mode_names": {"0": "Fixed setpoint", "2": "Manual schedule", "3": "Weheat Intelligence"},
        }


class ControlMethodSensor(WeheatEntity, SensorEntity):
    _attr_name = "Heating control method"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: WeheatCoordinator) -> None:
        super().__init__(coordinator, "control_method")

    @property
    def native_value(self) -> str | None:
        value = self.coordinator.data.get("controlMethod")
        return CONTROL_METHODS.get(value) if type(value) is int else None


class CurveSensor(WeheatEntity, SensorEntity):
    _attr_name = "Heating curve"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: WeheatCoordinator) -> None:
        super().__init__(coordinator, "heating_curve")

    @property
    def native_value(self) -> str | None:
        return "Available" if isinstance(self.coordinator.data.get("heatingCurveY"), list) else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "outdoor_temperatures": self.coordinator.data.get("heatingCurveX"),
            "water_temperatures": self.coordinator.data.get("heatingCurveY"),
            "unit": "°C",
        }


class RequestSensor(WeheatEntity, SensorEntity):
    _attr_name = "Settings request status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "request_status"
    _attr_options = [
        "idle",
        "submitting",
        "submission_uncertain",
        "pending",
        "executing",
        "succeeded_unverified",
        "succeeded",
        "cancelled",
        "cancelled_before_submission",
        "failed",
        "mismatch",
        "failed_device_wrong_state",
        "failed_wrong_execution_order",
        "failed_device_disconnected",
        "failed_settings_request_mismatch",
        "failed_settings_key_not_found",
        "failed_another_request_executing",
    ]

    def __init__(self, coordinator: WeheatCoordinator) -> None:
        super().__init__(coordinator, "request_status")

    @property
    def available(self) -> bool:
        # This is durable local request state, not a claim of cloud connectivity.
        return True

    @property
    def native_value(self) -> str:
        state = str(self.coordinator.operation.get("state", "idle"))
        # Older journals used HA's reserved unknown state for an actual outcome.
        return "submission_uncertain" if state == "unknown" else state

    @property
    def extra_state_attributes(self) -> dict[str, str | int | bool | None]:
        operation = self.coordinator.operation
        return {
            "request_id": str(operation["request_id"]) if "request_id" in operation else None,
            "upstream_status": operation.get("status") if type(operation.get("status")) is int else None,
            "unresolved": bool(operation) and operation.get("terminal") is not True,
            "readback_matches": operation.get("readback_matches")
            if type(operation.get("readback_matches")) is bool
            else None,
            "error_code": operation.get("error_code") if isinstance(operation.get("error_code"), str) else None,
            "http_status": operation.get("http_status") if type(operation.get("http_status")) is int else None,
        }


class BoostEndSensor(WeheatEntity, SensorEntity):
    _attr_name = "Hot-water boost end"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: WeheatCoordinator) -> None:
        super().__init__(coordinator, "boost_end")

    @property
    def native_value(self) -> datetime | None:
        value = self.coordinator.data.get("dhwBoostEndTime")
        try:
            if isinstance(value, str) and not value.isdigit():
                result = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return result if result.tzinfo else None
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                return datetime.fromtimestamp(float(value), UTC)
        except ValueError, OverflowError, OSError:
            pass
        return None
