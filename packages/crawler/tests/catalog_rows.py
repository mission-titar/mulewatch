"""Catalog rows for tests, written here only so that a schema change edits one module."""

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
