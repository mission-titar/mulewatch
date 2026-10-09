"""Every read of a file's observations, as a ``Sighting``."""

import sqlite3
from collections.abc import Iterator
from typing import Any

from mulewatch.domain.observation import Sighting

# Integer microseconds back to ``utc_iso``'s fixed-width text, in SQL for the read-only reader.
_ISO = (
    "strftime('%Y-%m-%dT%H:%M:%S', {0} / 1000000, 'unixepoch')"
    " || printf('.%06d+00:00', {0} % 1000000)"
)

# Each file's latest observation: its variant with the newest observation, then that variant's
# newest one, each a seek on observations' key. Ties go to the higher variant_id, then the higher
# source_count (stage 1, D4). One row per catalogued file, all NULL for a file never seen.
LATEST_SIGHTING_CTE = f"""latest_sighting AS (
    SELECT
        f.ed2k_hash AS ed2k_hash,
        v.filename AS name,
        {_ISO.format("o.observed_at")} AS last_seen,
        o.source_count AS source_count_max,
        v.size_bytes AS size_bytes,
        v.media_length_sec AS media_length_sec,
        v.bitrate_kbps AS bitrate_kbps,
        v.keyword AS keyword
    FROM files AS f
    LEFT JOIN observation_variants AS v ON v.variant_id = (
        SELECT v2.variant_id FROM observation_variants AS v2
        WHERE v2.ed2k_hash = f.ed2k_hash
        ORDER BY (
            SELECT max(o2.observed_at) FROM observations AS o2
            WHERE o2.variant_id = v2.variant_id
        ) DESC, v2.variant_id DESC
        LIMIT 1
    )
    LEFT JOIN observations AS o ON (o.variant_id, o.observed_at, o.source_count) = (
        SELECT o3.variant_id, o3.observed_at, o3.source_count FROM observations AS o3
        WHERE o3.variant_id = v.variant_id
        ORDER BY o3.observed_at DESC, o3.source_count DESC
        LIMIT 1
    )
)"""

# Every query below returns these columns in this order, so ``_sighting`` maps them all.
_COLUMNS = (
    "ed2k_hash, name, last_seen, source_count_max, size_bytes, media_length_sec, bitrate_kbps,"
    " keyword"
)

SELECT_LATEST_SIGHTING = f"""WITH {LATEST_SIGHTING_CTE}
SELECT {_COLUMNS} FROM latest_sighting WHERE ed2k_hash = ? AND name IS NOT NULL
"""

SELECT_LATEST_SIGHTINGS = f"""WITH {LATEST_SIGHTING_CTE}
SELECT {_COLUMNS} FROM latest_sighting WHERE name IS NOT NULL ORDER BY ed2k_hash
"""

# The file's timeline, oldest first; an instant's observations by variant_id, then source_count.
_SELECT_SIGHTINGS = f"""
SELECT v.ed2k_hash, v.filename, {_ISO.format("o.observed_at")}, o.source_count, v.size_bytes,
    v.media_length_sec, v.bitrate_kbps, v.keyword
FROM observation_variants AS v
JOIN observations AS o ON o.variant_id = v.variant_id
WHERE v.ed2k_hash = ?
ORDER BY o.observed_at, v.variant_id, o.source_count
"""

# Every name a hash was ever seen under (a file-level veto judges them all); a variant is only
# ever written with an observation, so the variants alone hold every name.
_SELECT_KNOWN_NAMES = """
SELECT DISTINCT filename FROM observation_variants WHERE ed2k_hash = ? ORDER BY 1
"""

# The name seen with the most sources, latest seen on a tie.
_SELECT_BEST_NAME = """
SELECT v.filename, v.size_bytes
FROM observation_variants AS v
JOIN observations AS o ON o.variant_id = v.variant_id
WHERE v.ed2k_hash = ?
ORDER BY o.source_count DESC, o.observed_at DESC, v.filename
LIMIT 1
"""


def _sighting(row: Any) -> Sighting:
    return Sighting(
        ed2k_hash=row[0],
        names=(row[1],),
        observation_count=1,
        first_seen=row[2],
        last_seen=row[2],
        source_count_min=row[3],
        source_count_max=row[3],
        size_bytes=row[4],
        media_length_sec=row[5],
        bitrate_kbps=row[6],
        keyword=row[7],
    )


def latest_sighting(connection: sqlite3.Connection, ed2k_hash: str) -> Sighting | None:
    """The latest observation; ``None`` for a file never seen."""
    row = connection.execute(SELECT_LATEST_SIGHTING, (ed2k_hash,)).fetchone()
    return None if row is None else _sighting(row)


def iter_latest_sightings(connection: sqlite3.Connection) -> Iterator[Sighting]:
    """Each seen file's latest sighting, sorted by hash, streamed."""
    return map(_sighting, connection.execute(SELECT_LATEST_SIGHTINGS))


def known_names(connection: sqlite3.Connection, ed2k_hash: str) -> tuple[str, ...]:
    """Every distinct name of the file, sorted."""
    rows = connection.execute(_SELECT_KNOWN_NAMES, (ed2k_hash,)).fetchall()
    return tuple(row[0] for row in rows)


def best_name(connection: sqlite3.Connection, ed2k_hash: str) -> tuple[str, int] | None:
    """The file's clean name (most sources, then latest) and its size; ``None`` if never seen."""
    row = connection.execute(_SELECT_BEST_NAME, (ed2k_hash,)).fetchone()
    return None if row is None else (row[0], row[1])


def sightings(connection: sqlite3.Connection, ed2k_hash: str) -> tuple[Sighting, ...]:
    """The file's timeline, oldest first."""
    rows = connection.execute(_SELECT_SIGHTINGS, (ed2k_hash,)).fetchall()
    return tuple(map(_sighting, rows))
