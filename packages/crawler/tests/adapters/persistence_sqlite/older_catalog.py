import sqlite3
from importlib import resources
from pathlib import Path

from mulewatch.adapters.persistence_sqlite.connection import (
    _apply_migrations,
    _configure,
    _load_scripts,
)

_CATALOG_MIGRATIONS = (
    resources.files("mulewatch.adapters.persistence_sqlite") / "migrations/catalog"
)


def open_catalog_at(path: Path, version: int) -> sqlite3.Connection:
    """A catalog.db migrated by the runner up to ``version`` only, as an older release left it."""
    connection = sqlite3.connect(path, autocommit=True)
    _configure(connection)
    scripts = _load_scripts(_CATALOG_MIGRATIONS)
    _apply_migrations(connection, tuple(s for s in scripts if s.version <= version))
    return connection
