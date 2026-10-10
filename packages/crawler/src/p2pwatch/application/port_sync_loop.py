"""The UNIFIED port-sync loop (boot + mid-life): read the forwarded port, align amuled, restart.

ONE algorithm covers both "the port is wrong at startup" and "the port became wrong along the
way" (VPN renegotiation): read gluetun's live forwarded port, compare it to amuled's listen
port, and on a difference write it and restart amuled, because the port is NOT re-bindable at
runtime. Guards: a restart rate-limit, a High-ID re-check that does not loop, and an
edge-triggered alert when the port stays wrong. Low-ID is a tolerated degraded mode.

``run_port_sync_cycle`` NEVER RAISES (top-level net like ``run_download_cycle``); every
re-looping path sleeps ``poll_interval_seconds`` (no busy-spin). ``port_sync_loop`` repeats
until shutdown. We declare local NARROW Protocols (the real ``AmuleApiClient`` AND a minimal
fake satisfy them) - we do NOT widen ``ports/mule_client.py``.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from p2pwatch.application.edge_state import EdgeState
from p2pwatch.domain.observability.events import (
    HighIdRecovered,
    PortMismatchUnresolved,
    PortSyncTriggered,
)
from p2pwatch.ports.client_errors import ClientError
from p2pwatch.ports.client_status import ClientStatus
from p2pwatch.ports.clock import Clock
from p2pwatch.ports.mule_restarter import MuleRestarter, RestarterError
from p2pwatch.ports.port_forwarding import PortForwardingReader
from p2pwatch.ports.telemetry import Telemetry

_logger = logging.getLogger("p2pwatch.application.port_sync_loop")

_MISMATCH = "port_mismatch"


class PortPreferences(Protocol):
    """The subset of the client this loop needs, declared here rather than widening the port."""

    async def connect(self) -> None: ...

    async def get_listen_port(self) -> int: ...

    async def set_listen_port(self, port: int) -> None: ...

    async def status(self) -> ClientStatus: ...


@dataclass
class PortSyncDeps:
    """Dependencies of a port-sync cycle (composition assembles them once, design §4.3)."""

    reader: PortForwardingReader  # reads the live forwarded port (gluetun)
    ports: PortPreferences  # get/set port + status (AmuleApiClient, dedicated session R6)
    restarter: MuleRestarter  # restart amuled via the proxy
    clock: Clock  # injected sleep/now (determinism)
    telemetry: Telemetry  # observability events
    edge: EdgeState  # edge-triggered alert (uncorrected port mismatch)
    poll_interval_seconds: float  # poll cadence
    restart_min_interval_seconds: float  # restart rate-limit (≤ 1 / window)


class _PortSyncState:
    """Inter-iteration state (mutable, single-threaded on the event loop, NOT persisted - like
    ``EdgeState``). Remembers the last restart's instant (rate-limit) and the target port."""

    def __init__(self) -> None:
        self._last_restart: datetime | None = None
        self._last_target: int | None = None

    def too_soon(self, now: datetime, window_seconds: float) -> bool:
        """``True`` if a restart happened less than ``window_seconds`` ago (rate-limit)."""
        if self._last_restart is None:
            return False
        return (now - self._last_restart).total_seconds() < window_seconds

    def record_restart(self, now: datetime, target: int) -> None:
        """Records the instant + target port of the restart (rate-limit + target)."""
        self._last_restart = now
        self._last_target = target


