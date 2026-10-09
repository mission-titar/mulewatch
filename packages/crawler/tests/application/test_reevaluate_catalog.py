"""Tests for the backfill use-case ``reevaluate_catalog`` (spec §7, plan Task 5).

Real engine + real SQLite catalog repo (same convention as ``test_decisions.py``: "real
repos on tmp_path"); only ``signal``/``telemetry`` are fakes (``RecordingSignal``/
``RecordingTelemetry``). Each hash under test is seeded via ``catalog.record_observation``
first so it shows up in ``iter_reevaluation_rows`` (the source the backfill iterates).
"""

import logging
import sqlite3

import pytest

from catalog_matching.engine import DecisionRecord, Explanation, MatchDecision, MatchingEngine
from mulewatch.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from mulewatch.application import reevaluate_catalog as reevaluate_module
from mulewatch.application.reevaluate_catalog import ReevalSummary, reevaluate_catalog
from mulewatch.application.run_download_cycle import DOWNLOAD_NUDGE_SUBJECT
from mulewatch.domain.file_key import FileKey, Network
from mulewatch.domain.observation import FileObservation
from mulewatch.domain.retraction import RETRACTED_TIER
from tests.application.fakes import RecordingSignal, RecordingTelemetry
from tests.catalog_rows import decision_tiers

_HASH_DL = "31d6cfe0d16ae931b73c59d7e0c089c0"
_HASH_CAT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_HASH_MULTI = "dddddddddddddddddddddddddddddddd"
_DL_NAME = "Keroro N°062A Les demoiselles cambrioleuses.avi"
_CAT_NAME = "keroro something.avi"
_MULTI_NAME = "Keroro 062 teletoon.avi"  # → 062A + 062B download (post-fan-out)


def _obs(ed2k_hash: str, filename: str) -> FileObservation:
    return FileObservation(
        file=FileKey(Network.ED2K, ed2k_hash),
        filename=filename,
        size_bytes=234_000_000,
        source_count=3,
        keyword="keroro",
    )


def _legacy_row(target_id: str, rule_name: str, tier: str) -> MatchDecision:
    return MatchDecision(
        target_id=target_id,
        rule_name=rule_name,
        tier=tier,
        explanation=Explanation(
            target_id=target_id, rules_fired=(), tokens_matched=(), coverage_values=()
        ),
    )


@pytest.mark.asyncio
async def test_two_changed_rows_are_all_evaluated_and_written(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_DL, _DL_NAME))  # never decided -> will change
    catalog.record_observation(_obs(_HASH_CAT, _CAT_NAME))  # never decided -> will change
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    summary = await reevaluate_catalog(
        catalog=catalog, engine=engine, signal=signal, telemetry=telemetry
    )
    assert summary == ReevalSummary(evaluated=2, written=2)
    tiers = dict(decision_tiers(catalog_connection))
    assert tiers == {_HASH_DL: "download", _HASH_CAT: "catalog"}
    assert len(telemetry.events) == 2
    # Iteration is ORDER BY ed2k_hash: "31d6..." sorts before "aaaa..." (ASCII '3' < 'a').
    assert signal.signalled == [DOWNLOAD_NUDGE_SUBJECT]


@pytest.mark.asyncio
async def test_unchanged_row_is_evaluated_but_not_written(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_CAT, _CAT_NAME))
    candidate = _obs(_HASH_CAT, _CAT_NAME).to_candidate()
    decisions = engine.evaluate(candidate)
    assert decisions  # non-empty
    # pre-seed the "already correct" verdict
    catalog.record_decision(FileKey(Network.ED2K, _HASH_CAT), decisions[0])
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    summary = await reevaluate_catalog(
        catalog=catalog, engine=engine, signal=signal, telemetry=telemetry
    )
    assert summary == ReevalSummary(evaluated=1, written=0)
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 1
    assert telemetry.events == []
    assert signal.signalled == []


@pytest.mark.asyncio
async def test_repository_error_on_one_row_is_absorbed_and_sweep_continues(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_DL, _DL_NAME))
    catalog.record_observation(_obs(_HASH_CAT, _CAT_NAME))
    # TEST trigger: makes the match_decisions INSERT fail for ONE hash only -> that row's
    # helper call raises RepositoryError, the other row must still be processed.
    catalog_connection.execute(
        "CREATE TRIGGER boom BEFORE INSERT ON match_decisions"
        f" WHEN NEW.ed2k_hash = '{_HASH_DL}'"
        " BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    summary = await reevaluate_catalog(
        catalog=catalog, engine=engine, signal=signal, telemetry=telemetry
    )
    assert summary == ReevalSummary(evaluated=2, written=1)
    assert decision_tiers(catalog_connection) == [(_HASH_CAT, "catalog")]
    assert catalog.last_decisions(FileKey(Network.ED2K, _HASH_DL)) == {}


