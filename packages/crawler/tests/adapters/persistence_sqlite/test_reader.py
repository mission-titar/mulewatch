"""Tests for the read-only connection provider (monolith-consolidation spec §7).

Folds the assertions of the deleted webui ``test_webui_db.py`` (RO-refuses-writes,
``temp_store=MEMORY``, ``row_factory`` dict access, reads rows) plus the provider's
per-thread reuse, thread affinity, and ``quiesce()`` seam.
"""

import sqlite3
import threading
from pathlib import Path

import pytest

from mulewatch.adapters.persistence_sqlite.connection import open_catalog
from mulewatch.adapters.persistence_sqlite.errors import PersistenceError
from mulewatch.adapters.persistence_sqlite.reader import ReaderProvider, open_reader
from tests.catalog_rows import insert_file

_HASH_A = "a" * 32
_HASH_B = "b" * 32


def _seed(path: Path) -> None:
    """Create + seed a real catalog.db via the writer (readers need a real file, mode=ro)."""
    writer = open_catalog(path)
    try:
        insert_file(writer, _HASH_A, 10)
    finally:
        writer.close()


# ---------------------------------------------------------------------------
# open_reader (low-level single connection)
# ---------------------------------------------------------------------------


def test_open_reader_reads_seeded_row_with_dict_access(tmp_path: Path) -> None:
    """A seeded row is readable and ``row_factory`` gives dict access (``row["col"]``)."""
    path = tmp_path / "catalog.db"
    _seed(path)
    reader = open_reader(path)
    try:
        row = reader.execute("SELECT native_id, size_bytes FROM files").fetchone()
        assert row["native_id"] == _HASH_A
        assert row["size_bytes"] == 10
    finally:
        reader.close()


def test_open_reader_refuses_writes(tmp_path: Path) -> None:
    """``mode=ro`` + ``query_only`` is a double guard: an INSERT raises ``OperationalError``."""
    path = tmp_path / "catalog.db"
    _seed(path)
    reader = open_reader(path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            insert_file(reader, _HASH_B, 1)
    finally:
        reader.close()


def test_open_reader_keeps_temp_store_in_memory(tmp_path: Path) -> None:
    """``temp_store=MEMORY`` (2) for the same reason the writer sets it (connection.py): temp
    b-trees live in the process heap, so nothing depends on how the temp directory was sized."""
    path = tmp_path / "catalog.db"
    _seed(path)
    reader = open_reader(path)
    try:
        assert reader.execute("PRAGMA temp_store").fetchone()[0] == 2  # 2 == MEMORY
    finally:
        reader.close()


# ---------------------------------------------------------------------------
# ReaderProvider (reused per-thread connection + quiesce seam)
# ---------------------------------------------------------------------------


def test_provider_reuses_the_same_connection_within_one_thread(tmp_path: Path) -> None:
    """Repeated ``connection()`` calls from one thread return the SAME object (warm cache)."""
    path = tmp_path / "catalog.db"
    _seed(path)
    provider = ReaderProvider(path)
    try:
        first = provider.connection()
        second = provider.connection()
        assert first is second
    finally:
        provider.quiesce()


def test_provider_gives_a_distinct_connection_per_thread(tmp_path: Path) -> None:
    """Two different threads each get their OWN connection (thread affinity)."""
    path = tmp_path / "catalog.db"
    _seed(path)
    provider = ReaderProvider(path)
    connections: dict[str, sqlite3.Connection] = {}

    def worker(name: str) -> None:
        connections[name] = provider.connection()

    first = threading.Thread(target=worker, args=("first",))
    second = threading.Thread(target=worker, args=("second",))
    first.start()
    second.start()
    first.join()
    second.join()
    try:
        assert id(connections["first"]) != id(connections["second"])
        assert connections["first"] is not connections["second"]
    finally:
        provider.quiesce()


def test_reused_connection_sees_data_committed_after_first_handout(tmp_path: Path) -> None:
    """A reused reader sees state committed AFTER it was first handed out: autocommit reads
    see the current committed snapshot, proving reuse is correct, not a stale snapshot."""
    path = tmp_path / "catalog.db"
    _seed(path)  # one row so far
    provider = ReaderProvider(path)
    writer = open_catalog(path)
    try:
        reader = provider.connection()
        assert reader.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
        insert_file(writer, _HASH_B, 20)
        reused = provider.connection()
        assert reused is reader
        assert reused.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 2
    finally:
        writer.close()
        provider.quiesce()


def test_quiesce_closes_connections_and_blocks_new_ones(tmp_path: Path) -> None:
    """``quiesce()`` closes handed-out connections (using one raises ``ProgrammingError``) AND
    makes a subsequent ``connection()`` raise ``PersistenceError``."""
    path = tmp_path / "catalog.db"
    _seed(path)
    provider = ReaderProvider(path)
    reader = provider.connection()
    provider.quiesce()
    with pytest.raises(sqlite3.ProgrammingError):
        reader.execute("SELECT 1")
    with pytest.raises(PersistenceError, match="quiesced"):
        provider.connection()


def test_quiesce_closes_a_connection_opened_on_another_thread(tmp_path: Path) -> None:
    """The whole point of ``check_same_thread=False``: ``quiesce()`` on the main thread closes
    a connection created on a worker thread."""
    path = tmp_path / "catalog.db"
    _seed(path)
    provider = ReaderProvider(path)
    worker_connection: dict[str, sqlite3.Connection] = {}

    def worker() -> None:
        worker_connection["it"] = provider.connection()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    provider.quiesce()  # main thread closes the worker's connection
    with pytest.raises(sqlite3.ProgrammingError):
        worker_connection["it"].execute("SELECT 1")


def test_quiesce_is_idempotent(tmp_path: Path) -> None:
    """A second ``quiesce()`` (registry already drained) does not raise."""
    path = tmp_path / "catalog.db"
    _seed(path)
    provider = ReaderProvider(path)
    provider.connection()
    provider.quiesce()
    provider.quiesce()  # must not raise