async def run_port_sync_cycle(deps: PortSyncDeps, state: _PortSyncState) -> None:
    """ONE cycle (design §4.4). NEVER RAISES; every re-looping path sleeps ``poll_interval``.

    Boot vs mid-life = SAME path: on the 1st cycle ``current`` is the image's hardcoded port;
    if ``live`` differs, we ``SetPort`` + restart once then re-check High-ID. On later cycles,
    same in case of VPN renegotiation. No "first time" branch.
    """
    try:
        live = await deps.reader.forwarded_port()
        if live is None:
            # control-server not ready / PF not negotiated → we stay Low-ID, NO alert.
            await deps.clock.sleep(deps.poll_interval_seconds)
            return
        # (Re)connect the dedicated client BEFORE any call. IDEMPOTENT (AmuleApiClient.connect
        # is a no-op when already connected), but ESSENTIAL after a restart: our own restart() - or
        # a VPN renegotiation - ends the session. Without this call the loop would stay stuck on
        # "client not connected" forever (the field deadlock). A failed reconnect (amuled still
        # down) raises under ``ClientError`` → absorbed + backoff below, like any other one.
        await deps.ports.connect()
        current = await deps.ports.get_listen_port()
        if live == current:
            # The preference is aligned with the forwarded port - but this is NOT proof that
            # amuled LISTENS on that port: ``set_listen_port`` writes the preference without
            # rebinding (the rebind requires a restart). EC does not expose the actually-bound
            # port; the only reliable signal that the right port is bound AND reachable is the
            # High-ID. So we clear the alert ONLY if High-ID; otherwise we backoff without
            # touching it - a failed restart keeps its alert lit instead of being masked by the
            # written preference (test-gaps#0). Low-ID tolerated: no re-restart (the
            # rate-limit/alert handle recovery).
            if _high_id(await deps.ports.status()):
                deps.edge.leave(_MISMATCH)
            await deps.clock.sleep(deps.poll_interval_seconds)
            return
        # --- divergence: live != current, and live > 0 guaranteed ---
        now = deps.clock.now()
        if state.too_soon(now, deps.restart_min_interval_seconds):
            # rate-limit: recent restart → we wait (don't loop restarts).
            await deps.clock.sleep(deps.poll_interval_seconds)
            return
        await deps.ports.set_listen_port(live)
        await deps.telemetry.emit(PortSyncTriggered(old=current, new=live))
        try:
            await deps.restarter.restart()
        except RestarterError as error:
            # restart impossible → edge-triggered alert + backoff.
            _logger.warning("amuled restart failed (%s): alert + backoff", error)
            await deps.telemetry.emit(
                PortMismatchUnresolved(
                    first_occurrence=deps.edge.enter(_MISMATCH), live=live, configured=current
                )
            )
            await deps.clock.sleep(deps.poll_interval_seconds)
            return
        state.record_restart(now, live)
        # --- re-check High-ID after restart (DECISION 4): DO NOT LOOP if not High-ID ---
        # we allow a bounded delay (amuled rebind) then read the status; if it is not High-ID,
        # we emit the alert and return - the rate-limit prevents an immediate re-restart.
        await deps.clock.sleep(deps.poll_interval_seconds)
        if _high_id(await deps.ports.status()):
            deps.edge.leave(_MISMATCH)
            await deps.telemetry.emit(HighIdRecovered(port=live))
        else:
            await deps.telemetry.emit(
                PortMismatchUnresolved(
                    first_occurrence=deps.edge.enter(_MISMATCH), live=live, configured=live
                )
            )
    except ClientError as error:
        # get/set_listen_port / status failed (amuled down, amuleapi down, or the
        # operation refused) → tolerated: we catch the port ANCESTOR ``ClientError``, which
        # covers unreachable AND application failure, without importing the adapter (dependency
        # rule §4). Backoff, no crash (top-level net §4.4).
        _logger.warning("amuleapi failed during port-sync (%s): tolerated, backoff", error)
        await deps.clock.sleep(deps.poll_interval_seconds)


def _high_id(status: ClientStatus) -> bool:
    """High-ID is the eD2k channel being connectable; Kad's reachability does not count."""
    return any(c.channel == "ed2k" and c.connectable is True for c in status.channels)


@dataclass
class PortSyncLoopDeps(PortSyncDeps):
    """``PortSyncDeps`` + shutdown (``verification_loop`` pattern). No nudge: the poll is enough."""

    shutdown: asyncio.Event


async def port_sync_loop(deps: PortSyncLoopDeps) -> None:
    """Repeats ``run_port_sync_cycle`` until shutdown (design §4.5, ``verification_loop`` pattern).

    Wired by ``CrawlerApp`` into the ``TaskGroup``; cancellation (shutdown) lands at the next
    ``await`` (poll/EC/sleep). ``run_port_sync_cycle`` NEVER RAISES → this loop cannot crash the
    ``TaskGroup``. The post-cycle ``if deps.shutdown.is_set(): break`` avoids one extra cycle when
    shutdown is requested DURING the cycle.
    """
    state = _PortSyncState()
    while not deps.shutdown.is_set():
        await run_port_sync_cycle(deps, state)
        if deps.shutdown.is_set():
            break
