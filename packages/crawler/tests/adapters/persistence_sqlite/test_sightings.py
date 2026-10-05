"""Sightings: every observation read, raw or compacted, through one module (spec 2026-10-05)."""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from mulewatch.adapters.persistence_sqlite import sightings
from mulewatch.adapters.persistence_sqlite.connection import open_catalog
from mulewatch.domain.observation import Sighting

_A, _B, _C = "a" * 32, "b" * 32, "c" * 32


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    catalog = open_catalog(tmp_path / "catalog.db")
    yield catalog
    catalog.close()


def _file(connection: sqlite3.Connection, ed2k_hash: str, size_bytes: int = 4242) -> None:
    connection.execute(
        "INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", (ed2k_hash, size_bytes)
    )


def _raw(
    connection: sqlite3.Connection,
    ed2k_hash: str,
    name: str,
    at: str,
    *,
    sources: int = 5,
    media: int | None = None,
) -> None:
    connection.execute(
        "INSERT INTO file_observations (ed2k_hash, filename, size_bytes, source_count,"
        " complete_source_count, media_length_sec, bitrate_kbps, raw_meta, keyword,"
        " observed_at, node_id) VALUES (?, ?, 100, ?, 1, ?, ?, '[]', 'keroro', ?, 'n1')",
        (ed2k_hash, name, sources, media, None if media is None else 900, at),
    )


def _range(connection: sqlite3.Connection, ed2k_hash: str, day: str, names: list[str]) -> None:
    connection.execute(
        "INSERT INTO file_observation_ranges (ed2k_hash, bucket, filenames, node_ids,"
        " observation_count, first_observed_at, last_observed_at, source_count_min,"
        " source_count_max, source_count_sum, complete_source_count_min,"
        " complete_source_count_max, complete_source_count_sum)"
        " VALUES (?, ?, ?, '[\"n1\"]', 7, ?, ?, 2, 9, 30, 0, 1, 1)",
        (ed2k_hash, day, json.dumps(sorted(names)), f"{day}T01:00", f"{day}T23:00"),
    )


def _raw_sighting(name: str, at: str, *, media: int | None = None) -> Sighting:
    return Sighting(
        ed2k_hash=_A,
        names=(name,),
        observation_count=1,
        first_seen=at,
        last_seen=at,
        source_count_min=5,
        source_count_max=5,
        size_bytes=100,
        media_length_sec=media,
        bitrate_kbps=None if media is None else 900,
        keyword="keroro",
        compacted=False,
    )


def _range_sighting(day: str, names: tuple[str, ...], ed2k_hash: str = _A) -> Sighting:
    return Sighting(
        ed2k_hash=ed2k_hash,
        names=names,
        observation_count=7,
        first_seen=f"{day}T01:00",
        last_seen=f"{day}T23:00",
        source_count_min=2,
        source_count_max=9,
        size_bytes=4242,
        media_length_sec=None,
        bitrate_kbps=None,
        keyword=None,
        compacted=True,
    )


def test_latest_sighting_is_the_latest_raw_observation(connection: sqlite3.Connection) -> None:
    _file(connection, _A)
    _raw(connection, _A, "old.avi", "2026-06-01T10:00")
    _raw(connection, _A, "new.avi", "2026-06-02T10:00", media=60)
    _raw(connection, _A, "older.avi", "2026-05-01T10:00")
    latest = _raw_sighting("new.avi", "2026-06-02T10:00", media=60)
    assert sightings.latest_sighting(connection, _A) == latest


def test_latest_sighting_breaks_an_observed_at_tie_on_the_highest_id(
    connection: sqlite3.Connection,
) -> None:
    _file(connection, _A)
    _raw(connection, _A, "first.avi", "2026-06-01T10:00")
    _raw(connection, _A, "second.avi", "2026-06-01T10:00")
    latest = sightings.latest_sighting(connection, _A)
    assert latest is not None
    assert latest.names == ("second.avi",)


