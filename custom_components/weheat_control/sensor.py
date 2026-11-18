"""Readback and durable settings request status."""

from datetime import UTC, datetime, timedelta

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from . import WeheatConfigEntry
from .controls import CONTROL_METHODS, DAYS, dhw_schedule_available, dhw_schedule_periods, dhw_schedule_read_key
from .coordinator import WeheatCoordinator
from .entity import WeheatEntity
from .plan import HEATING, LEGIONELLA, STANDBY, Plan, PlanWindow


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
            *(DhwScheduleSensor(entry.runtime_data, day) for day in DAYS),
        ]
    )
    if entry.runtime_data.plan is not None:
        async_add_entities(
            [
                DhwPlanStateSensor(entry.runtime_data),
                DhwPlanDaySensor(entry.runtime_data, -1, "Yesterday"),
                DhwPlanDaySensor(entry.runtime_data, 0, "Today"),
                DhwPlanDaySensor(entry.runtime_data, 1, "Tomorrow"),
                DhwPlanNextSensor(entry.runtime_data),
            ]
        )


class DhwScheduleSensor(WeheatEntity, SensorEntity):
    """Readable summary of one weekday of the manual hot-water schedule."""

    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: WeheatCoordinator, day: str) -> None:
        super().__init__(coordinator, f"dhw_schedule_{day.lower()}")
        self._day = day
        self._attr_name = f"Hot-water schedule {day}"

    def _periods(self) -> list[dict[str, str | float]] | None:
        if not dhw_schedule_available(self.coordinator.data):
            return None
        return dhw_schedule_periods(self.coordinator.data[dhw_schedule_read_key(self._day)])  # type: ignore[arg-type]

    @property
    def available(self) -> bool:
        return super().available and self._periods() is not None

    @property
    def native_value(self) -> str | None:
        periods = self._periods()
        if periods is None:
            return None
        if not periods:
            return "No periods"
        summary = ", ".join(f"{p['start']}–{p['end']} {p['temperature']:g} °C" for p in periods)
        return summary if len(summary) <= 255 else f"{len(periods)} periods"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "periods": self._periods() or [],
            "schedule_active": self.coordinator.data.get("dhwScheduleEnabled") == 2,
            "safety_temperature": self.coordinator.data.get("dhwScheduleSafetyTemperature"),
        }


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
        "expired_outcome_unknown",
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


class _PlanEntity(WeheatEntity, SensorEntity):
    """Read-only Intelligence plan entity that also updates at plan/day boundaries."""

    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: WeheatCoordinator, key: str) -> None:
        super().__init__(coordinator.plan, key)  # type: ignore[arg-type]
        self._unsub_timer: CALLBACK_TYPE | None = None

    @property
    def plan(self) -> Plan | None:
        data = self.coordinator.data
        return data if isinstance(data, Plan) and data.active else None

    @property
    def available(self) -> bool:
        return super().available and self.plan is not None

    def _next_change(self, now: datetime) -> datetime | None:
        return dt_util.start_of_local_day(dt_util.as_local(dt_util.utcnow())) + timedelta(days=1)

    @callback
    def _schedule(self) -> None:
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        if self.hass is None:
            return
        moment = self._next_change(dt_util.utcnow())
        if moment is not None:
            self._unsub_timer = async_track_point_in_utc_time(self.hass, self._at_boundary, moment)

    @callback
    def _at_boundary(self, _now: datetime) -> None:
        self._unsub_timer = None
        self.async_write_ha_state()
        self._schedule()

    @callback
    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()
        self._schedule()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._schedule()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        await super().async_will_remove_from_hass()


class DhwPlanStateSensor(_PlanEntity):
    """Current planned activity; its recorded history is a coloured timeline in HA."""

    _attr_name = "Hot-water Intelligence plan"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "dhw_plan"
    _attr_options = [HEATING, LEGIONELLA, STANDBY]

    def __init__(self, coordinator: WeheatCoordinator) -> None:
        super().__init__(coordinator, "dhw_plan")

    def _next_change(self, now: datetime) -> datetime | None:
        plan = self.plan
        return plan.next_boundary(now) if plan else None

    @property
    def native_value(self) -> str | None:
        plan = self.plan
        return plan.state_at(dt_util.utcnow()) if plan else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        plan = self.plan
        if plan is None:
            return {}
        now = dt_util.utcnow()
        return {
            "windows": [window.as_dict() for window in plan.windows],
            # Reported by the plan; not established to be the heat pump's actual setpoint.
            "reported_target_temperature": plan.target_temperatures.get(dt_util.as_local(now).date().isoformat()),
        }


class DhwPlanDaySensor(_PlanEntity):
    """Planned heating moments of yesterday, today or tomorrow (device-local time)."""

    def __init__(self, coordinator: WeheatCoordinator, offset: int, label: str) -> None:
        super().__init__(coordinator, f"dhw_plan_{label.lower()}")
        self._offset = offset
        self._attr_name = f"Hot-water Intelligence plan {label.lower()}"

    def _day(self) -> datetime:
        return dt_util.start_of_local_day(dt_util.as_local(dt_util.utcnow())) + timedelta(days=self._offset, hours=12)

    def _windows(self) -> list[PlanWindow] | None:
        plan = self.plan
        if plan is None:
            return None
        return plan.windows_on(self._day())

    @property
    def native_value(self) -> str | None:
        windows = self._windows()
        if windows is None:
            return None
        if not windows:
            plan = self.plan
            if plan is not None and not plan.has_day(self._day()):
                # Weheat has not sent a plan for this day (yet): not the same as "no heating".
                return "Not yet published" if self._offset > 0 else "No plan available"
            return "No planned heating"
        summary = ", ".join(window.label() for window in windows)
        return summary if len(summary) <= 255 else f"{len(windows)} planned moments"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        windows = self._windows()
        plan = self.plan
        if windows is None or plan is None:
            return {}
        day = (
            (dt_util.start_of_local_day(dt_util.as_local(dt_util.utcnow())) + timedelta(days=self._offset))
            .date()
            .isoformat()
        )
        return {
            "date": day,
            "windows": [window.as_dict() for window in windows],
            "reported_target_temperature": plan.target_temperatures.get(day),
        }


class DhwPlanNextSensor(_PlanEntity):
    _attr_name = "Hot-water Intelligence next planned heating"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: WeheatCoordinator) -> None:
        super().__init__(coordinator, "dhw_plan_next")

    def _next_change(self, now: datetime) -> datetime | None:
        plan = self.plan
        window = plan.next_start(now) if plan else None
        return window.start if window else None

    @property
    def native_value(self) -> datetime | None:
        plan = self.plan
        window = plan.next_start(dt_util.utcnow()) if plan else None
        return window.start if window else None
