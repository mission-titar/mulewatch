"""`compact_catalog`: compacts a catalog.db into a NEW output (compaction spec §5).

Rebuild via open_catalog (schema + triggers, ALL catalog migrations applied
dynamically — we do NOT freeze the numbers, to avoid drift on the next addition). ATTACH
of the source
(outside a transaction — SQLite refuses to attach inside a transaction), then INSIDE an explicit
transaction (BEGIN…COMMIT, best-effort ROLLBACK): verbatim copy of the 4 intact tables (FK order),
verbatim copy of the RECENT raw (observed_at >= cutoff_date), bucketize of the OLD raw
(observed_at < cutoff_date). COMMIT then DETACH (outside a transaction). We NEVER write to the
source (only SELECTs). The output is assumed NEW (the CLI guarantees it) → no dedup, except that a
raw row a source range already counts (``sightings.covered_by_range``) is dropped from both raw
paths.

UTC-DAY-aligned cutoff (spec §5bis): cutoff_date is a "YYYY-MM-DD" DATE; "old" ⟺
observed_at < cutoff_date — the lexicographic comparison puts every timestamp of the cutoff day
on the recent side ("2026-06-01" < "2026-06-01T.."). A day is only compacted in full.
"""

import sqlite3
from contextlib import suppress
from datetime import timedelta
from pathlib import Path

from mulewatch.adapters.persistence_sqlite.connection import Clock, open_catalog, utc_now
from mulewatch.adapters.persistence_sqlite.sightings import covered_by_range
from mulewatch.compact.errors import CompactError
from mulewatch.domain.retention.buckets import ObservationRow, bucketize

_SRC = "src"

_COPY_VERBATIM: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("files", ("ed2k_hash", "size_bytes", "aich_hash")),
    ("sources", ("user_hash", "client_name", "client_version")),
    (
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
    ),
    ("match_decisions", ("ed2k_hash", "target_id", "rule_name", "tier", "decided_at", "node_id")),
    (
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
    ),
)

_OBSERVATION_COLUMNS = (
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
)


_NOT_COVERED = f"NOT {covered_by_range(f'{_SRC}.file_observation_ranges', 'o')}"

_SELECT_OLD = (
    "SELECT ed2k_hash, node_id, filename, source_count, complete_source_count, observed_at "
    f"FROM {_SRC}.file_observations AS o WHERE observed_at < ? AND {_NOT_COVERED} "
    "ORDER BY ed2k_hash, observed_at, id"
)

_INSERT_RANGE = (
    "INSERT INTO main.file_observation_ranges (ed2k_hash, bucket, filenames, node_ids, "
    "observation_count, first_observed_at, last_observed_at, source_count_min, source_count_max, "
    "source_count_sum, complete_source_count_min, complete_source_count_max, "
    "complete_source_count_sum) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def compact_catalog(
    source: Path, output: Path, *, keep_recent_days: int, clock: Clock = utc_now
) -> None:
    """Compacts `source` into `output` (NEW), `keep_recent_days` window UTC-day-aligned."""
    cutoff_date = (clock() - timedelta(days=keep_recent_days)).date().isoformat()
    connection = open_catalog(output)
    try:
        _compact_one(connection, source, cutoff_date)
    finally:
        connection.close()


def _compact_one(connection: sqlite3.Connection, source: Path, cutoff_date: str) -> None:
    try:
        connection.execute(f"ATTACH DATABASE ? AS {_SRC}", (str(Path(source).resolve()),))
    except sqlite3.Error as error:
        raise CompactError(f"cannot attach source {source}: {error}") from error
    try:
        connection.execute("BEGIN")
        try:
            for table, columns in _COPY_VERBATIM:
                projection = ", ".join(columns)
                connection.execute(
                    f"INSERT INTO main.{table} ({projection}) "
                    f"SELECT {projection} FROM {_SRC}.{table}"
                )
            recent = ", ".join(_OBSERVATION_COLUMNS)
            connection.execute(
                f"INSERT INTO main.file_observations ({recent}) "
                f"SELECT {recent} FROM {_SRC}.file_observations AS o"
                f" WHERE observed_at >= ? AND {_NOT_COVERED}",
                (cutoff_date,),
            )
            _bucketize_old(connection, cutoff_date)
            connection.execute("COMMIT")
        except sqlite3.Error as error:
            with suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
            raise CompactError(f"compaction of {source} failed: {error}") from error
    finally:
        with suppress(sqlite3.Error):
            connection.execute(f"DETACH DATABASE {_SRC}")


def _bucketize_old(connection: sqlite3.Connection, cutoff_date: str) -> None:
    cursor = connection.execute(_SELECT_OLD, (cutoff_date,))
    rows = [
        ObservationRow(
            ed2k_hash=row[0],
            node_id=row[1],
            filename=row[2],
            source_count=row[3],
            complete_source_count=row[4],
            observed_at=row[5],
        )
        for row in cursor.fetchall()
    ]
    for bucket in bucketize(rows):
        connection.execute(
            _INSERT_RANGE,
            (
                bucket.ed2k_hash,
                bucket.bucket,
                bucket.filenames,
                bucket.node_ids,
                bucket.observation_count,
                bucket.first_observed_at,
                bucket.last_observed_at,
                bucket.source_count_min,
                bucket.source_count_max,
                bucket.source_count_sum,
                bucket.complete_source_count_min,
                bucket.complete_source_count_max,
                bucket.complete_source_count_sum,
            ),
        )
