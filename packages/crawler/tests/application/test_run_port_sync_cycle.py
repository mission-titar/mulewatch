"""Tests for ``run_port_sync_cycle`` (High-ID port-sync, design §4.4/§10.5) — the branch core.

Injected fakes: ``FakePortForwardingReader`` (scripted live port), ``FakePortPreferences``
(programmable get/set/status + injectable EC failures), ``FakeMuleRestarter`` (success /
``RestarterError``), ``FakeClock`` (recorded now/sleep), ``RecordingTelemetry``, real
``EdgeState``. We cover BOTH sides of each conditional (table §10.5).
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from p2pwatch.adapters.mule_api.errors import ApiRejectedError, ApiUnreachableError
from p2pwatch.application.edge_state import EdgeState
from p2pwatch.application.port_sync_loop import (
    _MISMATCH,
    PortSyncDeps,
    _PortSyncState,
    run_port_sync_cycle,
)
from p2pwatch.domain.observability.events import (
    HighIdRecovered,
    PortMismatchUnresolved,
    PortSyncTriggered,
)
from p2pwatch.ports.client_errors import ClientUnreachableError
from p2pwatch.ports.client_status import ChannelStatus, ClientStatus
from p2pwatch.ports.mule_restarter import RestarterError
from tests.application.fakes import RecordingTelemetry


class FakePortForwardingReader:
    """Forwarded-port reader: returns a scripted port (or ``None`` = not ready)."""

    def __init__(self, *, port: int | None) -> None:
        self._port = port
        self.calls = 0

    async def forwarded_port(self) -> int | None:
        self.calls += 1
        return self._port


class FakePortPreferences:
    """Programmable get/set port + status (subset of AmuleApiClient).

    ``get_error``/``set_error``/``status_error`` inject a failure on the matching method.
    ``ed2k_high`` drives the post-restart re-check; Kad always reads connectable.
    ``set_ports``/``status_calls`` trace.
    """

    def __init__(
        self,
        *,
        current_port: int = 4662,
        ed2k_high: bool = True,
        get_error: Exception | None = None,
        set_error: Exception | None = None,
        status_error: Exception | None = None,
        connected: bool = True,
    ) -> None:
        self._current_port = current_port
        self._ed2k_high = ed2k_high
        self._get_error = get_error
        self._set_error = set_error
        self._status_error = status_error
        self._connected = connected
        self.set_ports: list[int] = []
        self.status_calls = 0
        self.connect_calls = 0

    def _require_connected(self) -> None:
        # Mirrors AmuleApiClient._call: every op fails "not connected" until a
        # successful connect(). Models the field deadlock — a restart nulls the transport, and
        # without a reconnect every subsequent op raises this forever.
        if not self._connected:
            raise ApiUnreachableError("client not connected (call connect() first)")

    async def connect(self) -> None:
        # Idempotent, like AmuleApiClient.connect: a no-op when already connected, a real revival
        # after a prior EC failure dropped the transport (self._connected False).
        self.connect_calls += 1
        self._connected = True

    async def get_listen_port(self) -> int:
        self._require_connected()
        if self._get_error is not None:
            raise self._get_error
        return self._current_port

    async def set_listen_port(self, port: int) -> None:
        self._require_connected()
        if self._set_error is not None:
            raise self._set_error
        self.set_ports.append(port)
        # faithful set→get: writing the preference changes what get_listen_port will return
        # (like real amuled), EVEN without a rebind. Without this, the fake would hide test-gaps#0.
        self._current_port = port

    async def status(self) -> ClientStatus:
        self._require_connected()
        self.status_calls += 1
        if self._status_error is not None:
            raise self._status_error
        channels = (ChannelStatus("ed2k", True, self._ed2k_high), ChannelStatus("kad", True, True))
        return ClientStatus(version=None, channels=channels)


class FakeMuleRestarter:
    """Restarter: success, or raises ``RestarterError`` if ``fails=True``."""

    def __init__(self, *, fails: bool = False) -> None:
        self._fails = fails
        self.calls = 0

    async def restart(self) -> None:
        self.calls += 1
        if self._fails:
            raise RestarterError("proxy KO")


class FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 6, 15, tzinfo=UTC)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += timedelta(seconds=seconds)
        await asyncio.sleep(0)


_POLL = 60.0
_WINDOW = 300.0


def _deps(
    *,
    reader: FakePortForwardingReader,
    ports: FakePortPreferences,
    restarter: FakeMuleRestarter | None = None,
    clock: FakeClock | None = None,
    telemetry: RecordingTelemetry | None = None,
    edge: EdgeState | None = None,
) -> PortSyncDeps:
    return PortSyncDeps(
        reader=reader,
        ports=ports,
        restarter=restarter or FakeMuleRestarter(),
        clock=clock or FakeClock(),
        telemetry=telemetry or RecordingTelemetry(),
        edge=edge or EdgeState(),
        poll_interval_seconds=_POLL,
        restart_min_interval_seconds=_WINDOW,
    )


# ---------------------------------------------------------------- port not ready


@pytest.mark.asyncio
async def test_port_not_ready_sleeps_without_set_or_restart() -> None:
    reader = FakePortForwardingReader(port=None)
    ports = FakePortPreferences()
    restarter = FakeMuleRestarter()
    clock = FakeClock()
    telemetry = RecordingTelemetry()
    deps = _deps(reader=reader, ports=ports, restarter=restarter, clock=clock, telemetry=telemetry)
    await run_port_sync_cycle(deps, _PortSyncState())
    assert clock.sleeps == [_POLL]
    assert ports.set_ports == []
    assert restarter.calls == 0
    assert telemetry.events == []  # no event, no alert (Low-ID tolerated)


# ---------------------------------------------------------------- port unchanged


@pytest.mark.asyncio
async def test_port_unchanged_sleeps_and_leaves_mismatch() -> None:
    reader = FakePortForwardingReader(port=4662)
    ports = FakePortPreferences(current_port=4662)
    restarter = FakeMuleRestarter()
    clock = FakeClock()
    edge = EdgeState()
    edge.enter("port_mismatch")  # we were alerting → the rearm must clear it
    deps = _deps(reader=reader, ports=ports, restarter=restarter, clock=clock, edge=edge)
    await run_port_sync_cycle(deps, _PortSyncState())
    assert clock.sleeps == [_POLL]
    assert ports.set_ports == []
    assert restarter.calls == 0
    # rearmed: the condition is no longer active (leave was called).
    assert edge.enter("port_mismatch") is True  # re-enter yields True → it had indeed left


# ---------------------------------------------------------------- port changed, restart OK


@pytest.mark.asyncio
async def test_port_changed_restart_ok_high_id() -> None:
    reader = FakePortForwardingReader(port=51820)
    ports = FakePortPreferences(current_port=4662, ed2k_high=True)
    restarter = FakeMuleRestarter()
    clock = FakeClock()
    telemetry = RecordingTelemetry()
    edge = EdgeState()
    deps = _deps(
        reader=reader,
        ports=ports,
        restarter=restarter,
        clock=clock,
        telemetry=telemetry,
        edge=edge,
    )
    await run_port_sync_cycle(deps, _PortSyncState())
    assert ports.set_ports == [51820]  # SetPort(N)
    assert restarter.calls == 1
    assert ports.status_calls == 1  # re-check High-ID after restart
    triggered = [e for e in telemetry.events if isinstance(e, PortSyncTriggered)]
    assert triggered == [PortSyncTriggered(old=4662, new=51820)]
    recovered = [e for e in telemetry.events if isinstance(e, HighIdRecovered)]
    assert recovered == [HighIdRecovered(port=51820)]
    # alert rearmed (High-ID recovered) → re-enter yields True.
    assert edge.enter("port_mismatch") is True


@pytest.mark.asyncio
async def test_port_changed_restart_ok_but_not_high_id_alerts_without_second_restart() -> None:
    reader = FakePortForwardingReader(port=51820)
    ports = FakePortPreferences(current_port=4662, ed2k_high=False)
    restarter = FakeMuleRestarter()
    telemetry = RecordingTelemetry()
    edge = EdgeState()
    deps = _deps(reader=reader, ports=ports, restarter=restarter, telemetry=telemetry, edge=edge)
    await run_port_sync_cycle(deps, _PortSyncState())
    assert ports.set_ports == [51820]
    assert restarter.calls == 1  # NO immediate 2nd restart (DECISION 4)
    unresolved = [e for e in telemetry.events if isinstance(e, PortMismatchUnresolved)]
    assert len(unresolved) == 1
    assert unresolved[0].first_occurrence is True
    assert unresolved[0].live == 51820


# ---------------------------------------------------------------- restart FAILS


@pytest.mark.asyncio
async def test_port_changed_restart_fails_alerts_and_backs_off() -> None:
    reader = FakePortForwardingReader(port=51820)
    ports = FakePortPreferences(current_port=4662)
    restarter = FakeMuleRestarter(fails=True)
    clock = FakeClock()
    telemetry = RecordingTelemetry()
    edge = EdgeState()
    deps = _deps(
        reader=reader,
        ports=ports,
        restarter=restarter,
        clock=clock,
        telemetry=telemetry,
        edge=edge,
    )
    await run_port_sync_cycle(deps, _PortSyncState())  # does not raise
    assert ports.set_ports == [51820]  # set called BEFORE the restart
    assert restarter.calls == 1
    assert ports.status_calls == 0  # no re-check (the restart failed)
    unresolved = [e for e in telemetry.events if isinstance(e, PortMismatchUnresolved)]
    assert len(unresolved) == 1
    assert unresolved[0].first_occurrence is True
    assert unresolved[0].configured == 4662  # configured = current (the restart didn't take)
    assert clock.sleeps == [_POLL]  # backoff


# ---------------------------------------------------------------- rate-limit


@pytest.mark.asyncio
async def test_rate_limit_active_skips_set_and_restart() -> None:
    reader = FakePortForwardingReader(port=51820)
    ports = FakePortPreferences(current_port=4662)
    restarter = FakeMuleRestarter()
    clock = FakeClock()
    state = _PortSyncState()
    state.record_restart(clock.now(), 51820)  # restart "just now" → too_soon True
    deps = _deps(reader=reader, ports=ports, restarter=restarter, clock=clock)
    await run_port_sync_cycle(deps, state)
    assert ports.set_ports == []  # NEITHER set NOR restart (too_soon)
    assert restarter.calls == 0
    assert clock.sleeps == [_POLL]


@pytest.mark.asyncio
async def test_rate_limit_expired_runs_set_and_restart() -> None:
    reader = FakePortForwardingReader(port=51820)
    ports = FakePortPreferences(current_port=4662)
    restarter = FakeMuleRestarter()
    clock = FakeClock()
    state = _PortSyncState()
    state.record_restart(clock.now(), 51820)
    clock.advance(_WINDOW + 1)  # last restart > window → too_soon False
    deps = _deps(reader=reader, ports=ports, restarter=restarter, clock=clock)
    await run_port_sync_cycle(deps, state)
    assert ports.set_ports == [51820]  # set+restart executed
    assert restarter.calls == 1


# ---------------------------------------------------------------- EC unreachable


@pytest.mark.asyncio
async def test_ec_error_on_get_is_absorbed_and_sleeps() -> None:
    reader = FakePortForwardingReader(port=51820)
    ports = FakePortPreferences(get_error=ApiUnreachableError("amuled down"))
    restarter = FakeMuleRestarter()
    clock = FakeClock()
    deps = _deps(reader=reader, ports=ports, restarter=restarter, clock=clock)
    await run_port_sync_cycle(deps, _PortSyncState())  # does not raise
    assert ports.set_ports == []
    assert restarter.calls == 0
    assert clock.sleeps == [_POLL]


@pytest.mark.asyncio
async def test_ec_error_on_set_is_absorbed_and_sleeps() -> None:
    reader = FakePortForwardingReader(port=51820)
    ports = FakePortPreferences(current_port=4662, set_error=ApiUnreachableError("amuled down"))
    restarter = FakeMuleRestarter()
    clock = FakeClock()
    deps = _deps(reader=reader, ports=ports, restarter=restarter, clock=clock)
    await run_port_sync_cycle(deps, _PortSyncState())  # does not raise
    assert ports.set_ports == []  # set raised before recording
    assert restarter.calls == 0  # restart never attempted (set failed)
    assert clock.sleeps == [_POLL]


@pytest.mark.asyncio
async def test_application_level_ec_failure_is_also_absorbed() -> None:
    # ApiRejectedError (a SearchFailedError — NOT a ClientUnreachableError) must
    # ALSO be absorbed: the net catches ClientError, the ancestor of the port errors. Without
    # this wide net, a set_listen_port replying EC_OP_FAILED would crash the loop.
    reader = FakePortForwardingReader(port=51820)
    ports = FakePortPreferences(current_port=4662, set_error=ApiRejectedError("pref refused"))
    clock = FakeClock()
    deps = _deps(reader=reader, ports=ports, clock=clock)
    await run_port_sync_cycle(deps, _PortSyncState())  # does not raise
    assert ports.set_ports == []
    assert clock.sleeps == [_POLL]


@pytest.mark.asyncio
async def test_cycle_reconnects_ec_when_disconnected() -> None:
    # Field deadlock (the port-sync we debugged): a prior restart nulled the dedicated EC
    # transport, so the client is DISCONNECTED at the start of the cycle. Every EC op then raises
    # "not connected" until connect() is re-issued. A correct cycle must (re)connect its client
    # before using EC — otherwise it stays stuck forever, never re-aligning amuled's port. Here the
    # forwarded port (51820) diverges from amuled's (4662): a healthy cycle reconnects, reads the
    # port, and resumes the sync (SetPort + restart). A cycle that never reconnects raises on
    # get_listen_port, is absorbed, and does NOTHING (the bug).
    reader = FakePortForwardingReader(port=51820)
    ports = FakePortPreferences(current_port=4662, connected=False)
    restarter = FakeMuleRestarter()
    deps = _deps(reader=reader, ports=ports, restarter=restarter)
    await run_port_sync_cycle(deps, _PortSyncState())
    assert ports.connect_calls == 1  # it reconnected before touching EC
    assert ports.set_ports == [51820]  # and resumed the sync (proof it got past get_listen_port)
    assert restarter.calls == 1


@pytest.mark.asyncio
async def test_a_status_read_failing_after_the_restart_is_absorbed_without_an_alert() -> None:
    # status() raises when amuleapi answers but does not reach amuled: the same backoff as an
    # unreachable amuleapi, with neither a recovery nor an alert.
    ports = FakePortPreferences(
        current_port=4662, status_error=ClientUnreachableError("amuleapi does not reach amuled")
    )
    clock = FakeClock()
    telemetry = RecordingTelemetry()
    edge = EdgeState()
    deps = _deps(
        reader=FakePortForwardingReader(port=51820),
        ports=ports,
        clock=clock,
        telemetry=telemetry,
        edge=edge,
    )
    await run_port_sync_cycle(deps, _PortSyncState())  # does not raise
    assert ports.status_calls == 1
    assert telemetry.events == [PortSyncTriggered(old=4662, new=51820)]
    assert clock.sleeps == [_POLL, _POLL]  # the rebind delay, then the backoff
    assert edge.enter(_MISMATCH) is True  # no alert was raised


# ---------------------------------------------------------------- edge-trigger


@pytest.mark.asyncio
async def test_mismatch_then_recovered_rearms_the_alert() -> None:
    edge = EdgeState()
    telemetry = RecordingTelemetry()
    # Cycle 1: restart OK but not High-ID → unresolved (enter → True).
    deps1 = _deps(
        reader=FakePortForwardingReader(port=51820),
        ports=FakePortPreferences(current_port=4662, ed2k_high=False),
        telemetry=telemetry,
        edge=edge,
    )
    await run_port_sync_cycle(deps1, _PortSyncState())
    first = [e for e in telemetry.events if isinstance(e, PortMismatchUnresolved)]
    assert first and first[0].first_occurrence is True

    telemetry.events.clear()
    # Cycle 2: port aligned → leave rearms (the next occurrence will re-notify).
    deps2 = _deps(
        reader=FakePortForwardingReader(port=4662),
        ports=FakePortPreferences(current_port=4662),
        telemetry=telemetry,
        edge=edge,
    )
    await run_port_sync_cycle(deps2, _PortSyncState())
    assert edge.enter("port_mismatch") is True  # rearmed: the condition had indeed left


@pytest.mark.asyncio
async def test_second_unresolved_in_a_row_is_not_first_occurrence() -> None:
    edge = EdgeState()
    telemetry = RecordingTelemetry()
    state = _PortSyncState()  # shared state: rate-limit on the 2nd+ cycles
    # Cycle 1: restart fail → unresolved (first_occurrence True).
    deps1 = _deps(
        reader=FakePortForwardingReader(port=51820),
        ports=FakePortPreferences(current_port=4662),
        restarter=FakeMuleRestarter(fails=True),
        telemetry=telemetry,
        edge=edge,
    )
    await run_port_sync_cycle(deps1, state)
    first = [e for e in telemetry.events if isinstance(e, PortMismatchUnresolved)]
    assert first and first[0].first_occurrence is True

    telemetry.events.clear()
    # Cycle 2: restart fails again → unresolved, but first_occurrence False (already active).
    deps2 = _deps(
        reader=FakePortForwardingReader(port=51820),
        ports=FakePortPreferences(current_port=4662),
        restarter=FakeMuleRestarter(fails=True),
        telemetry=telemetry,
        edge=edge,
    )
    await run_port_sync_cycle(deps2, state)
    second = [e for e in telemetry.events if isinstance(e, PortMismatchUnresolved)]
    assert second and second[0].first_occurrence is False


# ---------------------------------------------------------------- test-gaps#0: preference ≠ bound


@pytest.mark.asyncio
async def test_failed_restart_is_not_masked_by_written_preference_next_cycle() -> None:
    # test-gaps#0: cycle 1, the restart FAILS AFTER set_listen_port (preference written = live);
    # cycle 2, get_listen_port returns that preference (== live) BUT amuled never rebound
    # (still Low-ID). The alert raised in cycle 1 must NOT be cleared by preference equality —
    # only High-ID clears it. Otherwise a transient failure hides the mismatch FOREVER and
    # silences the OPERATIONS signal.
    edge = EdgeState()
    state = _PortSyncState()
    ports = FakePortPreferences(current_port=4662, ed2k_high=False)  # set→get modeled, Low-ID
    # Cycle 1: 51820 != 4662 → set(51820) writes the preference, restart() FAILS → alert raised.
    await run_port_sync_cycle(
        _deps(
            reader=FakePortForwardingReader(port=51820),
            ports=ports,
            restarter=FakeMuleRestarter(fails=True),
            edge=edge,
        ),
        state,
    )
    assert ports.set_ports == [51820]
    assert edge.enter(_MISMATCH) is False  # alert active after the failed restart (stays active)

    # Cycle 2: live=51820, get_listen_port()==51820 (cycle 1's preference), but Low-ID.
    await run_port_sync_cycle(
        _deps(
            reader=FakePortForwardingReader(port=51820),
            ports=ports,  # SAME fake → current_port == 51820 (set→get)
            restarter=FakeMuleRestarter(),
            edge=edge,
        ),
        state,
    )
    # the alert is KEPT (port still wrong, no High-ID): re-enter still yields False.
    assert edge.enter(_MISMATCH) is False
