"""One task per (client, channel, keyword), searching again as soon as it may (spec stage 2, D4).

The tasks share one simulated time, so these tests run on the virtual-time loop.
"""

import asyncio
import logging
import sqlite3
from collections.abc import Coroutine, Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

from catalog_matching.engine import MatchingEngine
from mulewatch.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from mulewatch.adapters.persistence_sqlite.connection import open_local
from mulewatch.adapters.persistence_sqlite.scheduler_state_repository import (
    SqliteSchedulerStateRepository,
)
from mulewatch.application.search_tasks import run_search_tasks
from mulewatch.application.search_worker import (
    BackoffRegistry,
    SearchWorker,
    WorkerDeps,
    WorkerPolicy,
)
from mulewatch.domain.observation import FileObservation
from mulewatch.ports.client_errors import SearchFailedError
from mulewatch.ports.repository_errors import RepositoryError
from mulewatch.ports.scheduler_state_repository import ChannelBackoff
from tests.application.fakes import FakeRng, RecordingSignal, RecordingTelemetry, make_unreachable
from tests.virtual_time import LoopClock, run_virtual

_HOUR = 3600.0
_POLICY = WorkerPolicy(
    backoff_base_seconds=60.0,
    backoff_cap_seconds=600.0,
    backoff_factor=2.0,
    backoff_jitter_ratio=0.0,
    keyword_pause_min_seconds=1.0,
    keyword_pause_max_seconds=1.0,
)


class _TimedClient:
    """A search takes its channel's duration in loop time; scripted failures come first."""

    def __init__(
        self,
        durations: dict[str, float],
        *,
        connect_failures: list[Exception] | None = None,
        search_failures: list[Exception] | None = None,
    ) -> None:
        self.channels = tuple(durations)
        self._durations = durations
        self._connect_failures = connect_failures or []
        self._search_failures = search_failures or []
        self.searches: list[tuple[float, str, str]] = []

    async def connect(self) -> None:
        if self._connect_failures:
            raise self._connect_failures.pop(0)

    async def close(self) -> None: ...

    async def search(
        self, keyword: str, channel: str, budget_seconds: float
    ) -> tuple[FileObservation, ...]:
        self.searches.append((asyncio.get_running_loop().time(), keyword, channel))
        if self._search_failures:
            raise self._search_failures.pop(0)
        await asyncio.sleep(self._durations[channel])
        return ()


class _SavesRecorded(SqliteSchedulerStateRepository):
    """Records each saved backoff map; the first ``failures`` saves raise."""

    failures = 0

    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(connection)
        self.saved: list[dict[str, ChannelBackoff]] = []

    def save_channel_backoff(self, backoff: dict[str, ChannelBackoff]) -> None:
        if self.failures:
            self.failures -= 1
            raise RepositoryError("database is locked")
        super().save_channel_backoff(backoff)
        self.saved.append(backoff)


class _Tasks:
    """The tasks' dependencies, with one client named ``amuled``."""

    def __init__(self, deps: WorkerDeps, state: _SavesRecorded) -> None:
        self.deps = deps
        self.state = state
        self.resumed = asyncio.Event()
        self.resumed.set()

    def searching(self, client: Any, keywords: list[str]) -> Coroutine[Any, Any, None]:
        return run_search_tasks(
            workers=[SearchWorker("amuled", client, self.deps)],
            keywords=keywords,
            resumed=self.resumed,
            backoff=self.deps.backoff,
            scheduler_state=self.state,
            clock=self.deps.clock,
        )

    def run_for(
        self, seconds: float, client: Any, keywords: list[str], *beside: Coroutine[Any, Any, None]
    ) -> None:
        async def bounded() -> None:
            with suppress(TimeoutError):
                async with asyncio.timeout(seconds):
                    await asyncio.gather(self.searching(client, keywords), *beside)

        run_virtual(bounded())


@pytest.fixture
def tasks(
    catalog: SqliteCatalogRepository, engine: MatchingEngine, tmp_path: Path
) -> Iterator[_Tasks]:
    connection = open_local(tmp_path / "local.db")
    clock = LoopClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    signal, telemetry = RecordingSignal(), RecordingTelemetry()
    deps = WorkerDeps(catalog, engine, signal, clock, FakeRng(), _POLICY, backoff, telemetry)
    yield _Tasks(deps, _SavesRecorded(connection))
    connection.close()


