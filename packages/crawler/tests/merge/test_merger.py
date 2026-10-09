"""TDD tests for the ``merge_catalogs`` core (ATTACH + idempotent INSERT…SELECT), design §7.

Everything is tested without Docker: we build N real ``catalog.db`` files (helpers), merge,
and assert content + cardinality + reassigned ``id`` + idempotence.
"""

import sqlite3
from pathlib import Path

import pytest

from mulewatch.adapters.persistence_sqlite.connection import open_catalog
from mulewatch.merge.errors import MergeError, SchemaVersionMismatchError
from mulewatch.merge.merger import merge_catalogs

from .helpers import (
    FILE_OBSERVATION_COLUMNS,
    HASH_A,
    HASH_B,
    count,
    hash_for,
    ids,
    make_catalog,
    rows_without_id,
    stamp_user_version,
)

# The current catalog schema version = the count of catalog migrations (0001 through 0005).
# open_catalog stamps the output to it; the guard rejects any source that is not at it.
_CURRENT_SCHEMA_VERSION = 6


def _file_observation(ed2k_hash: str, *, node_id: str, observed_at: str) -> dict[str, object]:
    """A complete observation (nullable columns deliberately left None)."""
    return {
        "ed2k_hash": ed2k_hash,
        "filename": "keroro.avi",
        "size_bytes": 100,
        "source_count": 3,
        "complete_source_count": 1,
        "media_length_sec": None,
        "bitrate_kbps": None,
        "codec": None,
        "file_type": None,
        "raw_meta": "[]",
        "keyword": "keroro",
        "observed_at": observed_at,
        "node_id": node_id,
    }


def _full_catalog(letter: str, *, node_id: str) -> dict[str, list[dict[str, object]]]:
    """A consistent catalog: 1 file and 1 row in each of the 2 journals."""
    ed2k = hash_for(letter)
    return {
        "files": [{"ed2k_hash": ed2k, "size_bytes": 100}],
        "file_observations": [_file_observation(ed2k, node_id=node_id, observed_at="t1")],
        "match_decisions": [
            {
                "ed2k_hash": ed2k,
                "target_id": "062A",
                "rule_name": "r",
                "tier": "download",
                "decided_at": "t1",
                "node_id": node_id,
            }
        ],
    }


_ALL_TABLES = ("files", "file_observations", "match_decisions")


def test_t1_merge_two_distinct_catalogs(tmp_path: Path) -> None:
    src_a = make_catalog(tmp_path / "a.db", _full_catalog("a", node_id="node-a"))
    src_b = make_catalog(tmp_path / "b.db", _full_catalog("b", node_id="node-b"))
    src_c = make_catalog(tmp_path / "c.db", _full_catalog("c", node_id="node-c"))
    out = tmp_path / "out.db"

    # N=3 also proves we attach one source at a time (never > 1 attached DB).
    merge_catalogs(out, [src_a, src_b, src_c])

    # Each table's cardinality = sum (disjoint contents); FKs satisfied (otherwise the
    # COMMIT would have raised). All rows from the 3 sources are present in the output.
    for table in _ALL_TABLES:
        assert count(out, table) == 3
        expected = sorted(
            rows_without_id(src_a, table)
            + rows_without_id(src_b, table)
            + rows_without_id(src_c, table)
        )
        assert rows_without_id(out, table) == expected


def test_t2_merge_overlapping_identity_files_or_ignore(tmp_path: Path) -> None:
    # Both sources share the SAME ed2k_hash (content-identity).
    src_a = make_catalog(tmp_path / "a.db", {"files": [{"ed2k_hash": HASH_A, "size_bytes": 100}]})
    src_b = make_catalog(tmp_path / "b.db", {"files": [{"ed2k_hash": HASH_A, "size_bytes": 100}]})
    out = tmp_path / "out.db"

    merge_catalogs(out, [src_a, src_b])

    # INSERT OR IGNORE: the already-present PK is ignored → a single row.
    assert count(out, "files") == 1


