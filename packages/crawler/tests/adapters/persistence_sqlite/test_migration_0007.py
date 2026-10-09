"""Catalog 0007 stores observations as variants plus timestamps (stage 1, D4 and D7)."""

import sqlite3
from pathlib import Path

import pytest

from mulewatch.adapters.mule_api.mapping import map_search_results
from mulewatch.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from mulewatch.adapters.persistence_sqlite.connection import open_catalog
from mulewatch.adapters.persistence_sqlite.variants import iso_to_micros
from tests.adapters.persistence_sqlite.older_catalog import forcing_secure_delete, open_catalog_at

_A, _B = "a" * 32, "b" * 32
_T1 = "2026-06-11T12:00:00.000001+00:00"
_T2 = "2026-06-11T12:05:00.000000+00:00"
_T3 = "2026-06-12T08:00:00.250000+00:00"
_COLUMNS = (
    "ed2k_hash, filename, size_bytes, source_count, complete_source_count, media_length_sec,"
    " bitrate_kbps, codec, file_type, raw_meta, keyword, observed_at, node_id"
)
_NAME = "Kéroro 062A.avi"
_RAW_META = '[["0x0308", "0"], ["0x0999", "mystère"]]'
_FULL = (_A, _NAME, 100, 5, 2, 1474, 1200, "xvid", "Video", _RAW_META, "keroro", _T1, "n1")
_BARE = (_B, "b.avi", 200, 1, 0, None, None, None, None, "[]", "titar", _T1, "n2")
_FULL_VARIANT = (_A, _NAME, 100, 1474, 1200)
_BARE_VARIANT = (_B, "b.avi", 200, None, None)
_BARE_META = '[["codec", null], ["file_type", null], ["complete_source_count", 0]]'
# The variants as the readers rebuild them, observations as (observed_at, source_count).
_REBUILT = """
SELECT v.ed2k_hash, v.filename, v.size_bytes, v.media_length_sec, v.bitrate_kbps, v.raw_meta,
    v.keyword, v.node_id, o.observed_at, o.source_count
FROM observation_variants AS v JOIN observations AS o ON o.variant_id = v.variant_id
"""


def _full_meta(complete: int) -> str:
    return (
        '[["0x0308", "0"], ["0x0999", "mystère"], ["codec", "xvid"], ["file_type", "Video"],'
        f' ["complete_source_count", {complete}]]'
    )


def _old_catalog(path: Path, *rows: tuple[object, ...]) -> None:
    connection = open_catalog_at(path, 6)
    for ed2k_hash in sorted({str(row[0]) for row in rows}):
        connection.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, 1)", (ed2k_hash,))
    for row in rows:
        connection.execute(
            f"INSERT INTO file_observations ({_COLUMNS}) VALUES ({', '.join('?' * 13)})", row
        )
    connection.close()


def _with(row: tuple[object, ...], **changes: object) -> tuple[object, ...]:
    names = [name.strip() for name in _COLUMNS.split(",")]
    return tuple(changes.get(name, value) for name, value in zip(names, row, strict=True))


def test_observations_become_variants_with_their_timestamps_and_nothing_is_lost(
    tmp_path: Path,
) -> None:
    path = tmp_path / "catalog.db"
    _old_catalog(
        path,
        _FULL,
        _with(_FULL, observed_at=_T2, source_count=6),
        _with(_FULL, observed_at=_T3, complete_source_count=3),
        _BARE,
    )
    connection = open_catalog_at(path, 7)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 7
        assert sorted(connection.execute(_REBUILT).fetchall()) == [
            (*_FULL_VARIANT, _full_meta(2), "keroro", "n1", iso_to_micros(_T1), 5),
            (*_FULL_VARIANT, _full_meta(2), "keroro", "n1", iso_to_micros(_T2), 6),
            (*_FULL_VARIANT, _full_meta(3), "keroro", "n1", iso_to_micros(_T3), 5),
            (*_BARE_VARIANT, _BARE_META, "titar", "n2", iso_to_micros(_T1), 1),
        ]
        assert connection.execute("SELECT count(*) FROM observation_variants").fetchone()[0] == 3
    finally:
        connection.close()


def test_identical_old_rows_become_one_observation(tmp_path: Path) -> None:
    path = tmp_path / "catalog.db"
    _old_catalog(path, _BARE, _BARE)
    connection = open_catalog_at(path, 7)
    try:
        assert connection.execute("SELECT count(*) FROM observations").fetchone()[0] == 1
    finally:
        connection.close()


def test_the_old_table_and_its_indexes_are_gone(tmp_path: Path) -> None:
    path = tmp_path / "catalog.db"
    _old_catalog(path, _BARE)
    connection = open_catalog_at(path, 7)
    try:
        names = {row[0] for row in connection.execute("SELECT tbl_name FROM sqlite_schema")}
        assert "file_observations" not in names
        assert {"observation_variants", "observations"} <= names
    finally:
        connection.close()


def test_a_mapped_result_reuses_the_variant_0007_migrated_from_its_old_row(
    tmp_path: Path,
) -> None:
    """D11: the mapper's fold and 0007's agree, or every file gets a second variant."""
    result = {
        "hash": _A,
        "name": _NAME,
        "size_bytes": 100,
        "sources": {"total": 9, "complete": 2},
        "rating": 0,
        "file_type": "Video",
        "media": {"duration_seconds": 1474, "bitrate_kilobits_per_second": 1200, "codec": "xvid"},
        "status": "mystère",
    }
    # The same values as the pre-0007 mapper and repository stored them.
    old_meta = '[["rating", "0"], ["status", "mystère"]]'
    path = tmp_path / "catalog.db"
    _old_catalog(path, _with(_FULL, raw_meta=old_meta))
    (observation,), _ = map_search_results([result], "keroro")
    connection = open_catalog(path)
    try:
        SqliteCatalogRepository(connection, "n1").record_observation(observation)
        assert connection.execute("SELECT count(*) FROM observation_variants").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM observations").fetchone()[0] == 2
    finally:
        connection.close()


def test_the_runner_restores_the_pragmas_0007_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "catalog.db"
    _old_catalog(path, _BARE)
    forcing_secure_delete(monkeypatch)
    connection = open_catalog_at(path, 7)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 7
        assert connection.execute("PRAGMA secure_delete").fetchone()[0] == 1
        assert connection.execute("PRAGMA cache_size").fetchone()[0] == -2000
    finally:
        connection.close()


def test_dropping_the_old_table_keeps_its_pages_out_of_the_wal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "catalog.db"
    # One variant observed 2,000 times: a large old table, a small new one.
    long_name = _with(_BARE, filename="x" * 1000)
    _old_catalog(path, *(_with(long_name, source_count=n) for n in range(2000)))
    raw = sqlite3.connect(path)
    dropped = raw.execute("SELECT sum(pgsize) FROM dbstat WHERE name = 'file_observations'")
    dropped_bytes = int(dropped.fetchone()[0])
    raw.close()
    forcing_secure_delete(monkeypatch)
    connection = open_catalog_at(path, 7)
    try:
        wal_bytes = (tmp_path / "catalog.db-wal").stat().st_size
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 7
        assert dropped_bytes > 2_000_000
        assert wal_bytes < dropped_bytes, (wal_bytes, dropped_bytes)
    finally:
        connection.close()
