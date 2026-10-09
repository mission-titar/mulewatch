"""Shared test helpers for the merge script (real catalogs, never ``:memory:``).

WAL requires a real file (``open_catalog`` rejects ``:memory:``): each helper creates a
``catalog.db`` on disk via ``open_catalog`` (schema + append-only triggers), inserts the given
rows by explicit columns, closes, and returns the path. Style aligned with
``tests/adapters/persistence_sqlite/test_append_only.py`` (direct INSERTs, FK: ``files``
before the journals).
"""

import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path

from mulewatch.adapters.persistence_sqlite.connection import open_catalog

# Columns excluding id, in schema order (0001_initial.sql): for direct INSERTs and
# natural-key reads in the assertions.
FILE_COLUMNS = ("ed2k_hash", "size_bytes", "aich_hash")
FILE_OBSERVATION_COLUMNS = (
    "ed2k_hash",
    "filename",
    "size_bytes",
    "source_count",
    "complete_source_count",
    "media_length_sec",
    "bitrate_kbps",
    "codec",
    "file_type",
    "raw_meta",
    "keyword",
    "observed_at",
    "node_id",
)
MATCH_DECISION_COLUMNS = (
    "ed2k_hash",
    "target_id",
    "rule_name",
    "tier",
    "decided_at",
    "node_id",
)

_COLUMNS_BY_TABLE: Mapping[str, Sequence[str]] = {
    "files": FILE_COLUMNS,
    "file_observations": FILE_OBSERVATION_COLUMNS,
    "match_decisions": MATCH_DECISION_COLUMNS,
}

# One canonical eD2k hash (32 lowercase hex chars) per letter; satisfies the CHECK on files.
HASH_A = "a" * 32
HASH_B = "b" * 32
HASH_C = "c" * 32


def hash_for(letter: str) -> str:
    """A canonical 32-char hash repeating ``letter`` (a single hex character)."""
    return letter * 32


def insert_rows(
    connection: sqlite3.Connection, table: str, rows: Sequence[Mapping[str, object]]
) -> None:
    """Direct INSERT of ``rows`` into ``table`` (explicit columns, schema order)."""
    columns = _COLUMNS_BY_TABLE[table]
    placeholders = ", ".join("?" for _ in columns)
    statement = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    for row in rows:
        connection.execute(statement, tuple(row.get(column) for column in columns))


def make_catalog(
    path: Path, content: Mapping[str, Sequence[Mapping[str, object]]] | None = None
) -> Path:
    """Create a real ``catalog.db`` at ``path`` and insert ``content`` (per table, FK order).

    ``content`` maps a table name → rows (dict column→value). We insert in FK order
    (``files`` before the journals) so that references are satisfied. Returns
    ``path`` for chaining.
    """
    connection = open_catalog(path)
    try:
        if content is not None:
            for table in ("files", "file_observations", "match_decisions"):
                rows = content.get(table)
                if rows:
                    insert_rows(connection, table, rows)
    finally:
        connection.close()
    return path


def stamp_user_version(path: Path, version: int) -> None:
    """Force ``path``'s ``PRAGMA user_version`` to ``version`` (simulate an off-schema DB).

    Uses a RAW ``sqlite3`` connection on purpose: ``open_catalog`` would refuse a DB newer
    than the code, and re-running it would re-stamp the current version. We only rewrite the
    header's version counter (the on-disk schema stays whatever ``make_catalog`` laid down),
    which is exactly what the merge guard reads.
    """
    connection = sqlite3.connect(path)
    try:
        connection.execute(f"PRAGMA user_version = {int(version)}")
        connection.commit()
    finally:
        connection.close()


def count(path: Path, table: str) -> int:
    """Number of rows of ``table`` in the catalog ``path``."""
    connection = open_catalog(path)
    try:
        return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
    finally:
        connection.close()


def rows_without_id(path: Path, table: str) -> list[tuple[object, ...]]:
    """All rows of ``table`` (columns excluding ``id``, schema order), sorted."""
    columns = _COLUMNS_BY_TABLE[table]
    connection = open_catalog(path)
    try:
        cursor = connection.execute(f"SELECT {', '.join(columns)} FROM {table}")
        return sorted(cursor.fetchall(), key=lambda row: tuple(str(value) for value in row))
    finally:
        connection.close()


def ids(path: Path, table: str) -> list[int]:
    """The (reassigned) ``id`` values of ``table``, sorted ascending."""
    connection = open_catalog(path)
    try:
        cursor = connection.execute(f"SELECT id FROM {table} ORDER BY id")
        return [int(row[0]) for row in cursor.fetchall()]
    finally:
        connection.close()
