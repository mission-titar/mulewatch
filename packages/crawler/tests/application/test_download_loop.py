import asyncio
from datetime import UTC, datetime

import pytest

from catalog_matching.engine import DownloadCandidate
from mulewatch.application.run_download_cycle import (
    DOWNLOAD_NUDGE_SUBJECT,
    DownloadLoopDeps,
    download_loop,
)

# Reuse the fakes from test_run_download_cycle (imported explicitly).
from tests.application.fakes import RecordingTelemetry
from tests.application.test_run_download_cycle import (
    _TARGETS,
    FakeCatalogReads,
    FakeClock,
    FakeDiskSpace,
    FakeDownloadClient,
    FakeDownloadRepo,
)


class RecordingSignal:
    """Nudge hub that records + wakes (the test IS the producer)."""

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}
        self.waited: list[str] = []

    def signal(self, subject: str) -> None:
        self._events.setdefault(subject, asyncio.Event()).set()

    async def wait(self, subject: str) -> None:
        self.waited.append(subject)
        event = self._events.setdefault(subject, asyncio.Event())
        await event.wait()
        event.clear()


def _loop_deps(
    *, signal: RecordingSignal, shutdown: asyncio.Event, poll_interval: float = 30.0
) -> DownloadLoopDeps:
    return DownloadLoopDeps(
        client=FakeDownloadClient(),
        downloads=FakeDownloadRepo(),
        catalog=FakeCatalogReads(),
        targets=_TARGETS,
        disk=FakeDiskSpace(1_000_000),
        min_free_bytes=0,
        lost_after_seconds=86400.0,
        clock=FakeClock(),
        telemetry=RecordingTelemetry(),
        signal=signal,
        poll_interval_seconds=poll_interval,
        shutdown=shutdown,
    )


@pytest.mark.asyncio
async def test_loop_stops_when_shutdown_is_set_before_start() -> None:
    shutdown = asyncio.Event()
    shutdown.set()
    deps = _loop_deps(signal=RecordingSignal(), shutdown=shutdown)
    await asyncio.wait_for(download_loop(deps), timeout=1.0)  # runs no cycle


@pytest.mark.asyncio
async def test_loop_runs_a_cycle_then_sleeps_then_stops() -> None:
    shutdown = asyncio.Event()
    signal = RecordingSignal()
    deps = _loop_deps(signal=signal, shutdown=shutdown)

    async def stop_after_first_sleep() -> None:
        # let one cycle + the wait entry happen, then request shutdown and wake the loop.
        while not deps.clock.sleeps:  # type: ignore[attr-defined]
            await asyncio.sleep(0)
        shutdown.set()
        signal.signal(DOWNLOAD_NUDGE_SUBJECT)

    await asyncio.gather(
        asyncio.wait_for(download_loop(deps), timeout=2.0), stop_after_first_sleep()
    )
    assert deps.clock.sleeps  # type: ignore[attr-defined]  # at least one poll sleep


@pytest.mark.asyncio
async def test_nudge_wakes_the_loop_before_poll_expires() -> None:
    shutdown = asyncio.Event()
    signal = RecordingSignal()
    deps = _loop_deps(signal=signal, shutdown=shutdown, poll_interval=999.0)

    async def nudge_then_stop() -> None:
        while DOWNLOAD_NUDGE_SUBJECT not in signal.waited:
            await asyncio.sleep(0)
        shutdown.set()
        signal.signal(DOWNLOAD_NUDGE_SUBJECT)  # wakes the wait before the 999 s poll

    await asyncio.gather(asyncio.wait_for(download_loop(deps), timeout=2.0), nudge_then_stop())
    assert DOWNLOAD_NUDGE_SUBJECT in signal.waited


class _ShutdownDuringCycleCatalog(FakeCatalogReads):
    """CatalogReader that sets ``shutdown`` on the 1st ``download_decisions`` (DURING the cycle)."""

    def __init__(self, shutdown: asyncio.Event) -> None:
        super().__init__()
        self._shutdown = shutdown

    def download_decisions(self) -> tuple[DownloadCandidate, ...]:
        self._shutdown.set()
        return ()


@pytest.mark.asyncio
async def test_loop_breaks_when_shutdown_is_set_during_the_cycle() -> None:
    # the `if deps.shutdown.is_set(): break` AFTER the cycle: shutdown set DURING the cycle
    # (by the catalog) → break without calling _sleep_or_nudge (no sleep recorded).
    shutdown = asyncio.Event()
    clock = FakeClock()
    deps = _loop_deps(signal=RecordingSignal(), shutdown=shutdown)
    deps.clock = clock
    deps.catalog = _ShutdownDuringCycleCatalog(shutdown)
    await asyncio.wait_for(download_loop(deps), timeout=1.0)
    assert clock.sleeps == []  # break before any sleep/nudge


class _BlockingClock:
    """Clock whose ``sleep`` BLOCKS for good (the nudge must win and cancel the sleep).

    Structurally satisfies ``Clock``: ``now`` aware (unused by the loop) + ``sleep`` that
    never resolves → the nudge wins and the pending sleep_task is cancelled.
    """

    def now(self) -> datetime:
        return datetime(2026, 6, 13, tzinfo=UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.Event().wait()  # NEVER resolves


@pytest.mark.asyncio
async def test_nudge_wins_and_cancels_the_pending_sleep() -> None:
    # _sleep_or_nudge: nudge PRE-armed → the `if not task.done(): task.cancel()` branch is
    # exercised on the still-running sleep_task (the _BlockingClock never resolves it).
    shutdown = asyncio.Event()
    signal = RecordingSignal()
    deps = _loop_deps(signal=signal, shutdown=shutdown)
    deps.clock = _BlockingClock()
    signal.signal(DOWNLOAD_NUDGE_SUBJECT)  # nudge already armed → wait() returns at once

    async def stop_when_waited() -> None:
        while DOWNLOAD_NUDGE_SUBJECT not in signal.waited:
            await asyncio.sleep(0)
        shutdown.set()
        signal.signal(DOWNLOAD_NUDGE_SUBJECT)

    await asyncio.gather(asyncio.wait_for(download_loop(deps), timeout=2.0), stop_when_waited())
    assert DOWNLOAD_NUDGE_SUBJECT in signal.waited


@pytest.mark.asyncio
async def test_loop_cancelled_mid_wait_propagates_cleanly() -> None:
    # The real shutdown trigger in prod: CrawlerApp's TaskGroup cancels the loop task WHILE
    # it sleeps in _sleep_or_nudge. _BlockingClock + no nudge → the loop parks on
    # asyncio.wait; the cancellation lands there, the finally cancels the children, and the
    # CancelledError propagates cleanly (the loop stops, with no leaked task).
    shutdown = asyncio.Event()  # NEVER set: only cancellation stops the loop
    signal = RecordingSignal()
    deps = _loop_deps(signal=signal, shutdown=shutdown)
    deps.clock = _BlockingClock()
    task = asyncio.ensure_future(download_loop(deps))
    # wait until the loop is parked in _sleep_or_nudge (nudge_task has called wait())
    while DOWNLOAD_NUDGE_SUBJECT not in signal.waited:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