def test_t3_re_merge_is_idempotent(tmp_path: Path) -> None:
    src_a = make_catalog(tmp_path / "a.db", _full_catalog("a", node_id="node-a"))
    src_b = make_catalog(tmp_path / "b.db", _full_catalog("b", node_id="node-b"))
    out = tmp_path / "out.db"

    merge_catalogs(out, [src_a, src_b])
    first_pass = {table: rows_without_id(out, table) for table in _ALL_TABLES}

    # Re-merge same sources into same output → WHERE NOT EXISTS false everywhere → no-op.
    merge_catalogs(out, [src_a, src_b])

    for table in _ALL_TABLES:
        assert rows_without_id(out, table) == first_pass[table]


def test_t4_journal_dedup_identical_rows_including_nulls(tmp_path: Path) -> None:
    # BIT-FOR-BIT identical observation (same NULLs on media_length_sec/bitrate/codec/file_type).
    obs = _file_observation(HASH_A, node_id="node", observed_at="t1")
    src_a = make_catalog(
        tmp_path / "a.db",
        {"files": [{"ed2k_hash": HASH_A, "size_bytes": 100}], "file_observations": [obs]},
    )
    src_b = make_catalog(
        tmp_path / "b.db",
        {"files": [{"ed2k_hash": HASH_A, "size_bytes": 100}], "file_observations": [obs]},
    )
    out = tmp_path / "out.db"

    merge_catalogs(out, [src_a, src_b])

    # Without the IS operator, NULL=NULL would be false → 2 rows. With IS → a single one.
    assert count(out, "file_observations") == 1


def test_t4_journal_distinct_observed_at_keeps_both(tmp_path: Path) -> None:
    # Same file, same node, two INSTANTS → two REAL observations, distinct natural keys
    # → both kept (non-destructive).
    src_a = make_catalog(
        tmp_path / "a.db",
        {
            "files": [{"ed2k_hash": HASH_A, "size_bytes": 100}],
            "file_observations": [_file_observation(HASH_A, node_id="node", observed_at="t1")],
        },
    )
    src_b = make_catalog(
        tmp_path / "b.db",
        {
            "files": [{"ed2k_hash": HASH_A, "size_bytes": 100}],
            "file_observations": [_file_observation(HASH_A, node_id="node", observed_at="t2")],
        },
    )
    out = tmp_path / "out.db"

    merge_catalogs(out, [src_a, src_b])

    assert count(out, "file_observations") == 2


def test_t5_journal_drops_local_id(tmp_path: Path) -> None:
    # Each source has a DISTINCT observation that (by autoincrement) carries id=1.
    src_a = make_catalog(
        tmp_path / "a.db",
        {
            "files": [{"ed2k_hash": HASH_A, "size_bytes": 100}],
            "file_observations": [_file_observation(HASH_A, node_id="node-a", observed_at="t1")],
        },
    )
    src_b = make_catalog(
        tmp_path / "b.db",
        {
            "files": [{"ed2k_hash": HASH_B, "size_bytes": 200}],
            "file_observations": [_file_observation(HASH_B, node_id="node-b", observed_at="t2")],
        },
    )
    assert ids(src_a, "file_observations") == [1]
    assert ids(src_b, "file_observations") == [1]
    out = tmp_path / "out.db"

    merge_catalogs(out, [src_a, src_b])

    # We do NOT copy id: the DB reassigns 1 and 2, not a collision.
    assert ids(out, "file_observations") == [1, 2]


def test_t6_fk_order_inserts_identity_first(tmp_path: Path) -> None:
    # A source whose journals reference files. If the insertion order were reversed
    # (journals before identities), the FK would raise; the merge succeeds so the order holds.
    src = make_catalog(tmp_path / "a.db", _full_catalog("a", node_id="node-a"))
    out = tmp_path / "out.db"

    merge_catalogs(out, [src])

    assert count(out, "file_observations") == 1
    assert count(out, "match_decisions") == 1


def test_t14_aich_first_wins_a_then_b(tmp_path: Path) -> None:
    # srcA: aich=NULL; srcB: aich set; merge A→B → the row keeps aich=NULL.
    src_a = make_catalog(
        tmp_path / "a.db", {"files": [{"ed2k_hash": HASH_A, "size_bytes": 100, "aich_hash": None}]}
    )
    src_b = make_catalog(
        tmp_path / "b.db", {"files": [{"ed2k_hash": HASH_A, "size_bytes": 100, "aich_hash": "X"}]}
    )
    out = tmp_path / "out.db"

    merge_catalogs(out, [src_a, src_b])

    assert rows_without_id(out, "files") == [(HASH_A, 100, None)]


