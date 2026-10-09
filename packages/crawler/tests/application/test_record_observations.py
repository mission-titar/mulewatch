import asyncio
import sqlite3

import pytest

from catalog_matching.engine import MatchingEngine
from mulewatch.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from mulewatch.application.record_observations import record_observation
from mulewatch.application.run_download_cycle import DOWNLOAD_NUDGE_SUBJECT
from mulewatch.domain.file_key import FileKey, Network
from mulewatch.domain.observability.events import ObservationRecorded
from mulewatch.domain.observation import FileObservation
from tests.application.fakes import RecordingSignal, RecordingTelemetry
from tests.catalog_rows import count_observations

_HASH_DL = "31d6cfe0d16ae931b73c59d7e0c089c0"
_HASH_CAT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_HASH_DISCARD = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_DL_NAME = "Keroro N°062A Les demoiselles cambrioleuses.avi"
# Pins a STABLE target via its unique title (062A/notify/title_review), unlike the catch-all
# "keroro something.avi" whose target_id is an arbitrary min-key over the present targets.
_NOTIFY_NAME = "Keroro Les demoiselles cambrioleuses.avi"


def _obs(ed2k_hash: str, filename: str) -> FileObservation:
    return FileObservation(
        file=FileKey(Network.ED2K, ed2k_hash),
        filename=filename,
        size_bytes=234_000_000,
        source_count=3,
        keyword="keroro",
    )


@pytest.mark.asyncio
async def test_observation_is_always_recorded_even_when_discarded(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    changed = await record_observation(
        _obs(_HASH_DISCARD, "random.txt"),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
        network="ed2k",
    )
    assert changed == 0
    assert count_observations(catalog_connection) == 1
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 0
    assert signal.signalled == []


@pytest.mark.asyncio
async def test_new_verdict_is_persisted_and_nudged(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    changed = await record_observation(
        _obs(_HASH_DL, _DL_NAME),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
        network="ed2k",
    )
    assert changed == 1
    assert catalog_connection.execute("SELECT tier FROM match_decisions").fetchone() == (
        "download",
    )
    assert signal.signalled == [DOWNLOAD_NUDGE_SUBJECT]


@pytest.mark.asyncio
async def test_unchanged_verdict_is_not_reappended_or_nudged(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    observation = _obs(_HASH_CAT, "keroro something.avi")
    assert (
        await record_observation(
            observation,
            catalog=catalog,
            engine=engine,
            signal=signal,
            telemetry=telemetry,
            network="ed2k",
        )
        == 1
    )
    # Second observation of the SAME file: same catalog verdict → no re-append.
    assert (
        await record_observation(
            observation,
            catalog=catalog,
            engine=engine,
            signal=signal,
            telemetry=telemetry,
            network="ed2k",
        )
        == 0
    )
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 1
    # But the observation itself is re-persisted (periodic re-observation = the goal).
    assert count_observations(catalog_connection) == 2
    assert signal.signalled == []


@pytest.mark.asyncio
async def test_changed_verdict_is_reappended_and_nudged_again(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    await record_observation(
        _obs(_HASH_DL, _NOTIFY_NAME),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
        network="ed2k",
    )
    # 2nd view of the SAME hash, DOWNLOAD name → verdict changes → re-append + nudge.
    changed = await record_observation(
        _obs(_HASH_DL, _DL_NAME),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
        network="ed2k",
    )
    assert changed == 1
    tiers = [
        row[0]
        for row in catalog_connection.execute(
            "SELECT tier FROM match_decisions ORDER BY id"
        ).fetchall()
    ]
    assert tiers == ["notify", "download"]
    assert signal.signalled == [DOWNLOAD_NUDGE_SUBJECT]


@pytest.mark.asyncio
async def test_persistence_error_is_absorbed_and_cycle_continues(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # TEST trigger: makes the observation INSERT fail → RepositoryError absorbed.
    catalog_connection.execute(
        "CREATE TRIGGER boom BEFORE INSERT ON observations"
        " BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    changed = await record_observation(
        _obs(_HASH_DL, _DL_NAME),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
        network="ed2k",
    )
    assert changed == 0  # absorbed, the cycle continues
    assert signal.signalled == []
    assert f"file=ed2k:{_HASH_DL} " in caplog.text  # key=value, like the download side's hash=


@pytest.mark.asyncio
async def test_signal_consumer_awaits_the_nudge(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    # The hub IS consumed by an await (not dead code, DECISION 9): a consumer sleeps
    # on the subject and is woken by the post-commit nudge.
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    waiter = asyncio.create_task(signal.wait(DOWNLOAD_NUDGE_SUBJECT))
    await asyncio.sleep(0)
    assert not waiter.done()
    await record_observation(
        _obs(_HASH_DL, _DL_NAME),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
        network="ed2k",
    )
    await asyncio.wait_for(waiter, timeout=1.0)
    assert waiter.done()


@pytest.mark.asyncio
async def test_download_tier_verdict_also_nudges_the_download_subject(
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
) -> None:
    # a NEW "download"-tier verdict signals the subject by hash THEN the "download" subject
    # (wakes the download loop, DECISION DV9) — in that order.
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    changed = await record_observation(
        _obs(_HASH_DL, _DL_NAME),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
        network="ed2k",
    )
    assert changed == 1
    assert signal.signalled == [DOWNLOAD_NUDGE_SUBJECT]


@pytest.mark.asyncio
async def test_non_download_tier_verdict_does_not_nudge_the_download_subject(
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
) -> None:
    # a "catalog"-tier verdict signals the subject by hash but NEVER the "download" subject.
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    changed = await record_observation(
        _obs(_HASH_CAT, "keroro something.avi"),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
        network="ed2k",
    )
    assert changed == 1
    assert signal.signalled == []


@pytest.mark.asyncio
async def test_emits_observation_then_decision_on_change(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    obs = _obs(_HASH_DL, _DL_NAME)  # matches at tier=download
    await record_observation(
        obs, catalog=catalog, engine=engine, signal=signal, telemetry=telemetry, network="ed2k"
    )
    kinds = [type(e).__name__ for e in telemetry.events]
    assert kinds == ["ObservationRecorded", "DecisionsRecorded"]
    assert telemetry.events[0] == ObservationRecorded(network="ed2k")


@pytest.mark.asyncio
async def test_emits_only_observation_when_discarded(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    telemetry = RecordingTelemetry()
    obs = _obs(_HASH_DISCARD, "random.txt")  # discarded by the engine
    await record_observation(
        obs,
        catalog=catalog,
        engine=engine,
        signal=RecordingSignal(),
        telemetry=telemetry,
        network="kad",
    )
    assert [type(e).__name__ for e in telemetry.events] == ["ObservationRecorded"]


@pytest.mark.asyncio
async def test_multi_segment_observation_records_two_rows(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    changed = await record_observation(
        _obs(_HASH_DL, "Keroro 062 teletoon.avi"),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
        network="ed2k",
    )
    assert changed == 2
    assert catalog_connection.execute(
        "SELECT target_id FROM match_decisions ORDER BY id"
    ).fetchall() == [("062A",), ("062B",)]
