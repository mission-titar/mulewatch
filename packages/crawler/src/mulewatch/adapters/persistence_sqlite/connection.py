"""SQLite connection + migration runner (data-model spec §3/§4/§7).

Each connection is opened in REAL autocommit (``autocommit=True``, Python ≥ 3.12):
transactions are EXPLICIT (``BEGIN``/``COMMIT``/``ROLLBACK`` written by the
repositories), no implicit isolation. Opening PRAGMAs (spec §3):
``journal_mode=WAL`` - REQUIRED: ``:memory:`` does not carry it (it answers ``memory``)
and is therefore refused outright; the tests use real files (spec §8) -
``foreign_keys=ON``, and ``recursive_triggers=ON`` (without which ``INSERT OR REPLACE``
crosses the append-only triggers, spec §3 post-review amendment).

The migration runner applies the ``NNNN_*.sql`` scripts embedded in the package and tracks
them in ``PRAGMA user_version`` (see ``_apply_migrations``).

This module also carries the repositories' shared clock (``Clock``/``utc_now``/
``utc_iso``): ISO-8601 UTC as TEXT (spec §3), FIXED microseconds so that lexicographic
order IS chronological order (the FIFO claim sorts on ``enqueued_at``).
"""

import sqlite3
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from importlib.resources.abc import Traversable
from itertools import pairwise
from pathlib import Path

from mulewatch.adapters.persistence_sqlite.errors import (
    MigrationError,
    PersistenceError,
    wrap_sqlite_errors,
)
from mulewatch.adapters.persistence_sqlite.variants import register_functions

type Clock = Callable[[], datetime]

_MIGRATIONS = resources.files("mulewatch.adapters.persistence_sqlite") / "migrations"

_DIRECTIVE = "-- migration:"
_NO_TRANSACTION = "-- migration: no-transaction"
# Index = the value ``PRAGMA secure_delete`` reads; ``= 2`` would set ON, not FAST.
_SECURE_DELETE_MODES = ("OFF", "ON", "FAST")


@dataclass(frozen=True)
class Migration:
    version: int
    script: str
    transactional: bool = True


def utc_now() -> datetime:
    """Default clock for the repositories (spec §3: injectable, ``datetime.now(UTC)``)."""
    return datetime.now(UTC)


def utc_iso(moment: datetime) -> str:
    """Fixed-width ISO-8601 UTC (microseconds ALWAYS written), e.g.
    ``2026-06-11T12:00:00.000000+00:00``. ``moment`` must be AWARE (``Clock``
    contract, ENFORCED: naive → ``ValueError``); a non-UTC zone is normalized,
    never stored as-is."""
    if moment.tzinfo is None:
        raise ValueError("utc_iso exige un datetime aware (contrat de Clock)")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def open_catalog(path: Path | str) -> sqlite3.Connection:
    """Opens/migrates ``catalog.db`` (the append-only triggers are part of the schema)."""
    return _open(path, _MIGRATIONS / "catalog", register_functions)


def open_local(path: Path | str) -> sqlite3.Connection:
    """Opens/migrates ``local.db``."""
    return _open(path, _MIGRATIONS / "local")


def _open(
    path: Path | str,
    scripts_dir: Traversable,
    register: Callable[[sqlite3.Connection], None] | None = None,
) -> sqlite3.Connection:
    with wrap_sqlite_errors():
        connection = sqlite3.connect(path, autocommit=True)
    try:
        with wrap_sqlite_errors():
            _configure(connection)
            if register is not None:
                register(connection)
            _apply_migrations(connection, _load_scripts(scripts_dir))
    except BaseException:
        # Unconditional close: a NON-sqlite error (e.g. OSError from iterdir) must not
        # leak the connection; it then propagates as-is.
        connection.close()
        raise
    return connection


def _configure(connection: sqlite3.Connection) -> None:
    journal_mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if journal_mode != "wal":
        raise PersistenceError(
            f"journal_mode={journal_mode!r}: WAL required (spec §3), file-backed db only"
        )
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA recursive_triggers=ON")


