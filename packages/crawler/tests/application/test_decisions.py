"""Tests for the shared decision helper (spec §7): set diff keyed by (hash, target_id).

Real engine + real SQLite catalog repo (mirrors ``test_record_observations.py``: "real repos
on tmp_path"); only ``signal``/``telemetry`` are fakes. ``_record`` observes the name first:
the helper judges the file on every name the catalog knows for it.
"""

import sqlite3

import pytest

from catalog_matching.engine import DecisionRecord, Explanation, MatchDecision, MatchingEngine
from mulewatch.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from mulewatch.application.decisions import record_decision_if_changed
from mulewatch.application.record_observations import record_observation
from mulewatch.application.run_download_cycle import DOWNLOAD_NUDGE_SUBJECT
from mulewatch.domain.file_key import FileKey, Network
from mulewatch.domain.observability.events import DecisionChange, DecisionsRecorded
from mulewatch.domain.observation import FileObservation
from mulewatch.domain.retraction import RETRACTED_TIER
from tests.application.fakes import RecordingSignal, RecordingTelemetry
from tests.catalog_rows import insert_file

_HASH_DL = "31d6cfe0d16ae931b73c59d7e0c089c0"
_HASH_CAT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_HASH_DISCARD = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_HASH_NEVER = "cccccccccccccccccccccccccccccccc"
_HASH_MULTI = "dddddddddddddddddddddddddddddddd"
_DL_NAME = "Keroro N°062A Les demoiselles cambrioleuses.avi"
_CAT_NAME = "keroro something.avi"
_DISCARD_NAME = "random.txt"
_MULTI_NAME = "Keroro 062 teletoon.avi"  # bare number + source marker → 062A + 062B download
# A file that pins a STABLE, specific target via its unique title (062A/notify/title_review),
# unlike _CAT_NAME whose catch-all target_id is an arbitrary min-key over the present targets.
_NOTIFY_NAME = "Keroro Les demoiselles cambrioleuses.avi"
_TITLE_A, _TITLE_B = "Les demoiselles cambrioleuses", "Le grand combat sous-marin"
_SIZE = 234_000_000


def _key(ed2k_hash: str) -> FileKey:
    return FileKey(Network.ED2K, ed2k_hash)


def _event(ed2k_hash: str, filename: str, *changes: DecisionChange) -> DecisionsRecorded:
    return DecisionsRecorded(_key(ed2k_hash), filename, _SIZE, changes)


def _obs(ed2k_hash: str, filename: str, sources: int = 3) -> FileObservation:
    return FileObservation(
        ed2k_hash=ed2k_hash,
        filename=filename,
        size_bytes=_SIZE,
        source_count=sources,
        complete_source_count=1,
        keyword="keroro",
    )


def _seed_decision(catalog: SqliteCatalogRepository, ed2k_hash: str, target_id: str) -> None:
    explanation = Explanation(
        target_id=target_id, rules_fired=(), tokens_matched=(), coverage_values=()
    )
    catalog.record_decision(
        _key(ed2k_hash), MatchDecision(target_id, "title_review", "notify", explanation)
    )


async def _record(
    ed2k_hash: str,
    filename: str,
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
    signal: RecordingSignal,
    telemetry: RecordingTelemetry,
) -> int:
    catalog.record_observation(_obs(ed2k_hash, filename))
    return await record_decision_if_changed(
        _key(ed2k_hash),
        _obs(ed2k_hash, filename).to_candidate(),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
    )


