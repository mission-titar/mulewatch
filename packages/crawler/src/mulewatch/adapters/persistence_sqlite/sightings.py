"""Every read of a file's observations, as a ``Sighting``."""

import sqlite3
from collections.abc import Iterator
from typing import Any

from mulewatch.domain.observation import Sighting

# Each file's latest observation, seeked through idx_file_observations_hash_observed, never a
# scan. One row per catalogued file, all NULL for a file never seen.
LATEST_SIGHTING_CTE = """latest_sighting AS (
    SELECT
        f.ed2k_hash AS ed2k_hash,
        o.filename AS name,
        o.observed_at AS last_seen,
        o.source_count AS source_count_max,
        o.size_bytes AS size_bytes,
        o.media_length_sec AS media_length_sec,
        o.bitrate_kbps AS bitrate_kbps,
        o.keyword AS keyword
    FROM files AS f
    LEFT JOIN file_observations AS o ON o.id = (
        SELECT o2.id FROM file_observations AS o2
        WHERE o2.ed2k_hash = f.ed2k_hash
        ORDER BY o2.observed_at DESC, o2.id DESC
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

# The file's timeline, oldest first; ``id`` only orders rows of the same instant.
_SELECT_SIGHTINGS = """
SELECT ed2k_hash, filename, observed_at, source_count, size_bytes, media_length_sec,
    bitrate_kbps, keyword
FROM file_observations WHERE ed2k_hash = :hash
ORDER BY observed_at, id
"""

# Every name a hash was ever seen under (a file-level veto judges them all).
_SELECT_KNOWN_NAMES = """
SELECT DISTINCT filename FROM file_observations WHERE ed2k_hash = :hash ORDER BY 1
"""

# The name seen with the most sources, latest seen on a tie.
_SELECT_BEST_NAME = """
SELECT filename, size_bytes FROM file_observations WHERE ed2k_hash = :hash
ORDER BY source_count DESC, observed_at DESC, filename
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
    rows = connection.execute(_SELECT_KNOWN_NAMES, {"hash": ed2k_hash}).fetchall()
    return tuple(row[0] for row in rows)


def best_name(connection: sqlite3.Connection, ed2k_hash: str) -> tuple[str, int] | None:
    """The file's clean name (most sources, then latest) and its size; ``None`` if never seen."""
    row = connection.execute(_SELECT_BEST_NAME, {"hash": ed2k_hash}).fetchone()
    return None if row is None else (row[0], row[1])


def sightings(connection: sqlite3.Connection, ed2k_hash: str) -> tuple[Sighting, ...]:
    """The file's timeline, oldest first."""
    rows = connection.execute(_SELECT_SIGHTINGS, {"hash": ed2k_hash}).fetchall()
    return tuple(map(_sighting, rows))
