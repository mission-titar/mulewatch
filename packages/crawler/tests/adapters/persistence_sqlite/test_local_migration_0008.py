"""Local 0008 adds the downloads' lifecycle columns (stage 2, D12, D19)."""

from pathlib import Path

from mulewatch.domain.file_key import FileKey, Network
from tests.adapters.persistence_sqlite.older_catalog import open_local_at

_A = FileKey(Network.ED2K, "a" * 32)


def test_0008_adds_the_lifecycle_columns_unknown_on_existing_rows(tmp_path: Path) -> None:
    path = tmp_path / "local.db"
    connection = open_local_at(path, 7)
    connection.execute(
        "INSERT INTO downloads (file_id, network, native_id, target_id, state, queued_at)"
        " VALUES (?, 'ed2k', ?, '062A', 'downloading', '2026-10-01')",
        (_A.file_id, _A.native_id),
    )
    connection.close()

    connection = open_local_at(path, 8)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 8
        row = connection.execute(
            "SELECT native_id, state, bytes_done, last_progress_at, waiting_reason,"
            " failure_reason FROM downloads"
        ).fetchone()
    finally:
        connection.close()
    # bytes_done NULL: what the client held before 0008 is unknown, so no progress is invented.
    assert row == (_A.native_id, "downloading", None, None, None, None)
