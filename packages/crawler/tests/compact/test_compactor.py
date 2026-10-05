"""compact_catalog: rebuild into a fresh output, day-aligned window, idempotence."""

import sqlite3
from collections.abc import Callable
from dataclasses import astuple
from datetime import datetime
from pathlib import Path

import pytest

from mulewatch.adapters.persistence_sqlite.connection import open_catalog
from mulewatch.compact.compactor import compact_catalog, covered_by_range
from mulewatch.compact.errors import CompactError
from mulewatch.domain.retention.buckets import ObservationRow

from ..domain.retention.test_buckets import COVER_CASES, COVERING_RANGE
from ..merge.helpers import HASH_A, count, make_catalog
from .helpers import insert_ranges, read_observation_days, read_ranges

_H = HASH_A


def _obs(name: str, sc: int, csc: int, at: str, node: str = "n1") -> dict[str, object]:
    return {
        "ed2k_hash": _H,
        "filename": name,
        "size_bytes": 1,
        "source_count": sc,
        "complete_source_count": csc,
        "raw_meta": "[]",
        "keyword": "k",
        "observed_at": at,
        "node_id": node,
    }


def _clock(moment: str) -> Callable[[], datetime]:
    return lambda: datetime.fromisoformat(moment)


def _source(path: Path, observations: list[dict[str, object]]) -> Path:
    return make_catalog(
        path,
        {"files": [{"ed2k_hash": _H, "size_bytes": 1}], "file_observations": observations},
    )


def test_old_observations_become_one_bucket_per_day(tmp_path: Path) -> None:
    src = _source(
        tmp_path / "src.db",
        [
            _obs("f.avi", 1, 0, "2026-01-10T01:00:00.000000+00:00"),
            _obs("f.avi", 9, 2, "2026-01-10T20:00:00.000000+00:00"),
            _obs("f.avi", 4, 1, "2026-01-11T05:00:00.000000+00:00"),
        ],
    )
    out = tmp_path / "out.db"
    compact_catalog(src, out, keep_recent_days=90, clock=_clock("2026-06-01T00:00:00+00:00"))
    assert [r[1] for r in read_ranges(out)] == ["2026-01-10", "2026-01-11"]
    assert count(out, "file_observations") == 0


def test_recent_window_is_kept_raw(tmp_path: Path) -> None:
    src = _source(
        tmp_path / "src.db",
        [
            _obs("f.avi", 1, 0, "2026-01-10T01:00:00.000000+00:00"),
            _obs("f.avi", 2, 0, "2026-05-30T01:00:00.000000+00:00"),
        ],
    )
    out = tmp_path / "out.db"
    compact_catalog(src, out, keep_recent_days=90, clock=_clock("2026-06-01T00:00:00+00:00"))
    assert read_observation_days(out) == ["2026-05-30T01:00:00.000000+00:00"]
    assert [r[1] for r in read_ranges(out)] == ["2026-01-10"]


def test_cutoff_day_is_whole_day_aligned(tmp_path: Path) -> None:
    src = _source(tmp_path / "src.db", [_obs("f.avi", 1, 0, "2026-06-01T00:00:00.000000+00:00")])
    out = tmp_path / "out.db"
    compact_catalog(src, out, keep_recent_days=90, clock=_clock("2026-08-30T12:00:00+00:00"))
    assert read_observation_days(out) == ["2026-06-01T00:00:00.000000+00:00"]
    assert read_ranges(out) == []


def test_verbatim_tables_copied(tmp_path: Path) -> None:
    src = make_catalog(
        tmp_path / "src.db",
        {
            "files": [{"ed2k_hash": _H, "size_bytes": 7}],
            "match_decisions": [
                {
                    "ed2k_hash": _H,
                    "target_id": "062A",
                    "rule_name": "r",
                    "tier": "download",
                    "decided_at": "t",
                    "node_id": "n",
                }
            ],
        },
    )
    out = tmp_path / "out.db"
    compact_catalog(src, out, keep_recent_days=90, clock=_clock("2026-06-01T00:00:00+00:00"))
    assert count(out, "files") == 1
    assert count(out, "match_decisions") == 1


def test_source_observations_copied_verbatim(tmp_path: Path) -> None:
    src = make_catalog(
        tmp_path / "src.db",
        {
            "files": [{"ed2k_hash": _H, "size_bytes": 1}],
            "sources": [{"user_hash": "u1"}],
            "source_observations": [
                {
                    "user_hash": "u1",
                    "ed2k_hash": _H,
                    "raw_meta": "[]",
                    "observed_at": "2026-01-01T00:00:00.000000+00:00",
                    "node_id": "n",
                }
            ],
        },
    )
    out = tmp_path / "out.db"
    compact_catalog(src, out, keep_recent_days=90, clock=_clock("2026-06-01T00:00:00+00:00"))
    assert count(out, "sources") == 1
    assert count(out, "source_observations") == 1


def test_idempotent_on_already_compacted_source(tmp_path: Path) -> None:
    src = _source(tmp_path / "src.db", [_obs("f.avi", 1, 0, "2026-01-10T01:00:00.000000+00:00")])
    out1 = tmp_path / "out1.db"
    compact_catalog(src, out1, keep_recent_days=90, clock=_clock("2026-06-01T00:00:00+00:00"))
    out2 = tmp_path / "out2.db"
    compact_catalog(out1, out2, keep_recent_days=90, clock=_clock("2026-06-01T00:00:00+00:00"))
    assert read_ranges(out2) == read_ranges(out1)


