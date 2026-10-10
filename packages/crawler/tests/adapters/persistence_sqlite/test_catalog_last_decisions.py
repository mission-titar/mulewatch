import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from catalog_matching.engine import DecisionRecord, Explanation, MatchDecision
from p2pwatch.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from p2pwatch.adapters.persistence_sqlite.connection import open_catalog
from p2pwatch.domain.file_key import FileKey, Network
from p2pwatch.domain.observation import FileObservation
from p2pwatch.domain.retraction import RETRACTED_TIER

_A = FileKey(Network.ED2K, "a" * 32)
_NODE = "11111111-2222-3333-4444-555555555555"


class _AdvancingClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 7, 6, 10, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        moment = self._now
        self._now += timedelta(minutes=1)
        return moment


def _obs(file: FileKey) -> FileObservation:
    return FileObservation(
        file=file,
        filename="Keroro.avi",
        size_bytes=100,
        source_count=1,
        keyword="keroro",
    )


def _decision(rule_name: str, tier: str, target_id: str = "062A") -> MatchDecision:
    return MatchDecision(
        target_id=target_id,
        rule_name=rule_name,
        tier=tier,
        explanation=Explanation(
            target_id=target_id, rules_fired=(rule_name,), tokens_matched=(), coverage_values=()
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


def test_last_decisions_is_empty_when_never_decided(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_A))
    assert repository.last_decisions(_A) == {}


def test_last_decisions_returns_the_latest_record_per_target(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_A))
    repository.record_decision(_A, _decision("keroro_large", "catalog", "062A"))
    repository.record_decision(_A, _decision("id_segment_exact", "download", "062A"))  # newer
    repository.record_decision(_A, _decision("numero_nu", "notify", "062B"))
    assert repository.last_decisions(_A) == {
        "062A": DecisionRecord(target_id="062A", rule_name="id_segment_exact", tier="download"),
        "062B": DecisionRecord(target_id="062B", rule_name="numero_nu", tier="notify"),
    }


def test_last_decisions_includes_a_target_whose_latest_tier_is_retracted(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_A))
    repository.record_decision(_A, _decision("id_segment_exact", "download", "062A"))
    repository.record_decision(_A, _decision("", RETRACTED_TIER, "062A"))
    assert repository.last_decisions(_A) == {
        "062A": DecisionRecord(target_id="062A", rule_name="", tier=RETRACTED_TIER)
    }


def test_last_decisions_excludes_the_legacy_empty_sentinel(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_A))
    repository.record_decision(_A, _decision("numero_nu", "notify", "062A"))
    repository.record_decision(_A, _decision("", RETRACTED_TIER, ""))  # legacy target_id="" row
    assert repository.last_decisions(_A) == {
        "062A": DecisionRecord(target_id="062A", rule_name="numero_nu", tier="notify")
    }


def test_last_decisions_for_unknown_hash_is_empty(
    repository: SqliteCatalogRepository,
) -> None:
    assert repository.last_decisions(_A) == {}
