"""TDD tests for the migrations that retire the verification subsystem (scope-reduction spec §7).

catalog 0005 drops ``file_verifications``; its append-only triggers and its index have to go
first or the drop aborts. local 0004 drops ``verification_tasks`` and rewrites the legacy
``quarantined`` download state, which ``DownloadState`` can no longer parse: a surviving row
would raise ``ValueError`` on the next read and wedge the download loop.
"""

import sqlite3
from collections.abc import Iterator
from importlib import resources
from pathlib import Path

import pytest

from mulewatch.adapters.persistence_sqlite.connection import open_catalog, open_local
from mulewatch.adapters.persistence_sqlite.download_repository import SqliteDownloadRepository
from mulewatch.domain.download.states import DownloadState
from mulewatch.domain.file_key import FileKey, Network

_LOCAL_MIGRATIONS = resources.files("mulewatch.adapters.persistence_sqlite") / "migrations/local"


def _names(connection: sqlite3.Connection, kind: str) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,))
    return {str(row[0]) for row in rows}


def _write_legacy_local_db(path: Path) -> None:
    """A local.db stamped at version 3: verification_tasks present, 0004 not applied yet."""
    connection = sqlite3.connect(path, autocommit=True)
    connection.execute("PRAGMA journal_mode=WAL")
    for entry in sorted(_LOCAL_MIGRATIONS.iterdir(), key=lambda item: item.name):
        if int(entry.name.partition("_")[0]) <= 3:
            connection.executescript(entry.read_text(encoding="utf-8"))
    connection.execute("PRAGMA user_version = 3")
    connection.close()


@pytest.fixture
def catalog(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_catalog(tmp_path / "catalog.db")
    yield connection
    connection.close()


def test_catalog_drops_the_file_verifications_table(catalog: sqlite3.Connection) -> None:
    assert "file_verifications" not in _names(catalog, "table")


def test_catalog_drops_the_file_verifications_triggers(catalog: sqlite3.Connection) -> None:
    assert not {name for name in _names(catalog, "trigger") if "file_verifications" in name}


def test_catalog_drops_the_file_verifications_index(catalog: sqlite3.Connection) -> None:
    assert "idx_file_verifications_hash_verified" not in _names(catalog, "index")


def test_local_drops_the_verification_tasks_table(tmp_path: Path) -> None:
    connection = open_local(tmp_path / "local.db")
    try:
        assert "verification_tasks" not in _names(connection, "table")
        assert not {name for name in _names(connection, "index") if "verification_tasks" in name}
    finally:
        connection.close()


def test_local_rewrites_a_legacy_quarantined_download_as_completed(tmp_path: Path) -> None:
    path = tmp_path / "local.db"
    _write_legacy_local_db(path)
    legacy = sqlite3.connect(path, autocommit=True)
    legacy.execute(
        "INSERT INTO downloads (ed2k_hash, target_id, state, queued_at, size_bytes) "
        "VALUES ('a1', '062A', 'quarantined', '2026-09-01T00:00:00.000000+00:00', 42)"
    )
    legacy.close()

    connection = open_local(path)
    try:
        repository = SqliteDownloadRepository(connection)
        assert repository.active_states() == {FileKey(Network.ED2K, "a1"): DownloadState.COMPLETED}
    finally:
        connection.close()
