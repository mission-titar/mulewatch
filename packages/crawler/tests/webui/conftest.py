"""Shared pytest fixtures for the webui: a migrated catalog.db, a hand-written local.db."""

import contextlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from mulewatch.adapters.persistence_sqlite.connection import open_catalog

# ---------------------------------------------------------------------------
# DDL helpers (module-level, not exported)
# ---------------------------------------------------------------------------


def _apply_local_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE node_runtime (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE downloads (
            ed2k_hash TEXT PRIMARY KEY,
            target_id TEXT NOT NULL,
            state TEXT NOT NULL,
            queued_at TEXT NOT NULL,
            completed_at TEXT,
            size_bytes INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE scheduler_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        PRAGMA journal_mode=WAL;
    """)


# ---------------------------------------------------------------------------
# Fixtures pytest
# ---------------------------------------------------------------------------


@pytest.fixture
def catalog_db(tmp_path: Path) -> Path:
    """An empty catalog.db migrated by the crawler's own migrations."""
    path = tmp_path / "catalog.db"
    open_catalog(path).close()
    return path


@pytest.fixture
def local_db(tmp_path: Path) -> Path:
    """Create a local.db with the realistic schema (WAL, empty), return the Path."""
    path = tmp_path / "local.db"
    with sqlite3.connect(path) as conn:
        _apply_local_schema(conn)
        conn.commit()
    return path


@pytest.fixture(autouse=True)
def _close_test_connections(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Close every SQLite connection a test opens.

    The suite pervasively uses ``with sqlite3.connect(...) as conn`` (seeds/fixtures) and
    ``CatalogReader(open_reader(db))`` (readers) without closing. ``sqlite3.Connection.__exit__``
    only ends the transaction, it does NOT close, so each connection lingers until GC and
    surfaces as ``ResourceWarning: unclosed database``. This wraps ``sqlite3.connect`` for the
    duration of each test, tracks every connection it hands out (``open_reader`` opens through
    it too), and closes them at teardown. The app's ``ReaderProvider`` connections pass through
    here as well; the provider deliberately REUSES them across requests (never closing per
    request), so this teardown is what closes them when the test app is discarded.
    """
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def tracking_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection: sqlite3.Connection = real_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracking_connect)
    yield
    for connection in opened:
        with contextlib.suppress(sqlite3.Error):
            connection.close()
