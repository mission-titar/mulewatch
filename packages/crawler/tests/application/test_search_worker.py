import dataclasses
import sqlite3

import pytest

from catalog_matching.engine import MatchingEngine
from mulewatch.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from mulewatch.application.search_worker import (
    SEARCH_BUDGET_SECONDS,
    BackoffRegistry,
    SearchTask,
    SearchWorker,
    WorkerDeps,
    WorkerPolicy,
)
from mulewatch.domain.file_key import FileKey, Network
from mulewatch.domain.observability.events import (
    InstanceUnreachable,
    ObservationRecorded,
    SearchExecuted,
    SearchFailed,
)
from mulewatch.domain.observation import FileObservation
from mulewatch.ports.scheduler_state_repository import ChannelBackoff
from tests.application.fakes import (
    FakeClock,
    FakeMuleClient,
    FakeRng,
    RecordingSignal,
    RecordingTelemetry,
    make_search_failed,
    make_unreachable,
)
from tests.catalog_rows import count_observations

_HASH = "31d6cfe0d16ae931b73c59d7e0c089c0"
_DL_NAME = "Keroro N°062A Les demoiselles cambrioleuses.avi"

# jitter_ratio 0.0 + FakeRng(jitter_value=0.0) → backoff = exact NOMINAL delay (clean assertions).
_POLICY = WorkerPolicy(
    backoff_base_seconds=2.0,
    backoff_cap_seconds=60.0,
    backoff_factor=2.0,
    backoff_jitter_ratio=0.0,
)


def _obs() -> FileObservation:
    return FileObservation(
        file=FileKey(Network.ED2K, _HASH),
        filename=_DL_NAME,
        size_bytes=234_000_000,
        source_count=3,
        keyword="keroro",
    )


def _registry(clock: FakeClock, rng: FakeRng | None = None) -> BackoffRegistry:
    return BackoffRegistry(_POLICY, clock, rng or FakeRng())


def _deps(
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
    backoff: BackoffRegistry,
    *,
    telemetry: RecordingTelemetry | None = None,
) -> WorkerDeps:
    return WorkerDeps(
        catalog=catalog,
        engine=engine,
        signal=RecordingSignal(),
        backoff=backoff,
        telemetry=telemetry or RecordingTelemetry(),
    )


# --- BackoffRegistry (logic, deterministic via fake clock/rng) ---


def test_backoff_registry_grows_then_resets() -> None:
    clock = FakeClock()
    registry = _registry(clock)
    assert registry.record_failure("k") == 2.0  # 1st attempt = base
    clock.advance(2.0)
    assert registry.record_failure("k") == 4.0  # × factor
    registry.reset("k")
    assert registry.record_failure("k") == 2.0  # back to base


def test_a_failure_while_backed_off_does_not_count_again() -> None:
    # Concurrent searches started before a backoff fail together: one round, one attempt.
    clock = FakeClock()
    registry = _registry(clock)
    registry.record_failure("k")
    clock.advance(0.5)
    assert registry.record_failure("k") == 1.5  # what is left of the backoff already set
    clock.advance(1.5)
    assert registry.record_failure("k") == 4.0  # the second attempt


def test_backoff_registry_keys_are_independent() -> None:
    registry = _registry(FakeClock())
    assert registry.record_failure("a") == 2.0
    assert registry.record_failure("b") == 2.0  # 'b' did not inherit 'a's counter


def test_backoff_registry_reset_unknown_key_is_a_noop() -> None:
    _registry(FakeClock()).reset("jamais-vu")  # does not raise


def test_backoff_registry_sets_retry_after_in_the_future() -> None:
    clock = FakeClock()
    registry = _registry(clock)
    registry.record_failure("amule-1:kad")
    assert registry.is_in_backoff("amule-1:kad") is True
    clock.advance(1.9)  # still before retry_after (2.0s)
    assert registry.is_in_backoff("amule-1:kad") is True
    clock.advance(0.2)  # now past retry_after
    assert registry.is_in_backoff("amule-1:kad") is False


def test_backoff_registry_unknown_key_is_not_in_backoff() -> None:
    assert _registry(FakeClock()).is_in_backoff("unknown") is False


def test_backoff_registry_jitter_extends_the_delay() -> None:
    clock = FakeClock()
    policy = dataclasses.replace(_POLICY, backoff_jitter_ratio=0.5)  # jitter in [0, 0.5*delay)
    rng = FakeRng(jitter_value=1.0)  # constant 1.0s jitter
    registry = BackoffRegistry(policy, clock, rng)
    delay = registry.record_failure("k")
    assert delay == 3.0  # base 2.0 + jitter 1.0
    assert rng.jitter_spans == [1.0]  # span = jitter_ratio (0.5) * delay (2.0)