@pytest.mark.asyncio
async def test_empty_catalogue_yields_zero_summary(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    telemetry = RecordingTelemetry()
    signal = RecordingSignal()
    summary = await reevaluate_catalog(
        catalog=catalog, engine=engine, signal=signal, telemetry=telemetry
    )
    assert summary == ReevalSummary(evaluated=0, written=0)
    assert telemetry.events == []
    assert signal.signalled == []


@pytest.mark.asyncio
async def test_backfill_retracts_a_legacy_arbitrary_target_row(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    # §10: an old "unidentified" row under an arbitrary target_id (001A/catalog) is retracted
    # by the set-diff when the file becomes identified (062A + 062B) on the backfill pass.
    catalog.record_observation(_obs(_HASH_MULTI, _MULTI_NAME))
    catalog.record_decision(
        FileKey(Network.ED2K, _HASH_MULTI), _legacy_row("001A", "keroro_large", "catalog")
    )
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    summary = await reevaluate_catalog(
        catalog=catalog, engine=engine, signal=signal, telemetry=telemetry
    )
    assert summary == ReevalSummary(evaluated=1, written=3)  # 062A + 062B + retract 001A
    assert catalog.last_decisions(FileKey(Network.ED2K, _HASH_MULTI)) == {
        "062A": DecisionRecord(target_id="062A", rule_name="numero_nu_confirmed", tier="download"),
        "062B": DecisionRecord(target_id="062B", rule_name="numero_nu_confirmed", tier="download"),
        "001A": DecisionRecord(target_id="001A", rule_name="", tier=RETRACTED_TIER),
    }


@pytest.mark.asyncio
async def test_backfill_ignores_the_legacy_empty_sentinel(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    # §10: the old whole-file retraction sentinel (target_id="") is invisible to the set-diff.
    catalog.record_observation(_obs(_HASH_MULTI, _MULTI_NAME))
    catalog.record_decision(FileKey(Network.ED2K, _HASH_MULTI), _legacy_row("", "", RETRACTED_TIER))
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    summary = await reevaluate_catalog(
        catalog=catalog, engine=engine, signal=signal, telemetry=telemetry
    )
    assert summary == ReevalSummary(evaluated=1, written=2)  # only 062A + 062B; "" untouched
    assert set(catalog.last_decisions(FileKey(Network.ED2K, _HASH_MULTI))) == {"062A", "062B"}
    assert (
        catalog_connection.execute(
            "SELECT count(*) FROM match_decisions WHERE target_id = ''"
        ).fetchone()[0]
        == 1
    )


@pytest.mark.asyncio
async def test_backfill_judges_a_hash_on_all_its_names_not_only_the_latest(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    catalog.record_observation(_obs(_HASH_DL, _DL_NAME))
    catalog.record_observation(_obs(_HASH_DL, "random.txt"))  # latest name matches nothing
    summary = await reevaluate_catalog(
        catalog=catalog, engine=engine, signal=RecordingSignal(), telemetry=RecordingTelemetry()
    )
    assert summary == ReevalSummary(evaluated=1, written=1)
    assert catalog.last_decisions(FileKey(Network.ED2K, _HASH_DL)) == {
        "062A": DecisionRecord(target_id="062A", rule_name="id_segment_exact", tier="download")
    }


@pytest.mark.asyncio
async def test_progress_is_logged_at_each_multiple_of_the_cadence_only(
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(reevaluate_module, "_PROGRESS_EVERY", 2)
    catalog.record_observation(_obs(_HASH_DL, _DL_NAME))
    catalog.record_observation(_obs(_HASH_CAT, _CAT_NAME))
    catalog.record_observation(_obs(_HASH_MULTI, "random.txt"))
    with caplog.at_level(logging.INFO, logger="mulewatch.application.reevaluate_catalog"):
        await reevaluate_catalog(
            catalog=catalog,
            engine=engine,
            signal=RecordingSignal(),
            telemetry=RecordingTelemetry(),
        )
    assert [record.getMessage() for record in caplog.records] == [
        "catalogue re-evaluation: 2/3 files, 2 rows written"
    ]


def test_progress_cadence_is_every_200_files() -> None:
    assert reevaluate_module._PROGRESS_EVERY == 200
