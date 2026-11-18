"""Read-only Weheat Intelligence hot-water plan (GET /installations/{id}/dhw-schedules).

Observed response: a list with one item per planned day, e.g.
``{"scheduleStartAt": ..., "targetTemperatureC": 52.9, "holdTimeMinutes": 120,
"averageCycleHoldTimeMinutes": 120, "cycles": [{"startAt": "...Z", "isLegionella": false}, ...]}``.
The app draws each cycle as ``startAt`` + ``averageCycleHoldTimeMinutes`` (legionella and
fallback: ``holdTimeMinutes``); cycles without ``startAt`` are skipped.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import Json, WeheatApi, WeheatError
from .const import DOMAIN, INSTALLATION_ID

_LOGGER = logging.getLogger(__name__)

HEATING = "heating"
LEGIONELLA = "legionella"
STANDBY = "standby"


@dataclass(frozen=True)
class PlanWindow:
    start: datetime
    end: datetime
    legionella: bool

    def as_dict(self) -> dict[str, object]:
        start, end = dt_util.as_local(self.start), dt_util.as_local(self.end)
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "legionella": self.legionella,
        }

    def label(self) -> str:
        start, end = dt_util.as_local(self.start), dt_util.as_local(self.end)
        text = f"{start:%H:%M}–{end:%H:%M}"
        return f"{text} (legionella)" if self.legionella else text


@dataclass(frozen=True)
class Plan:
    """Parsed plan; ``active`` is False outside Intelligence mode."""

    active: bool = False
    windows: tuple[PlanWindow, ...] = ()
    # targetTemperatureC per local date, as reported; its meaning is not established.
    target_temperatures: dict[str, float] = field(default_factory=dict)
    # Local dates for which Weheat returned a plan item; tomorrow's appears in the afternoon.
    days: frozenset[str] = frozenset()

    def has_day(self, day: datetime) -> bool:
        return dt_util.as_local(day).date().isoformat() in self.days

    def state_at(self, moment: datetime) -> str:
        for window in self.windows:
            if window.start <= moment < window.end:
                return LEGIONELLA if window.legionella else HEATING
        return STANDBY

    def next_boundary(self, moment: datetime) -> datetime | None:
        edges = [edge for window in self.windows for edge in (window.start, window.end) if edge > moment]
        return min(edges, default=None)

    def next_start(self, moment: datetime) -> PlanWindow | None:
        return next((window for window in self.windows if window.start > moment), None)

    def windows_on(self, day: datetime) -> list[PlanWindow]:
        """Windows whose local start date is the local date of ``day``."""
        local_day = dt_util.as_local(day).date()
        return [window for window in self.windows if dt_util.as_local(window.start).date() == local_day]


def _timestamp(value: Json) -> datetime | None:
    if not isinstance(value, str):
        return None
    parsed = dt_util.parse_datetime(value)
    return dt_util.as_utc(parsed) if parsed is not None and parsed.tzinfo is not None else None


def _minutes(value: Json) -> float | None:
    if type(value) in (int, float) and math.isfinite(value) and 0 < value <= 24 * 60:  # type: ignore[arg-type]
        return float(value)  # type: ignore[arg-type]
    return None


def parse_plan(items: Json) -> Plan:
    """Fail closed per item/cycle: skip what cannot be parsed, never invent a window."""
    if isinstance(items, dict):
        items = items.get("data")
    if not isinstance(items, list):
        raise WeheatError("Unexpected hot-water plan response")
    windows: dict[tuple[datetime, bool], PlanWindow] = {}
    targets: dict[str, float] = {}
    days: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        day = _timestamp(item.get("scheduleStartAt"))
        if day is not None:
            days.add(dt_util.as_local(day).date().isoformat())
        target = item.get("targetTemperatureC")
        if day is not None and type(target) in (int, float) and math.isfinite(target):  # type: ignore[arg-type]
            targets[dt_util.as_local(day).date().isoformat()] = round(float(target), 1)  # type: ignore[arg-type]
        hold = _minutes(item.get("holdTimeMinutes"))
        average = _minutes(item.get("averageCycleHoldTimeMinutes"))
        cycles = item.get("cycles")
        if not isinstance(cycles, list):
            continue
        for cycle in cycles:
            if not isinstance(cycle, dict):
                continue
            start = _timestamp(cycle.get("startAt"))
            legionella = cycle.get("isLegionella") is True
            minutes = hold if legionella else (average or hold)
            if start is None or minutes is None:
                continue
            # The same cycle can be listed by neighbouring days; keep it once.
            windows[(start, legionella)] = PlanWindow(start, start + timedelta(minutes=minutes), legionella)
    return Plan(True, tuple(sorted(windows.values(), key=lambda window: window.start)), targets, frozenset(days))


class WeheatPlanCoordinator(DataUpdateCoordinator[Plan]):
    """Polls the plan from yesterday through tomorrow; only in hot-water Intelligence mode."""

    def __init__(self, hass: HomeAssistant, main: DataUpdateCoordinator, api: WeheatApi) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_dhw_plan",
            config_entry=main.config_entry,
            update_interval=timedelta(minutes=15),
        )
        self.main = main
        self.api = api
        self.entry = main.config_entry

    async def _async_update_data(self) -> Plan:
        settings = self.main.data or {}
        if settings.get("dhwScheduleEnabled") != 3:
            return Plan()
        today = dt_util.start_of_local_day(dt_util.as_local(dt_util.utcnow()))
        start, end = today - timedelta(days=1), today + timedelta(days=2)
        try:
            items = await self.api.dhw_schedules(str(self.entry.data[INSTALLATION_ID]), start, end)
            return parse_plan(items)
        except WeheatError as error:
            # Login renewal is driven by the settings coordinator; the plan is optional.
            raise UpdateFailed(str(error)) from None
