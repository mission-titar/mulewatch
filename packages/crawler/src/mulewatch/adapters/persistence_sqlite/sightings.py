"""Every read of a file's observations, raw or compacted, as a ``Sighting`` (spec 2026-10-05).
Only this module reads ``file_observations``/``file_observation_ranges``, besides compact and merge.
"""

import json
import sqlite3
from collections.abc import Iterator
from typing import Any

from mulewatch.domain.observation import Sighting


def covered_by_range(ranges: str, raw: str) -> str:
    """SQL: a row of ``ranges`` already counts raw row ``raw`` (its hash, UTC day and node)."""
    # One uncorrelated scalar IN, built once per statement: a correlated EXISTS was 11x slower in
    # merge, a row-value NOT IN 100x. Hash (32) and day (10) are fixed width: an unambiguous key.
    return (
        f"({raw}.ed2k_hash || substr({raw}.observed_at, 1, 10) || {raw}.node_id) IN"
        f" (SELECT cr.ed2k_hash || cr.bucket || cn.value FROM {ranges} AS cr,"
        " json_each(cr.node_ids) AS cn)"
    )


# Each file's latest sighting: its latest raw observation (seeked through
# idx_file_observations_hash_observed, never a scan), else its latest range. The only place this
# fallback is written. One row per catalogued file, all NULL for a file never seen.
LATEST_SIGHTING_CTE = """latest_sighting AS (
    SELECT
        f.ed2k_hash AS ed2k_hash,
        COALESCE(o.filename, json_extract(r.filenames, '$[0]')) AS name,
        r.filenames AS filenames,
        COALESCE(r.observation_count, 1) AS observation_count,
        COALESCE(o.observed_at, r.first_observed_at) AS first_seen,
        COALESCE(o.observed_at, r.last_observed_at) AS last_seen,
        COALESCE(o.source_count, r.source_count_min) AS source_count_min,
        COALESCE(o.source_count, r.source_count_max) AS source_count_max,
        COALESCE(o.size_bytes, f.size_bytes) AS size_bytes,
        o.media_length_sec AS media_length_sec,
        o.bitrate_kbps AS bitrate_kbps,
        o.keyword AS keyword,
        r.id IS NOT NULL AS compacted
    FROM files AS f
    LEFT JOIN file_observations AS o ON o.id = (
        SELECT o2.id FROM file_observations AS o2
        WHERE o2.ed2k_hash = f.ed2k_hash
        ORDER BY o2.observed_at DESC, o2.id DESC
        LIMIT 1
    )
    LEFT JOIN file_observation_ranges AS r ON o.id IS NULL AND r.id = (
        SELECT r2.id FROM file_observation_ranges AS r2
        WHERE r2.ed2k_hash = f.ed2k_hash
        ORDER BY r2.last_observed_at DESC, r2.id DESC
        LIMIT 1
    )
)"""

# The latest name or any compacted name LIKE the pattern, bound twice. Expects ``files AS f``
# joined to ``latest_sighting AS obs``; older raw aliases are out (a LIKE over every raw row).
NAME_MATCH_CLAUSE = (
    "(obs.name LIKE ? OR EXISTS (SELECT 1 FROM file_observation_ranges AS qr,"
    " json_each(qr.filenames) AS qn WHERE qr.ed2k_hash = f.ed2k_hash AND qn.value LIKE ?))"
)

# Every query below returns these columns in this order, so ``_sighting`` maps them all.
_COLUMNS = (
    "ed2k_hash, name, filenames, observation_count, first_seen, last_seen, source_count_min,"
    " source_count_max, size_bytes, media_length_sec, bitrate_kbps, keyword, compacted"
)

SELECT_LATEST_SIGHTING = f"""WITH {LATEST_SIGHTING_CTE}
SELECT {_COLUMNS} FROM latest_sighting WHERE ed2k_hash = ? AND name IS NOT NULL
"""

SELECT_LATEST_SIGHTINGS = f"""WITH {LATEST_SIGHTING_CTE}
SELECT {_COLUMNS} FROM latest_sighting WHERE name IS NOT NULL ORDER BY ed2k_hash
"""

# The file's timeline, both forms, oldest first; ``id`` only orders raw rows of the same instant.
_SELECT_SIGHTINGS = f"""
SELECT {_COLUMNS} FROM (
    SELECT
        ed2k_hash, filename AS name, NULL AS filenames, 1 AS observation_count,
        observed_at AS first_seen, observed_at AS last_seen, source_count AS source_count_min,
        source_count AS source_count_max, size_bytes, media_length_sec, bitrate_kbps, keyword,
        0 AS compacted, id
    FROM file_observations WHERE ed2k_hash = :hash
    UNION ALL
    SELECT
        r.ed2k_hash, json_extract(r.filenames, '$[0]'), r.filenames, r.observation_count,
        r.first_observed_at, r.last_observed_at, r.source_count_min, r.source_count_max,
        f.size_bytes, NULL, NULL, NULL, 1, r.id
    FROM file_observation_ranges AS r JOIN files AS f ON f.ed2k_hash = r.ed2k_hash
    WHERE r.ed2k_hash = :hash
)
ORDER BY first_seen, last_seen, compacted DESC, id
"""

# Every name a hash was ever seen under, both forms (a file-level veto judges them all).
_SELECT_KNOWN_NAMES = """
SELECT filename FROM file_observations WHERE ed2k_hash = :hash
UNION
SELECT j.value FROM file_observation_ranges AS r, json_each(r.filenames) AS j
WHERE r.ed2k_hash = :hash
ORDER BY 1
"""


def _sighting(row: Any) -> Sighting:
    return Sighting(
        ed2k_hash=row[0],
        names=(row[1],) if row[2] is None else tuple(json.loads(row[2])),
        observation_count=row[3],
        first_seen=row[4],
        last_seen=row[5],
        source_count_min=row[6],
        source_count_max=row[7],
        size_bytes=row[8],
        media_length_sec=row[9],
        bitrate_kbps=row[10],
        keyword=row[11],
        compacted=bool(row[12]),
    )


def latest_sighting(connection: sqlite3.Connection, ed2k_hash: str) -> Sighting | None:
    """The latest raw observation, else the latest range; ``None`` for a file never seen."""
    row = connection.execute(SELECT_LATEST_SIGHTING, (ed2k_hash,)).fetchone()
    return None if row is None else _sighting(row)


def iter_latest_sightings(connection: sqlite3.Connection) -> Iterator[Sighting]:
    """Each seen file's latest sighting, sorted by hash, streamed."""
    return map(_sighting, connection.execute(SELECT_LATEST_SIGHTINGS))


def known_names(connection: sqlite3.Connection, ed2k_hash: str) -> tuple[str, ...]:
    """Every distinct name of the file, both forms, sorted."""
    rows = connection.execute(_SELECT_KNOWN_NAMES, {"hash": ed2k_hash}).fetchall()
    return tuple(row[0] for row in rows)


def sightings(connection: sqlite3.Connection, ed2k_hash: str) -> tuple[Sighting, ...]:
    """The file's timeline, both forms, oldest first."""
    rows = connection.execute(_SELECT_SIGHTINGS, {"hash": ed2k_hash}).fetchall()
    return tuple(map(_sighting, rows))
