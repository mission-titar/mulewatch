"""End to end: every catalog read still sees every file and alias after a REAL compaction.

Lives under tests/webui for its autouse fixture that closes every SQLite connection the app opens.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

import mulewatch.webui
from catalog_matching.engine import Explanation, MatchDecision, MatchingEngine
from catalog_matching.validation import parse_matcher_config, parse_targets
from mulewatch.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from mulewatch.adapters.persistence_sqlite.connection import open_catalog, open_local
from mulewatch.adapters.persistence_sqlite.reader import open_reader
from mulewatch.application.reevaluate_catalog import reevaluate_catalog
from mulewatch.compact.compactor import compact_catalog
from mulewatch.domain.observation import FileObservation
from mulewatch.ports.catalog_repository import ObservedFile
from mulewatch.webui.adapters.catalog_read import CatalogReader
from mulewatch.webui.composition.app import build_app
from tests.application.fakes import RecordingSignal, RecordingTelemetry

_GONE = "a" * 32  # every observation is older than the cutoff: ranges only
_SPLIT = "b" * 32  # an old alias compacted, a recent name kept raw
_FRESH = "c" * 32  # recent only: untouched
_OLD, _OLDER, _RECENT = "2026-05-02", "2026-05-01", "2026-06-10"
_NAMES = {
    _GONE: {_OLDER: "Keroro vf.avi", _OLD: "Keroro vf v2.avi"},
    _SPLIT: {_OLDER: "Keroro ITA.avi", _RECENT: "Keroro vf.mkv"},
    _FRESH: {_RECENT: "Keroro vf fresh.avi"},
}
_MATCHER = """\
tokens:
  keroro: {keyword: keroro}
  vf: {regex: vf}
  ita: {regex: '\\bITA\\b'}
vetoes: [ita]
rules:
  - {name: catalog, tier: catalog, scope: unattributed, all: [keroro, vf]}
"""
_TARGETS = """\
episodes:
  - season: 2
    seasonal_number: 11
    absolute_number: 62
    segments:
      - {letter: a, title: La Grenouille Cosmique}
"""


class _DayClock:
    def __init__(self) -> None:
        self.day = _OLDER

    def __call__(self) -> datetime:
        return datetime.fromisoformat(f"{self.day}T12:00:00+00:00")


def _catalog_decision() -> MatchDecision:
    return MatchDecision(
        target_id="062A",
        rule_name="catalog",
        tier="catalog",
        explanation=Explanation(
            target_id="062A", rules_fired=(), tokens_matched=(), coverage_values=()
        ),
    )


@pytest.fixture
def compacted(tmp_path: Path) -> Path:
    """A raw catalog compacted with a cutoff of 2026-06-04: _GONE whole, _SPLIT in part."""
    source, output = tmp_path / "source.db", tmp_path / "compacted.db"
    clock = _DayClock()
    connection = open_catalog(source)
    repository = SqliteCatalogRepository(connection, "node", clock=clock)
    for ed2k_hash, by_day in _NAMES.items():
        for day, name in by_day.items():
            clock.day = day
            repository.record_observation(
                FileObservation(
                    ed2k_hash=ed2k_hash,
                    filename=name,
                    size_bytes=1000,
                    source_count=4,
                    complete_source_count=1,
                    keyword="keroro",
                )
            )
        repository.record_decision(ed2k_hash, _catalog_decision())
    connection.close()
    compact_catalog(
        source, output, keep_recent_days=7, clock=lambda: datetime(2026, 6, 11, tzinfo=UTC)
    )
    return output


def test_the_fixture_really_compacts_one_file_whole_and_one_in_part(compacted: Path) -> None:
    connection = open_catalog(compacted)
    raw = dict(
        connection.execute(
            "SELECT ed2k_hash, COUNT(*) FROM file_observations GROUP BY ed2k_hash"
        ).fetchall()
    )
    ranged = {row[0] for row in connection.execute("SELECT ed2k_hash FROM file_observation_ranges")}
    connection.close()
    assert raw == {_SPLIT: 1, _FRESH: 1}
    assert ranged == {_GONE, _SPLIT}


def test_crawler_reads_see_every_file_and_alias(compacted: Path) -> None:
    connection = open_catalog(compacted)
    repository = SqliteCatalogRepository(connection, "node")
    rows = {row.ed2k_hash: row.filename for row in repository.iter_reevaluation_rows()}
    assert rows == {
        _GONE: "Keroro vf v2.avi",
        _SPLIT: "Keroro vf.mkv",
        _FRESH: "Keroro vf fresh.avi",
    }
    for ed2k_hash, by_day in _NAMES.items():
        assert repository.known_filenames(ed2k_hash) == tuple(sorted(by_day.values()))
    assert repository.last_observation(_GONE) == ObservedFile("Keroro vf v2.avi", 1000)
    assert repository.best_observation(_GONE) == ObservedFile("Keroro vf v2.avi", 1000)
    assert repository.best_observation(_SPLIT) == ObservedFile("Keroro vf.mkv", 1000)
    connection.close()


def test_webui_reads_see_every_file_and_alias(compacted: Path) -> None:
    reader = CatalogReader(open_reader(compacted))
    rows = reader.list_files(target=None, tier=None, query=None, page=1)
    assert {row.ed2k_hash: row.filename for row in rows} == {
        _GONE: "Keroro vf v2.avi",
        _SPLIT: "Keroro vf.mkv",
        _FRESH: "Keroro vf fresh.avi",
    }
    assert [
        row.ed2k_hash for row in reader.list_files(target=None, tier=None, query="ITA", page=1)
    ] == [_SPLIT]
    for ed2k_hash, by_day in _NAMES.items():
        detail = reader.file_detail(ed2k_hash)
        assert detail is not None
        assert detail.known_filenames == tuple(sorted(by_day.values()))
        assert {n for s in detail.sightings for n in s.names} == set(by_day.values())


@pytest.mark.asyncio
async def test_webui_explanation_agrees_with_the_crawler_verdict(
    compacted: Path, tmp_path: Path
) -> None:
    # The ITA alias of _SPLIT survives only in a range: both sides must still veto on it.
    matcher, targets = (
        parse_matcher_config(yaml.safe_load(_MATCHER)),
        parse_targets(yaml.safe_load(_TARGETS)),
    )
    open_local(tmp_path / "local.db").close()
    webui_dir = Path(mulewatch.webui.__file__).parent / "adapters"
    app = build_app(
        catalog_db=compacted,
        local_db=tmp_path / "local.db",
        matcher_config=matcher,
        targets=targets,
        templates_dir=webui_dir / "templates",
        static_dir=webui_dir / "static",
        control=_NoControl(),
        amule_url="http://localhost:4711",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pages = {h: (await client.get(f"/files/{h}")).text for h in _NAMES}
    assert "<li>ita</li>" in pages[_SPLIT]
    assert "<li>ita</li>" not in pages[_GONE]
    assert "Evaluated against the current configuration" in pages[_GONE]

    connection = open_catalog(compacted)
    catalog = SqliteCatalogRepository(connection, "node")
    await reevaluate_catalog(
        catalog=catalog,
        engine=MatchingEngine(matcher, targets),
        signal=RecordingSignal(),
        telemetry=RecordingTelemetry(),
    )
    tiers = {h: {r.tier for r in catalog.last_decisions(h).values()} for h in _NAMES}
    connection.close()
    assert tiers == {_GONE: {"catalog"}, _SPLIT: {"retracted"}, _FRESH: {"catalog"}}


class _NoControl:
    def force_cycle(self) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def restart(self) -> None: ...
