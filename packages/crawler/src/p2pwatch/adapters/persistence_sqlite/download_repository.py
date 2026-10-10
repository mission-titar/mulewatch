"""``SqliteDownloadRepository``: the state of downloads (local.db, download spec §7).

Implements persistence of the downloads managed by the crawler. ``downloads`` is NOT
append-only (mutable state, not the catalog) → UPSERT/UPDATE allowed, no triggers. Same
disciplines as the other repos (data-model spec §7): timestamp stamped BEFORE ``BEGIN``,
``BEGIN IMMEDIATE`` + rollback on ``BaseException`` (a NON-sqlite failure does not leave the
connection ``in_transaction``), ``wrap_sqlite_errors``.

Methods take a ``FileKey``; rows are keyed by its ``file_id`` (stage 2 spec D19).
``record_queued`` is dedup-safe (PK = ``file_id``, ``ON CONFLICT DO NOTHING``); ``set_state``
stamps ``completed_at`` on completion (injected clock); ``mark_seen``/``expire_lost`` carry
the lost-download TTL (2026-09-13 spec §2) and the lifecycle columns (stage 2 spec D12);
``active_states`` returns the file→state map (the loop's monitor reconciles against it).
"""

import sqlite3
from collections.abc import Iterable
from contextlib import suppress
from datetime import timedelta

from p2pwatch.adapters.persistence_sqlite.connection import Clock, utc_iso, utc_now
from p2pwatch.adapters.persistence_sqlite.errors import PersistenceError, wrap_sqlite_errors
from p2pwatch.domain.download.states import DownloadState
from p2pwatch.domain.file_key import FileKey, Network
from p2pwatch.ports.download_client import DownloadStatus, FailureReason

_INSERT = """
INSERT INTO downloads
    (file_id, network, native_id, target_id, state, queued_at, size_bytes, last_seen_at,
     bytes_done)
VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, 0)
ON CONFLICT (file_id) DO NOTHING
"""

_SET_STATE = "UPDATE downloads SET state = ?, failure_reason = ? WHERE file_id = ?"

_SET_STATE_COMPLETED = """
UPDATE downloads SET state = ?, failure_reason = NULL, completed_at = ? WHERE file_id = ?
"""

_IS_DOWNLOADED = "SELECT 1 FROM downloads WHERE file_id = ?"

_ACTIVE_STATES = "SELECT network, native_id, state FROM downloads"

_GET_TARGET_ID = "SELECT target_id FROM downloads WHERE file_id = ?"

# Every right-hand side reads the row as it was, so the CASE compares against the old bytes.
_MARK_SEEN = """
UPDATE downloads SET
    last_seen_at = :now,
    last_progress_at = CASE WHEN :bytes_done > bytes_done THEN :now ELSE last_progress_at END,
    bytes_done = :bytes_done,
    waiting_reason = :waiting_reason
WHERE file_id = :file_id
"""

# The non-terminal states listed here MUST stay synchronized with _TERMINAL_STATES (states.py).
_EXPIRE_LOST = """
UPDATE downloads SET state = 'failed', failure_reason = 'lost'
WHERE state IN ('queued', 'downloading') AND last_seen_at < ?
RETURNING network, native_id
"""


class SqliteDownloadRepository:
    """SQLite implementation of download persistence (STRUCTURAL satisfaction)."""

    def __init__(self, connection: sqlite3.Connection, *, clock: Clock = utc_now) -> None:
        self._connection = connection
        self._clock = clock

    def record_queued(self, file: FileKey, target_id: str, size_bytes: int) -> bool:
        """INSERT of a ``queued`` download (dedup-safe). ``True`` if new, ``False`` if duplicate."""
        queued_at = utc_iso(self._clock())
        with wrap_sqlite_errors():
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._connection.execute(
                    _INSERT,
                    (
                        file.file_id,
                        file.network,
                        file.native_id,
                        target_id,
                        queued_at,
                        size_bytes,
                        queued_at,
                    ),
                )
                self._connection.execute("COMMIT")
            except BaseException:
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                raise
        return cursor.rowcount == 1

    def set_state(
        self, file: FileKey, state: DownloadState, failure_reason: FailureReason | None = None
    ) -> None:
        """UPDATE the state and its ``failure_reason``, which any other call clears.

        Requires an existing download (an unknown file → ``PersistenceError``: caller-code bug).
        Only ``completed`` is timestamped; ``failed`` does not overwrite the ``completed_at``.
        """
        with wrap_sqlite_errors():
            if state == DownloadState.COMPLETED:
                cursor = self._connection.execute(
                    _SET_STATE_COMPLETED, (state.value, utc_iso(self._clock()), file.file_id)
                )
            else:
                cursor = self._connection.execute(
                    _SET_STATE, (state.value, failure_reason, file.file_id)
                )
        if cursor.rowcount != 1:
            raise PersistenceError(f"download {file.native_id} not found (caller bug)")

    def is_downloaded(self, file: FileKey) -> bool:
        """``True`` if this file is already known to ``downloads`` (dedup, spec §6)."""
        with wrap_sqlite_errors():
            row = self._connection.execute(_IS_DOWNLOADED, (file.file_id,)).fetchone()
        return row is not None

    def mark_seen(self, statuses: Iterable[DownloadStatus]) -> None:
        """Stamps ``last_seen_at`` and the progress of each download the client lists.

        ``last_progress_at`` moves only when ``bytes_done`` grows. An unknown file updates
        nothing (a shared file the crawler never queued): no error.
        """
        seen_at = utc_iso(self._clock())
        with wrap_sqlite_errors():
            self._connection.executemany(
                _MARK_SEEN,
                [
                    {
                        "now": seen_at,
                        "bytes_done": status.bytes_done,
                        "waiting_reason": status.waiting_reason,
                        "file_id": status.file.file_id,
                    }
                    for status in statuses
                ],
            )

    def expire_lost(self, max_age_seconds: float) -> tuple[FileKey, ...]:
        """Fails the non-terminal downloads amuled has not shown for ``max_age_seconds``.

        Returns the files it failed, for the caller to log. An entry stays in amuled's queue
        even with zero sources, so absence is a strong signal: the entry was removed, or the
        file completed and was moved out of IncomingDir before the next poll.
        """
        cutoff = utc_iso(self._clock() - timedelta(seconds=max_age_seconds))
        with wrap_sqlite_errors():
            rows = self._connection.execute(_EXPIRE_LOST, (cutoff,)).fetchall()
        return tuple(FileKey(Network(row[0]), row[1]) for row in rows)

    def active_states(self) -> dict[FileKey, DownloadState]:
        """File→state map of ALL known downloads (the monitor reconciles against it)."""
        with wrap_sqlite_errors():
            rows = self._connection.execute(_ACTIVE_STATES).fetchall()
        return {FileKey(Network(row[0]), row[1]): DownloadState(row[2]) for row in rows}

    def get_target_id(self, file: FileKey) -> str | None:
        """``target_id`` of a downloaded file, or ``None`` (never queued) — READ.

        The download loop uses it to label the completion notification; ``None`` is a normal
        case (a shared file the crawler never queued), reported as ``unknown``.
        """
        with wrap_sqlite_errors():
            row = self._connection.execute(_GET_TARGET_ID, (file.file_id,)).fetchone()
        if row is None:
            return None
        return str(row[0])
