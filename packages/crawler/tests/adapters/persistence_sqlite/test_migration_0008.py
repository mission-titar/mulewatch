"""Catalog 0008 keys every table that names a file by ``file_id`` (stage 1, D1, D4 and D7)."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from p2pwatch.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from p2pwatch.adapters.persistence_sqlite.variants import content_hash
from p2pwatch.domain.file_key import FileKey, Network
from p2pwatch.domain.observation import FileObservation
from tests.adapters.persistence_sqlite.older_catalog import forcing_secure_delete, open_catalog_at

_A, _B = "a" * 32, "b" * 32
_ID_A, _ID_B = FileKey(Network.ED2K, _A).file_id, FileKey(Network.ED2K, _B).file_id
_VARIANTS = (
    (_A, "a.avi", 100, 1474, 1200, '[["codec", "xvid"]]', "keroro", "n1"),
    (_A, "a [VF].avi", 100, None, None, "[]", "titar", "n2"),
    (_B, "b.avi", 200, None, None, "[]", "keroro", "n1"),
)
_OBSERVATIONS = ((1, 10, 5), (1, 20, 6), (2, 10, 1), (3, 30, 2))
_DECISIONS = (
    (_A, "062A", "exact", "download", "2026-06-11T12:00:00.000000+00:00", "n1"),
    (_B, "062B", "loose", "notify", "2026-06-11T12:00:01.000000+00:00", "n1"),
    (_A, "062A", "", "retracted", "2026-06-11T12:00:02.000000+00:00", "n2"),
)
_ED2K_COLUMNS = """
SELECT m.name, p.name FROM sqlite_schema AS m, pragma_table_info(m.name) AS p
WHERE m.type = 'table' AND (p.name LIKE '%ed2k%' OR p.name LIKE 'aich%')
"""


def _catalog_at_7(path: Path) -> None:
    connection = open_catalog_at(path, 7)
    for native_id, size in ((_A, 100), (_B, 200)):
        connection.execute(
            "INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", (native_id, size)
        )
    for variant in _VARIANTS:
        connection.execute(
            "INSERT INTO observation_variants (ed2k_hash, filename, size_bytes, media_length_sec,"
            " bitrate_kbps, raw_meta, keyword, node_id, content_hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*variant, content_hash(*variant)),
        )
    connection.executemany("INSERT INTO observations VALUES (?, ?, ?)", _OBSERVATIONS)
    connection.executemany(
        "INSERT INTO match_decisions (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        _DECISIONS,
    )
    connection.close()


@pytest.fixture
def migrated(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    _catalog_at_7(tmp_path / "catalog.db")
    connection = open_catalog_at(tmp_path / "catalog.db", 8)
    yield connection
    connection.close()


def test_files_are_keyed_by_the_file_id_of_their_network_and_native_id(
    migrated: sqlite3.Connection,
) -> None:
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == 8
    assert migrated.execute("SELECT * FROM files ORDER BY native_id").fetchall() == [
        (_ID_A, "ed2k", _A, 100),
        (_ID_B, "ed2k", _B, 200),
    ]


def test_variants_keep_their_id_and_hash_the_file_id(migrated: sqlite3.Connection) -> None:
    file_ids = {_A: _ID_A, _B: _ID_B}
    expected = [
        (n, file_ids[v[0]], *v[1:], content_hash(file_ids[v[0]], *v[1:]))
        for n, v in enumerate(_VARIANTS, start=1)
    ]
    assert migrated.execute("SELECT * FROM observation_variants ORDER BY 1").fetchall() == expected
    assert migrated.execute("SELECT * FROM observations ORDER BY 1, 2").fetchall() == list(
        _OBSERVATIONS
    )


def test_decisions_keep_their_id_and_name_the_file_id(migrated: sqlite3.Connection) -> None:
    file_ids = {_A: _ID_A, _B: _ID_B}
    expected = [(n, file_ids[d[0]], *d[1:]) for n, d in enumerate(_DECISIONS, start=1)]
    assert migrated.execute("SELECT * FROM match_decisions ORDER BY id").fetchall() == expected


def test_no_ed2k_identity_or_old_table_is_left(migrated: sqlite3.Connection) -> None:
    assert migrated.execute(_ED2K_COLUMNS).fetchall() == []
    tables = {row[0] for row in migrated.execute("SELECT name FROM sqlite_schema")}
    assert not {name for name in tables if name.endswith("_old")}
    indexes = migrated.execute(
        "SELECT name, tbl_name FROM sqlite_schema WHERE type = 'index' AND sql IS NOT NULL"
    )
    # The index on file_id alone would be a prefix of the decision index: not carried.
    assert sorted(indexes.fetchall()) == [
        ("idx_match_decisions_file_target_decided", "match_decisions"),
        ("idx_observation_variants_file_id", "observation_variants"),
    ]


def test_files_refuse_a_non_canonical_ed2k_id_or_an_unknown_network(
    migrated: sqlite3.Connection,
) -> None:
    for network, native_id in (("ed2k", _A.upper()), ("ed2k", "c" * 31), ("kad", "c" * 32)):
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            migrated.execute(
                "INSERT INTO files VALUES (randomblob(16), ?, ?, 1)", (network, native_id)
            )


def test_a_new_sighting_reuses_the_variant_0008_rekeyed(migrated: sqlite3.Connection) -> None:
    """The repository and 0008 hash a variant alike, or every file gets a second variant."""
    observation = FileObservation(
        file=FileKey(Network.ED2K, _B),
        filename="b.avi",
        size_bytes=200,
        source_count=4,
        keyword="keroro",
    )
    SqliteCatalogRepository(migrated, "n1").record_observation(observation)
    assert migrated.execute("SELECT count(*) FROM observation_variants").fetchone()[0] == 3
    assert migrated.execute("SELECT count(*) FROM observations").fetchone()[0] == 5


def test_the_runner_restores_the_pragmas_0008_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _catalog_at_7(tmp_path / "catalog.db")
    forcing_secure_delete(monkeypatch)
    connection = open_catalog_at(tmp_path / "catalog.db", 8)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 8
        assert connection.execute("PRAGMA secure_delete").fetchone()[0] == 1
        assert connection.execute("PRAGMA cache_size").fetchone()[0] == -2000
    finally:
        connection.close()
