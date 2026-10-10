"""Lightweight end-to-end: the REAL crawl loop against a REAL amuled (spec §8).

Dedicated run: uv run pytest -m orchestration_integration --no-cov
Validates that a real ``CrawlerApp`` (real ``AmuleApiClient`` + real SQLite DBs) runs
ONE full search task against the provided ``amuled`` then stops CLEANLY. The results may be
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
from mulewatch.composition.app import CrawlerApp
from mulewatch.domain.observation import FileObservation
from mulewatch.ports.client_status import ClientStatus
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


class _ShutdownAfterFirstSearchClient:
    """Wraps a real client and shuts down when a channel's task comes back for its next search.

    A refused search does not count: a daemon off eD2k refuses every ed2k search at once, and
    only a search that returned, then was recorded, proves a whole task iteration ran."""

    def __init__(self, inner: object, app_holder: dict[str, CrawlerApp]) -> None:
        self._inner = inner
        self._app_holder = app_holder
        self.returned: set[str] = set()

    channels: tuple[str, ...] = ("ed2k", "kad")

    async def connect(self) -> None:
        await self._inner.connect()  # type: ignore[attr-defined]

    async def close(self) -> None:
        await self._inner.close()  # type: ignore[attr-defined]

    async def search(
        self, keyword: str, channel: str, budget_seconds: float
    ) -> tuple[FileObservation, ...]:
        app = self._app_holder["app"]
        if channel in self.returned and not app._shutdown.is_set():
            app._on_signal()
        results = await self._inner.search(keyword, channel, budget_seconds)  # type: ignore[attr-defined]
        self.returned.add(channel)
        return results  # type: ignore[no-any-return]

    async def status(self) -> ClientStatus:
        return await self._inner.status()  # type: ignore[attr-defined,no-any-return]


@pytest.mark.asyncio
async def test_real_loop_runs_one_search_and_stops(amuled: ApiEndpoint, tmp_path: Path) -> None:
    import asyncio

    from mulewatch.adapters.mule_api.client import AmuleApiClient

    matcher_config = parse_matcher_config(load_yaml(_MATCHER))
    crawler_config = CrawlerConfig(
        backoff=BackoffConfig(base_seconds=2.0, cap_seconds=60.0, factor=2.0, jitter_ratio=0.3),
        decision_poll_interval_seconds=5.0,
        shutdown_deadline_seconds=30.0,
        amule_api_password=amuled.password,
        catalog_db_path=str(tmp_path / "catalog.db"),
        local_db_path=str(tmp_path / "local.db"),
        node_id=None,
        # One keyword: a Kad search lasts 45 s at most, so a task comes back within a minute.
        search_keywords=("titar",),
        # The webui binds a FIXED 0.0.0.0:8080; off here so the test never collides with
        # whatever already listens there on the developer's machine.
        webui=WebuiConfig(enabled=False),
    )
    app_holder: dict[str, CrawlerApp] = {}
    clients: list[_ShutdownAfterFirstSearchClient] = []

    def factory(endpoint: AmuleEndpoint) -> _ShutdownAfterFirstSearchClient:
        # The endpoint is derived from code constants now: use the caller's daemon instead.
        inner = AmuleApiClient(amuled.host, amuled.port, endpoint.password, timeout=30.0)
        clients.append(_ShutdownAfterFirstSearchClient(inner, app_holder))
        return clients[-1]

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
    assert (tmp_path / "catalog.db").exists()
    assert (tmp_path / "local.db").exists()
    # Without this, the test passed before any search even returned.
    assert clients[0].returned
