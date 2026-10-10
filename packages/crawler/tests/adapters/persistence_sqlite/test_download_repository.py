import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mulewatch.adapters.persistence_sqlite.connection import open_local
from mulewatch.adapters.persistence_sqlite.download_repository import SqliteDownloadRepository
from mulewatch.adapters.persistence_sqlite.errors import PersistenceError
from mulewatch.domain.download.states import DownloadState
from mulewatch.domain.file_key import FileKey, Network

_A = FileKey(Network.ED2K, "a" * 32)
_B = FileKey(Network.ED2K, "b" * 32)


class _SettableClock:
    """Clock the test moves by hand (the TTL needs jumps, not a fixed tick)."""

    def __init__(self) -> None:
        self.now = datetime(2026, 6, 13, 10, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class _AdvancingClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 6, 13, 10, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        moment = self._now
        self._now += timedelta(minutes=1)
        return moment


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    local = open_local(tmp_path / "local.db")
    yield local
    local.close()


@pytest.fixture
def repository(connection: sqlite3.Connection) -> SqliteDownloadRepository:
    return SqliteDownloadRepository(connection)


def test_record_queued_inserts_a_new_download(repository: SqliteDownloadRepository) -> None:
    assert repository.record_queued(_A, "062A", 100) is True
    assert repository.is_downloaded(_A) is True


def test_record_queued_is_dedup_safe(repository: SqliteDownloadRepository) -> None:
    assert repository.record_queued(_A, "062A", 100) is True
    assert repository.record_queued(_A, "062A", 100) is False  # duplicate ignored


def test_is_downloaded_is_false_for_unknown_hash(repository: SqliteDownloadRepository) -> None:
    assert repository.is_downloaded(_A) is False


def test_set_state_updates_the_state(repository: SqliteDownloadRepository) -> None:
    repository.record_queued(_A, "062A", 100)
    repository.set_state(_A, DownloadState.DOWNLOADING)
    assert repository.active_states()[_A] is DownloadState.DOWNLOADING


def test_set_state_to_completed_stamps_completed_at(
    connection: sqlite3.Connection,
) -> None:
    repository = SqliteDownloadRepository(connection, clock=_AdvancingClock())
    repository.record_queued(_A, "062A", 100)
    repository.set_state(_A, DownloadState.COMPLETED)
    stamped = connection.execute(
        "SELECT completed_at FROM downloads WHERE ed2k_hash = ?", (_A.native_id,)
    ).fetchone()[0]
    assert stamped is not None


def test_set_state_non_completed_leaves_completed_at_null(
    repository: SqliteDownloadRepository, connection: sqlite3.Connection
) -> None:
    repository.record_queued(_A, "062A", 100)
    repository.set_state(_A, DownloadState.DOWNLOADING)
    stamped = connection.execute(
        "SELECT completed_at FROM downloads WHERE ed2k_hash = ?", (_A.native_id,)
    ).fetchone()[0]
    assert stamped is None


def test_set_state_on_unknown_hash_raises(repository: SqliteDownloadRepository) -> None:
    with pytest.raises(PersistenceError):
        repository.set_state(_A, DownloadState.DOWNLOADING)


def test_active_states_maps_hash_to_state(repository: SqliteDownloadRepository) -> None:
    repository.record_queued(_A, "062A", 100)
    repository.record_queued(_B, "063A", 200)
    repository.set_state(_B, DownloadState.FAILED)
    states = repository.active_states()
    assert states == {_A: DownloadState.QUEUED, _B: DownloadState.FAILED}


def test_record_queued_is_atomic_on_injected_failure(
    repository: SqliteDownloadRepository, connection: sqlite3.Connection
) -> None:
    connection.execute(
        "CREATE TRIGGER boom BEFORE INSERT ON downloads"
        " BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    with pytest.raises(PersistenceError, match="injected failure"):
        repository.record_queued(_A, "062A", 100)
    assert repository.is_downloaded(_A) is False


def test_get_target_id_returns_target_for_known_hash(
    repository: SqliteDownloadRepository,
) -> None:
    repository.record_queued(_A, "062A", 100)
    assert repository.get_target_id(_A) == "062A"


def test_get_target_id_is_none_for_unknown_hash(repository: SqliteDownloadRepository) -> None:
    assert repository.get_target_id(_A) is None


def _last_seen(connection: sqlite3.Connection, file: FileKey) -> str:
    row = connection.execute(
        "SELECT last_seen_at FROM downloads WHERE ed2k_hash = ?", (file.native_id,)
    ).fetchone()
    return str(row[0])


def test_record_queued_stamps_last_seen_at_with_queued_at(
    connection: sqlite3.Connection,
) -> None:
    # A row amuled never picks up must still age out: NULL would make it immortal.
    repository = SqliteDownloadRepository(connection, clock=_SettableClock())
    repository.record_queued(_A, "062A", 100)
    queued_at = connection.execute(
        "SELECT queued_at FROM downloads WHERE ed2k_hash = ?", (_A.native_id,)
    ).fetchone()[0]
    assert _last_seen(connection, _A) == queued_at


def test_mark_seen_refreshes_last_seen_at(connection: sqlite3.Connection) -> None:
    clock = _SettableClock()
    repository = SqliteDownloadRepository(connection, clock=clock)
    repository.record_queued(_A, "062A", 100)
    before = _last_seen(connection, _A)
    clock.now += timedelta(hours=1)
    repository.mark_seen([_A])
    assert _last_seen(connection, _A) > before


def test_mark_seen_ignores_a_hash_it_does_not_know(
    repository: SqliteDownloadRepository,
) -> None:
    repository.mark_seen([_A])  # a shared file the crawler never queued: no row, no raise
    assert repository.is_downloaded(_A) is False


def test_expire_lost_fails_the_rows_amuled_stopped_showing(
    connection: sqlite3.Connection,
) -> None:
    clock = _SettableClock()
    repository = SqliteDownloadRepository(connection, clock=clock)
    repository.record_queued(_A, "062A", 100)
    repository.set_state(_A, DownloadState.DOWNLOADING)
    repository.record_queued(_B, "063A", 200)
    clock.now += timedelta(hours=25)
    repository.mark_seen([_B])  # _B is still in amuled's queue, _A vanished 25 h ago
    assert repository.expire_lost(86400) == (_A,)
    assert repository.active_states() == {_A: DownloadState.FAILED, _B: DownloadState.QUEUED}


def test_expire_lost_spares_a_row_seen_within_the_ttl(
    connection: sqlite3.Connection,
) -> None:
    clock = _SettableClock()
    repository = SqliteDownloadRepository(connection, clock=clock)
    repository.record_queued(_A, "062A", 100)
    clock.now += timedelta(hours=23)
    assert repository.expire_lost(86400) == ()
    assert repository.active_states() == {_A: DownloadState.QUEUED}


def test_expire_lost_leaves_terminal_rows_alone(connection: sqlite3.Connection) -> None:
    clock = _SettableClock()
    repository = SqliteDownloadRepository(connection, clock=clock)
    repository.record_queued(_A, "062A", 100)
    repository.set_state(_A, DownloadState.COMPLETED)
    clock.now += timedelta(days=30)
    assert repository.expire_lost(86400) == ()
    assert repository.active_states() == {_A: DownloadState.COMPLETED}
