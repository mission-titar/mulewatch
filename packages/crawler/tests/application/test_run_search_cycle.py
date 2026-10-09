import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from catalog_matching.engine import MatchingEngine
from mulewatch.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from mulewatch.adapters.persistence_sqlite.connection import open_local
from mulewatch.adapters.persistence_sqlite.scheduler_state_repository import (
    SqliteSchedulerStateRepository,
)
from mulewatch.application.edge_state import EdgeState
from mulewatch.application.run_search_cycle import run_search_cycle
from mulewatch.application.search_worker import (
    BackoffRegistry,
    SearchWorker,
    WorkerDeps,
    WorkerPolicy,
)
from mulewatch.domain.file_key import FileKey, Network
from mulewatch.domain.observation import FileObservation
from tests.application.fakes import (
    FakeClock,
    FakeMuleClient,
    FakeRng,
    RecordingSignal,
    RecordingTelemetry,
)

_HASH = "31d6cfe0d16ae931b73c59d7e0c089c0"
_DL_NAME = "Keroro N°062A Les demoiselles cambrioleuses.avi"
_KEYWORDS = ("keroro", "titar")

# keyword_pause 1.0..1.0 (min == max) → FIXED pause of 1.0s (jitter span 0): each
# inter-keyword pause adds EXACTLY 1.0s to clock.sleeps, which makes the pause OBSERVABLE
# and the "between not after" assertion exact.
_POLICY = WorkerPolicy(
    backoff_base_seconds=2.0,
    backoff_cap_seconds=60.0,
    backoff_factor=2.0,
    backoff_jitter_ratio=0.0,
    keyword_pause_min_seconds=1.0,
    keyword_pause_max_seconds=1.0,
)


class _NoopRng:
    """Identity Rng: preserves order + zero jitter (test determinism)."""

    def shuffled(self, items: tuple[str, ...], seed: str) -> tuple[str, ...]:
        return items

    def jitter(self, span: float) -> float:
        return 0.0


def _obs() -> FileObservation:
    return FileObservation(
        file=FileKey(Network.ED2K, _HASH),
        filename=_DL_NAME,
        size_bytes=234_000_000,
        source_count=3,
        keyword="keroro",
    )


@pytest.fixture
def local_connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_local(tmp_path / "local.db")
    yield connection
    connection.close()


def _worker(name: str, client: FakeMuleClient, deps: WorkerDeps) -> SearchWorker:
    return SearchWorker(name, client, deps)


def _deps(
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
    clock: FakeClock,
    backoff: BackoffRegistry,
    *,
    telemetry: RecordingTelemetry | None = None,
) -> WorkerDeps:
    return WorkerDeps(
        catalog=catalog,
        engine=engine,
        signal=RecordingSignal(),
        clock=clock,
        rng=_NoopRng(),
        policy=_POLICY,
        backoff=backoff,
        telemetry=telemetry or RecordingTelemetry(),
    )


@pytest.mark.asyncio
async def test_single_instance_cycle_records_and_advances(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    client = FakeMuleClient(results=[(_obs(),)])  # the download appears on the 1st search
    worker = _worker("amule-1", client, _deps(catalog, engine, clock, backoff))
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=[worker],
        clients=[client],
        keywords=_KEYWORDS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=0,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
        telemetry=RecordingTelemetry(),
        edge=EdgeState(),
    )
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 1
    assert scheduler_state.read_cycle_index() == 1  # index = N+1, persisted at cycle end


