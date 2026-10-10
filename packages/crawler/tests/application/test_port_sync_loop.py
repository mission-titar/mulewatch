"""Tests for ``port_sync_loop`` (design §4.5/§10.6) — shutdown (``verification_loop`` pattern)."""

import asyncio

import pytest

from p2pwatch.application.edge_state import EdgeState
from p2pwatch.application.port_sync_loop import PortSyncLoopDeps, port_sync_loop
from tests.application.fakes import RecordingTelemetry
from tests.application.test_run_port_sync_cycle import (
    FakeClock,
    FakeMuleRestarter,
    FakePortForwardingReader,
    FakePortPreferences,
)


def _loop_deps(
    *,
    reader: FakePortForwardingReader,
    shutdown: asyncio.Event,
    clock: FakeClock | None = None,
) -> PortSyncLoopDeps:
    return PortSyncLoopDeps(
        reader=reader,
        ports=FakePortPreferences(),
        restarter=FakeMuleRestarter(),
        clock=clock or FakeClock(),
        telemetry=RecordingTelemetry(),
        edge=EdgeState(),
        poll_interval_seconds=60.0,
        restart_min_interval_seconds=300.0,
        shutdown=shutdown,
    )


@pytest.mark.asyncio
async def test_loop_stops_when_shutdown_is_set_before_start() -> None:
    shutdown = asyncio.Event()
    shutdown.set()
    reader = FakePortForwardingReader(port=None)
    deps = _loop_deps(reader=reader, shutdown=shutdown)
    await asyncio.wait_for(port_sync_loop(deps), timeout=1.0)
    assert reader.calls == 0  # no cycle


@pytest.mark.asyncio
async def test_loop_runs_cycles_then_stops() -> None:
    shutdown = asyncio.Event()
    clock = FakeClock()
    reader = FakePortForwardingReader(port=None)  # not ready → sleeps each cycle
    deps = _loop_deps(reader=reader, shutdown=shutdown, clock=clock)

    async def stop_after_second_sleep() -> None:
        while len(clock.sleeps) < 2:
            await asyncio.sleep(0)
        shutdown.set()

    await asyncio.gather(
        asyncio.wait_for(port_sync_loop(deps), timeout=2.0), stop_after_second_sleep()
    )
    assert reader.calls >= 2
    assert len(clock.sleeps) >= 2  # two full cycles → while-continue branch covered


class _ShutdownDuringCycleReader(FakePortForwardingReader):
    """Set ``shutdown`` on the 1st ``forwarded_port`` (DURING the cycle) → break, no 2nd cycle."""

    def __init__(self, shutdown: asyncio.Event) -> None:
        super().__init__(port=None)
        self._shutdown = shutdown

    async def forwarded_port(self) -> int | None:
        self._shutdown.set()
        return await super().forwarded_port()


@pytest.mark.asyncio
async def test_loop_breaks_when_shutdown_is_set_during_the_cycle() -> None:
    shutdown = asyncio.Event()
    reader = _ShutdownDuringCycleReader(shutdown)
    deps = _loop_deps(reader=reader, shutdown=shutdown)
    await asyncio.wait_for(port_sync_loop(deps), timeout=1.0)
    assert reader.calls == 1  # a single cycle, then break (shutdown set during)
