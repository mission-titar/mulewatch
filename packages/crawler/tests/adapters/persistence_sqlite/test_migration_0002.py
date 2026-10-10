import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from p2pwatch.adapters.persistence_sqlite.connection import open_local


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    local = open_local(tmp_path / "local.db")
    yield local
    local.close()


def test_downloads_has_size_bytes_column(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(downloads)")}
    assert "size_bytes" in columns


def test_user_version_is_at_least_two(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert version >= 2