def test_preexisting_ranges_copied_verbatim(tmp_path: Path) -> None:
    src = make_catalog(tmp_path / "src.db", {"files": [{"ed2k_hash": _H, "size_bytes": 1}]})
    insert_ranges(
        src, [(_H, "2025-12-01", '["x"]', '["n"]', 2, "2025-12-01", "2025-12-01", 1, 3, 4, 0, 1, 1)]
    )
    out = tmp_path / "out.db"
    compact_catalog(src, out, keep_recent_days=90, clock=_clock("2026-06-01T00:00:00+00:00"))
    assert [r[1] for r in read_ranges(out)] == ["2025-12-01"]


def test_no_old_observations_is_a_clean_noop(tmp_path: Path) -> None:
    src = _source(tmp_path / "src.db", [_obs("f.avi", 1, 0, "2026-05-31T01:00:00.000000+00:00")])
    out = tmp_path / "out.db"
    compact_catalog(src, out, keep_recent_days=90, clock=_clock("2026-06-01T00:00:00+00:00"))
    assert read_ranges(out) == []
    assert count(out, "file_observations") == 1


def test_output_has_append_only_ranges(tmp_path: Path) -> None:
    src = _source(tmp_path / "src.db", [_obs("f.avi", 1, 0, "2026-01-10T01:00:00.000000+00:00")])
    out = tmp_path / "out.db"
    compact_catalog(src, out, keep_recent_days=90, clock=_clock("2026-06-01T00:00:00+00:00"))
    connection = open_catalog(out)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM file_observation_ranges")
    finally:
        connection.close()


def test_unattachable_source_errors(tmp_path: Path) -> None:
    bad = tmp_path / "garbage.db"
    bad.write_bytes(b"not a sqlite database header" * 8)
    with pytest.raises(CompactError, match="cannot attach"):
        compact_catalog(
            bad, tmp_path / "out.db", keep_recent_days=90, clock=_clock("2026-06-01T00:00:00+00:00")
        )


def test_broken_schema_source_rolls_back(tmp_path: Path) -> None:
    broken = tmp_path / "broken.db"
    raw = sqlite3.connect(broken)
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("CREATE TABLE files (ed2k_hash TEXT PRIMARY KEY, size_bytes INTEGER)")
    raw.commit()
    raw.close()
    with pytest.raises(CompactError, match="compaction of .* failed"):
        compact_catalog(
            broken,
            tmp_path / "out.db",
            keep_recent_days=90,
            clock=_clock("2026-06-01T00:00:00+00:00"),
        )


@pytest.mark.parametrize(("observation", "covered"), COVER_CASES)
def test_the_sql_covered_predicate_agrees_with_the_domain(
    tmp_path: Path, observation: ObservationRow, covered: bool
) -> None:
    src = make_catalog(tmp_path / "src.db", {"files": [{"ed2k_hash": _H, "size_bytes": 1}]})
    insert_ranges(src, [astuple(COVERING_RANGE)])
    connection = open_catalog(src)
    try:
        row = connection.execute(
            f"SELECT {covered_by_range('main.file_observation_ranges', 'o')}"
            " FROM (SELECT ? AS ed2k_hash, ? AS observed_at, ? AS node_id) AS o",
            (observation.ed2k_hash, observation.observed_at, observation.node_id),
        ).fetchone()
    finally:
        connection.close()
    assert bool(row[0]) is covered


def test_a_raw_observation_already_in_a_range_is_not_counted_again(tmp_path: Path) -> None:
    # A merged catalog may hold both forms of a day; only the other node's row is new there.
    src = _source(
        tmp_path / "src.db",
        [
            _obs("f.avi", 1, 0, "2026-01-10T01:00:00.000000+00:00"),
            _obs("f.avi", 9, 2, "2026-01-10T20:00:00.000000+00:00"),
            _obs("g.avi", 4, 1, "2026-01-10T21:00:00.000000+00:00", node="n2"),
            _obs("f.avi", 3, 0, "2026-05-31T01:00:00.000000+00:00"),
        ],
    )
    covering = [
        (_H, "2026-01-10", '["f.avi"]', '["n1"]', 2, "2026-01-10T01:00:00.000000+00:00",
         "2026-01-10T20:00:00.000000+00:00", 1, 9, 10, 0, 2, 2),
        (_H, "2026-05-31", '["f.avi"]', '["n1"]', 1, "2026-05-31T01:00:00.000000+00:00",
         "2026-05-31T01:00:00.000000+00:00", 3, 3, 3, 0, 0, 0),
    ]  # fmt: skip
    insert_ranges(src, covering)
    out = tmp_path / "out.db"
    compact_catalog(src, out, keep_recent_days=90, clock=_clock("2026-06-01T00:00:00+00:00"))
    other_node = (_H, "2026-01-10", '["g.avi"]', '["n2"]', 1, "2026-01-10T21:00:00.000000+00:00",
                  "2026-01-10T21:00:00.000000+00:00", 4, 4, 4, 1, 1, 1)  # fmt: skip
    assert read_ranges(out) == sorted([*covering, other_node])
    assert count(out, "file_observations") == 0  # the recent raw row is covered as well
