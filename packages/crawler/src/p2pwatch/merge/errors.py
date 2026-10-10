"""``MergeError``: usage or merge error (clear message for the CLI, never bare).

The merge is a standalone operator tool (merge spec §2): it does not depend on the
repositories' error contract. ``MergeError`` is its own exception (``ValueError`` style:
a readable message that ``__main__`` prints on ``stderr`` with a non-zero exit code).
"""


class MergeError(Exception):
    """Invalid usage or a copy that fails (fail-fast, clear message for the operator)."""


class SchemaVersionMismatchError(MergeError):
    """A source's ``PRAGMA user_version`` differs from the current catalog schema.

    The merge only ever stamps the OUTPUT to the current schema (via ``open_catalog``);
    it never migrates a source in place. Copying rows out of a source whose schema is
    older (e.g. a ``0001``/``0002`` DB) or newer (a future ``0004``) could silently
    mis-copy or fail with an obscure SQL error, so we refuse it up front. A
    ``MergeError`` subtype: the CLI prints it on ``stderr`` with a non-zero exit code.
    """
