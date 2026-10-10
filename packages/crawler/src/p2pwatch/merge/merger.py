"""``merge_catalogs``: idempotent merge of N ``catalog.db`` into a single output.

Mechanism (merge spec §3/§4): the output is created/opened via ``open_catalog`` (the catalog
migrations, NO duplicated DDL). For each source: we ``ATTACH`` it (outside a transaction),
then INSIDE an explicit transaction (``BEGIN``…``COMMIT``, best-effort ``ROLLBACK`` on error)
we copy its tables in **FK order** (identities first), then ``COMMIT`` and ``DETACH``. A half-copied
source is never committed; a failed merge is re-run safely.

Idempotence (spec §4):
- ``files`` (global content PK) → ``INSERT OR IGNORE`` (first sighting
  wins); NEVER ``OR REPLACE`` (= DELETE + INSERT → collides with the append-only trigger).
- ``observation_variants`` → ``INSERT OR IGNORE`` on its ``content_hash`` (the output
  numbers its own ``variant_id``), then ``observations`` mapped to the output's variant by
  ``content_hash``, ``INSERT OR IGNORE`` on their full key (stage 1, D4).
- ``match_decisions`` (LOCAL ``id``, no global meaning) → explicit columns WITHOUT ``id`` (the
  DB reassigns the ``id``) + dedup by **full natural key** via ``WHERE NOT EXISTS``,
  comparisons with the ``IS`` operator (not ``=``) because some columns are nullable
  (``NULL = NULL`` is false in SQL → re-insertion → not idempotent; ``NULL IS NULL`` is
  true). Re-merging = no-op (each source row already has its exact twin).

The SQL lives in **Python constants** (consistent with ``catalog_repository.py``; no
new ``.sql`` for sqlfluff to lint). We NEVER write to the source (only SELECTs).
"""

import sqlite3
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

from p2pwatch.adapters.persistence_sqlite.connection import open_catalog
from p2pwatch.adapters.persistence_sqlite.errors import PersistenceError
from p2pwatch.merge.errors import MergeError, SchemaVersionMismatchError

# Attach alias of the current source (one at a time → we stay at 1 attached DB
# whatever N, well under the SQLITE_MAX_ATTACHED cap of 10, spec §8).
_SRC = "src"

# --- Content-identity tables: INSERT OR IGNORE, explicit columns (no SELECT *). ---

_COPY_FILES = (
    f"INSERT OR IGNORE INTO main.files (file_id, network, native_id, size_bytes) "
    f"SELECT file_id, network, native_id, size_bytes FROM {_SRC}.files"
)


def _copy_journal(table: str, columns: Sequence[str]) -> str:
    """SQL for one journal copy: explicit columns (without ``id``) + ``IS`` dedup.

    The natural key = ALL journal columns except ``id``. Two dedup levels,
    both at the "full natural key" granularity (§4.2):

    - ``SELECT DISTINCT`` collapses the **source-internal** duplicates in a single pass
      (``WHERE NOT EXISTS`` would not see them: at SELECT time, ``main`` is still
      empty for this source, so two internal twin rows would both pass). ``DISTINCT``
      treats ``NULL`` as equal to ``NULL`` (consistent with ``IS``) and NEVER
      collapses two legitimately distinct rows (they differ on ≥ 1 column).
      This is the "at-least-once" duplicate normalization of a single catalog (§1/§8).
    - ``WHERE NOT EXISTS`` (``IS`` comparisons, NULL-safe) dedups against the **destination**
      (cross-source and re-merge). Re-merge ⇒ 0 insertions.
    """
    projection = ", ".join(columns)
    not_exists = "\n      AND ".join(f"d.{column} IS s.{column}" for column in columns)
    return (
        f"INSERT INTO main.{table} ({projection})\n"
        f"SELECT DISTINCT {projection}\n"
        f"FROM {_SRC}.{table} AS s\n"
        f"WHERE NOT EXISTS (\n"
        f"    SELECT 1 FROM main.{table} AS d\n"
        f"    WHERE {not_exists}\n"
        f")"
    )


_VARIANT_COLUMNS = (
    "file_id, filename, size_bytes, media_length_sec, bitrate_kbps, raw_meta, keyword, node_id,"
    " content_hash"
)

_COPY_VARIANTS = (
    f"INSERT OR IGNORE INTO main.observation_variants ({_VARIANT_COLUMNS}) "
    f"SELECT {_VARIANT_COLUMNS} FROM {_SRC}.observation_variants ORDER BY variant_id"
)

