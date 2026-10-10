import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from p2pwatch.adapters.persistence_sqlite.connection import open_local
from p2pwatch.adapters.persistence_sqlite.errors import PersistenceError
from p2pwatch.adapters.persistence_sqlite.local_state_repository import (
    SqliteLocalStateRepository,
)
from p2pwatch.ports.local_state_repository import LocalStateRepository

_START = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
_NODE_ID_QUERY = "SELECT value FROM node_runtime WHERE key = 'node_id'"


class _FakeClock:
    """Injectable frozen clock: zero sleep, zero flakiness (spec §8)."""

    def __init__(self) -> None:
        self.now = _START

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    local = open_local(tmp_path / "local.db")
    yield local
    local.close()


@pytest.fixture
def clock() -> _FakeClock:
    return _FakeClock()


@pytest.fixture
def repository(connection: sqlite3.Connection, clock: _FakeClock) -> SqliteLocalStateRepository:
    return SqliteLocalStateRepository(connection, clock=clock)


# --- node_id (spec §3) ---------------------------------------------------------------


def test_node_id_is_created_on_first_call_and_stable(
    repository: SqliteLocalStateRepository, connection: sqlite3.Connection
) -> None:
    created = repository.node_id()
    assert uuid.UUID(created)  # a REAL UUID, verifiable
    assert repository.node_id() == created  # stable on the second call
    fresh = SqliteLocalStateRepository(connection)  # and for any future instance
    assert fresh.node_id() == created


def test_node_id_persists_created_at_alongside(
    repository: SqliteLocalStateRepository, connection: sqlite3.Connection
) -> None:
    repository.node_id()
    row = connection.execute("SELECT value FROM node_runtime WHERE key = 'created_at'").fetchone()
    assert row == ("2026-06-11T12:00:00.000000+00:00",)


def test_node_id_creation_failure_is_wrapped_and_rolled_back(
    repository: SqliteLocalStateRepository, connection: sqlite3.Connection
) -> None:
    # 'created_at' pre-existing -> the two-row INSERT violates the PK -> full rollback.
    connection.execute("INSERT INTO node_runtime (key, value) VALUES ('created_at', 'already')")
    with pytest.raises(PersistenceError, match="UNIQUE"):
        repository.node_id()
    assert connection.execute(_NODE_ID_QUERY).fetchone() is None


def test_node_id_clock_failure_does_not_wedge_the_connection(
    connection: sqlite3.Connection, clock: _FakeClock
) -> None:
    # BUGGY clock (naive): utc_iso raises ValueError — a NON-sqlite failure must
    # NEVER leave the connection in_transaction (otherwise every subsequent call dies with
    # "cannot start a transaction within a transaction", a misleading diagnostic).
    broken = SqliteLocalStateRepository(connection, clock=lambda: datetime(2026, 6, 11, 12, 0, 0))
    with pytest.raises(ValueError, match="aware"):
        broken.node_id()
    assert connection.in_transaction is False  # healthy connection, no zombie transaction
    assert connection.execute(_NODE_ID_QUERY).fetchone() is None  # nothing half-written
    healthy = SqliteLocalStateRepository(connection, clock=clock)
    assert uuid.UUID(healthy.node_id())  # the SAME connection stays fully usable


# --- port satisfaction ------------------------------------------------------------------


def test_repository_satisfies_the_port_structurally(
    repository: SqliteLocalStateRepository,
) -> None:
    port: LocalStateRepository = repository  # mypy proves structural satisfaction
    assert port.last_backfill_policy() is None


# --- backfill policy marker (spec §7.1 — startup re-evaluation gate) -------------------


def test_last_backfill_policy_is_none_before_any_set(
    repository: SqliteLocalStateRepository,
) -> None:
    assert repository.last_backfill_policy() is None


def test_set_last_backfill_policy_then_read_back(
    repository: SqliteLocalStateRepository,
) -> None:
    repository.set_last_backfill_policy("abc")
    assert repository.last_backfill_policy() == "abc"


def test_set_last_backfill_policy_overwrites_on_a_second_call(
    repository: SqliteLocalStateRepository,
) -> None:
    repository.set_last_backfill_policy("abc")
    repository.set_last_backfill_policy("def")
    assert repository.last_backfill_policy() == "def"
