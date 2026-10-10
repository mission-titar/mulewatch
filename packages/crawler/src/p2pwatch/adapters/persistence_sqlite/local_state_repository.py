"""``SqliteLocalStateRepository``: node identity + backfill policy marker (spec §4/§6).

``node_id`` (spec §3): UUID generated on the first call, persisted in ``node_runtime``
with ``created_at``, stable thereafter (scheduler seed §6 MVP + observation tag).
"""

import sqlite3
import uuid
from contextlib import suppress

from p2pwatch.adapters.persistence_sqlite.connection import Clock, utc_iso, utc_now
from p2pwatch.adapters.persistence_sqlite.errors import wrap_sqlite_errors

_SELECT_NODE_ID = "SELECT value FROM node_runtime WHERE key = 'node_id'"

_INSERT_NODE_IDENTITY = """
INSERT INTO node_runtime (key, value)
VALUES ('node_id', ?), ('created_at', ?)
"""

_SELECT_BACKFILL_POLICY = "SELECT policy_sha256 FROM backfill_state WHERE id = 1"

_UPSERT_BACKFILL_POLICY = """
INSERT INTO backfill_state (id, policy_sha256)
VALUES (1, ?)
ON CONFLICT (id) DO UPDATE SET policy_sha256 = excluded.policy_sha256
"""


class SqliteLocalStateRepository:
    """SQLite implementation of the ``LocalStateRepository`` port (STRUCTURAL satisfaction)."""

    def __init__(self, connection: sqlite3.Connection, *, clock: Clock = utc_now) -> None:
        self._connection = connection
        self._clock = clock

    def node_id(self) -> str:
        """UUID created (and persisted with ``created_at``) on the first call, stable after."""
        with wrap_sqlite_errors():
            row = self._connection.execute(_SELECT_NODE_ID).fetchone()
            if row is not None:
                return str(row[0])
            generated = str(uuid.uuid4())
            # Stamp computed BEFORE the BEGIN (same hygiene as the catalog repo): a
            # buggy clock must not raise in the middle of a transaction.
            created_at = utc_iso(self._clock())
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(_INSERT_NODE_IDENTITY, (generated, created_at))
                self._connection.execute("COMMIT")
            except BaseException:
                # Rollback on BaseException (same discipline as catalog_repository):
                # a NON-sqlite failure must not leave the connection in_transaction —
                # otherwise the repository would be permanently broken.
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                raise
        return generated

    def last_backfill_policy(self) -> str | None:
        """The stored policy fingerprint, or ``None`` if no backfill ever ran (spec §7.1)."""
        with wrap_sqlite_errors():
            row = self._connection.execute(_SELECT_BACKFILL_POLICY).fetchone()
        return str(row[0]) if row is not None else None

    def set_last_backfill_policy(self, sha256: str) -> None:
        """Upserts the single-row marker (called only AFTER a full backfill pass)."""
        with wrap_sqlite_errors():
            self._connection.execute(_UPSERT_BACKFILL_POLICY, (sha256,))
