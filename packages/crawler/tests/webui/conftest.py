"""Shared pytest fixtures for the webui: DDL schemas without importing mulewatch."""

import contextlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# DDL helpers (module-level, not exported)
# ---------------------------------------------------------------------------


def _apply_catalog_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE files (
            ed2k_hash TEXT PRIMARY KEY,
            size_bytes INTEGER NOT NULL,
            aich_hash TEXT,
            CHECK (LENGTH(ed2k_hash) = 32 AND ed2k_hash NOT GLOB '*[^0-9a-f]*')
        );

        CREATE TABLE file_observations (
            id INTEGER PRIMARY KEY,
            ed2k_hash TEXT NOT NULL,
            filename TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            source_count INTEGER NOT NULL,
            complete_source_count INTEGER NOT NULL,
            media_length_sec INTEGER,
            bitrate_kbps INTEGER,
            codec TEXT,
            file_type TEXT,
            raw_meta TEXT NOT NULL,
            keyword TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            node_id TEXT NOT NULL
        );

        CREATE TABLE file_observation_ranges (
            id INTEGER PRIMARY KEY,
            ed2k_hash TEXT NOT NULL,
            bucket TEXT NOT NULL,
            filenames TEXT NOT NULL,
            node_ids TEXT NOT NULL,
            observation_count INTEGER NOT NULL,
            first_observed_at TEXT NOT NULL,
            last_observed_at TEXT NOT NULL,
            source_count_min INTEGER NOT NULL,
            source_count_max INTEGER NOT NULL,
            source_count_sum INTEGER NOT NULL,
            complete_source_count_min INTEGER NOT NULL,
            complete_source_count_max INTEGER NOT NULL,
            complete_source_count_sum INTEGER NOT NULL
        );

        CREATE TABLE match_decisions (
            id INTEGER PRIMARY KEY,
            ed2k_hash TEXT NOT NULL,
            target_id TEXT NOT NULL,
            rule_name TEXT NOT NULL,
            tier TEXT NOT NULL,
            decided_at TEXT NOT NULL,
            node_id TEXT NOT NULL
        );

        PRAGMA journal_mode=WAL;
    """)


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
    """Create a catalog.db with the realistic schema (WAL, empty), return the Path."""
    path = tmp_path / "catalog.db"
    with sqlite3.connect(path) as conn:
        _apply_catalog_schema(conn)
        conn.commit()
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
