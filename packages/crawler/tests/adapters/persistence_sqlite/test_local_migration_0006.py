"""Local 0006 deletes the cycle's scheduler state, the backoff map with it (stage 2, D7, D19)."""

import sqlite3
from importlib import resources
from pathlib import Path

from tests.adapters.persistence_sqlite.older_catalog import open_local_at

_LOCAL_MIGRATIONS = resources.files("p2pwatch.adapters.persistence_sqlite") / "migrations/local"


def _write_local_db_at_version_5(path: Path) -> None:
    connection = sqlite3.connect(path, autocommit=True)
    for entry in sorted(_LOCAL_MIGRATIONS.iterdir(), key=lambda item: item.name):
        if int(entry.name.partition("_")[0]) <= 5:
            connection.executescript(entry.read_text(encoding="utf-8"))
    connection.execute("PRAGMA user_version = 5")
    connection.executemany(
        "INSERT INTO scheduler_state (key, value) VALUES (?, ?)",
        [
            ("cycle_index", "4120"),
            ("last_full_cycle_at", "2026-10-09T00:00:00.000000+00:00"),
            ("channel_backoff", '{"amuled:global": {"attempts": 3}}'),
        ],
    )
    connection.close()


def test_0006_deletes_the_cycle_state_and_the_backoff_map(tmp_path: Path) -> None:
    path = tmp_path / "local.db"
    _write_local_db_at_version_5(path)

    connection = open_local_at(path, 6)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
        assert connection.execute("SELECT key FROM scheduler_state").fetchall() == []
    finally:
        connection.close()
