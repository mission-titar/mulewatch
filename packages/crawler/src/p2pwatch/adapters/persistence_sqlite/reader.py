"""Centralized read-only connection management (monolith-consolidation spec §7).

This is the READER side of the persistence adapter, sitting beside the writer's
``connection.py``. The crawler is the single writer (WAL, migrations, ``foreign_keys``);
the webui is strictly read-only. This module owns every read open policy so there is ONE
place that decides how a reader is opened and reused.

``open_reader`` opens a single connection in ``mode=ro`` (an OS-level read-only handle) and
sets ``PRAGMA query_only=ON``: a DOUBLE guard, a write is impossible even if the SQL says
otherwise. It NEVER sets ``journal_mode=WAL``/``foreign_keys``/migrations: those are the
writer's job (``connection.py``); the reader inherits WAL from the single writer, and an
autocommit read holds no persistent read lock, so a long-lived reader does not block the
writer's WAL checkpointing. ``row_factory = sqlite3.Row`` gives the read adapters access by
column name. ``temp_store=FILE``, as the migration runner (stage 1, D8): the temp b-trees the
reads materialize (sorts, ``group_concat``) spill to disk, since an in-memory sort is unbounded.
The ``MEMORY`` hotfix (461b135) answered a tmpfs ``/tmp`` and ``read_only``, gone since 2026-09-16.

``check_same_thread=False`` is DELIBERATE: it lets a central ``quiesce()`` close a
connection from a thread OTHER than the one that opened it (livrable 3's maintenance swap).
Concurrent misuse is prevented structurally by ``ReaderProvider``'s thread-local: each
thread only ever touches its OWN connection, so disabling sqlite3's own same-thread check is
safe here.

``ReaderProvider`` reuses one connection per thread (warming the SQLite page cache instead of
paying today's per-request cold open) and exposes the ``quiesce()`` seam that livrable 3's
maintenance swap needs. ``quiesce()`` assumes readers are DRAINED (no in-flight query); the
maintenance coordination that guarantees this is livrable 3, out of scope here.
"""

import sqlite3
import threading
from pathlib import Path

from p2pwatch.adapters.persistence_sqlite.errors import PersistenceError


def open_reader(path: Path | str) -> sqlite3.Connection:
    """Open ``path`` read-only: ``mode=ro`` + ``query_only`` (double guard), ``Row`` factory,
    ``temp_store=FILE``. See the module docstring for the full rationale."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA temp_store=FILE")
    return connection


class ReaderProvider:
    """A reused, thread-affine read-only connection source with a ``quiesce()`` seam.

    Each calling thread gets its OWN connection, opened on first use and reused thereafter
    (warm page cache). ``quiesce()`` blocks new handouts and closes every connection opened so
    far, safely across threads (``check_same_thread=False``).
    """

    def __init__(self, path: Path | str) -> None:
        self._path = path
        self._local = threading.local()
        self._lock = threading.Lock()
        self._connections: list[sqlite3.Connection] = []
        self._quiesced = False

    def connection(self) -> sqlite3.Connection:
        """Return the calling thread's connection, opening + registering it on first use."""
        if self._quiesced:
            raise PersistenceError("reader is quiesced")
        existing: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if existing is not None:
            return existing
        connection = open_reader(self._path)
        with self._lock:
            self._connections.append(connection)
        self._local.connection = connection
        return connection

    def quiesce(self) -> None:
        """Block new handouts, then close every registered connection. Idempotent."""
        with self._lock:
            self._quiesced = True
            connections = self._connections
            self._connections = []
        for connection in connections:
            connection.close()
