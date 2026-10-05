"""``merge_catalogs``: idempotent merge of N ``catalog.db`` into a single output.

Mechanism (merge spec §3/§4): the output is created/opened via ``open_catalog`` (schema
+ append-only triggers, migration ``0001`` — NO duplicated DDL). Two passes over the sources:
first every source's ``files`` and compacted ranges, then the rest, minus the raw observations a
range of the output already counts (``covered_by_range``, spec 2026-10-05 §4), so a day is never
counted twice whatever the source order. For each source and pass: we ``ATTACH`` it (outside a
transaction), then INSIDE an explicit transaction (``BEGIN``…``COMMIT``, best-effort
``ROLLBACK`` on error) we copy its tables in **FK order** (identities first), then ``COMMIT``
and ``DETACH``. A half-copied pass is never committed; a failed merge is re-run safely.

Idempotence (spec §4):
- ``files``/``sources`` (global content PK) → ``INSERT OR IGNORE`` (first sighting
  wins); NEVER ``OR REPLACE`` (= DELETE + INSERT → collides with the append-only trigger).
- the 5 journals (LOCAL ``id``, no global meaning) → explicit columns WITHOUT ``id`` (the
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

from mulewatch.adapters.persistence_sqlite.connection import open_catalog
from mulewatch.compact.compactor import covered_by_range
from mulewatch.merge.errors import MergeError, SchemaVersionMismatchError

# Attach alias of the current source (one at a time → we stay at 1 attached DB
# whatever N, well under the SQLITE_MAX_ATTACHED cap of 10, spec §8).
_SRC = "src"

# --- Content-identity tables: INSERT OR IGNORE, explicit columns (no SELECT *). ---

_COPY_FILES = (
    f"INSERT OR IGNORE INTO main.files (ed2k_hash, size_bytes, aich_hash) "
    f"SELECT ed2k_hash, size_bytes, aich_hash FROM {_SRC}.files"
)

_COPY_SOURCES = (
    f"INSERT OR IGNORE INTO main.sources (user_hash, client_name, client_version) "
    f"SELECT user_hash, client_name, client_version FROM {_SRC}.sources"
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


_COVERED = covered_by_range("main.file_observation_ranges", "s")

# The journal copy, its WHERE NOT EXISTS narrowed to the raw rows no range of the output counts.
_COPY_FILE_OBSERVATIONS = (
    _copy_journal(
        "file_observations",
        (
            "ed2k_hash",
            "filename",
            "size_bytes",
            "source_count",
            "complete_source_count",
            "media_length_sec",
            "bitrate_kbps",
            "codec",
            "file_type",
            "raw_meta",
            "keyword",
            "observed_at",
            "node_id",
        ),
    )
    + f"\nAND NOT {_COVERED}"
)

_COPY_SOURCE_OBSERVATIONS = _copy_journal(
    "source_observations",
    (
        "user_hash",
        "ed2k_hash",
        "ip",
        "port",
        "nickname",
        "client_name",
        "client_version",
        "country",
        "id_type",
        "has_complete_file",
        "origin",
        "raw_meta",
        "observed_at",
        "node_id",
    ),
)

_COPY_MATCH_DECISIONS = _copy_journal(
    "match_decisions",
    ("ed2k_hash", "target_id", "rule_name", "tier", "decided_at", "node_id"),
)

_COPY_FILE_OBSERVATION_RANGES = _copy_journal(
    "file_observation_ranges",
    (
        "ed2k_hash",
        "bucket",
        "filenames",
        "node_ids",
        "observation_count",
        "first_observed_at",
        "last_observed_at",
        "source_count_min",
        "source_count_max",
        "source_count_sum",
        "complete_source_count_min",
        "complete_source_count_max",
        "complete_source_count_sum",
    ),
)

# The raw rows a range of the output already counts: not copied, only counted.
_COUNT_COVERED = f"SELECT COUNT(*) FROM {_SRC}.file_observations AS s WHERE {_COVERED}"

# MANDATORY FK order (spec §4.3): identities (files, sources) BEFORE the journals that
# reference them. Ranges go first, for every source, so the raw copy can skip what they count.
_RANGES_PASS = (_COPY_FILES, _COPY_FILE_OBSERVATION_RANGES)
_REST_PASS = (
    _COPY_SOURCES,
    _COPY_FILE_OBSERVATIONS,
    _COPY_SOURCE_OBSERVATIONS,
    _COPY_MATCH_DECISIONS,
)


def merge_catalogs(output: Path, sources: Sequence[Path], *, dest_is_source: bool = False) -> int:
    """Merges ``sources`` into ``output``, idempotent; returns the covered raw rows not copied.

    ``output`` is opened via ``open_catalog``: if it's new, the ``0001`` migration lays
    down the schema + the triggers; if it already exists as a valid ``catalog.db``, no
    migration is replayed and the merge APPENDS into it (never a truncate).

    ``dest_is_source`` (``--into`` mode): the output is itself one of the ``sources``;
    we do not re-attach to ourselves (idempotence guarantees we duplicate nothing there),
    so we skip the source whose path resolves to ``output``.

    Any ``sqlite3.Error`` (corrupt source, incompatible schema, FK…) is wrapped in
    ``MergeError`` (fail-fast, clear message). The ``ROLLBACK`` is best-effort.

    Before copying a source we check its ``PRAGMA user_version`` equals the current schema
    version (the one ``open_catalog`` just stamped on ``output``) and refuse any mismatch
    with a ``SchemaVersionMismatchError`` (a ``MergeError`` subtype). We never migrate a
    source in place, so an off-version schema could silently mis-copy.
    """
    connection = open_catalog(output)
    try:
        # The current schema version = whatever open_catalog stamped on the output; we read
        # it back rather than hardcode it, so this tracks the migration set automatically.
        expected_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        output_resolved = Path(output).resolve()
        others = [
            s for s in sources if not (dest_is_source and Path(s).resolve() == output_resolved)
        ]
        for source in others:
            _merge_one(connection, source, expected_version, _RANGES_PASS)
        return sum(_merge_one(connection, s, expected_version, _REST_PASS) for s in others)
    finally:
        connection.close()


def _merge_one(
    connection: sqlite3.Connection, source: Path, expected_version: int, statements: Sequence[str]
) -> int:
    """Attaches ``source``, runs ``statements`` in ONE transaction, detaches; returns the
    source's raw rows a range already counts when ``statements`` copies the raw table, else 0.

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
            for statement in statements:
                connection.execute(statement)
            covered = 0
            if _COPY_FILE_OBSERVATIONS in statements:
                covered = int(connection.execute(_COUNT_COVERED).fetchone()[0])
            connection.execute("COMMIT")
        except sqlite3.Error as error:
            with suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
            raise MergeError(f"copy of {source} failed: {error}") from error
        return covered
    finally:
        with suppress(sqlite3.Error):
            connection.execute(f"DETACH DATABASE {_SRC}")
