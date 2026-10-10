import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from p2pwatch.adapters.persistence_sqlite.connection import open_local
from p2pwatch.adapters.persistence_sqlite.errors import PersistenceError
from p2pwatch.adapters.persistence_sqlite.scheduler_state_repository import (
    SqliteSchedulerStateRepository,
)
from p2pwatch.ports.scheduler_state_repository import ChannelBackoff

_BACKOFF = {
    "amule-1:kad": ChannelBackoff(attempts=2, retry_after="2026-06-12T10:05:00.000000+00:00"),
    "amule-1": ChannelBackoff(attempts=1, retry_after="2026-06-12T10:02:00.000000+00:00"),
}


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    local = open_local(tmp_path / "local.db")
    yield local
    local.close()


@pytest.fixture
def repository(connection: sqlite3.Connection) -> SqliteSchedulerStateRepository:
    return SqliteSchedulerStateRepository(connection)


def test_load_channel_backoff_is_empty_on_a_fresh_database(
    repository: SqliteSchedulerStateRepository,
) -> None:
    assert repository.load_channel_backoff() == {}


def test_channel_backoff_round_trips_through_a_new_repo_instance(
    repository: SqliteSchedulerStateRepository, connection: sqlite3.Connection
) -> None:
    # Save → NEW repo instance (same DB) → reload → identical: that's
    # SURVIVAL ACROSS RESTART (spec §3/§7). A new instance has no in-memory state.
    repository.save_channel_backoff(_BACKOFF)
    reborn = SqliteSchedulerStateRepository(connection)
    assert reborn.load_channel_backoff() == _BACKOFF


def test_save_channel_backoff_replaces_the_whole_map(
    repository: SqliteSchedulerStateRepository,
) -> None:
    repository.save_channel_backoff(_BACKOFF)
    repository.save_channel_backoff({})  # empty snapshot → replaces everything
    assert repository.load_channel_backoff() == {}


def test_save_channel_backoff_is_atomic_on_injected_failure(
    repository: SqliteSchedulerStateRepository, connection: sqlite3.Connection
) -> None:
    connection.execute(
        "CREATE TRIGGER boom BEFORE INSERT ON scheduler_state"
        " WHEN NEW.key = 'channel_backoff'"
        " BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    with pytest.raises(PersistenceError, match="injected failure"):
        repository.save_channel_backoff(_BACKOFF)
    assert repository.load_channel_backoff() == {}
