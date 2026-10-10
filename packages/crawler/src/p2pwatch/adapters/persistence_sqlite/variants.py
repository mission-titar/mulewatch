"""An observation variant's ``content_hash``, and the SQL functions the catalog migrations call.

Changing the serialization changes every stored ``content_hash``: it needs a migration.
"""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from functools import cache

from p2pwatch.domain.file_key import FileKey, Network
from p2pwatch.domain.observation import fold_raw_meta

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)


def content_hash(
    file_ref: str | bytes,
    filename: str,
    size_bytes: int,
    media_length_sec: int | None,
    bitrate_kbps: int | None,
    raw_meta: str,
    keyword: str,
    node_id: str,
) -> bytes:
    """blake2b-128 of the columns as compact JSON, a BLOB ``file_ref`` written as lowercase hex."""
    columns = (
        file_ref.hex() if isinstance(file_ref, bytes) else file_ref,
        filename,
        size_bytes,
        media_length_sec,
        bitrate_kbps,
        raw_meta,
        keyword,
        node_id,
    )
    canonical = json.dumps(columns, ensure_ascii=False, separators=(",", ":"))
    return hashlib.blake2b(canonical.encode(), digest_size=16).digest()


def iso_to_micros(stamp: str) -> int:
    """Microseconds since the Unix epoch of an aware ISO-8601 stamp; a naive one raises."""
    return (datetime.fromisoformat(stamp) - _EPOCH) // _MICROSECOND


def file_id(network: str, native_id: str) -> bytes:
    """``FileKey.file_id`` for the SQL function; an unknown network raises."""
    return FileKey(Network(network), native_id).file_id


def register_functions(connection: sqlite3.Connection) -> None:
    """Registers ``content_hash``, ``file_id``, ``fold_raw_meta`` and ``iso_to_micros``."""

    # A stored raw_meta, folded and serialized as record_observation serializes it.
    def fold_stored(raw_meta: str, codec: str | None, file_type: str | None, complete: int) -> str:
        return json.dumps(
            fold_raw_meta(json.loads(raw_meta), codec, file_type, complete), ensure_ascii=False
        )

    # Memoized for the connection's life, the crawler's included: one entry per variant 0007 wrote.
    connection.create_function("content_hash", 8, cache(content_hash), deterministic=True)
    connection.create_function("fold_raw_meta", 4, cache(fold_stored), deterministic=True)
    connection.create_function("file_id", 2, file_id, deterministic=True)
    # Never memoized: every observation has its own stamp, the memo would hold them all.
    connection.create_function("iso_to_micros", 1, iso_to_micros, deterministic=True)
