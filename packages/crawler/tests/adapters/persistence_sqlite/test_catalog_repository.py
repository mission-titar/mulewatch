import dataclasses
import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from catalog_matching.engine import DecisionRecord, Explanation, MatchDecision
from mulewatch.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from mulewatch.adapters.persistence_sqlite.connection import open_catalog
from mulewatch.adapters.persistence_sqlite.errors import PersistenceError
from mulewatch.domain.observation import FileObservation
from mulewatch.domain.retraction import RETRACTED_TIER
from mulewatch.ports.catalog_repository import CatalogRepository, ObservedFile, ReevalRow

_HASH = "31d6cfe0d16ae931b73c59d7e0c089c0"
_HASH_B = "b" * 32
_NODE = "11111111-2222-3333-4444-555555555555"
_FROZEN_NOW = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
_FROZEN_ISO = "2026-06-11T12:00:00.000000+00:00"


def _frozen_clock() -> datetime:
    return _FROZEN_NOW


class _AdvancingClock:
    """A clock that ticks forward on every call (to order two observations in time)."""

    def __init__(self) -> None:
        self._now = _FROZEN_NOW

    def __call__(self) -> datetime:
        moment = self._now
        self._now += timedelta(minutes=1)
        return moment


def _observation(
    *,
    filename: str = "Keroro 062A.avi",
    size_bytes: int = 234567890,
    media_length_sec: int | None = None,
    bitrate_kbps: int | None = None,
    codec: str | None = None,
    file_type: str | None = None,
) -> FileObservation:
    # media None by default (EC exposes NO media metadata — report 2026-06-11);
    # raw_meta with a DUPLICATE, wire order and non-ASCII (the three properties to preserve).
    return FileObservation(
        ed2k_hash=_HASH,
        filename=filename,
        size_bytes=size_bytes,
        source_count=5,
        complete_source_count=2,
        keyword="keroro",
        media_length_sec=media_length_sec,
        bitrate_kbps=bitrate_kbps,
        codec=codec,
        file_type=file_type,
        raw_meta=(("0x0308", "0"), ("0x0308", "0"), ("0x0999", "mystère")),
    )


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    catalog = open_catalog(tmp_path / "catalog.db")
    yield catalog
    catalog.close()


@pytest.fixture
def repository(connection: sqlite3.Connection) -> SqliteCatalogRepository:
    return SqliteCatalogRepository(connection, _NODE, clock=_frozen_clock)


def test_record_observation_round_trip(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_observation())
    file_row = connection.execute("SELECT ed2k_hash, size_bytes, aich_hash FROM files").fetchone()
    assert file_row == (_HASH, 234567890, None)
    row = connection.execute(
        "SELECT ed2k_hash, filename, size_bytes, source_count, complete_source_count,"
        " media_length_sec, bitrate_kbps, codec, file_type, raw_meta, keyword,"
        " observed_at, node_id FROM file_observations"
    ).fetchone()
    assert row == (
        _HASH,
        "Keroro 062A.avi",
        234567890,
        5,
        2,
        None,
        None,
        None,
        None,
        '[["0x0308", "0"], ["0x0308", "0"], ["0x0999", "mystère"]]',
        "keroro",
        _FROZEN_ISO,
        _NODE,
    )


def test_raw_meta_preserves_order_duplicates_and_non_ascii(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_observation())
    stored = connection.execute("SELECT raw_meta FROM file_observations").fetchone()[0]
    assert "mystère" in stored  # ensure_ascii=False: the accent is stored AS IS
    assert json.loads(stored) == [["0x0308", "0"], ["0x0308", "0"], ["0x0999", "mystère"]]


def test_record_observation_twice_first_seen_wins_in_files(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_observation())
    # Same hash, DIFFERENT size and name (hostile input, deviation 1 spec §5).
    repository.record_observation(_observation(filename="leurre.avi", size_bytes=999))
    assert connection.execute("SELECT size_bytes FROM files").fetchall() == [(234567890,)]
    observed_sizes = connection.execute(
        "SELECT size_bytes FROM file_observations ORDER BY id"
    ).fetchall()
    assert observed_sizes == [(234567890,), (999,)]  # the anomaly stays VISIBLE


