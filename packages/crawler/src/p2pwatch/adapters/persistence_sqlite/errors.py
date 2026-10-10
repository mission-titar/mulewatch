"""Persistence adapter error hierarchy (data-model spec §7; orchestration §7).

The adapter SIGNALS, it does not decide (same philosophy as the EC adapter): any
unexpected ``sqlite3.Error`` comes out wrapped as ``PersistenceError``, never bare.
An append-only trigger that fires is a BUG in the caller code, not a business
case → the same ``PersistenceError``. ``wrap_sqlite_errors`` is the SINGLE
wrapper shared by the connection and the two repositories (chained cause kept).

``PersistenceError`` INHERITS from the ``RepositoryError`` port contract (``ports/
repository_errors.py``): the application catches ``RepositoryError`` (orchestration spec §7,
"a failed obs is logged, the cycle continues"), never this adapter class — dependency
rule §4. adapter→port dependency, allowed.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from p2pwatch.ports.repository_errors import RepositoryError


class PersistenceError(RepositoryError):
    """Base of all persistence adapter errors (under the port contract)."""


class MigrationError(PersistenceError):
    """Database newer than the code, or a script that fails (fail-fast, MVP spec §14)."""


@contextmanager
def wrap_sqlite_errors() -> Iterator[None]:
    """Wraps any ``sqlite3.Error`` as ``PersistenceError`` (chained cause)."""
    try:
        yield
    except sqlite3.Error as error:
        raise PersistenceError(str(error)) from error
