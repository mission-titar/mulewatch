"""Catalog rows for tests, written and read here only so that a schema change edits one module."""

import sqlite3

from mulewatch.adapters.persistence_sqlite.variants import content_hash, iso_to_micros
from mulewatch.domain.file_key import FileKey, Network

SEEN_AT = "2026-06-22T10:00:00.000000+00:00"


def _file_id(ed2k_hash: str) -> bytes:
    return FileKey(Network.ED2K, ed2k_hash).file_id


def insert_file(conn: sqlite3.Connection, ed2k_hash: str, size_bytes: int = 100) -> None:
    conn.execute(
        "INSERT INTO files (file_id, network, native_id, size_bytes) VALUES (?, 'ed2k', ?, ?)",
        (_file_id(ed2k_hash), ed2k_hash, size_bytes),
    )


def insert_observation(
    conn: sqlite3.Connection,
    ed2k_hash: str,
    filename: str,
    *,
    observed_at: str = SEEN_AT,
    size_bytes: int = 100,
    source_count: int = 1,
    media_length_sec: int | None = None,
    bitrate_kbps: int | None = None,
    node_id: str = "n1",
) -> None:
    variant = (
        _file_id(ed2k_hash),
        filename,
        size_bytes,
        media_length_sec,
        bitrate_kbps,
        "[]",
        "keroro",
        node_id,
    )
    key = content_hash(*variant)
    conn.execute(
        "INSERT OR IGNORE INTO observation_variants"
        " (file_id, filename, size_bytes, media_length_sec, bitrate_kbps, raw_meta, keyword,"
        " node_id, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (*variant, key),
    )
    conn.execute(
        "INSERT INTO observations (variant_id, observed_at, source_count)"
        " SELECT variant_id, ?, ? FROM observation_variants WHERE content_hash = ?",
        (iso_to_micros(observed_at), source_count, key),
    )


def insert_decision(
    conn: sqlite3.Connection,
    ed2k_hash: str,
    target_id: str,
    tier: str,
    *,
    rule_name: str = "rule",
    decided_at: str = SEEN_AT,
    node_id: str = "n1",
) -> None:
    conn.execute(
        "INSERT INTO match_decisions"
        " (file_id, target_id, rule_name, tier, decided_at, node_id)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (_file_id(ed2k_hash), target_id, rule_name, tier, decided_at, node_id),
    )


def count_observations(conn: sqlite3.Connection) -> int:
    count: int = conn.execute("SELECT count(*) FROM observations").fetchone()[0]
    return count


def observation_node_ids(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT node_id FROM observation_variants")}


def decision_tiers(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """``(ed2k_hash, tier)`` of every decision, oldest first."""
    rows = conn.execute(
        "SELECT f.native_id, d.tier FROM match_decisions AS d"
        " JOIN files AS f USING (file_id) ORDER BY d.id"
    ).fetchall()
    return [(row[0], row[1]) for row in rows]