def _load_scripts(directory: Traversable) -> tuple[Migration, ...]:
    """The ``NNNN_*.sql`` scripts by name; a bad name, a misordered version or an unknown
    directive raises ``MigrationError`` (non-``.sql`` files are ignored, gaps allowed)."""
    scripts: list[Migration] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if not entry.name.endswith(".sql"):
            continue
        prefix = entry.name.partition("_")[0]
        if not prefix.isdigit():
            raise MigrationError(f"invalid script name (NNNN_*.sql expected): {entry.name}")
        script = entry.read_text(encoding="utf-8")
        scripts.append(Migration(int(prefix), script, _is_transactional(entry.name, script)))
    for left, right in pairwise(script.version for script in scripts):
        if right <= left:
            raise MigrationError(
                f"migration versions not strictly increasing: {left} then {right} "
                "(unique zero-padded NNNN prefixes required)"
            )
    return tuple(scripts)


def _is_transactional(name: str, script: str) -> bool:
    first_line = script.partition("\n")[0]
    if first_line == _NO_TRANSACTION:
        return False
    if first_line.startswith(_DIRECTIVE):
        raise MigrationError(f"{name}: unknown directive {first_line!r}, only {_NO_TRANSACTION!r}")
    return True


def _apply_migrations(connection: sqlite3.Connection, scripts: tuple[Migration, ...]) -> None:
    """Applies the scripts above ``user_version`` in order, each stamped once it succeeds; the
    rules for writing one: ``docs/contributing/architecture.md``, "Écrire une migration"."""
    current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    latest = scripts[-1].version if scripts else 0
    if current > latest:
        raise MigrationError(
            f"db at version {current}, code at version {latest}: "
            "db newer than the code, refusing to start (spec §3)"
        )
    # On disk: an in-memory sort is unbounded (+697 MB for one index at 11.5M rows).
    connection.execute("PRAGMA temp_store=FILE")
    try:
        for migration in scripts:
            if migration.version > current:
                _run_restoring_pragmas(connection, migration)
    finally:
        connection.execute("PRAGMA temp_store=DEFAULT")


def _run_restoring_pragmas(connection: sqlite3.Connection, migration: Migration) -> None:
    secure_delete = int(connection.execute("PRAGMA secure_delete").fetchone()[0])
    cache_size = int(connection.execute("PRAGMA cache_size").fetchone()[0])
    try:
        if migration.transactional:
            _run_in_transaction(connection, migration)
        else:
            _run_outside_transaction(connection, migration)
    finally:
        connection.execute(f"PRAGMA secure_delete = {_SECURE_DELETE_MODES[secure_delete]}")
        connection.execute(f"PRAGMA cache_size = {cache_size}")


def _run_in_transaction(connection: sqlite3.Connection, migration: Migration) -> None:
    # Two concurrent runners: the loser fails cleanly, single writer by doctrine (spec §3).
    try:
        connection.execute("BEGIN")
        connection.executescript(migration.script)
        if not connection.in_transaction:
            raise MigrationError(
                f"migration {migration.version}: the script closed the runner's transaction "
                "(COMMIT/ROLLBACK forbidden inside a migration script)"
            )
        connection.execute(f"PRAGMA user_version = {migration.version}")
        connection.execute("COMMIT")
    except sqlite3.Error as error:
        with suppress(sqlite3.Error):
            connection.execute("ROLLBACK")
        raise MigrationError(f"migration {migration.version} failed: {error}") from error


def _run_outside_transaction(connection: sqlite3.Connection, migration: Migration) -> None:
    # A stop between the script and the stamp replays the script: it must be safe to replay.
    try:
        connection.executescript(migration.script)
        connection.execute(f"PRAGMA user_version = {migration.version}")
    except sqlite3.Error as error:
        raise MigrationError(f"migration {migration.version} failed: {error}") from error