@pytest.mark.asyncio
async def test_the_cycle_searches_the_channels_its_clients_declare(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    class _OneChannel(FakeMuleClient):
        channels: tuple[str, ...] = ("x",)

    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    client = _OneChannel()
    worker = _worker("amule-1", client, _deps(catalog, engine, clock, backoff))
    await run_search_cycle(
        workers=[worker],
        clients=[client],
        keywords=_KEYWORDS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=0,
        scheduler_state=SqliteSchedulerStateRepository(local_connection),
        backoff=backoff,
        clock=clock,
        telemetry=RecordingTelemetry(),
        edge=EdgeState(),
    )
    assert sorted(client.searches) == [("keroro", "x"), ("titar", "x")]


@pytest.mark.asyncio
async def test_two_workers_drain_the_same_queue(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    client_a = FakeMuleClient()
    client_b = FakeMuleClient()
    deps = _deps(catalog, engine, clock, backoff)
    workers = [_worker("amule-1", client_a, deps), _worker("amule-2", client_b, deps)]
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=workers,
        clients=[client_a, client_b],
        keywords=_KEYWORDS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=3,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
        telemetry=RecordingTelemetry(),
        edge=EdgeState(),
    )
    total_searches = len(client_a.searches) + len(client_b.searches)
    assert total_searches >= 2  # all tasks distributed between the two workers
    assert scheduler_state.read_cycle_index() == 4


@pytest.mark.asyncio
async def test_channel_backoff_is_persisted_at_cycle_end(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    # A search fails (EC_OP_FAILED) → the channel enters backoff IN the shared registry;
    # the cycle PERSISTS the snapshot at cycle end (spec §3/§7). A fresh repo instance
    # (simulating a restart) re-reads this backoff.
    from mulewatch.ports.client_errors import SearchFailedError

    class _AlwaysFails(FakeMuleClient):
        """Fails on EVERY search → the channels STAY in backoff (never reset)."""

        async def search(
            self, keyword: str, channel: str, budget_seconds: float
        ) -> tuple[FileObservation, ...]:
            raise SearchFailedError("EC_OP_FAILED")

    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    client = _AlwaysFails()
    worker = _worker("amule-1", client, _deps(catalog, engine, clock, backoff))
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=[worker],
        clients=[client],
        keywords=_KEYWORDS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=0,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
        telemetry=RecordingTelemetry(),
        edge=EdgeState(),
    )
    persisted = SqliteSchedulerStateRepository(local_connection).load_channel_backoff()
    # Both amule-1 channels are in persisted backoff (all searches fail).
    assert set(persisted) == {"amule-1:ed2k", "amule-1:kad"}


@pytest.mark.asyncio
async def test_one_worker_pauses_between_items_not_after_the_last(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    # A SINGLE worker drains ALL items → the inter-keyword pause (fixed 1.0s, min==max;
    # the fake search never sleeps) falls BETWEEN two items and NEVER after the
    # last: exactly (N_items - 1) pauses of 1.0s.
    from mulewatch.domain.search.keywords import generate_keywords

    n_items = len(generate_keywords(_KEYWORDS)) * len(FakeMuleClient.channels)
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    client = FakeMuleClient()
    worker = _worker("amule-1", client, _deps(catalog, engine, clock, backoff))
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=[worker],
        clients=[client],
        keywords=_KEYWORDS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=0,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
        telemetry=RecordingTelemetry(),
        edge=EdgeState(),
    )
    assert clock.sleeps == [1.0] * (n_items - 1)  # between each item, not after the last


@pytest.mark.asyncio
async def test_drained_queue_skips_the_final_pause_with_two_workers(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    # Two workers SHARE the fake clock: the pause is only slept between two real items
    # (the "queue not empty" guard). The total of pauses is STRICTLY less than the number
    # of items (at least the last item of each drain does not trigger a pause).
    from mulewatch.domain.search.keywords import generate_keywords

    n_items = len(generate_keywords(_KEYWORDS)) * len(FakeMuleClient.channels)
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    client_a = FakeMuleClient()
    client_b = FakeMuleClient()
    deps = _deps(catalog, engine, clock, backoff)
    workers = [_worker("amule-1", client_a, deps), _worker("amule-2", client_b, deps)]
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=workers,
        clients=[client_a, client_b],
        keywords=_KEYWORDS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=0,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
        telemetry=RecordingTelemetry(),
        edge=EdgeState(),
    )
    # All pauses are 1.0s; there are STRICTLY fewer than items (the last one of each
    # worker is skipped because the queue is drained).
    assert all(s == 1.0 for s in clock.sleeps)
    assert 0 < len(clock.sleeps) < n_items


@pytest.mark.asyncio
async def test_worker_in_backoff_does_not_consume_peers_tasks(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    # Regression logic-search#0 (spec §14 "NO LOSS"): a worker whose instance is in backoff
    # must NOT drain/discard the remaining tasks. The queue is shared → if A is in backoff
    # and B healthy, B must process ALL tasks (not half).
    from mulewatch.domain.search.keywords import generate_keywords

    n_items = len(generate_keywords(_KEYWORDS)) * len(FakeMuleClient.channels)
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    # 4 accumulated failures → base × factor^3 = 2×8 = 16 s, retry_after well beyond the
    # cycle's accumulated pauses (at most 9s with 10 items × 1.0s pause, excluding the last).
    for _ in range(4):
        backoff.record_failure("amule-1")
    client_a = FakeMuleClient()
    client_b = FakeMuleClient()
    deps = _deps(catalog, engine, clock, backoff)
    workers = [_worker("amule-1", client_a, deps), _worker("amule-2", client_b, deps)]
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=workers,
        clients=[client_a, client_b],
        keywords=_KEYWORDS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=0,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
        telemetry=RecordingTelemetry(),
        edge=EdgeState(),
    )
    assert client_a.searches == []  # A in backoff → runs no search
    assert len(client_b.searches) == n_items  # B processes ALL tasks (zero loss)


@pytest.mark.asyncio
async def test_all_workers_in_backoff_drop_tasks_with_telemetry(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    # Terminal case: if ALL instances are in backoff, no one can process anymore → the
    # tasks are DROPPED with a telemetry trace (visibility), and the cycle finishes anyway
    # (queue drained, index advances).
    from mulewatch.domain.observability.events import SearchTaskDropped
    from mulewatch.domain.search.keywords import generate_keywords

    n_items = len(generate_keywords(_KEYWORDS)) * len(FakeMuleClient.channels)
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    # 4 failures per instance → backoff beyond the cycle's duration (cf. previous test).
    for _ in range(4):
        backoff.record_failure("amule-1")
        backoff.record_failure("amule-2")
    client_a = FakeMuleClient()
    client_b = FakeMuleClient()
    telemetry = RecordingTelemetry()
    deps = _deps(catalog, engine, clock, backoff, telemetry=telemetry)
    workers = [_worker("amule-1", client_a, deps), _worker("amule-2", client_b, deps)]
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=workers,
        clients=[client_a, client_b],
        keywords=_KEYWORDS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=0,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
        telemetry=telemetry,
        edge=EdgeState(),
    )
    assert client_a.searches == []
    assert client_b.searches == []
    drops = [e for e in telemetry.events if isinstance(e, SearchTaskDropped)]
    assert len(drops) == n_items  # one trace per lost task (operational visibility)
    assert scheduler_state.read_cycle_index() == 1  # the cycle finishes anyway


@pytest.mark.asyncio
async def test_repository_error_on_write_cycle_state_is_absorbed(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Regression error-boundary#1: a RepositoryError on write_cycle_state must NOT
    # propagate out of run_search_cycle (the supervisor TaskGroup would cancel the 3 other
    # loops → app crash). Aligned with run_download/verify ("NEVER RAISES").
    from mulewatch.ports.repository_errors import RepositoryError
    from mulewatch.ports.scheduler_state_repository import ChannelBackoff

    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    client = FakeMuleClient()
    worker = _worker("amule-1", client, _deps(catalog, engine, clock, backoff))
    inner = SqliteSchedulerStateRepository(local_connection)

    class _SchedulerWriteRaises:
        def read_cycle_index(self) -> int:
            return inner.read_cycle_index()

        def load_channel_backoff(self) -> dict[str, ChannelBackoff]:
            return inner.load_channel_backoff()

        def write_cycle_state(self, cycle_index: int, last_full_cycle_at: object) -> None:
            raise RepositoryError("disque plein")

        def save_channel_backoff(self, snapshot: dict[str, ChannelBackoff]) -> None:
            inner.save_channel_backoff(snapshot)

    with caplog.at_level(logging.ERROR, logger="mulewatch.application.run_search_cycle"):
        await run_search_cycle(
            workers=[worker],
            clients=[client],
            keywords=_KEYWORDS,
            rng=_NoopRng(),
            node_id="node-A",
            cycle_index=0,
            scheduler_state=_SchedulerWriteRaises(),
            backoff=backoff,
            clock=clock,
            telemetry=RecordingTelemetry(),
            edge=EdgeState(),
        )
    assert "repo" in caplog.text.lower()
    assert inner.read_cycle_index() == 0  # index did NOT advance: will replay the cycle


@pytest.mark.asyncio
async def test_repository_error_on_save_channel_backoff_is_absorbed(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Regression error-boundary#1 (symmetric): a RepositoryError on save_channel_backoff
    # is absorbed the same way. The index must NOT advance (atomicity §3/§7: if one of the
    # two fails, the cycle is replayable at the next startup).
    from mulewatch.ports.repository_errors import RepositoryError
    from mulewatch.ports.scheduler_state_repository import ChannelBackoff

    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    client = FakeMuleClient()
    worker = _worker("amule-1", client, _deps(catalog, engine, clock, backoff))
    inner = SqliteSchedulerStateRepository(local_connection)

    class _SchedulerSaveRaises:
        def read_cycle_index(self) -> int:
            return inner.read_cycle_index()

        def load_channel_backoff(self) -> dict[str, ChannelBackoff]:
            return inner.load_channel_backoff()

        def write_cycle_state(self, cycle_index: int, last_full_cycle_at: object) -> None:
            inner.write_cycle_state(cycle_index, last_full_cycle_at)  # type: ignore[arg-type]

        def save_channel_backoff(self, snapshot: dict[str, ChannelBackoff]) -> None:
            raise RepositoryError("local.db locked")

    with caplog.at_level(logging.ERROR, logger="mulewatch.application.run_search_cycle"):
        await run_search_cycle(
            workers=[worker],
            clients=[client],
            keywords=_KEYWORDS,
            rng=_NoopRng(),
            node_id="node-A",
            cycle_index=0,
            scheduler_state=_SchedulerSaveRaises(),
            backoff=backoff,
            clock=clock,
            telemetry=RecordingTelemetry(),
            edge=EdgeState(),
        )
    assert "repo" in caplog.text.lower()


@pytest.mark.asyncio
async def test_emits_cycle_completed(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    telemetry, edge = RecordingTelemetry(), EdgeState()
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    client = FakeMuleClient()
    worker = _worker("amule-1", client, _deps(catalog, engine, clock, backoff))
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=[worker],
        clients=[client],
        keywords=_KEYWORDS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=0,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
        telemetry=telemetry,
        edge=edge,
    )
    types = [type(e).__name__ for e in telemetry.events]
    assert types[-1] == "SearchCycleCompleted"