def test_t14_aich_first_wins_b_then_a(tmp_path: Path) -> None:
    # Reverse order B→A → the row keeps aich='X' (first-come wins, §6 rule frozen).
    src_a = make_catalog(
        tmp_path / "a.db", {"files": [{"ed2k_hash": HASH_A, "size_bytes": 100, "aich_hash": None}]}
    )
    src_b = make_catalog(
        tmp_path / "b.db", {"files": [{"ed2k_hash": HASH_A, "size_bytes": 100, "aich_hash": "X"}]}
    )
    out = tmp_path / "out.db"

    merge_catalogs(out, [src_b, src_a])

    assert rows_without_id(out, "files") == [(HASH_A, 100, "X")]


def test_t15_append_only_triggers_present_on_output(tmp_path: Path) -> None:
    src = make_catalog(tmp_path / "a.db", {"files": [{"ed2k_hash": HASH_A, "size_bytes": 100}]})
    out = tmp_path / "out.db"
    merge_catalogs(out, [src])

    connection = open_catalog(out)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="files is append-only"):
            connection.execute("UPDATE files SET size_bytes = 2")
        with pytest.raises(sqlite3.IntegrityError, match="files is append-only"):
            connection.execute("DELETE FROM files")
    finally:
        connection.close()


def test_t16_merger_wraps_source_copy_in_a_transaction(tmp_path: Path) -> None:
    # A source with a broken schema (empty SQLite DB, no tables) → the copy fails
    # midway → ROLLBACK: the output keeps NO partial copy of this source.
    good = make_catalog(tmp_path / "good.db", _full_catalog("a", node_id="node-a"))

    broken = tmp_path / "broken.db"
    raw = sqlite3.connect(broken)
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("CREATE TABLE files (ed2k_hash TEXT PRIMARY KEY, size_bytes INTEGER)")
    raw.execute(f"INSERT INTO files VALUES ('{HASH_B}', 200)")
    raw.commit()
    raw.close()
    # Stamp the current schema version so this source clears the version guard and reaches
    # the copy: we are exercising the mid-copy ROLLBACK, not the version rejection.
    stamp_user_version(broken, _CURRENT_SCHEMA_VERSION)

    out = tmp_path / "out.db"
    merge_catalogs(out, [good])  # the good source first, in its own successful merge.

    with pytest.raises(MergeError, match="copy of .* failed"):
        merge_catalogs(out, [broken], dest_is_source=False)

    # files (1st table) may have been copied BEFORE the failure on file_observations (missing
    # table); the ROLLBACK must have undone it → the output still has ONLY the content of `good`.
    assert rows_without_id(out, "files") == [(HASH_A, 100, None)]


def test_t16_unattachable_source_errors(tmp_path: Path) -> None:
    # A source that is NOT a SQLite database (file exists but header invalid) →
    # the ATTACH itself raises → clear MergeError (attach branch, distinct from the copy).
    not_a_db = tmp_path / "garbage.db"
    not_a_db.write_bytes(b"not a sqlite database header" * 8)
    out = tmp_path / "out.db"

    with pytest.raises(MergeError, match="cannot attach"):
        merge_catalogs(out, [not_a_db])


def test_t17_single_source_merge(tmp_path: Path) -> None:
    src = make_catalog(tmp_path / "a.db", _full_catalog("a", node_id="node-a"))
    out = tmp_path / "out.db"

    merge_catalogs(out, [src])

    for table in _ALL_TABLES:
        assert rows_without_id(out, table) == rows_without_id(src, table)


