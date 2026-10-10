import sqlite3
from importlib import resources
from pathlib import Path

import pytest

from p2pwatch.adapters.persistence_sqlite import connection as connection_module
from p2pwatch.adapters.persistence_sqlite.variants import register_functions

_MIGRATIONS = resources.files("p2pwatch.adapters.persistence_sqlite") / "migrations"


def open_catalog_at(path: Path, version: int) -> sqlite3.Connection:
    """A catalog.db migrated by the runner up to ``version`` only, as an older release left it."""
    return _open_at(path, "catalog", version)


def open_local_at(path: Path, version: int) -> sqlite3.Connection:
    """The same for a local.db."""
    return _open_at(path, "local", version)


def _open_at(path: Path, database: str, version: int) -> sqlite3.Connection:
    connection = sqlite3.connect(path, autocommit=True)
    connection_module._configure(connection)
    register_functions(connection)
    scripts = connection_module._load_scripts(_MIGRATIONS / database)
    connection_module._apply_migrations(
        connection, tuple(s for s in scripts if s.version <= version)
    )
    return connection


def forcing_secure_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stands for the image's SQLite, which compiles ``SECURE_DELETE`` in."""
    configure = connection_module._configure

    def configure_then_force(connection: sqlite3.Connection) -> None:
        configure(connection)
        connection.execute("PRAGMA secure_delete = ON")

    monkeypatch.setattr(connection_module, "_configure", configure_then_force)