def test_backoff_registry_snapshot_and_load_round_trip() -> None:
    clock = FakeClock()
    registry = _registry(clock)
    registry.record_failure("amule-1:kad")
    snapshot = registry.snapshot()
    assert "amule-1:kad" in snapshot
    assert isinstance(snapshot["amule-1:kad"], ChannelBackoff)
    # Reload into a FRESH registry (simulates a restart) → same skip applied.
    reborn = _registry(clock)
    assert reborn.is_in_backoff("amule-1:kad") is False  # empty before load
    reborn.load_from(snapshot)
    assert reborn.is_in_backoff("amule-1:kad") is True


@pytest.mark.parametrize(
    ("failed", "ready_in"),
    [((), 0.0), (("amule-1",), 2.0), (("amule-1:kad",), 2.0), (("amule-1:ed2k",), 0.0)],
)
def test_a_worker_is_ready_for_a_channel_when_neither_it_nor_the_channel_is_backed_off(
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
    failed: tuple[str, ...],
    ready_in: float,
) -> None:
    clock = FakeClock()
    registry = _registry(clock)
    for key in failed:
        registry.record_failure(key)
    worker = SearchWorker("amule-1", FakeMuleClient(), _deps(catalog, engine, registry))
    assert worker.seconds_until_ready("kad") == ready_in
    clock.advance(3.0)
    assert worker.seconds_until_ready("kad") == 0.0  # a past backoff is no wait


def test_a_worker_waits_for_the_later_of_its_two_backoffs(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    registry = _registry(clock)
    registry.record_failure("amule-1:kad")
    clock.advance(1.0)
    registry.record_failure("amule-1")
    worker = SearchWorker("amule-1", FakeMuleClient(), _deps(catalog, engine, registry))
    assert worker.seconds_until_ready("kad") == 2.0
    clock.advance(1.5)
    registry.reset("amule-1")
    assert worker.seconds_until_ready("kad") == 0.0  # the channel's own ended first


# --- SearchWorker ---


@pytest.mark.asyncio
async def test_successful_task_records_observation(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    clock = FakeClock()
    client = FakeMuleClient(results=[(_obs(),)])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, _registry(clock)))
    await worker.run_task(SearchTask(keyword="keroro", channel="ed2k"))
    assert client.searches == [("keroro", "ed2k")]
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_multiple_observations_some_unchanged_are_all_processed(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    clock = FakeClock()
    discarded = FileObservation(
        file=FileKey(Network.ED2K, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        filename="random.txt",  # discarded by the engine → record_observation returns False
        size_bytes=10,
        source_count=1,
        keyword="keroro",
    )
    # Two observations in the same readout: the 1st is discarded (False → loop back), the 2nd
    # changes a verdict. Covers the "if False → next observation" edge.
    client = FakeMuleClient(results=[(discarded, _obs())])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, _registry(clock)))
    await worker.run_task(SearchTask(keyword="keroro", channel="ed2k"))
    assert count_observations(catalog_connection) == 2
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_connect_failure_arms_instance_backoff_and_skips_the_item(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    registry = _registry(clock)
    client = FakeMuleClient(connect_failures=[make_unreachable()])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, registry))
    await worker.run_task(SearchTask(keyword="keroro", channel="ed2k"))
    assert client.searches == []  # item dropped, never searched
    assert registry.is_in_backoff("amule-1") is True  # instance in backoff (skip until retry)
    assert clock.sleeps == []  # no more backoff sleep: we SKIP, we don't wait


@pytest.mark.asyncio
async def test_instance_in_backoff_skips_without_connecting(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    registry = _registry(clock)
    registry.record_failure("amule-1")  # instance already in backoff
    client = FakeMuleClient(results=[(_obs(),)])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, registry))
    await worker.run_task(SearchTask(keyword="keroro", channel="ed2k"))
    assert client.connect_calls == 0  # neither connect nor search: skipped
    assert client.searches == []


@pytest.mark.asyncio
async def test_channel_in_backoff_skips_that_item(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    registry = _registry(clock)
    registry.record_failure("amule-1:kad")  # kad channel in backoff
    client = FakeMuleClient(results=[(_obs(),), (_obs(),)])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, registry))
    await worker.run_task(SearchTask(keyword="k", channel="kad"))  # skipped
    assert client.searches == []
    await worker.run_task(SearchTask(keyword="k", channel="ed2k"))  # other channel OK
    assert client.searches == [("k", "ed2k")]


@pytest.mark.asyncio
async def test_backoff_expires_and_item_runs_again(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    registry = _registry(clock)
    registry.record_failure("amule-1:kad")  # retry_after = +2.0s
    client = FakeMuleClient(results=[(_obs(),)])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, registry))
    clock.advance(3.0)  # retry_after passed → the channel is no longer in backoff
    await worker.run_task(SearchTask(keyword="k", channel="kad"))
    assert client.searches == [("k", "kad")]


