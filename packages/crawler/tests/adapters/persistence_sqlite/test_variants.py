import hashlib
import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from mulewatch.adapters.persistence_sqlite import variants
from mulewatch.adapters.persistence_sqlite.connection import utc_iso
from mulewatch.adapters.persistence_sqlite.variants import (
    content_hash,
    iso_to_micros,
    register_functions,
)
from mulewatch.domain.observation import fold_raw_meta

_HASH = "31d6cfe0d16ae931b73c59d7e0c089c0"


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:", autocommit=True)
    register_functions(connection)
    yield connection
    connection.close()


def _hash(keyword: str = "keroro", node_id: str = "node-1", bitrate: int | None = 1100) -> bytes:
    return content_hash(_HASH, "Kéroro 062A.avi", 123, None, bitrate, "[]", keyword, node_id)


def test_content_hash_is_blake2b_128_of_the_compact_json_of_the_columns() -> None:
    canonical = f'["{_HASH}","Kéroro 062A.avi",123,null,1100,"[]","keroro","node-1"]'
    assert _hash() == hashlib.blake2b(canonical.encode(), digest_size=16).digest()


def test_content_hash_writes_a_blob_as_lowercase_hex() -> None:
    blob = content_hash(
        bytes.fromhex(_HASH), "Kéroro 062A.avi", 123, None, 1100, "[]", "keroro", "node-1"
    )
    assert blob == _hash()


def test_content_hash_tells_null_from_zero() -> None:
    assert _hash(bitrate=None) != _hash(bitrate=0)


def test_content_hash_keeps_the_boundary_between_columns() -> None:
    assert _hash(keyword="ab", node_id="c") != _hash(keyword="a", node_id="bc")


def test_content_hash_sql_function_matches_the_python_one(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT content_hash(?, 'Kéroro 062A.avi', 123, NULL, 1100, '[]', 'keroro', 'node-1')",
        (_HASH,),
    ).fetchone()
    assert row[0] == _hash()


def test_content_hash_sql_function_hashes_each_distinct_key_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def counting(*values: object) -> bytes:
        calls.append(values)
        return b"h"

    monkeypatch.setattr(variants, "content_hash", counting)
    connection = sqlite3.connect(":memory:", autocommit=True)
    register_functions(connection)
    connection.execute(
        "SELECT content_hash(h, 'f', 1, NULL, NULL, '[]', 'k', 'n')"
        " FROM (SELECT 'a' AS h UNION ALL SELECT 'a' UNION ALL SELECT 'b')"
    ).fetchall()
    connection.close()
    assert len(calls) == 2


def test_fold_raw_meta_sql_function_folds_the_stored_text_as_the_domain_does(
    connection: sqlite3.Connection,
) -> None:
    pairs = (("0x0308", "0"), ("media.title", "Kéroro"))
    stored = json.dumps(pairs, ensure_ascii=False)
    row = connection.execute("SELECT fold_raw_meta(?, 'xvid', NULL, 2)", (stored,)).fetchone()
    assert row[0] == json.dumps(fold_raw_meta(pairs, "xvid", None, 2), ensure_ascii=False)


def test_fold_raw_meta_sql_function_folds_each_distinct_key_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def counting(*values: object) -> tuple[tuple[str, str | int | None], ...]:
        calls.append(values)
        return ()

    monkeypatch.setattr(variants, "fold_raw_meta", counting)
    connection = sqlite3.connect(":memory:", autocommit=True)
    register_functions(connection)
    connection.execute(
        "SELECT fold_raw_meta('[]', c, NULL, 0)"
        " FROM (SELECT 'a' AS c UNION ALL SELECT 'a' UNION ALL SELECT 'b')"
    ).fetchall()
    connection.close()
    assert len(calls) == 2


def test_iso_to_micros_counts_microseconds_since_the_epoch() -> None:
    assert iso_to_micros("1970-01-01T00:00:00.000001+00:00") == 1


def test_iso_to_micros_reads_back_what_utc_iso_writes() -> None:
    moment = datetime(2026, 6, 11, 12, 0, 0, 123456, tzinfo=UTC)
    assert iso_to_micros(utc_iso(moment)) == 1_781_179_200_123_456


def test_iso_to_micros_normalizes_an_offset_to_utc() -> None:
    assert iso_to_micros("2026-06-11T14:00:00.123456+02:00") == 1_781_179_200_123_456


def test_iso_to_micros_refuses_a_naive_stamp() -> None:
    with pytest.raises(TypeError):
        iso_to_micros("2026-06-11T12:00:00.123456")


def test_iso_to_micros_sql_function_matches_the_python_one(connection: sqlite3.Connection) -> None:
    row = connection.execute("SELECT iso_to_micros('1970-01-01T00:00:00.000001+00:00')").fetchone()
    assert row[0] == 1
