"""Sightings: every observation read, through one module (spec 2026-10-05)."""

import dataclasses
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from mulewatch.adapters.persistence_sqlite import sightings
from mulewatch.adapters.persistence_sqlite.connection import open_catalog
from mulewatch.domain.observation import Sighting
from tests.catalog_rows import insert_file, insert_observation

_A, _B, _C = "a" * 32, "b" * 32, "c" * 32


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    catalog = open_catalog(tmp_path / "catalog.db")
    yield catalog
    catalog.close()


def _raw(
    connection: sqlite3.Connection,
    ed2k_hash: str,
    name: str,
    at: str,
    *,
    sources: int = 5,
    media: int | None = None,
) -> None:
    insert_observation(
        connection,
        ed2k_hash,
        name,
        observed_at=at,
        source_count=sources,
        media_length_sec=media,
        bitrate_kbps=None if media is None else 900,
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
    )


def test_latest_sighting_is_the_latest_raw_observation(connection: sqlite3.Connection) -> None:
    insert_file(connection, _A)
    _raw(connection, _A, "old.avi", "2026-06-01T10:00")
    _raw(connection, _A, "new.avi", "2026-06-02T10:00", media=60)
    _raw(connection, _A, "older.avi", "2026-05-01T10:00")
    latest = _raw_sighting("new.avi", "2026-06-02T10:00", media=60)
    assert sightings.latest_sighting(connection, _A) == latest


def test_latest_sighting_breaks_an_observed_at_tie_on_the_highest_id(
    connection: sqlite3.Connection,
) -> None:
    insert_file(connection, _A)
    _raw(connection, _A, "first.avi", "2026-06-01T10:00")
    _raw(connection, _A, "second.avi", "2026-06-01T10:00")
    latest = sightings.latest_sighting(connection, _A)
    assert latest is not None
    assert latest.names == ("second.avi",)


def test_latest_sighting_of_a_file_never_seen_or_unknown_is_none(
    connection: sqlite3.Connection,
) -> None:
    insert_file(connection, _A)
    assert sightings.latest_sighting(connection, _A) is None
    assert sightings.latest_sighting(connection, _B) is None


def test_iter_latest_sightings_yields_one_per_seen_file_sorted_by_hash(
    connection: sqlite3.Connection,
) -> None:
    for ed2k_hash in (_C, _B, _A):
        insert_file(connection, ed2k_hash)
    _raw(connection, _C, "c.avi", "2026-05-01T10:00")
    _raw(connection, _A, "old.avi", "2026-06-01T10:00")
    _raw(connection, _A, "new.avi", "2026-06-02T10:00")
    assert list(sightings.iter_latest_sightings(connection)) == [
        _raw_sighting("new.avi", "2026-06-02T10:00"),
        dataclasses.replace(_raw_sighting("c.avi", "2026-05-01T10:00"), ed2k_hash=_C),
    ]


def test_known_names_are_every_distinct_name_of_the_file_sorted(
    connection: sqlite3.Connection,
) -> None:
    insert_file(connection, _A)
    insert_file(connection, _B)
    _raw(connection, _A, "raw.avi", "2026-06-01T10:00")
    _raw(connection, _A, "raw.avi", "2026-06-02T10:00")
    _raw(connection, _A, "old [ES].avi", "2026-05-01T10:00")
    _raw(connection, _B, "other.avi", "2026-05-01T10:00")
    assert sightings.known_names(connection, _A) == ("old [ES].avi", "raw.avi")
    assert sightings.known_names(connection, _C) == ()


def test_best_name_of_raw_rows_is_the_most_sourced(connection: sqlite3.Connection) -> None:
    insert_file(connection, _A)
    _raw(connection, _A, "clean.avi", "2026-06-01T10:00", sources=8)
    _raw(connection, _A, "mojibake.avi", "2026-06-02T10:00", sources=3)
    assert sightings.best_name(connection, _A) == ("clean.avi", 100)


def test_best_name_takes_the_latest_seen_on_a_source_tie(connection: sqlite3.Connection) -> None:
    insert_file(connection, _A)
    _raw(connection, _A, "later.avi", "2026-06-02T10:00")
    _raw(connection, _A, "earlier.avi", "2026-06-01T10:00")
    assert sightings.best_name(connection, _A) == ("later.avi", 100)


def test_best_name_of_a_file_never_seen_is_none(connection: sqlite3.Connection) -> None:
    insert_file(connection, _A)
    assert sightings.best_name(connection, _A) is None


def test_sightings_are_the_timeline_oldest_first(connection: sqlite3.Connection) -> None:
    insert_file(connection, _A)
    insert_file(connection, _B)
    _raw(connection, _A, "late.avi", "2026-06-02T10:00")
    _raw(connection, _A, "early.avi", "2026-05-01T10:00")
    _raw(connection, _A, "tie 1.avi", "2026-05-15T10:00")
    _raw(connection, _A, "tie 2.avi", "2026-05-15T10:00")
    _raw(connection, _B, "other.avi", "2026-05-20T10:00")
    assert sightings.sightings(connection, _A) == (
        _raw_sighting("early.avi", "2026-05-01T10:00"),
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
