import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from catalog_matching.engine import Explanation, MatchDecision
from mulewatch.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from mulewatch.adapters.persistence_sqlite.connection import open_catalog
from mulewatch.domain.file_key import FileKey, Network
from mulewatch.domain.observation import FileObservation
from mulewatch.ports.catalog_repository import DownloadCandidate, ObservedFile

_A = FileKey(Network.ED2K, "a" * 32)
_B = FileKey(Network.ED2K, "b" * 32)
_C = FileKey(Network.ED2K, "c" * 32)
_NODE = "11111111-2222-3333-4444-555555555555"


class _AdvancingClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 6, 13, 10, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        moment = self._now
        self._now += timedelta(minutes=1)
        return moment


def _obs(file: FileKey, *, name: str = "Keroro.avi", size: int = 100) -> FileObservation:
    return FileObservation(
        file=file,
        filename=name,
        size_bytes=size,
        source_count=1,
        keyword="keroro",
    )


def _decision(tier: str, target_id: str = "062A") -> MatchDecision:
    return MatchDecision(
        target_id=target_id,
        rule_name="r",
        tier=tier,
        explanation=Explanation(
            target_id=target_id, rules_fired=("r",), tokens_matched=(), coverage_values=()
        ),
    )


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    catalog = open_catalog(tmp_path / "catalog.db")
    yield catalog
    catalog.close()


@pytest.fixture
def repository(connection: sqlite3.Connection) -> SqliteCatalogRepository:
    return SqliteCatalogRepository(connection, _NODE, clock=_AdvancingClock())


def test_download_decisions_includes_hash_whose_latest_verdict_is_download(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_A))
    repository.record_decision(_A, _decision("catalog"))
    repository.record_decision(_A, _decision("download"))  # more recent = download
    assert repository.download_decisions() == (DownloadCandidate(file=_A, target_id="062A"),)


def test_download_decisions_includes_a_single_download_only_decision(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_A))
    repository.record_decision(_A, _decision("download"))  # a single decision, = download
    assert repository.download_decisions() == (DownloadCandidate(file=_A, target_id="062A"),)


def test_download_decisions_excludes_hash_whose_latest_verdict_is_not_download(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_B))
    repository.record_decision(_B, _decision("download"))
    repository.record_decision(_B, _decision("catalog"))  # more recent = catalog
    assert repository.download_decisions() == ()


def test_download_decisions_isolates_per_hash(
    repository: SqliteCatalogRepository,
) -> None:
    # two distinct hashes in the same query: _A ends in download, _B ends in catalog.
    # The PARTITION BY file_id window must isolate them → only _A is returned.
    repository.record_observation(_obs(_A))
    repository.record_observation(_obs(_B))
    repository.record_decision(_A, _decision("catalog"))
    repository.record_decision(_A, _decision("download"))  # _A latest = download
    repository.record_decision(_B, _decision("download"))
    repository.record_decision(_B, _decision("catalog"))  # _B latest = catalog
    assert repository.download_decisions() == (DownloadCandidate(file=_A, target_id="062A"),)


def test_download_decisions_is_empty_with_no_decisions(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_C))
    assert repository.download_decisions() == ()


def test_download_decisions_returns_both_segments_of_one_hash(
    repository: SqliteCatalogRepository,
) -> None:
    # spec §6: a whole-episode file both segments in download must NOT lose a candidate.
    repository.record_observation(_obs(_A))
    repository.record_decision(_A, _decision("download", "062A"))
    repository.record_decision(_A, _decision("download", "062B"))
    assert repository.download_decisions() == (
        DownloadCandidate(file=_A, target_id="062A"),
        DownloadCandidate(file=_A, target_id="062B"),
    )


def test_download_decisions_isolates_per_target_within_one_hash(
    repository: SqliteCatalogRepository,
) -> None:
    # same hash, two targets: 062A latest=download, 062B latest=catalog → only 062A.
    repository.record_observation(_obs(_A))
    repository.record_decision(_A, _decision("download", "062A"))
    repository.record_decision(_A, _decision("catalog", "062B"))
    assert repository.download_decisions() == (DownloadCandidate(file=_A, target_id="062A"),)


def test_last_observation_returns_filename_and_size(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_A, name="Keroro 062A.avi", size=4242))
    assert repository.last_observation(_A) == ObservedFile(
        filename="Keroro 062A.avi", size_bytes=4242
    )


def test_last_observation_returns_the_most_recent(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_A, name="old.avi", size=1))
    repository.record_observation(_obs(_A, name="new.avi", size=2))
    assert repository.last_observation(_A) == ObservedFile(filename="new.avi", size_bytes=2)


def test_last_observation_unknown_hash_is_none(repository: SqliteCatalogRepository) -> None:
    assert repository.last_observation(_A) is None


def test_best_observation_is_the_clean_name_and_its_size(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_A, name="clean.avi", size=7))
    assert repository.best_observation(_A) == ObservedFile(filename="clean.avi", size_bytes=7)
    assert repository.best_observation(_B) is None


def test_known_filenames_are_the_distinct_sorted_names_of_that_hash_only(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_A, name="b.avi", size=1))
    repository.record_observation(_obs(_A, name="a.avi", size=1))
    repository.record_observation(_obs(_A, name="b.avi", size=1))
    repository.record_observation(_obs(_B, name="other.avi", size=1))
    assert repository.known_filenames(_A) == ("a.avi", "b.avi")


def test_known_filenames_of_an_unknown_hash_is_empty(repository: SqliteCatalogRepository) -> None:
    assert repository.known_filenames(_A) == ()
