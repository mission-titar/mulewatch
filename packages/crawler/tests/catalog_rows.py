"""Catalog rows for tests, written and read here only so that a schema change edits one module."""

import sqlite3

SEEN_AT = "2026-06-22T10:00:00.000000+00:00"


def insert_file(conn: sqlite3.Connection, ed2k_hash: str, size_bytes: int = 100) -> None:
    conn.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", (ed2k_hash, size_bytes))


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
) -> None:
    conn.execute(
        "INSERT INTO file_observations"
        " (ed2k_hash, filename, size_bytes, source_count, complete_source_count,"
        " media_length_sec, bitrate_kbps, raw_meta, keyword, observed_at, node_id)"
        " VALUES (?, ?, ?, ?, 0, ?, ?, '[]', 'keroro', ?, 'n1')",
        (
            ed2k_hash,
            filename,
            size_bytes,
            source_count,
            media_length_sec,
            bitrate_kbps,
            observed_at,
        ),
    )


def insert_decision(
    conn: sqlite3.Connection,
    ed2k_hash: str,
    target_id: str,
    tier: str,
    *,
    rule_name: str = "rule",
    decided_at: str = SEEN_AT,
) -> None:
    conn.execute(
        "INSERT INTO match_decisions"
        " (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
        " VALUES (?, ?, ?, ?, ?, 'n1')",
        (ed2k_hash, target_id, rule_name, tier, decided_at),
    )


def count_observations(conn: sqlite3.Connection) -> int:
    count: int = conn.execute("SELECT count(*) FROM file_observations").fetchone()[0]
    return count


def observation_node_ids(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT node_id FROM file_observations")}


def decision_tiers(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """``(ed2k_hash, tier)`` of every decision, oldest first."""
    rows = conn.execute("SELECT ed2k_hash, tier FROM match_decisions ORDER BY id").fetchall()
    return [(row[0], row[1]) for row in rows]
