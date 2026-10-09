"""Append-only ENFORCED BY THE DATABASE (spec data-model §3): a SCHEMA property, not the code's.

Every table in ``catalog.db`` carries a ``BEFORE UPDATE`` and a ``BEFORE DELETE`` trigger
→ ``RAISE(ABORT, '<table> is append-only')``. Verified here by DIRECT UPDATE/DELETE on
the connection (as a third-party tool would): the violation surfaces as
``sqlite3.IntegrityError`` (the REAL class observed, SQLite 3.47.1).
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from mulewatch.adapters.persistence_sqlite.connection import open_catalog

# Canonical 32-char lowercase hex hash (satisfies the CHECK constraint on files.ed2k_hash).
_HASH = "a" * 32

# One row per table (FKs respected: files first) + the UPDATE that MUST fail.
_SEED = (
    f"INSERT INTO files (ed2k_hash, size_bytes) VALUES ('{_HASH}', 1)",
    f"INSERT INTO file_observations (ed2k_hash, filename, size_bytes, source_count,"
    f" complete_source_count, raw_meta, keyword, observed_at, node_id)"
    f" VALUES ('{_HASH}', 'f', 1, 0, 0, '[]', 'k', 't', 'n')",
    f"INSERT INTO match_decisions (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
    f" VALUES ('{_HASH}', '062A', 'r', 'download', 't', 'n')",
)

_UPDATES = {
    "files": "UPDATE files SET size_bytes = 2",
    "file_observations": "UPDATE file_observations SET filename = 'autre'",
    "match_decisions": "UPDATE match_decisions SET tier = 'notify'",
}


@pytest.fixture
def seeded_catalog(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_catalog(tmp_path / "catalog.db")
    for statement in _SEED:
        connection.execute(statement)
    yield connection
    connection.close()


@pytest.mark.parametrize("table", sorted(_UPDATES))
def test_direct_update_is_rejected_by_the_database(
    seeded_catalog: sqlite3.Connection, table: str
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match=f"{table} is append-only"):
        seeded_catalog.execute(_UPDATES[table])


@pytest.mark.parametrize("table", sorted(_UPDATES))
def test_direct_delete_is_rejected_by_the_database(
    seeded_catalog: sqlite3.Connection, table: str
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match=f"{table} is append-only"):
        seeded_catalog.execute(f"DELETE FROM {table}")


def test_insert_remains_allowed_on_every_table(seeded_catalog: sqlite3.Connection) -> None:
    # Append-only = you can ALWAYS add (the seed INSERT already succeeded);
    # here we prove a SECOND insert passes too (the triggers only block U/D).
    _hash2 = "b" * 32
    seeded_catalog.execute(f"INSERT INTO files (ed2k_hash, size_bytes) VALUES ('{_hash2}', 2)")
    assert seeded_catalog.execute("SELECT count(*) FROM files").fetchone()[0] == 2
