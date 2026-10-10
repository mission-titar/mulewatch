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

from p2pwatch.adapters.persistence_sqlite.connection import open_catalog
from tests.catalog_rows import insert_decision, insert_file, insert_observation

# Canonical 32-char lowercase hex hash (satisfies the eD2k CHECK on files.native_id).
_HASH = "a" * 32

# The UPDATE that MUST fail, per table.
_UPDATES = {
    "files": "UPDATE files SET size_bytes = 2",
    "observation_variants": "UPDATE observation_variants SET filename = 'autre'",
    "observations": "UPDATE observations SET source_count = 2",
    "match_decisions": "UPDATE match_decisions SET tier = 'notify'",
}


@pytest.fixture
def seeded_catalog(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_catalog(tmp_path / "catalog.db")
    insert_file(connection, _HASH, 1)
    insert_observation(connection, _HASH, "f")
    insert_decision(connection, _HASH, "062A", "download")
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
    insert_file(seeded_catalog, "b" * 32, 2)
    assert seeded_catalog.execute("SELECT count(*) FROM files").fetchone()[0] == 2