@pytest.mark.asyncio
async def test_already_connected_does_not_reconnect(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    client = FakeMuleClient(results=[(), ()])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, _registry(clock)))
    await worker.run_task(SearchTask(keyword="k1", channel="ed2k"))
    await worker.run_task(SearchTask(keyword="k2", channel="ed2k"))
    assert client.connect_calls == 1  # connected only once for two tasks


@pytest.mark.asyncio
async def test_search_failure_arms_channel_backoff(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    registry = _registry(clock)
    client = FakeMuleClient(search_failures=[make_search_failed()])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, registry))
    await worker.run_task(SearchTask(keyword="keroro", channel="ed2k"))
    assert registry.is_in_backoff("amule-1:ed2k") is True  # channel in backoff
    assert registry.is_in_backoff("amule-1") is False  # but not the whole instance


@pytest.mark.asyncio
async def test_transport_failure_marks_instance_down(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    registry = _registry(clock)
    # search raises a transport failure (dead stream) → instance down + instance backoff.
    client = FakeMuleClient(search_failures=[make_unreachable()], results=[(_obs(),)])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, registry))
    await worker.run_task(SearchTask(keyword="k1", channel="ed2k"))
    assert registry.is_in_backoff("amule-1") is True
    # After the backoff expires, the next task FORCES a reconnect (down marked).
    clock.advance(3.0)
    await worker.run_task(SearchTask(keyword="k2", channel="ed2k"))
    assert client.connect_calls == 2


@pytest.mark.asyncio
async def test_a_task_is_one_search_given_the_cores_budget(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    client = FakeMuleClient(results=[(_obs(),)])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, _registry(clock)))
    await worker.run_task(SearchTask(keyword="keroro", channel="kad"))
    assert client.searches == [("keroro", "kad")]
    assert client.budgets == [SEARCH_BUDGET_SECONDS] == [120.0]
    assert clock.sleeps == []  # how to wait for the end is the client's business


# --- observability event emission (Plan E.2) ---


@pytest.mark.asyncio
async def test_successful_search_emits_search_executed_with_network_and_count(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    # A successful ed2k search emits SearchExecuted FIRST, labelled with its client,
    # before the per-observation events (ObservationRecorded/DecisionsRecorded).
    clock = FakeClock()
    telemetry = RecordingTelemetry()
    client = FakeMuleClient(results=[(_obs(),)])
    deps = _deps(catalog, engine, _registry(clock), telemetry=telemetry)
    worker = SearchWorker("amule-1", client, deps)
    await worker.run_task(SearchTask(keyword="keroro", channel="ed2k"))
    assert telemetry.events[0] == SearchExecuted(client="amule-1", network="ed2k", n_results=1)
    kinds = [type(e).__name__ for e in telemetry.events]
    assert kinds == ["SearchExecuted", "ObservationRecorded", "DecisionsRecorded"]
    assert telemetry.events[1] == ObservationRecorded(client="amule-1", network="ed2k")


@pytest.mark.asyncio
async def test_search_failure_emits_search_failed(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    # An application-level channel failure (SearchFailedError) emits
    # SearchFailed(client, network).
    clock = FakeClock()
    telemetry = RecordingTelemetry()
    client = FakeMuleClient(search_failures=[make_search_failed()])
    deps = _deps(catalog, engine, _registry(clock), telemetry=telemetry)
    worker = SearchWorker("amule-1", client, deps)
    await worker.run_task(SearchTask(keyword="keroro", channel="ed2k"))
    assert telemetry.events == [SearchFailed(client="amule-1", network="ed2k")]


@pytest.mark.asyncio
async def test_connect_failure_emits_instance_unreachable(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    # A connection failure (unreachable daemon) emits InstanceUnreachable.
    clock = FakeClock()
    telemetry = RecordingTelemetry()
    client = FakeMuleClient(connect_failures=[make_unreachable()])
    deps = _deps(catalog, engine, _registry(clock), telemetry=telemetry)
    worker = SearchWorker("amule-1", client, deps)
    await worker.run_task(SearchTask(keyword="keroro", channel="ed2k"))
    assert telemetry.events == [InstanceUnreachable("amule-1")]


@pytest.mark.asyncio
async def test_transport_failure_during_search_emits_instance_unreachable(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    # A transport failure during search (dead stream) emits InstanceUnreachable.
    clock = FakeClock()
    telemetry = RecordingTelemetry()
    client = FakeMuleClient(search_failures=[make_unreachable()], results=[(_obs(),)])
    deps = _deps(catalog, engine, _registry(clock), telemetry=telemetry)
    worker = SearchWorker("amule-1", client, deps)
    await worker.run_task(SearchTask(keyword="keroro", channel="ed2k"))
    assert telemetry.events == [InstanceUnreachable("amule-1")]