def test_t18_dedups_identical_rows_internal_to_one_source(tmp_path: Path) -> None:
    # A SINGLE source containing TWO bit-for-bit identical journal rows (same natural
    # key, different id by autoincrement, NULL COLUMNS included) PLUS a legitimately
    # distinct row (a single field differs). The N=1 merge must NORMALIZE: collapse the
    # internal duplicates (§1/§8 promise: at-least-once dedup of a single catalog) without
    # ever losing the distinct row.
    identical = _file_observation(HASH_A, node_id="node", observed_at="t1")
    distinct = _file_observation(HASH_A, node_id="node", observed_at="t2")  # observed_at differs
    src = make_catalog(
        tmp_path / "a.db",
        {
            "files": [{"ed2k_hash": HASH_A, "size_bytes": 100}],
            "file_observations": [identical, dict(identical), distinct],
        },
    )
    # The source indeed contains 3 rows (2 of them twins) BEFORE merge.
    assert count(src, "file_observations") == 3
    out = tmp_path / "out.db"

    merge_catalogs(out, [src])

    # Internal duplicate collapsed (2 twins → 1); distinct row preserved → 2 rows.
    assert count(out, "file_observations") == 2
    assert rows_without_id(out, "file_observations") == sorted(
        [
            tuple(identical[column] for column in FILE_OBSERVATION_COLUMNS),
            tuple(distinct[column] for column in FILE_OBSERVATION_COLUMNS),
        ],
        key=lambda row: tuple(str(value) for value in row),
    )

    # Re-merge = no-op (idempotent even after normalization).
    merge_catalogs(out, [src])
    assert count(out, "file_observations") == 2


def test_rejects_source_with_older_schema_version(tmp_path: Path) -> None:
    # A source stamped to an OLDER schema (e.g. a 0001/0002-era DB) is refused BEFORE any
    # row is copied: the merge never migrates a source in place, so an off-version schema
    # could silently mis-copy. Fail-fast with the source path + both versions in the message.
    src = make_catalog(tmp_path / "old.db", _full_catalog("a", node_id="node-a"))
    stamp_user_version(src, _CURRENT_SCHEMA_VERSION - 1)
    out = tmp_path / "out.db"

    with pytest.raises(SchemaVersionMismatchError) as excinfo:
        merge_catalogs(out, [src])

    message = str(excinfo.value)
    assert str(src) in message
    assert str(_CURRENT_SCHEMA_VERSION - 1) in message
    assert str(_CURRENT_SCHEMA_VERSION) in message
    # Rejected before its BEGIN: no row of the source reached the output.
    assert count(out, "files") == 0


def test_rejects_source_with_newer_schema_version(tmp_path: Path) -> None:
    # Strict equality: a NEWER source (a future 0004 schema the code does not know) is
    # refused too, consistent with open_catalog refusing a DB newer than the code.
    src = make_catalog(tmp_path / "new.db", _full_catalog("a", node_id="node-a"))
    stamp_user_version(src, _CURRENT_SCHEMA_VERSION + 1)
    out = tmp_path / "out.db"

    with pytest.raises(SchemaVersionMismatchError):
        merge_catalogs(out, [src])

    assert count(out, "files") == 0


def test_source_at_current_schema_version_still_merges(tmp_path: Path) -> None:
    # Happy path: a source at the current schema version passes the guard and merges.
    # (make_catalog stamps the current version; the explicit stamp documents the intent.)
    src = make_catalog(tmp_path / "a.db", _full_catalog("a", node_id="node-a"))
    stamp_user_version(src, _CURRENT_SCHEMA_VERSION)
    out = tmp_path / "out.db"

    merge_catalogs(out, [src])

    assert count(out, "files") == 1


def test_guard_checks_every_source_not_just_the_first(tmp_path: Path) -> None:
    # A valid first source then an off-version second: the guard runs per source, so the
    # second is rejected. The first was committed in its own transaction and survives.
    good = make_catalog(tmp_path / "good.db", _full_catalog("a", node_id="node-a"))
    bad = make_catalog(tmp_path / "bad.db", _full_catalog("b", node_id="node-b"))
    stamp_user_version(bad, _CURRENT_SCHEMA_VERSION - 1)
    out = tmp_path / "out.db"

    with pytest.raises(SchemaVersionMismatchError, match=str(bad)):
        merge_catalogs(out, [good, bad])

    # The good source merged first (own transaction, committed); only its row is present.
    assert count(out, "files") == 1
    assert rows_without_id(out, "files") == [(hash_for("a"), 100, None)]