def test_record_observation_with_media_metadata_and_default_clock(tmp_path: Path) -> None:
    connection = open_catalog(tmp_path / "catalog.db")
    try:
        repository = SqliteCatalogRepository(connection, _NODE)  # default clock (utc_now)
        repository.record_observation(
            _observation(media_length_sec=1474, bitrate_kbps=1200, codec="xvid", file_type="Video")
        )
        row = connection.execute(
            "SELECT media_length_sec, bitrate_kbps, codec, file_type, observed_at"
            " FROM file_observations"
        ).fetchone()
        assert row[:4] == (1474, 1200, "xvid", "Video")
        stamped = datetime.fromisoformat(row[4])
        assert stamped.tzinfo == UTC  # the default clock does stamp aware UTC
    finally:
        connection.close()


def test_record_observation_is_one_transaction(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    # Failure injected BETWEEN the two INSERTs: a TEST trigger makes the second fail.
    connection.execute(
        "CREATE TRIGGER boom BEFORE INSERT ON file_observations"
        " WHEN NEW.filename = '__boom__'"
        " BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    with pytest.raises(PersistenceError, match="injected failure"):
        repository.record_observation(_observation(filename="__boom__"))
    # ATOMICITY: the INSERT OR IGNORE into files was rolled back with the transaction.
    assert connection.execute("SELECT count(*) FROM files").fetchone()[0] == 0
    # The repository stays USABLE: rollback done, connection out of transaction.
    assert not connection.in_transaction
    repository.record_observation(_observation())
    assert connection.execute("SELECT count(*) FROM file_observations").fetchone()[0] == 1


def test_record_observation_rejects_non_canonical_hash(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    # INSERT OR IGNORE SILENTLY swallows a CHECK violation (documented SQLite
    # behavior): without Python validation BEFORE the transaction, a non-canonical hash
    # would only survive thanks to the foreign_keys pragma (opaque diagnostic), and a connection
    # without that pragma would commit an ORPHAN observation.
    upper = dataclasses.replace(_observation(), ed2k_hash=_HASH.upper())
    with pytest.raises(PersistenceError, match="non-canonical eD2k hash"):
        repository.record_observation(upper)
    assert connection.execute("SELECT count(*) FROM files").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM file_observations").fetchone()[0] == 0


def test_rollback_on_non_sqlite_error_keeps_connection_usable(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    # An isolated surrogate makes the parameter BINDING fail (UnicodeEncodeError, which
    # is NOT a sqlite3.Error): without a rollback on BaseException, the connection
    # would stay in_transaction=True and every later call would fail permanently
    # ("cannot start a transaction within a transaction").
    with pytest.raises(UnicodeEncodeError):
        repository.record_observation(_observation(filename="a\ud800"))
    assert not connection.in_transaction
    repository.record_observation(_observation())
    assert connection.execute("SELECT count(*) FROM file_observations").fetchone()[0] == 1


def test_outer_transaction_survives_record_observation_failure(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    # Nested-transaction contract: the repository's BEGIN fails ("cannot start a
    # transaction within a transaction") BEFORE the try → NO rollback is attempted,
    # the OUTER transaction and its pending rows SURVIVE.
    connection.execute("BEGIN")
    connection.execute(
        "INSERT INTO files (ed2k_hash, size_bytes, aich_hash) VALUES (?, 1, NULL)", (_HASH,)
    )
    with pytest.raises(PersistenceError, match="cannot start a transaction within a transaction"):
        repository.record_observation(_observation())
    assert connection.in_transaction  # the outer transaction is INTACT
    assert connection.execute("SELECT count(*) FROM files").fetchone()[0] == 1
    connection.execute("ROLLBACK")
    assert connection.execute("SELECT count(*) FROM files").fetchone()[0] == 0


def _decision() -> MatchDecision:
    return MatchDecision(
        target_id="062A",
        rule_name="exact_062a",
        tier="download",
        explanation=Explanation(
            target_id="062A",
            rules_fired=("exact_062a",),
            tokens_matched=("keroro",),
            coverage_values=(("titre", 0.91),),
        ),
    )


def test_record_decision_round_trip(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_observation())
    repository.record_decision(_HASH, _decision())
    row = connection.execute(
        "SELECT ed2k_hash, target_id, rule_name, tier, decided_at, node_id FROM match_decisions"
    ).fetchone()
    assert row == (_HASH, "062A", "exact_062a", "download", _FROZEN_ISO, _NODE)


def test_explanation_is_never_persisted(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_observation())
    repository.record_decision(_HASH, _decision())
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(match_decisions)").fetchall()
    }
    assert columns == {"id", "ed2k_hash", "target_id", "rule_name", "tier", "decided_at", "node_id"}


def test_record_decision_for_unknown_file_raises_persistence_error(
    repository: SqliteCatalogRepository,
) -> None:
    # FK violated (file never observed): sqlite3.IntegrityError WRAPPED, never bare.
    with pytest.raises(PersistenceError, match="FOREIGN KEY"):
        repository.record_decision("0" * 32, _decision())


def test_record_decision_rejects_non_canonical_hash(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    # Python validation BEFORE any transaction: an uppercase hash is rejected
    # with a clear message, no row is written.
    with pytest.raises(PersistenceError, match="non-canonical eD2k hash"):
        repository.record_decision(_HASH.upper(), _decision())
    assert connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 0


def test_repository_satisfies_the_port_structurally(
    repository: SqliteCatalogRepository,
) -> None:
    port: CatalogRepository = repository  # mypy proves structural satisfaction
    port.record_observation(_observation())


def test_record_retraction_round_trip(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_observation())
    repository.record_retraction(_HASH, "062A")
    row = connection.execute(
        "SELECT ed2k_hash, target_id, rule_name, tier, decided_at, node_id FROM match_decisions"
    ).fetchone()
    assert row == (_HASH, "062A", "", RETRACTED_TIER, _FROZEN_ISO, _NODE)
    assert repository.last_decisions(_HASH) == {
        "062A": DecisionRecord(target_id="062A", rule_name="", tier=RETRACTED_TIER)
    }


def test_record_retraction_rejects_non_canonical_hash(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    with pytest.raises(PersistenceError, match="non-canonical eD2k hash"):
        repository.record_retraction("NOTAHASH", "062A")
    assert connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 0


def test_record_retraction_for_unknown_file_raises_persistence_error(
    repository: SqliteCatalogRepository,
) -> None:
    # FK violated (file never observed): mirrors record_decision's own guard.
    with pytest.raises(PersistenceError, match="FOREIGN KEY"):
        repository.record_retraction("0" * 32, "062A")


def test_record_retraction_row_is_append_only(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_observation())
    repository.record_retraction(_HASH, "062A")
    with pytest.raises(sqlite3.IntegrityError, match="match_decisions is append-only"):
        connection.execute("UPDATE match_decisions SET tier = 'catalog'")
    with pytest.raises(sqlite3.IntegrityError, match="match_decisions is append-only"):
        connection.execute("DELETE FROM match_decisions")
    # The retracted row SURVIVED both attempts, untouched.
    assert repository.last_decisions(_HASH) == {
        "062A": DecisionRecord(target_id="062A", rule_name="", tier=RETRACTED_TIER)
    }


def test_iter_reevaluation_rows_returns_the_latest_observation_per_hash(
    connection: sqlite3.Connection,
) -> None:
    # _HASH gets TWO observations (advancing clock): only the LATEST must come back.
    # _HASH_B gets a single observation with media metadata present.
    repository = SqliteCatalogRepository(connection, _NODE, clock=_AdvancingClock())
    repository.record_observation(_observation(filename="old.avi", size_bytes=100))
    repository.record_observation(_observation(filename="new.avi", size_bytes=200))
    repository.record_observation(
        dataclasses.replace(
            _observation(
                filename="other.avi", size_bytes=300, media_length_sec=1234, bitrate_kbps=1500
            ),
            ed2k_hash=_HASH_B,
        )
    )
    rows = list(repository.iter_reevaluation_rows())
    assert rows == [
        ReevalRow(
            ed2k_hash=_HASH,
            filename="new.avi",
            size_bytes=200,
            media_length_sec=None,
            bitrate_kbps=None,
        ),
        ReevalRow(
            ed2k_hash=_HASH_B,
            filename="other.avi",
            size_bytes=300,
            media_length_sec=1234,
            bitrate_kbps=1500,
        ),
    ]


def test_iter_reevaluation_rows_is_empty_with_an_empty_catalogue(
    repository: SqliteCatalogRepository,
) -> None:
    assert list(repository.iter_reevaluation_rows()) == []


def test_iter_reevaluation_rows_breaks_an_observed_at_tie_on_the_highest_id(
    repository: SqliteCatalogRepository,
) -> None:
    # Frozen clock: both rows share observed_at, so the later INSERT (higher id) wins.
    repository.record_observation(_observation(filename="first.avi"))
    repository.record_observation(_observation(filename="second.avi"))
    assert [row.filename for row in repository.iter_reevaluation_rows()] == ["second.avi"]


def test_count_files_counts_catalogued_hashes_not_observations(
    repository: SqliteCatalogRepository,
) -> None:
    assert repository.count_files() == 0
    repository.record_observation(_observation())
    repository.record_observation(_observation())
    repository.record_observation(dataclasses.replace(_observation(), ed2k_hash=_HASH_B))
    assert repository.count_files() == 2


def _insert_file(connection: sqlite3.Connection, ed2k_hash: str, size_bytes: int) -> None:
    connection.execute(
        "INSERT INTO files (ed2k_hash, size_bytes, aich_hash) VALUES (?, ?, NULL)",
        (ed2k_hash, size_bytes),
    )


def _insert_range(
    connection: sqlite3.Connection, ed2k_hash: str, day: str, filenames: list[str]
) -> None:
    # A compacted day bucket, as the compactor writes it (canonical sorted JSON names).
    connection.execute(
        "INSERT INTO file_observation_ranges (ed2k_hash, bucket, filenames, node_ids,"
        " observation_count, first_observed_at, last_observed_at, source_count_min,"
        " source_count_max, source_count_sum, complete_source_count_min,"
        " complete_source_count_max, complete_source_count_sum)"
        " VALUES (?, ?, ?, '[]', 1, ?, ?, 1, 1, 1, 0, 0, 0)",
        (ed2k_hash, day, json.dumps(sorted(filenames)), f"{day}T00:00:00", f"{day}T23:00:00"),
    )


def test_known_filenames_include_the_names_of_compacted_ranges(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    # A file-level veto judges ALL names: an alias compacted away must not be forgotten.
    repository.record_observation(_observation(filename="raw.avi"))
    _insert_range(connection, _HASH, "2026-05-01", ["old [ES].avi", "raw.avi"])
    _insert_range(connection, _HASH, "2026-05-02", ["older.avi"])
    _insert_file(connection, _HASH_B, 1)
    _insert_range(connection, _HASH_B, "2026-05-01", ["other.avi"])
    assert repository.known_filenames(_HASH) == ("old [ES].avi", "older.avi", "raw.avi")


def test_iter_reevaluation_rows_keeps_a_file_left_with_ranges_only(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    _insert_file(connection, _HASH, 4242)
    _insert_range(connection, _HASH, "2026-05-02", ["z.avi", "m.avi"])
    _insert_range(connection, _HASH, "2026-05-01", ["a.avi"])
    assert list(repository.iter_reevaluation_rows()) == [
        ReevalRow(
            ed2k_hash=_HASH,
            filename="m.avi",
            size_bytes=4242,
            media_length_sec=None,
            bitrate_kbps=None,
        )
    ]


def test_iter_reevaluation_rows_prefers_the_raw_observation_over_ranges(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_observation(filename="raw.avi", media_length_sec=60))
    _insert_range(connection, _HASH, "2026-05-01", ["a.avi"])
    rows = list(repository.iter_reevaluation_rows())
    assert [(row.filename, row.media_length_sec) for row in rows] == [("raw.avi", 60)]


def test_iter_reevaluation_rows_skips_a_file_with_neither_observation_nor_range(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    _insert_file(connection, _HASH, 1)
    assert list(repository.iter_reevaluation_rows()) == []


def test_last_observation_of_a_file_left_with_ranges_only_falls_back_to_the_latest_range(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    # Same rule as the re-evaluation read: a compacted download candidate still gets a link.
    _insert_file(connection, _HASH, 4242)
    _insert_range(connection, _HASH, "2026-05-02", ["z.avi", "m.avi"])
    _insert_range(connection, _HASH, "2026-05-01", ["a.avi"])
    assert repository.last_observation(_HASH) == ObservedFile(filename="m.avi", size_bytes=4242)


def test_last_observation_prefers_the_raw_observation_over_ranges(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_observation(filename="raw.avi", size_bytes=7))
    _insert_range(connection, _HASH, "2026-05-01", ["a.avi"])
    assert repository.last_observation(_HASH) == ObservedFile(filename="raw.avi", size_bytes=7)


def test_last_observation_of_a_file_with_neither_observation_nor_range_is_none(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    _insert_file(connection, _HASH, 1)
    assert repository.last_observation(_HASH) is None
