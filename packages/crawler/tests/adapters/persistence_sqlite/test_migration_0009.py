"""Catalog 0009 gives back the pages the rewrite freed, outside a transaction (stage 1, D7, D8)."""

import sqlite3
from pathlib import Path

from mulewatch.adapters.persistence_sqlite.connection import open_catalog
from tests.adapters.persistence_sqlite.older_catalog import open_catalog_at
from tests.catalog_rows import insert_file

_HASH = "a" * 32


def _catalog_at_8_with_free_pages(path: Path) -> None:
    connection = open_catalog_at(path, 8)
    insert_file(connection, _HASH, 1)
    # Stands for the tables 0006 to 0008 dropped.
    connection.execute(
        "CREATE TABLE dropped AS WITH RECURSIVE n (i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n"
        " WHERE i < 64) SELECT randomblob(4096) FROM n"
    )
    connection.execute("DROP TABLE dropped")
    assert connection.execute("PRAGMA freelist_count").fetchone()[0] > 0
    connection.close()


def test_0009_vacuums_the_free_pages_away_and_keeps_the_rows(tmp_path: Path) -> None:
    path = tmp_path / "catalog.db"
    _catalog_at_8_with_free_pages(path)

    connection = open_catalog(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 9
        assert connection.execute("PRAGMA freelist_count").fetchone()[0] == 0
        assert connection.execute("SELECT native_id FROM files").fetchall() == [(_HASH,)]
    finally:
        connection.close()


def test_0009_is_safe_to_replay(tmp_path: Path) -> None:
    """A stop between the VACUUM and the stamp runs it again at the next start."""
    path = tmp_path / "catalog.db"
    open_catalog(path).close()
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 8")
    raw.close()

    connection = open_catalog(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 9
    finally:
        connection.close()


def test_0009_leaves_the_wal_truncated(tmp_path: Path) -> None:
    """The crawler keeps its connection open, so a WAL the VACUUM grew would stay on disk."""
    path = tmp_path / "catalog.db"
    _catalog_at_8_with_free_pages(path)

    connection = open_catalog(path)
    try:
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        # The WAL header, then the one frame of the user_version stamp that follows 0009.
        assert Path(f"{path}-wal").stat().st_size <= 32 + 24 + page_size
    finally:
        connection.close()
