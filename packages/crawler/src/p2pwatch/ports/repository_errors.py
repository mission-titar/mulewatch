"""Error contract of the repositories (spec orchestration §4/§7).

PORTS layer: the error CONTRACT the application catches ("a ``RepositoryError`` on an obs is
logged, the cycle continues", spec §7) lives at the port level, NEVER at an adapter —
otherwise the application would depend on an adapter (dependency rule §4). The SQLite adapter
makes its ``PersistenceError`` inherit from ``RepositoryError`` (adapter→port dependency,
allowed). The application only knows ``RepositoryError``.
"""


class RepositoryError(Exception):
    """Persistence failure reported by a repository (the adapter reports, it does not decide)."""
