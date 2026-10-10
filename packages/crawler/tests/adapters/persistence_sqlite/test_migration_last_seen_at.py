"""TDD test for local 0005, which adds ``downloads.last_seen_at`` (lost-download TTL spec §3).

The backfill is load-bearing: without it every pre-existing row starts with a NULL
``last_seen_at``, and ``expire_lost`` would never reach it (``NULL < ?`` is NULL, never true),
so a download lost before the upgrade would stay ``downloading`` forever.
"""

import sqlite3
from importlib import resources
from pathlib import Path

from tests.adapters.persistence_sqlite.older_catalog import open_local_at

_LOCAL_MIGRATIONS = resources.files("mulewatch.adapters.persistence_sqlite") / "migrations/local"
_QUEUED_AT = "2026-09-01T00:00:00.000000+00:00"


def _write_local_db_at_version_4(path: Path) -> None:
    """A local.db stamped at version 4: downloads present, 0005 not applied yet."""
    connection = sqlite3.connect(path, autocommit=True)
    connection.execute("PRAGMA journal_mode=WAL")
    for entry in sorted(_LOCAL_MIGRATIONS.iterdir(), key=lambda item: item.name):
        if int(entry.name.partition("_")[0]) <= 4:
            connection.executescript(entry.read_text(encoding="utf-8"))
    connection.execute("PRAGMA user_version = 4")
    connection.execute(
        "INSERT INTO downloads (ed2k_hash, target_id, state, queued_at, size_bytes) "
        f"VALUES ('a1', '062A', 'downloading', '{_QUEUED_AT}', 42)"
    )
    connection.close()


def test_backfills_last_seen_at_with_queued_at(tmp_path: Path) -> None:
    path = tmp_path / "local.db"
    _write_local_db_at_version_4(path)
    connection = open_local_at(path, 5)
    try:
        row = connection.execute("SELECT last_seen_at FROM downloads WHERE ed2k_hash = 'a1'")
        assert row.fetchone()[0] == _QUEUED_AT
    finally:
        connection.close()
