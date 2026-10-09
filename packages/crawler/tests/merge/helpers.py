"""Shared test helpers for the merge script (real catalogs, never ``:memory:``).

WAL requires a real file (``open_catalog`` rejects ``:memory:``): each helper creates a
``catalog.db`` on disk via ``open_catalog`` (schema + append-only triggers), inserts the given
rows (FK: ``files`` before the journals), closes, and returns the path.
"""

import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from mulewatch.adapters.persistence_sqlite.connection import open_catalog
from tests.catalog_rows import insert_observation

# Columns excluding id, in schema order: for direct INSERTs.
FILE_COLUMNS = ("ed2k_hash", "size_bytes", "aich_hash")
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
    "match_decisions": MATCH_DECISION_COLUMNS,
}

# Natural-key reads; an observation is read with its variant's columns, as the readers do.
_ROWS_BY_TABLE: Mapping[str, str] = {
    "files": "SELECT ed2k_hash, size_bytes, aich_hash FROM files",
    "observations": (
        "SELECT v.ed2k_hash, v.filename, v.size_bytes, v.media_length_sec, v.bitrate_kbps,"
        " v.raw_meta, v.keyword, v.node_id, o.observed_at, o.source_count"
        " FROM observation_variants AS v JOIN observations AS o USING (variant_id)"
    ),
    "match_decisions": f"SELECT {', '.join(MATCH_DECISION_COLUMNS)} FROM match_decisions",
}

# One canonical eD2k hash (32 lowercase hex chars) per letter; satisfies the CHECK on files.
HASH_A = "a" * 32
HASH_B = "b" * 32


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
    path: Path, content: Mapping[str, Sequence[Mapping[str, Any]]] | None = None
) -> Path:
    """Create a real ``catalog.db`` at ``path`` and insert ``content`` (per table, FK order).

    ``content`` maps a table name → rows (dict column→value); an ``observations`` row holds
    ``insert_observation``'s arguments. Returns ``path`` for chaining.
    """
    connection = open_catalog(path)
    try:
        if content is not None:
            insert_rows(connection, "files", content.get("files", ()))
            for row in content.get("observations", ()):
                insert_observation(connection, **row)
            insert_rows(connection, "match_decisions", content.get("match_decisions", ()))
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
    """All rows of ``table`` by their natural key (no local id), sorted."""
    connection = open_catalog(path)
    try:
        cursor = connection.execute(_ROWS_BY_TABLE[table])
        return sorted(cursor.fetchall(), key=lambda row: tuple(str(value) for value in row))
    finally:
        connection.close()


def variant_ids(path: Path) -> list[int]:
    """The (reassigned) ``variant_id`` values, sorted ascending."""
    connection = open_catalog(path)
    try:
        cursor = connection.execute("SELECT variant_id FROM observation_variants ORDER BY 1")
        return [int(row[0]) for row in cursor.fetchall()]
    finally:
        connection.close()
