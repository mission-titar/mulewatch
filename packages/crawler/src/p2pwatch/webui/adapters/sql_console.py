"""Read-only SQL console execution adapter (monolith-consolidation spec §11).

This is an ADAPTER (an I/O boundary), so unlike the pure domain it MAY use ``sqlite3`` and
``time.monotonic`` directly. It runs an operator-supplied SQL string against a FRESH read-only
connection and returns a structured ``ConsoleOutcome`` (rows + timing, or a single error string).

Why a fresh, dedicated connection per query (not the shared ``ReaderProvider``): the console
sets a PER-QUERY progress handler (the wall-clock abort) and a runaway query is aborted mid-flight;
isolating that on its own connection keeps it from disturbing the reused page-serving readers. The
connection is always closed in a ``finally``.

Three guardrails make this structurally safe against a hostile / clumsy operator (writes are
already impossible, so these are the real risks, spec §11/§12):

- **Read-only** is doubly enforced by ``open_reader`` (OS-level ``mode=ro`` PLUS
  ``PRAGMA query_only=ON``): a write raises ``sqlite3.OperationalError`` ("attempt to write a
  readonly database"), mapped to a clear read-only error.
- **Single statement**: ``Connection.execute`` runs exactly one statement and rejects a second
  ("SELECT 1; SELECT 2") with ``sqlite3.ProgrammingError`` ("You can only execute one statement at
  a time"), mapped to a single-statement error.
- **Wall-clock timeout**: a ``set_progress_handler`` callback fires every ``_PROGRESS_INSTRUCTIONS``
  VM steps and returns non-zero once the elapsed time exceeds the budget, which aborts the query
  with ``sqlite3.OperationalError`` ("interrupted"). A mutable flag records the abort so the handler
  can be distinguished from an unrelated ``OperationalError`` (e.g. a syntax error).
- **Row cap**: at most ``row_cap`` rows are returned; one extra row is fetched to detect (and flag)
  truncation.

**Boundary discipline (E-D13).** Elsewhere in the codebase, in-process 100%-tested code crashes
loudly on failure. This adapter is the deliberate exception: a console query is *arbitrary operator
input*, so a malformed query, a write attempt, a multi-statement input, a runaway query, or any
other ``sqlite3.Error`` is EXPECTED user error and is absorbed into ``ConsoleOutcome.error`` instead
of crashing the request handler. This is a documented boundary absorption, NOT a silent swallow of
an internal bug (which would still surface: a bug in this module's own logic is not a
``sqlite3.Error`` and would propagate).
"""

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from p2pwatch.adapters.persistence_sqlite.reader import open_reader

# Hardcoded safety limits (spec §11). ``ROW_CAP`` / ``TIMEOUT_SECONDS`` are PUBLIC: the module's
# contract, consumed by the webui handler (call bounds + the truncation message). They could later
# move to ``WebuiConfig`` but are NOT wired to config in P7. ``_PROGRESS_INSTRUCTIONS`` is internal
# (the VM-step granularity of the wall-clock check).
ROW_CAP = 1000
TIMEOUT_SECONDS = 5.0
_PROGRESS_INSTRUCTIONS = 1000


@dataclass(frozen=True)
class ConsoleOutcome:
    """The result of running one console query. On success ``error`` is ``None`` and the data
    fields are populated; on any handled failure only ``error`` carries a concise human string
    (never a raw traceback) and the data fields keep their empty defaults."""

    columns: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    row_count: int = 0
    elapsed_ms: int = 0
    truncated: bool = False
    error: str | None = None


def _stringify(value: object) -> str:
    """Render a cell for display: ``None`` becomes the literal ``NULL`` (an empty cell would be
    ambiguous), a BLOB its lowercase hex, everything else its ``str``."""
    if value is None:
        return "NULL"
    return value.hex() if isinstance(value, bytes) else str(value)


def run_query(*, db_path: Path, sql: str, row_cap: int, timeout_seconds: float) -> ConsoleOutcome:
    """Run ``sql`` read-only against ``db_path`` and return a structured ``ConsoleOutcome``.

    See the module docstring for the read-only / single-statement / timeout / row-cap guardrails
    and the deliberate absorption of ``sqlite3.Error`` into ``error``.
    """
    connection = open_reader(db_path)
    try:
        start = time.monotonic()
        aborted = [False]

        def _progress() -> int:
            # Abort (non-zero) once the wall-clock budget is exceeded; record it so the
            # OperationalError handler can tell a timeout abort from a genuine SQL error.
            if time.monotonic() - start > timeout_seconds:
                aborted[0] = True
                return 1
            return 0

        connection.set_progress_handler(_progress, _PROGRESS_INSTRUCTIONS)
        try:
            cursor = connection.execute(sql)
            fetched = cursor.fetchmany(row_cap + 1)
            elapsed_ms = round((time.monotonic() - start) * 1000)
            truncated = len(fetched) > row_cap
            kept = fetched[:row_cap]
            description = cursor.description
            columns = tuple(col[0] for col in description) if description is not None else ()
            rows = tuple(tuple(_stringify(value) for value in row) for row in kept)
            return ConsoleOutcome(
                columns=columns,
                rows=rows,
                row_count=len(rows),
                elapsed_ms=elapsed_ms,
                truncated=truncated,
                error=None,
            )
        except sqlite3.OperationalError as exc:
            if aborted[0]:
                return ConsoleOutcome(
                    error=f"Query aborted: it exceeded the {timeout_seconds:g} second time limit."
                )
            if "readonly" in str(exc):
                return ConsoleOutcome(
                    error="The database is read-only. Only read queries (SELECT) are allowed."
                )
            return ConsoleOutcome(error=f"SQL error: {exc}")
        except sqlite3.ProgrammingError:
            # In this console (no parameter binding) the sole realistic ProgrammingError is the
            # multi-statement guard ("You can only execute one statement at a time").
            return ConsoleOutcome(error="Only a single SQL statement is allowed per query.")
        except sqlite3.Error as exc:
            # Catch-all for any other sqlite3 failure (e.g. a DataError: blob too big). Absorbed
            # into an error result, per the boundary discipline in the module docstring.
            return ConsoleOutcome(error=f"SQL error: {exc}")
    finally:
        connection.close()
