"""Lightweight end-to-end: the REAL crawl loop against a REAL amuled (spec §8).

Dedicated run: uv run pytest -m orchestration_integration --no-cov
Validates that a real ``CrawlerApp`` (real ``AmuleApiClient`` + real SQLite DBs) runs
ONE full cycle against the provided ``amuled`` then stops CLEANLY. The results may be
empty (no guaranteed eD2k network access): it is the LOOP (startup, search, cataloging,
bounded shutdown) that is validated, not the richness of the results.
"""

from pathlib import Path

import pytest

from catalog_matching.models import TargetSegment
from catalog_matching.validation import parse_matcher_config
from mulewatch.adapters.clock_asyncio import AsyncioClock, SeededRng
from mulewatch.adapters.config.crawler_config import (
    AmuleEndpoint,
    BackoffConfig,
    CrawlerConfig,
    WebuiConfig,
)
from mulewatch.adapters.config.yaml_loader import load_yaml
from mulewatch.adapters.decision_signal_asyncio import AsyncioDecisionSignal
from mulewatch.adapters.persistence_sqlite.connection import open_local
from mulewatch.adapters.persistence_sqlite.scheduler_state_repository import (
    SqliteSchedulerStateRepository,
)
from mulewatch.composition.app import CrawlerApp
from mulewatch.domain.observation import FileObservation
from mulewatch.ports.port_sync import NetworkStatus
from tests.integration.conftest import ApiEndpoint

pytestmark = pytest.mark.orchestration_integration

_MATCHER = Path(__file__).resolve().parents[4] / "deploy" / "matcher.yml"
_TARGETS = (
    TargetSegment(
        season=2,
        seasonal_number=11,
        absolute_number=62,
        segment="A",
        title="Les demoiselles cambrioleuses",
    ),
)


class _ShutdownAfterFirstCycleClient:
    """Wraps a real client and triggers shutdown on the 2nd cycle's status poll.

    We do NOT trigger on the 1st poll (start of the 1st cycle): a shutdown set during the status
    poll cancels the in-flight cycle BEFORE its final ``write_cycle_state``, and the index would
    never advance (verified empirically). So we let the 1st cycle COMPLETE (it writes
    ``cycle_index=1``), then we trigger shutdown on the 2nd cycle's 1st poll. The index stays at
    1, proof that a full cycle actually ran. The ``cycle_interval`` is tiny → the 2nd cycle starts
    right after the 1st (the run stays bounded, under the 180 s ``wait_for``)."""

    def __init__(self, inner: object, app_holder: dict[str, CrawlerApp]) -> None:
        self._inner = inner
        self._app_holder = app_holder
        self._status_calls = 0

    channels: tuple[str, ...] = ("ed2k", "kad")

    async def connect(self) -> None:
        await self._inner.connect()  # type: ignore[attr-defined]

    async def close(self) -> None:
        await self._inner.close()  # type: ignore[attr-defined]

    async def search(
        self, keyword: str, channel: str, budget_seconds: float
    ) -> tuple[FileObservation, ...]:
        return await self._inner.search(keyword, channel, budget_seconds)  # type: ignore[attr-defined,no-any-return]

    async def network_status(self) -> NetworkStatus:
        status = await self._inner.network_status()  # type: ignore[attr-defined]
        self._status_calls += 1
        if self._status_calls == 2:  # 1st poll of the 2nd cycle (the 1st cycle wrote its index)
            self._app_holder["app"]._on_signal()
        return status  # type: ignore[no-any-return]


@pytest.mark.asyncio
async def test_real_loop_runs_one_cycle_and_stops(amuled: ApiEndpoint, tmp_path: Path) -> None:
    import asyncio

    from mulewatch.adapters.mule_api.client import AmuleApiClient

    matcher_config = parse_matcher_config(load_yaml(_MATCHER))
    crawler_config = CrawlerConfig(
        # Tiny interval: the 2nd cycle starts right after the 1st (which wrote its index)
        # → the shutdown at the 2nd cycle's poll bounds the run, well under wait_for 120 s.
        cycle_interval_seconds=0.05,
        keyword_pause_min_seconds=0.01,  # tiny pauses (the test does not measure spacing)
        keyword_pause_max_seconds=0.05,
        backoff=BackoffConfig(base_seconds=2.0, cap_seconds=60.0, factor=2.0, jitter_ratio=0.3),
        decision_poll_interval_seconds=5.0,
        shutdown_deadline_seconds=30.0,
        amule_api_password=amuled.password,
        catalog_db_path=str(tmp_path / "catalog.db"),
        local_db_path=str(tmp_path / "local.db"),
        node_id=None,
        # One keyword: a Kad search lasts 45 s and ed2k starts are 60 s apart, so the cycle
        # takes about a minute.
        search_keywords=("titar",),
        # The webui binds a FIXED 0.0.0.0:8080; off here so the test never collides with
        # whatever already listens there on the developer's machine.
        webui=WebuiConfig(enabled=False),
    )
    app_holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _ShutdownAfterFirstCycleClient:
        # The endpoint is derived from code constants now: use the caller's daemon instead.
        inner = AmuleApiClient(amuled.host, amuled.port, endpoint.password, timeout=30.0)
        return _ShutdownAfterFirstCycleClient(inner, app_holder)

    app = CrawlerApp(
        crawler_config=crawler_config,
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=AsyncioClock(),
        rng=SeededRng(),
        signal_hub=AsyncioDecisionSignal(),
        policy_fingerprint="test-policy-fingerprint",
        client_factory=factory,
    )
    app_holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=180.0)
    # catalog.db AND local.db exist (open_catalog/open_local create them), BUT above all the
    # cycle COMPLETED: the cycle index advanced (write_cycle_state(cycle_index+1, …) only runs
    # at the END of a cycle). Without this assertion, the test passed before any cycle even ran.
    assert (tmp_path / "catalog.db").exists()
    assert (tmp_path / "local.db").exists()
    local_conn = open_local(Path(crawler_config.local_db_path))
    try:
        scheduler_state = SqliteSchedulerStateRepository(local_conn)
        assert scheduler_state.read_cycle_index() >= 1  # a full cycle advanced the index
    finally:
        local_conn.close()
