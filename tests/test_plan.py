"""Read-only Intelligence hot-water plan: parsing, timeline state, day sensors and polling gate."""

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiohttp import ClientSession
from homeassistant.util import dt as dt_util

from custom_components.weheat_control.api import Tokens, WeheatApi, WeheatError
from custom_components.weheat_control.plan import (
    HEATING,
    LEGIONELLA,
    STANDBY,
    Plan,
    PlanWindow,
    WeheatPlanCoordinator,
    parse_plan,
)
from custom_components.weheat_control.sensor import DhwPlanDaySensor, DhwPlanNextSensor, DhwPlanStateSensor

pytest_plugins = ["test_weheat"]

# Shape of a real response (cost/probability arrays trimmed).
LIVE = [
    {
        "scheduleStartAt": "2026-11-17T23:00:00Z",
        "targetTemperatureC": 52.916009336518066,
        "holdTimeMinutes": 120,
        "averageCycleHoldTimeMinutes": 120,
        "legionellaCyclePosition": 0,
        "cycles": [
            {"startAt": "2026-11-17T21:00:00Z", "isLegionella": False},
            {"startAt": "2026-11-18T02:15:00Z", "isLegionella": False},
            {"startAt": None, "isLegionella": False},
        ],
        "dhwSchedulingCostEur": None,
        "costCoefficients": [0.28, 0.28],
        "baselineDhwProbabilities": [0.01, 0.01],
    }
]


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


@pytest.fixture(autouse=True)
def amsterdam():
    previous = dt_util.get_default_time_zone()
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Amsterdam"))
    yield
    dt_util.set_default_time_zone(previous)


def test_live_response_parses_to_two_windows():
    plan = parse_plan(LIVE)
    assert plan.active
    assert plan.windows == (
        PlanWindow(utc("2026-11-17T21:00:00"), utc("2026-11-17T23:00:00"), False),
        PlanWindow(utc("2026-11-18T02:15:00"), utc("2026-11-18T04:15:00"), False),
    )
    assert [window.label() for window in plan.windows] == ["22:00–00:00", "03:15–05:15"]
    assert plan.target_temperatures == {"2026-11-18": 52.9}
    assert plan.days == frozenset({"2026-11-18"})
    assert parse_plan({"data": LIVE}) == plan
    assert parse_plan([]) == Plan(True, (), {}, frozenset())


def test_state_boundaries_and_next_start():
    plan = parse_plan(LIVE)
    assert plan.state_at(utc("2026-11-17T20:59:59")) == STANDBY
    assert plan.state_at(utc("2026-11-17T21:00:00")) == HEATING
    assert plan.state_at(utc("2026-11-17T23:00:00")) == STANDBY  # end is exclusive
    assert plan.next_boundary(utc("2026-11-17T22:00:00")) == utc("2026-11-17T23:00:00")
    assert plan.next_boundary(utc("2026-11-18T05:00:00")) is None
    assert plan.next_start(utc("2026-11-17T22:00:00")).start == utc("2026-11-18T02:15:00")


def test_legionella_uses_hold_time_and_duplicates_collapse():
    items = [
        {
            "scheduleStartAt": "2026-11-17T23:00:00Z",
            "holdTimeMinutes": 60,
            "averageCycleHoldTimeMinutes": 120,
            "cycles": [{"startAt": "2026-11-18T10:00:00Z", "isLegionella": True}],
        },
        {
            "scheduleStartAt": "2026-11-18T23:00:00Z",
            "holdTimeMinutes": 60,
            "averageCycleHoldTimeMinutes": 120,
            "cycles": [{"startAt": "2026-11-18T10:00:00Z", "isLegionella": True}],
        },
    ]
    plan = parse_plan(items)
    assert plan.windows == (PlanWindow(utc("2026-11-18T10:00:00"), utc("2026-11-18T11:00:00"), True),)
    assert plan.state_at(utc("2026-11-18T10:30:00")) == LEGIONELLA


@pytest.mark.parametrize(
    "cycle,item",
    [
        ({"startAt": "2026-11-18T02:15:00"}, {}),  # no timezone
        ({"startAt": "not a date"}, {}),
        ({"startAt": 123}, {}),
        ({"startAt": "2026-11-18T02:15:00Z"}, {"holdTimeMinutes": None, "averageCycleHoldTimeMinutes": None}),
        ({"startAt": "2026-11-18T02:15:00Z"}, {"holdTimeMinutes": -5, "averageCycleHoldTimeMinutes": 0}),
        ({"startAt": "2026-11-18T02:15:00Z"}, {"holdTimeMinutes": float("nan"), "averageCycleHoldTimeMinutes": None}),
    ],
)
def test_unparseable_cycles_are_skipped_not_invented(cycle, item):
    base = {"holdTimeMinutes": 120, "averageCycleHoldTimeMinutes": 120}
    assert parse_plan([{**base, **item, "cycles": [cycle]}]).windows == ()