_COPY_OBSERVATIONS = f"""
INSERT OR IGNORE INTO main.observations (variant_id, observed_at, source_count)
SELECT d.variant_id, o.observed_at, o.source_count
FROM {_SRC}.observations AS o
JOIN {_SRC}.observation_variants AS s ON s.variant_id = o.variant_id
JOIN main.observation_variants AS d ON d.content_hash = s.content_hash
"""

_COPY_MATCH_DECISIONS = _copy_journal(
    "match_decisions",
    ("file_id", "target_id", "rule_name", "tier", "decided_at", "node_id"),
)

# MANDATORY FK order (spec §4.3): the identities (files) BEFORE the journals that reference them.
_STATEMENTS = (_COPY_FILES, _COPY_VARIANTS, _COPY_OBSERVATIONS, _COPY_MATCH_DECISIONS)


def merge_catalogs(output: Path, sources: Sequence[Path], *, dest_is_source: bool = False) -> None:
    """Merges ``sources`` into ``output``, idempotent.

    ``output`` is opened via ``open_catalog``: a new file gets the schema + the triggers, an
    existing older catalog is first migrated in place, and the merge APPENDS (never a truncate).

    ``dest_is_source`` (``--into`` mode): the output is itself one of the ``sources``;
    we do not re-attach to ourselves (idempotence guarantees we duplicate nothing there),
    so we skip the source whose path resolves to ``output``.

    Any ``sqlite3.Error`` (corrupt source, incompatible schema, FK…), and ``open_catalog``'s
    ``PersistenceError`` on the output, is wrapped in ``MergeError``; ``ROLLBACK`` is best-effort.

    Before copying a source we check its ``PRAGMA user_version`` equals the current schema
    version (the one ``open_catalog`` just stamped on ``output``) and refuse any mismatch
    with a ``SchemaVersionMismatchError`` (a ``MergeError`` subtype). We never migrate a
    source in place, so an off-version schema could silently mis-copy.
    """
    try:
        connection = open_catalog(output)
    except PersistenceError as error:
        raise MergeError(f"cannot open output {output}: {error}") from error
    try:
        # The current schema version = whatever open_catalog stamped on the output; we read
        # it back rather than hardcode it, so this tracks the migration set automatically.
        expected_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        output_resolved = Path(output).resolve()
        for source in sources:
            if not (dest_is_source and Path(source).resolve() == output_resolved):
                _merge_one(connection, source, expected_version)
    finally:
        connection.close()


def _merge_one(connection: sqlite3.Connection, source: Path, expected_version: int) -> None:
    """Attaches ``source``, copies its tables in ONE transaction, detaches.

    ``ATTACH``/``DETACH`` are OUTSIDE a transaction (SQLite refuses to attach inside an
    open transaction). Right after the attach, BEFORE the ``BEGIN``, we reject any source
    whose schema version differs from ``expected_version`` (nothing is written yet; the
    ``finally`` still detaches). The copy is wrapped by ``BEGIN``/``COMMIT``; an error
    triggers a best-effort ``ROLLBACK`` then a best-effort ``DETACH``, and propagates as
    ``MergeError``: the output keeps no partial copy of this source.
    """
    try:
        # RESOLVED path (consistent with the --into skip that compares ``Path.resolve()``):
        # avoids a self-attach if the same DB is passed under two path forms.
        connection.execute(f"ATTACH DATABASE ? AS {_SRC}", (str(Path(source).resolve()),))
    except sqlite3.Error as error:
        raise MergeError(f"cannot attach source {source}: {error}") from error
    try:
        source_version = int(connection.execute(f"PRAGMA {_SRC}.user_version").fetchone()[0])
        if source_version != expected_version:
            raise SchemaVersionMismatchError(
                f"source {source} has catalog schema version {source_version}, "
                f"expected {expected_version} (the current schema); "
                "merge refuses an out-of-version source (it never migrates a source in place)"
            )
        connection.execute("BEGIN")
        try:
            for statement in _STATEMENTS:
                connection.execute(statement)
            connection.execute("COMMIT")
        except sqlite3.Error as error:
            with suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
            raise MergeError(f"copy of {source} failed: {error}") from error
    finally:
        with suppress(sqlite3.Error):
            connection.execute(f"DETACH DATABASE {_SRC}")