def test_latest_sighting_of_a_compacted_file_is_its_latest_range(
    connection: sqlite3.Connection,
) -> None:
    _file(connection, _A)
    _range(connection, _A, "2026-05-02", ["z.avi", "m.avi"])
    _range(connection, _A, "2026-05-01", ["a.avi"])
    latest = sightings.latest_sighting(connection, _A)
    assert latest == _range_sighting("2026-05-02", ("m.avi", "z.avi"))


def test_latest_sighting_prefers_a_raw_observation_over_a_later_range(
    connection: sqlite3.Connection,
) -> None:
    _file(connection, _A)
    _raw(connection, _A, "raw.avi", "2026-05-01T10:00")
    _range(connection, _A, "2026-06-01", ["later.avi"])
    assert sightings.latest_sighting(connection, _A) == _raw_sighting("raw.avi", "2026-05-01T10:00")


def test_latest_sighting_of_a_file_never_seen_or_unknown_is_none(
    connection: sqlite3.Connection,
) -> None:
    _file(connection, _A)
    assert sightings.latest_sighting(connection, _A) is None
    assert sightings.latest_sighting(connection, _B) is None


def test_iter_latest_sightings_yields_one_per_seen_file_sorted_by_hash(
    connection: sqlite3.Connection,
) -> None:
    for ed2k_hash in (_C, _B, _A):
        _file(connection, ed2k_hash)
    _range(connection, _C, "2026-05-01", ["c.avi"])
    _raw(connection, _A, "old.avi", "2026-06-01T10:00")
    _raw(connection, _A, "new.avi", "2026-06-02T10:00")
    assert list(sightings.iter_latest_sightings(connection)) == [
        _raw_sighting("new.avi", "2026-06-02T10:00"),
        _range_sighting("2026-05-01", ("c.avi",), ed2k_hash=_C),
    ]


def test_known_names_are_every_distinct_name_of_both_forms(
    connection: sqlite3.Connection,
) -> None:
    _file(connection, _A)
    _file(connection, _B)
    _raw(connection, _A, "raw.avi", "2026-06-01T10:00")
    _raw(connection, _A, "raw.avi", "2026-06-02T10:00")
    _range(connection, _A, "2026-05-01", ["old [ES].avi", "raw.avi"])
    _range(connection, _B, "2026-05-01", ["other.avi"])
    assert sightings.known_names(connection, _A) == ("old [ES].avi", "raw.avi")
    assert sightings.known_names(connection, _C) == ()


def test_sightings_are_the_timeline_of_both_forms_oldest_first(
    connection: sqlite3.Connection,
) -> None:
    _file(connection, _A)
    _file(connection, _B)
    _raw(connection, _A, "late.avi", "2026-06-02T10:00")
    _range(connection, _A, "2026-05-01", ["a.avi", "b.avi"])
    _raw(connection, _A, "tie 1.avi", "2026-05-15T10:00")
    _raw(connection, _A, "tie 2.avi", "2026-05-15T10:00")
    _raw(connection, _B, "other.avi", "2026-05-20T10:00")
    assert sightings.sightings(connection, _A) == (
        _range_sighting("2026-05-01", ("a.avi", "b.avi")),
        _raw_sighting("tie 1.avi", "2026-05-15T10:00"),
        _raw_sighting("tie 2.avi", "2026-05-15T10:00"),
        _raw_sighting("late.avi", "2026-06-02T10:00"),
    )
    assert sightings.sightings(connection, _C) == ()


@pytest.mark.parametrize(
    "sql",
    [sightings.SELECT_LATEST_SIGHTING, sightings.SELECT_LATEST_SIGHTINGS],
    ids=["one file", "every file"],
)
def test_latest_sighting_reads_seek_the_raw_table_through_its_index(
    connection: sqlite3.Connection, sql: str
) -> None:
    # Asserts the PLAN, not a duration: a scan of the raw table is 10.9M rows on the node.
    plan = [
        str(row[3])
        for row in connection.execute("EXPLAIN QUERY PLAN " + sql, (_A,) * sql.count("?"))
    ]
    assert any(
        step.startswith("SEARCH") and "idx_file_observations_hash_observed" in step for step in plan
    ), plan
    assert not any(step.startswith("SCAN") and "file_observations" in step for step in plan), plan
    assert not any(step.startswith("SCAN o") for step in plan), plan