@pytest.mark.asyncio
async def test_new_decision_is_recorded_emitted_and_nudged(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    written = await _record(_HASH_DL, _DL_NAME, catalog, engine, signal, telemetry)
    assert written == 1
    assert catalog_connection.execute("SELECT target_id, tier FROM match_decisions").fetchone() == (
        "062A",
        "download",
    )
    assert telemetry.events == [
        _event(_HASH_DL, _DL_NAME, DecisionChange("062A", _TITLE_A, None, "download"))
    ]
    assert signal.signalled == [DOWNLOAD_NUDGE_SUBJECT]


@pytest.mark.asyncio
async def test_multi_segment_file_records_both_segments_then_is_idempotent(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    written = await _record(_HASH_MULTI, _MULTI_NAME, catalog, engine, signal, telemetry)
    assert written == 2
    assert catalog_connection.execute(
        "SELECT target_id, rule_name, tier FROM match_decisions ORDER BY id"
    ).fetchall() == [
        ("062A", "numero_nu_confirmed", "download"),
        ("062B", "numero_nu_confirmed", "download"),
    ]
    assert telemetry.events == [
        _event(
            _HASH_MULTI,
            _MULTI_NAME,
            DecisionChange("062A", _TITLE_A, None, "download"),
            DecisionChange("062B", _TITLE_B, None, "download"),
        )
    ]
    assert signal.signalled == [DOWNLOAD_NUDGE_SUBJECT, DOWNLOAD_NUDGE_SUBJECT]
    again = await _record(_HASH_MULTI, _MULTI_NAME, catalog, engine, signal, telemetry)
    assert again == 0
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 2


@pytest.mark.asyncio
async def test_changed_decision_is_reappended_emitted_and_nudged(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    await _record(_HASH_DL, _NOTIFY_NAME, catalog, engine, signal, telemetry)
    written = await _record(_HASH_DL, _DL_NAME, catalog, engine, signal, telemetry)
    assert written == 1
    tiers = [
        row[0]
        for row in catalog_connection.execute(
            "SELECT tier FROM match_decisions ORDER BY id"
        ).fetchall()
    ]
    assert tiers == ["notify", "download"]
    assert [e.changes for e in telemetry.events if isinstance(e, DecisionsRecorded)] == [
        (DecisionChange("062A", _TITLE_A, None, "notify"),),
        (DecisionChange("062A", _TITLE_A, "notify", "download"),),
    ]
    assert signal.signalled == [DOWNLOAD_NUDGE_SUBJECT]


@pytest.mark.asyncio
async def test_unchanged_decision_is_not_reappended_emitted_or_signalled(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    first = await _record(_HASH_CAT, _CAT_NAME, catalog, engine, signal, telemetry)
    second = await _record(_HASH_CAT, _CAT_NAME, catalog, engine, signal, telemetry)
    assert (first, second) == (1, 0)
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 1
    assert len(telemetry.events) == 1
    assert signal.signalled == []


@pytest.mark.asyncio
async def test_a_target_no_name_supports_is_retracted_without_nudge(
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_CAT, _DISCARD_NAME))
    _seed_decision(catalog, _HASH_CAT, "062A")
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    written = await _record(_HASH_CAT, _DISCARD_NAME, catalog, engine, signal, telemetry)
    assert written == 1
    assert catalog.last_decisions(_key(_HASH_CAT)) == {
        "062A": DecisionRecord(target_id="062A", rule_name="", tier=RETRACTED_TIER)
    }
    assert telemetry.events == [
        _event(_HASH_CAT, _DISCARD_NAME, DecisionChange("062A", _TITLE_A, "notify", RETRACTED_TIER))
    ]
    assert signal.signalled == []


@pytest.mark.asyncio
async def test_every_unsupported_target_is_retracted(
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_MULTI, _DISCARD_NAME))
    _seed_decision(catalog, _HASH_MULTI, "062A")
    _seed_decision(catalog, _HASH_MULTI, "062B")
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    written = await _record(_HASH_MULTI, _DISCARD_NAME, catalog, engine, signal, telemetry)
    assert written == 2
    assert catalog.last_decisions(_key(_HASH_MULTI)) == {
        "062A": DecisionRecord(target_id="062A", rule_name="", tier=RETRACTED_TIER),
        "062B": DecisionRecord(target_id="062B", rule_name="", tier=RETRACTED_TIER),
    }
    assert len(telemetry.events) == 1


@pytest.mark.asyncio
async def test_already_retracted_then_none_is_a_no_op(
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
) -> None:
    catalog.record_observation(_obs(_HASH_CAT, _DISCARD_NAME))
    _seed_decision(catalog, _HASH_CAT, "062A")
    catalog.record_retraction(_key(_HASH_CAT), "062A")
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    written = await _record(_HASH_CAT, _DISCARD_NAME, catalog, engine, signal, telemetry)
    assert written == 0
    assert telemetry.events == []


@pytest.mark.asyncio
async def test_never_matched_then_none_is_a_no_op(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    written = await _record(_HASH_NEVER, _DISCARD_NAME, catalog, engine, signal, telemetry)
    assert written == 0
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 0
    assert telemetry.events == []
    assert signal.signalled == []


@pytest.mark.asyncio
async def test_non_download_tier_decision_does_not_nudge_the_download_subject(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    written = await _record(_HASH_CAT, _CAT_NAME, catalog, engine, signal, telemetry)
    assert written == 1
    assert signal.signalled == []


@pytest.mark.asyncio
async def test_a_second_name_that_matches_nothing_does_not_retract_the_first(
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
) -> None:
    # Regression (spec amuleapi-migration §8.4): names of one hash used to alternate verdicts.
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    written = [
        await record_observation(
            _obs(_HASH_DL, name),
            catalog=catalog,
            engine=engine,
            signal=signal,
            telemetry=telemetry,
            network="ed2k",
        )
        for name in (_DL_NAME, _DISCARD_NAME, _DL_NAME, _DISCARD_NAME)
    ]
    assert written == [1, 0, 0, 0]
    assert catalog.last_decisions(_key(_HASH_DL)) == {
        "062A": DecisionRecord(target_id="062A", rule_name="id_segment_exact", tier="download")
    }


@pytest.mark.asyncio
async def test_a_weaker_name_seen_again_does_not_downgrade_the_verdict(
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
) -> None:
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    written = [
        await _record(_HASH_DL, name, catalog, engine, signal, telemetry)
        for name in (_NOTIFY_NAME, _DL_NAME, _NOTIFY_NAME, _DL_NAME)
    ]
    assert written == [1, 1, 0, 0]
    assert catalog.last_decisions(_key(_HASH_DL))["062A"].tier == "download"


@pytest.mark.asyncio
async def test_the_event_names_the_file_by_its_most_sourced_name(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    catalog.record_observation(_obs(_HASH_DL, _DL_NAME, sources=9))
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    await _record(_HASH_DL, _NOTIFY_NAME, catalog, engine, signal, telemetry)
    assert [e.filename for e in telemetry.events if isinstance(e, DecisionsRecorded)] == [_DL_NAME]


@pytest.mark.asyncio
async def test_a_retracted_target_gone_from_the_targets_has_no_title(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    catalog.record_observation(_obs(_HASH_CAT, _DISCARD_NAME))
    _seed_decision(catalog, _HASH_CAT, "999Z")
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    await _record(_HASH_CAT, _DISCARD_NAME, catalog, engine, signal, telemetry)
    assert telemetry.events == [
        _event(_HASH_CAT, _DISCARD_NAME, DecisionChange("999Z", "", "notify", RETRACTED_TIER))
    ]


@pytest.mark.asyncio
async def test_a_file_with_no_name_left_is_named_by_the_candidate(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    # Only a retraction can be written then, and it is never notified: the size is unknown.
    insert_file(catalog_connection, _HASH_CAT, 1)
    _seed_decision(catalog, _HASH_CAT, "062A")
    telemetry, signal = RecordingTelemetry(), RecordingSignal()
    await record_decision_if_changed(
        _key(_HASH_CAT),
        _obs(_HASH_CAT, _DISCARD_NAME).to_candidate(),
        catalog=catalog,
        engine=engine,
        signal=signal,
        telemetry=telemetry,
    )
    assert telemetry.events == [
        DecisionsRecorded(
            _key(_HASH_CAT),
            _DISCARD_NAME,
            0,
            (DecisionChange("062A", _TITLE_A, "notify", RETRACTED_TIER),),
        )
    ]
