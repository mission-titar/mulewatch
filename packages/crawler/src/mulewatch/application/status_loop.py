"""One client's status loop: a reading every minute feeds the channel gauges, and a degraded state
alerts once it has lasted, whatever came before it, boot included (spec stage 2, D15).

The clocks live in memory: a restart restarts them, so an ongoing degradation alerts at most one
delay later.
"""

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from mulewatch.domain.observability.events import (
    ChannelDegraded,
    ChannelField,
    ChannelRecovered,
    ChannelStatusSampled,
    ClientReachableAgain,
    ClientUnreachableLasting,
    Event,
    InstanceUnreachable,
)
from mulewatch.ports.client_errors import ClientAuthError, ClientError
from mulewatch.ports.client_status import ClientStatus, StatusClient
from mulewatch.ports.clock import Clock
from mulewatch.ports.telemetry import Telemetry

STATUS_PERIOD_SECONDS = 60.0
CHANNEL_ALERT_SECONDS = 300.0
UNREACHABLE_ALERT_SECONDS = 120.0
CURRENT_FOR_SECONDS = 2 * STATUS_PERIOD_SECONDS


@dataclass(frozen=True)
class ClientReading:
    read_at: datetime
    status: ClientStatus | None  # None: the client's API did not answer


class StatusBoard:
    """Each client's last reading, published by its loop and read on the webui's thread (D21)."""

    def __init__(self, clients: Iterable[str], clock: Clock) -> None:
        self._clock = clock
        self._readings: Mapping[str, ClientReading | None] = dict.fromkeys(clients)

    def publish(self, client: str, reading: ClientReading) -> None:
        # A new map, never a mutation: the other thread reads one whole snapshot, no lock needed.
        self._readings = {**self._readings, client: reading}

    def readings(self) -> Mapping[str, ClientReading | None]:
        return self._readings

    def is_current(self, reading: ClientReading) -> bool:
        return (self._clock.now() - reading.read_at).total_seconds() <= CURRENT_FOR_SECONDS


@dataclass
class StatusLoopDeps:
    name: str  # the client's name, as alerts and metrics label it
    client: StatusClient
    clock: Clock
    telemetry: Telemetry
    shutdown: asyncio.Event
    board: StatusBoard


class _Watch:
    """Since when one state reads degraded, and whether its alert has fired."""

    def __init__(self, delay_seconds: float) -> None:
        self._delay = delay_seconds
        self._since: datetime | None = None
        self._alerted = False

    def read(self, good: bool | None, now: datetime) -> Literal["alert", "recovery"] | None:
        if good is None:  # unknown stops the clock; a recovery stays due until a good reading
            self._since = None
            return None
        if good:
            self._since = None
            recovered, self._alerted = self._alerted, False
            return "recovery" if recovered else None
        self._since = self._since or now
        if self._alerted or (now - self._since).total_seconds() < self._delay:
            return None
        self._alerted = True
        return "alert"


async def status_loop(deps: StatusLoopDeps) -> None:
    """Reads the status every period until shutdown."""
    api = _Watch(UNREACHABLE_ALERT_SECONDS)
    channels: dict[tuple[str, ChannelField], _Watch] = {}

    async def watch(watch: _Watch, good: bool | None, alert: Event, recovery: Event) -> None:
        change = watch.read(good, deps.clock.now())
        if change is not None:
            await deps.telemetry.emit(alert if change == "alert" else recovery)

    while not deps.shutdown.is_set():
        try:
            await deps.client.connect()  # nothing else may connect a shared session (a pause)
            status = await deps.client.status()
        except ClientAuthError:
            raise  # a wrong password is config, not an outage
        except ClientError:  # a refused route too: a renamed /version must not end the crawler
            deps.board.publish(deps.name, ClientReading(deps.clock.now(), None))
            await deps.telemetry.emit(InstanceUnreachable(deps.name))
            # Every channel is unknown while the API is: only the API's own alert can fire.
            readings: list[tuple[str, bool | None, bool | None]] = [
                (name, None, None) for name in dict.fromkeys(n for n, _ in channels)
            ]
            reachable = False
        else:
            deps.board.publish(deps.name, ClientReading(deps.clock.now(), status))
            readings = [(c.channel, c.on_network, c.connectable) for c in status.channels]
            reachable = True
        await watch(
            api,
            reachable,
            ClientUnreachableLasting(deps.name, UNREACHABLE_ALERT_SECONDS),
            ClientReachableAgain(deps.name),
        )
        for name, on_network, connectable in readings:
            await deps.telemetry.emit(
                ChannelStatusSampled(deps.name, name, on_network, connectable)
            )
            fields: tuple[tuple[ChannelField, bool | None], ...] = (
                ("on_network", on_network),
                ("connectable", connectable),
            )
            for field, good in fields:
                await watch(
                    channels.setdefault((name, field), _Watch(CHANNEL_ALERT_SECONDS)),
                    good,
                    ChannelDegraded(deps.name, name, field, CHANNEL_ALERT_SECONDS),
                    ChannelRecovered(deps.name, name, field),
                )
        await deps.clock.sleep(STATUS_PERIOD_SECONDS)