def test_each_channel_and_keyword_searches_in_its_own_task(tasks: _Tasks) -> None:
    # A channel named `x` needs no code: the tasks never test a channel's name.
    client = _TimedClient({"x": 30.0, "y": 30.0})
    tasks.run_for(100.0, client, ["keroro", "titar", "keroro", ""])
    assert sorted(client.searches) == sorted(
        (start, keyword, channel)
        for start in (0.0, 30.0, 60.0, 90.0)
        for keyword in ("keroro", "titar")
        for channel in ("x", "y")
    )


def test_channels_do_not_wait_for_each_other(tasks: _Tasks) -> None:
    client = _TimedClient({"ed2k": 6.0, "kad": 60.0})
    tasks.run_for(_HOUR, client, ["keroro"])
    counts = {name: sum(1 for *_, c in client.searches if c == name) for name in client.channels}
    assert counts["ed2k"] >= 5 * counts["kad"]


def test_a_paused_task_waits_before_its_next_search_and_finishes_the_one_in_flight(
    tasks: _Tasks,
) -> None:
    client = _TimedClient({"kad": 30.0})

    async def pause_then_resume() -> None:
        await asyncio.sleep(10.0)
        tasks.resumed.clear()
        await asyncio.sleep(990.0)
        tasks.resumed.set()

    tasks.run_for(1040.0, client, ["keroro"], pause_then_resume())
    assert [start for start, *_ in client.searches] == [0.0, 1000.0, 1030.0]


def test_a_failed_channel_sleeps_until_its_backoff_ends(tasks: _Tasks) -> None:
    client = _TimedClient({"kad": 30.0}, search_failures=[SearchFailedError("refused")])
    tasks.run_for(100.0, client, ["keroro"])
    assert [start for start, *_ in client.searches] == [0.0, 60.0, 90.0]


def test_an_unreachable_client_sleeps_until_its_backoff_ends(tasks: _Tasks) -> None:
    client = _TimedClient({"ed2k": 30.0, "kad": 30.0}, connect_failures=[make_unreachable()])
    tasks.run_for(100.0, client, ["keroro"])
    # The first connect fails; the other channel's task meets the backoff it set.
    assert [start for start, *_ in client.searches] == [60.0, 60.0, 90.0, 90.0]


def test_the_backoff_is_saved_at_each_change(tasks: _Tasks) -> None:
    client = _TimedClient({"kad": 30.0}, search_failures=[SearchFailedError("refused")])
    tasks.run_for(_HOUR, client, ["keroro"])
    assert [set(saved) for saved in tasks.state.saved] == [{"amuled:kad"}, set()]
    assert tasks.state.load_channel_backoff() == {}


def test_a_failed_save_is_logged_and_retried_after_the_next_search(
    tasks: _Tasks, caplog: pytest.LogCaptureFixture
) -> None:
    tasks.state.failures = 1
    refused: list[Exception] = [SearchFailedError("refused"), SearchFailedError("refused")]
    client = _TimedClient({"kad": 30.0}, search_failures=refused)
    with caplog.at_level(logging.ERROR, logger="mulewatch.application.search_tasks"):
        tasks.run_for(100.0, client, ["keroro"])
    assert "database is locked" in caplog.text
    assert tasks.state.saved == [
        {"amuled:kad": ChannelBackoff(2, "2026-10-09T00:03:00.000000+00:00")}
    ]


class _NeverSuspends:
    """A search that returns without suspending, and fails if the loop ran nothing else since
    the previous one."""

    channels = ("x",)

    def __init__(self) -> None:
        self.calls = 0
        self.ticks = 0  # advanced by a concurrent coroutine
        self._ticks_seen = -1

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def search(
        self, keyword: str, channel: str, budget_seconds: float
    ) -> tuple[FileObservation, ...]:
        self.calls += 1
        assert self.ticks > self._ticks_seen, "the loop is starved"
        self._ticks_seen = self.ticks
        return ()


def test_a_search_that_never_suspends_does_not_starve_the_loop(tasks: _Tasks) -> None:
    client = _NeverSuspends()

    async def main() -> None:
        searching = asyncio.ensure_future(tasks.searching(client, ["keroro"]))
        while not searching.done():
            client.ticks += 1
            if client.calls >= 100:
                searching.cancel()
            await asyncio.sleep(0)
        with suppress(asyncio.CancelledError):
            await searching

    run_virtual(main())
    assert client.calls >= 100
