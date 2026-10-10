"""Catalog 0006 drops the dead tables, and refuses a catalog whose ranges hold a row (stage 1, D6).

A compacted day cannot become observations: it keeps no keyword, duration, bitrate or raw_meta.
"""

import sqlite3
from pathlib import Path

import pytest

from p2pwatch.adapters.persistence_sqlite.connection import open_catalog
from p2pwatch.adapters.persistence_sqlite.errors import MigrationError
from tests.adapters.persistence_sqlite.older_catalog import open_catalog_at

_HASH = "a" * 32
_DEAD_TABLES = ("source_observations", "sources", "file_observation_ranges")
_RANGE = (
    "INSERT INTO file_observation_ranges (ed2k_hash, bucket, filenames, node_ids,"
    " observation_count, first_observed_at, last_observed_at, source_count_min,"
    " source_count_max, source_count_sum, complete_source_count_min,"
    " complete_source_count_max, complete_source_count_sum) VALUES"
    f" ('{_HASH}', '2026-03-01', '[\"f.avi\"]', '[\"n\"]', 1,"
    " '2026-03-01T00:00:00.000000+00:00', '2026-03-01T00:00:00.000000+00:00',"
    " 1, 1, 1, 0, 0, 0)"
)


def _write_catalog_at_5(path: Path, *, with_range: bool) -> None:
    connection = open_catalog_at(path, 5)
    connection.execute(f"INSERT INTO files (ed2k_hash, size_bytes) VALUES ('{_HASH}', 1)")
    if with_range:
        connection.execute(_RANGE)
    connection.close()


def _schema_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name, tbl_name FROM sqlite_schema")
    return {str(name) for row in rows for name in row}


def test_a_catalog_holding_ranges_refuses_to_migrate(tmp_path: Path) -> None:
    path = tmp_path / "catalog.db"
    _write_catalog_at_5(path, with_range=True)

    with pytest.raises(MigrationError, match="file_observation_ranges"):
        open_catalog(path)

    raw = sqlite3.connect(path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 5
        assert raw.execute("SELECT count(*) FROM file_observation_ranges").fetchone()[0] == 1
    finally:
        raw.close()


def test_a_catalog_without_ranges_drops_the_dead_tables_and_keeps_its_files(
    tmp_path: Path,
) -> None:
    path = tmp_path / "catalog.db"
    _write_catalog_at_5(path, with_range=False)

    connection = open_catalog_at(path, 6)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
        assert not _schema_names(connection) & {*_DEAD_TABLES, "migration_0006_guard"}
        assert connection.execute("SELECT ed2k_hash FROM files").fetchall() == [(_HASH,)]
    finally:
        connection.close()
