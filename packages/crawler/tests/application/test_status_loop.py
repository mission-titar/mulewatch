"""The status loop: a reading every minute feeds the channel gauges, and a degraded state alerts
once it has lasted, whatever came before it, boot included (spec stage 2, D15)."""

import asyncio

import pytest

from mulewatch.application.status_loop import StatusLoopDeps, status_loop
from mulewatch.domain.observability import events as ev
from mulewatch.ports.client_errors import ClientUnreachableError
from mulewatch.ports.client_status import ChannelStatus, ClientStatus
from tests.application.fakes import FakeClock, make_unreachable

type _Reading = ClientStatus | ClientUnreachableError

_ALERTS = (
    ev.ChannelDegraded,
    ev.ChannelRecovered,
    ev.ClientUnreachableLasting,
    ev.ClientReachableAgain,
)
_UP = (True, True)
_LOW_ID = (True, False)  # on the network, not connectable
_DOWN = make_unreachable()


def _status(**channels: tuple[bool, bool | None]) -> ClientStatus:
    return ClientStatus(
        "3.0.1",
        tuple(ChannelStatus(name, on, connectable) for name, (on, connectable) in channels.items()),
    )


class _ScriptedClient:
    """One scripted reading per call; the last one asks for shutdown."""

    def __init__(self, readings: list[_Reading], shutdown: asyncio.Event) -> None:
        self._readings = readings
        self._shutdown = shutdown

    async def status(self) -> ClientStatus:
        reading = self._readings.pop(0)
        if not self._readings:
            self._shutdown.set()
        if isinstance(reading, ClientUnreachableError):
            raise reading
        return reading


class _TimedTelemetry:
    """Each event with the seconds elapsed on the fake clock when it was emitted."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self._start = clock.now()
        self.events: list[tuple[float, ev.Event]] = []

    async def emit(self, event: ev.Event) -> None:
        self.events.append(((self._clock.now() - self._start).total_seconds(), event))


async def _run(*readings: _Reading, client: str = "amuled") -> list[tuple[float, ev.Event]]:
    clock = FakeClock()
    shutdown = asyncio.Event()
    telemetry = _TimedTelemetry(clock)
    deps = StatusLoopDeps(
        client, _ScriptedClient(list(readings), shutdown), clock, telemetry, shutdown
    )
    await asyncio.wait_for(status_loop(deps), timeout=1.0)
    return telemetry.events


async def _alerts(*readings: _Reading) -> list[tuple[float, ev.Event]]:
    return [(at, event) for at, event in await _run(*readings) if isinstance(event, _ALERTS)]


def _degraded(channel: str, field: ev.ChannelField) -> ev.ChannelDegraded:
    return ev.ChannelDegraded("amuled", channel, field, 300.0)


@pytest.mark.asyncio
async def test_not_connectable_from_the_first_reading_alerts_at_five_minutes_once() -> None:
    alerts = await _alerts(*[_status(ed2k=_LOW_ID)] * 10)
    assert alerts == [(300.0, _degraded("ed2k", "connectable"))]


@pytest.mark.asyncio
async def test_a_degraded_state_lasting_four_minutes_never_alerts_nor_recovers() -> None:
    alerts = await _alerts(*[_status(ed2k=_LOW_ID)] * 5, _status(ed2k=_UP))
    assert alerts == []


@pytest.mark.asyncio
async def test_off_the_network_alerts_whatever_the_client_and_channel() -> None:
    events = await _run(*[_status(x=(False, None))] * 6, client="fake")
    assert [e for _, e in events if isinstance(e, _ALERTS)] == [
        ev.ChannelDegraded("fake", "x", "on_network", 300.0)
    ]


@pytest.mark.asyncio
async def test_a_good_reading_after_an_alert_recovers() -> None:
    alerts = await _alerts(*[_status(kad=_LOW_ID)] * 6, _status(kad=_UP))
    assert alerts == [
        (300.0, _degraded("kad", "connectable")),
        (360.0, ev.ChannelRecovered("amuled", "kad", "connectable")),
    ]


@pytest.mark.asyncio
async def test_unknown_between_two_degraded_readings_restarts_the_clock() -> None:
    unknown = _status(kad=(True, None))
    alerts = await _alerts(*[_status(kad=_LOW_ID)] * 3, unknown, *[_status(kad=_LOW_ID)] * 6)
    assert alerts == [(540.0, _degraded("kad", "connectable"))]


@pytest.mark.asyncio
async def test_unknown_after_an_alert_is_no_recovery() -> None:
    unknown = _status(kad=(True, None))
    alerts = await _alerts(*[_status(kad=_LOW_ID)] * 6, unknown, _status(kad=_UP))
    assert alerts == [
        (300.0, _degraded("kad", "connectable")),
        (420.0, ev.ChannelRecovered("amuled", "kad", "connectable")),
    ]


@pytest.mark.asyncio
async def test_unreachable_alerts_at_two_minutes_and_recovers_when_it_answers() -> None:
    events = await _run(_DOWN, _DOWN, _DOWN, _status(ed2k=_UP))
    assert [(at, e) for at, e in events if isinstance(e, _ALERTS)] == [
        (120.0, ev.ClientUnreachableLasting("amuled", 120.0)),
        (180.0, ev.ClientReachableAgain("amuled")),
    ]
    assert [e for _, e in events].count(ev.InstanceUnreachable()) == 3


@pytest.mark.asyncio
async def test_unreachable_for_one_minute_never_alerts() -> None:
    assert await _alerts(_DOWN, _DOWN, _status(ed2k=_UP)) == []


@pytest.mark.asyncio
async def test_while_unreachable_only_the_api_alerts_and_channel_clocks_restart() -> None:
    low = _status(ed2k=_LOW_ID)
    alerts = await _alerts(*[low] * 4, *[_DOWN] * 5, *[low] * 6)
    assert alerts == [
        (360.0, ev.ClientUnreachableLasting("amuled", 120.0)),
        (540.0, ev.ClientReachableAgain("amuled")),
        (840.0, _degraded("ed2k", "connectable")),
    ]


@pytest.mark.asyncio
async def test_each_reading_samples_every_channel_and_unreachable_makes_them_unknown() -> None:
    events = await _run(_DOWN, _status(ed2k=_UP, kad=(False, None)), _DOWN)
    assert [(at, e) for at, e in events if isinstance(e, ev.ChannelStatusSampled)] == [
        (60.0, ev.ChannelStatusSampled("amuled", "ed2k", True, True)),
        (60.0, ev.ChannelStatusSampled("amuled", "kad", False, None)),
        (120.0, ev.ChannelStatusSampled("amuled", "ed2k", None, None)),
        (120.0, ev.ChannelStatusSampled("amuled", "kad", None, None)),
    ]