@pytest.mark.parametrize("response", [None, "x", {"data": "x"}, 3])
def test_unexpected_response_is_an_error(response):
    with pytest.raises(WeheatError):
        parse_plan(response)


def plan_coordinator(plan: Plan):
    current = Mock()
    current.plan = Mock()
    current.plan.data = plan
    current.plan.last_update_success = True
    current.plan.entry.data = {"installation_id": "installation-1"}
    return current


def test_sensors_from_live_plan():
    current = plan_coordinator(parse_plan(LIVE))
    with patch("homeassistant.util.dt.utcnow", return_value=utc("2026-11-17T22:30:00")):
        state = DhwPlanStateSensor(current)
        assert state.available
        assert state.native_value == HEATING
        assert state.unique_id == "installation-1_dhw_plan"
        assert state._next_change(utc("2026-11-17T22:30:00")) == utc("2026-11-17T23:00:00")
        assert len(state.extra_state_attributes["windows"]) == 2
        assert DhwPlanNextSensor(current).native_value == utc("2026-11-18T02:15:00")
        today = DhwPlanDaySensor(current, 0, "Today")
        # 22:30 UTC on the 17th is 23:30 local: "today" is the 17th, tomorrow the 18th.
        assert today.native_value == "22:00–00:00"
        tomorrow = DhwPlanDaySensor(current, 1, "Tomorrow")
        assert tomorrow.native_value == "03:15–05:15"
        assert tomorrow.extra_state_attributes["reported_target_temperature"] == 52.9
        assert DhwPlanDaySensor(current, -1, "Yesterday").native_value == "No plan available"
    # Midday on the 18th: today's plan exists, tomorrow's is not published yet (comes at 16:00).
    with patch("homeassistant.util.dt.utcnow", return_value=utc("2026-11-18T11:00:00")):
        assert DhwPlanDaySensor(current, 0, "Today").native_value == "03:15–05:15"
        assert DhwPlanDaySensor(current, 1, "Tomorrow").native_value == "Not yet published"
    published = [*LIVE, {**LIVE[0], "scheduleStartAt": "2026-11-18T23:00:00Z", "cycles": []}]
    current.plan.data = parse_plan(published)
    with patch("homeassistant.util.dt.utcnow", return_value=utc("2026-11-18T15:30:00")):
        assert DhwPlanDaySensor(current, 1, "Tomorrow").native_value == "No planned heating"


def test_sensors_unavailable_outside_intelligence_or_on_failure():
    current = plan_coordinator(Plan())
    for entity in (DhwPlanStateSensor(current), DhwPlanDaySensor(current, 0, "Today"), DhwPlanNextSensor(current)):
        assert not entity.available
        assert entity.native_value is None
    current.plan.data = parse_plan(LIVE)
    current.plan.last_update_success = False
    assert not DhwPlanStateSensor(current).available


async def test_coordinator_fetches_yesterday_through_tomorrow_only_in_intelligence(hass):
    main = Mock()
    main.config_entry.data = {"installation_id": "installation-1"}
    api = Mock()
    api.dhw_schedules = AsyncMock(return_value=LIVE)
    coordinator = WeheatPlanCoordinator(hass, main, api)
    main.data = {"dhwScheduleEnabled": 0}
    assert await coordinator._async_update_data() == Plan()
    api.dhw_schedules.assert_not_awaited()
    main.data = {"dhwScheduleEnabled": 3}
    with patch("homeassistant.util.dt.utcnow", return_value=utc("2026-11-18T11:00:00")):
        assert (await coordinator._async_update_data()).windows == parse_plan(LIVE).windows
    installation, start, end = api.dhw_schedules.await_args.args
    assert installation == "installation-1"
    assert (dt_util.as_local(start).isoformat(), end - start) == ("2026-11-17T00:00:00+01:00", timedelta(days=3))
    api.dhw_schedules.side_effect = WeheatError("down")
    with pytest.raises(Exception):
        await coordinator._async_update_data()


async def test_api_request_matches_the_app():
    async with ClientSession() as session:
        api = WeheatApi(session, Tokens("fake", "fake-refresh", time.time() + 300))
        with patch.object(api, "_http", new_callable=AsyncMock, return_value=LIVE) as http:
            result = await api.dhw_schedules(
                "installation 1", utc("2026-11-17T23:00:00"), utc("2026-11-19T22:59:59.999000")
            )
        assert result == LIVE
        method, url = http.await_args.args
        assert method == "GET"
        assert url.endswith(
            "/installations/installation%201/dhw-schedules"
            "?from=2026-11-17T23%3A00%3A00.000Z&to=2026-11-19T22%3A59%3A59.999Z"
        )
        assert http.await_args.kwargs.get("body") is None
