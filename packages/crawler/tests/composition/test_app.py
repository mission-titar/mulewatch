import asyncio
import logging
import sqlite3
import threading
from importlib.metadata import version
from pathlib import Path
from typing import cast

import pytest
from prometheus_client import CollectorRegistry
from starlette.applications import Starlette

from catalog_matching.config import MatcherConfig
from catalog_matching.models import TargetSegment
from catalog_matching.validation import parse_matcher_config
from mulewatch.adapters.config.crawler_config import (
    AmuleEndpoint,
    BackoffConfig,
    CrawlerConfig,
    DownloadConfig,
    MetricsConfig,
    ObservabilityConfig,
    PortSyncConfig,
    WebuiConfig,
)
from mulewatch.adapters.config.yaml_loader import load_yaml
from mulewatch.adapters.crawler_control_loop import LoopCrawlerControl
from mulewatch.adapters.persistence_sqlite.connection import open_local
from mulewatch.adapters.persistence_sqlite.local_state_repository import (
    SqliteLocalStateRepository,
)
from mulewatch.application.edge_state import EdgeState
from mulewatch.application.search_worker import BackoffRegistry
from mulewatch.composition.app import CrawlerApp, WebuiServer, default_client_factory
from mulewatch.domain.file_key import FileKey, Network
from mulewatch.domain.observation import FileObservation
from mulewatch.ports.client_errors import ClientUnreachableError
from mulewatch.ports.client_status import ClientStatus
from mulewatch.ports.mule_download_client import DownloadEntry, SharedFileEntry
from mulewatch.ports.port_sync import KadStatus, NetworkStatus
from mulewatch.ports.telemetry import Telemetry
from tests.application.fakes import FakeClock, FakeMuleClient, RecordingSignal
from tests.catalog_rows import observation_node_ids

_TARGETS = (
    TargetSegment(
        season=2,
        seasonal_number=11,
        absolute_number=62,
        segment="A",
        title="Les demoiselles cambrioleuses",
    ),
)
_MATCHER = Path(__file__).resolve().parents[4] / "deploy" / "matcher.yml"
_DL_NAME = "Keroro N°062A Les demoiselles cambrioleuses.avi"
# Arbitrary policy fingerprint for tests that do not exercise the backfill gate itself
# (Task 6 tests below construct their OWN fingerprint to drive the "ran"/"skipped" branches).
_FP = "test-policy-fingerprint"
# Existing app tests do not exercise the webui: keep it OFF by default so run() never builds a
# real uvicorn server. The webui-specific tests below opt in with an enabled config + a fake
# server factory (no real HTTP).
_WEBUI_OFF = WebuiConfig(enabled=False)


class _NoopRng:
    """Identity RNG: preserves order + zero jitter (test determinism)."""

    def shuffled(self, items: tuple[str, ...], seed: str) -> tuple[str, ...]:
        return items

    def jitter(self, span: float) -> float:
        return 0.0


@pytest.fixture
def matcher_config() -> MatcherConfig:
    return parse_matcher_config(load_yaml(_MATCHER))


def _crawler_config(
    tmp_path: Path,
    shutdown_deadline: float = 30.0,
    *,
    node_id: str | None = None,
    observability: ObservabilityConfig | None = None,
    download: DownloadConfig | None = None,
    port_sync: PortSyncConfig | None = None,
    webui: WebuiConfig = _WEBUI_OFF,
) -> CrawlerConfig:
    return CrawlerConfig(
        cycle_interval_seconds=300.0,
        keyword_pause_min_seconds=1.0,
        keyword_pause_max_seconds=2.0,
        backoff=BackoffConfig(base_seconds=2.0, cap_seconds=60.0, factor=2.0, jitter_ratio=0.0),
        decision_poll_interval_seconds=5.0,
        shutdown_deadline_seconds=shutdown_deadline,
        amule_api_password="p",
        catalog_db_path=str(tmp_path / "catalog.db"),
        local_db_path=str(tmp_path / "local.db"),
        node_id=node_id,
        observability=observability,
        download=download,
        port_sync=port_sync,
        webui=webui,
    )


def _download_config(tmp_path: Path) -> DownloadConfig:
    return DownloadConfig(
        poll_interval_seconds=30.0,
        min_free_bytes=1_000_000_000,
        lost_after_seconds=86_400.0,
        output_dir=str(tmp_path),
    )


def _full_crawler_config(tmp_path: Path) -> CrawlerConfig:
    """FULL-mode config: ``download`` section present (enabled)."""
    return _crawler_config(tmp_path, download=_download_config(tmp_path))


def _port_sync_config() -> PortSyncConfig:
    return PortSyncConfig(
        poll_interval_seconds=60.0,
        restart_min_interval_seconds=300.0,
        gluetun_control_url="http://localhost:8000",
    )


def _port_sync_crawler_config(tmp_path: Path) -> CrawlerConfig:
    return _crawler_config(tmp_path, port_sync=_port_sync_config())


def _make_app(
    tmp_path: Path,
    matcher_config: MatcherConfig,
    *,
    factory: object,
    clock: FakeClock | None = None,
    node_id: str | None = None,
    shutdown_deadline: float = 30.0,
    observability: ObservabilityConfig | None = None,
    metrics_server: object | None = None,
) -> CrawlerApp:
    extra: dict[str, object] = {}
    if metrics_server is not None:
        extra["metrics_server"] = metrics_server
    return CrawlerApp(
        crawler_config=_crawler_config(
            tmp_path, shutdown_deadline, node_id=node_id, observability=observability
        ),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=clock or FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        policy_fingerprint=_FP,
        client_factory=factory,  # type: ignore[arg-type]
        **extra,  # type: ignore[arg-type]
    )


class _ShutdownOnStatusClient(FakeMuleClient):
    """Client that triggers app shutdown on the status loop's FIRST reading."""

    def __init__(
        self,
        app_holder: dict[str, CrawlerApp],
        results: list[tuple[FileObservation, ...]] | None = None,
    ) -> None:
        super().__init__(results=results)
        self._app_holder = app_holder
        self._fired = False

    async def status(self) -> ClientStatus:
        if not self._fired:
            self._fired = True
            self._app_holder["app"]._on_signal()  # simulate a SIGINT once the loops run
        return await super().status()


@pytest.mark.asyncio
async def test_app_runs_one_cycle_then_shuts_down_cleanly(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    created: list[_ShutdownOnStatusClient] = []
    app_holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        client = _ShutdownOnStatusClient(app_holder)
        created.append(client)
        return client

    # observability non-None → covers the `obs is not None` branch of the notification timeout
    # (the dispatcher is built with the configured timeout, not the default 5.0).
    app = _make_app(
        tmp_path,
        matcher_config,
        factory=factory,
        observability=ObservabilityConfig(
            log_level="INFO", metrics=None, notification_timeout_seconds=5.0, notifications=()
        ),
    )
    app_holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)
    assert created and created[0].close_calls == 1  # client closed AFTER the unwind
    assert created[0].connect_calls >= 1  # connected at setup
    assert (tmp_path / "catalog.db").exists()
    assert (tmp_path / "local.db").exists()


@pytest.mark.asyncio
async def test_run_logs_the_package_version_at_startup(
    tmp_path: Path, matcher_config: MatcherConfig, caplog: pytest.LogCaptureFixture
) -> None:
    # run() logs the mulewatch version (from installed metadata) at startup: the operability
    # signal a correlated-to-version regression relies on (spec 2026-07-10-git-driven-versioning).
    app_holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(app_holder)

    app = _make_app(tmp_path, matcher_config, factory=factory)
    app_holder["app"] = app
    with caplog.at_level(logging.INFO, logger="mulewatch.composition.app"):
        await asyncio.wait_for(app.run(), timeout=5.0)
    assert any(
        r.getMessage() == f"mulewatch version {version('mulewatch')}" for r in caplog.records
    )


@pytest.mark.asyncio
async def test_the_status_loop_reads_the_search_client_under_its_endpoint_name(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    registries: list[CollectorRegistry] = []
    app_holder: dict[str, CrawlerApp] = {}
    app = _make_app(
        tmp_path,
        matcher_config,
        factory=lambda e: _ShutdownOnStatusClient(app_holder),
        observability=ObservabilityConfig(
            log_level="INFO",
            metrics=MetricsConfig(enabled=True, port=9123),
            notification_timeout_seconds=5.0,
            notifications=(),
        ),
        metrics_server=lambda port, registry: registries.append(registry),
    )
    app_holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)
    gauge = registries[0].get_sample_value(
        "p2pwatch_channel_on_network", {"client": "amuled", "network": "kad"}
    )
    assert gauge == 1.0


class _UnreachableAtStartupClient(_ShutdownOnStatusClient):
    """Client whose 1st ``connect`` (at setup) raises ``ClientUnreachableError``.

    Models a daemon down at startup: the composition root must CATCH, log, and
    CONTINUE (the worker's backoff will govern reconnections). Also triggers the shutdown on the
    1st status read to bound the run."""

    def __init__(self, app_holder: dict[str, CrawlerApp]) -> None:
        super().__init__(app_holder, results=None)
        self._connect_seen = 0

    async def connect(self) -> None:
        self._connect_seen += 1
        self.connect_calls += 1
        if self._connect_seen == 1:
            raise ClientUnreachableError("daemon unreachable at startup")


@pytest.mark.asyncio
async def test_unreachable_client_at_startup_does_not_crash_the_run(
    tmp_path: Path, matcher_config: MatcherConfig, caplog: pytest.LogCaptureFixture
) -> None:
    # A client unreachable at setup (connect raises ClientUnreachableError) must NOT bring down
    # run(): the composition root catches, warns, and CONTINUES. This matters MORE in one
    # container, not less: the crawler, amuled and amuleweb start simultaneously under s6, so
    # reaching EC before amuled listens is the normal cold start (design §4). The loops
    # run anyway (the status read → the shutdown fires).
    created: list[_UnreachableAtStartupClient] = []
    app_holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _UnreachableAtStartupClient:
        client = _UnreachableAtStartupClient(app_holder)
        created.append(client)
        return client

    app = _make_app(tmp_path, matcher_config, factory=factory)
    app_holder["app"] = app
    with caplog.at_level(logging.WARNING, logger="mulewatch.composition.app"):
        await asyncio.wait_for(app.run(), timeout=5.0)  # does NOT raise (down instance tolerated)
    # The tolerance warning comes from the COMPOSITION ROOT (not the worker): it is the
    # `except ClientUnreachableError` branch of the client setup.
    startup_warnings = [
        record
        for record in caplog.records
        if record.name == "mulewatch.composition.app" and record.levelno == logging.WARNING
    ]
    assert startup_warnings, "the composition root must log the startup tolerance"
    assert "amuled unreachable at startup" in startup_warnings[0].getMessage()
    assert created and created[0].connect_calls >= 1  # connect attempted at setup (then retried)
    assert created[0]._fired  # the status loop read the client → the loops did start


@pytest.mark.asyncio
async def test_node_id_override_is_used(tmp_path: Path, matcher_config: MatcherConfig) -> None:
    observation = FileObservation(
        file=FileKey(Network.ED2K, "31d6cfe0d16ae931b73c59d7e0c089c0"),
        filename=_DL_NAME,
        size_bytes=234_000_000,
        source_count=3,
        keyword="keroro",
    )
    app_holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(app_holder, results=[(observation,)])

    app = _make_app(tmp_path, matcher_config, factory=factory, node_id="forced-node")
    app_holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)
    catalog = sqlite3.connect(tmp_path / "catalog.db")
    try:
        node_ids = observation_node_ids(catalog)
    finally:
        catalog.close()
    assert node_ids == {"forced-node"}


@pytest.mark.asyncio
async def test_second_signal_forces_exit(tmp_path: Path, matcher_config: MatcherConfig) -> None:
    app = _make_app(tmp_path, matcher_config, factory=lambda e: FakeMuleClient())
    app._on_signal()  # 1st signal: shutdown request
    with pytest.raises(SystemExit):
        app._on_signal()  # 2nd signal: escalation → SystemExit


class _ShutdownOnSleepClock(FakeClock):
    """Clock that triggers the shutdown on the LONG inter-cycle sleep (≥ 100s), NOT on the
    short inter-keyword pauses (1-2s) → the cycle COMPLETES, then the loop re-tests its
    condition and EXITS on its own (without cancellation) on the next iteration."""

    def __init__(self, app_holder: dict[str, CrawlerApp]) -> None:
        super().__init__()
        self._app_holder = app_holder

    async def sleep(self, seconds: float) -> None:
        await super().sleep(seconds)
        if seconds >= 100.0:  # the inter-cycle sleep (cycle_interval − elapsed), not a pause
            self._app_holder["app"]._shutdown.set()


class _SilentStatusClient(FakeMuleClient):
    """``status`` never answers: the status loop leaves the fake clock to the cycle loop."""

    async def status(self) -> ClientStatus:
        await asyncio.Event().wait()
        return await super().status()


@pytest.mark.asyncio
async def test_loop_exits_cleanly_when_shutdown_set_during_sleep(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # The shutdown is set during the inter-cycle sleep: the loop re-tests its condition and
    # EXITS on its own (without cancellation) → covers the normal exit of the `while`.
    app_holder: dict[str, CrawlerApp] = {}
    clock = _ShutdownOnSleepClock(app_holder)
    app = _make_app(tmp_path, matcher_config, factory=lambda e: _SilentStatusClient(), clock=clock)
    app_holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)


class _BlockingClient(FakeMuleClient):
    """Client whose ``search`` BLOCKS: the loop stays in flight → cancellation hits it."""

    async def search(
        self, keyword: str, channel: str, budget_seconds: float
    ) -> tuple[FileObservation, ...]:
        await asyncio.Event().wait()  # never resolves: blocks until cancellation
        return ()


@pytest.mark.asyncio
async def test_signal_cancels_an_in_flight_cycle(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # A worker is BLOCKED in search; an external SIGINT cancels the TaskGroup →
    # covers the cancellation path (clean unwind + "Workers stopped" line).
    app = _make_app(tmp_path, matcher_config, factory=lambda e: _BlockingClient())
    run_task = asyncio.create_task(app.run())
    for _ in range(20):  # let the cycle start and block in search
        await asyncio.sleep(0)
    app._on_signal()
    await asyncio.wait_for(run_task, timeout=5.0)


class _RealPacedClient(FakeMuleClient):
    """Client that paces each status reading with a SMALL REAL sleep (not the FakeClock).

    Used to prove the timing invariant: ``status`` yields REAL time instead of
    busy-spinning, so the status loop advances at a controlled real pace. The normal run
    (without a signal) must OUTLIVE ``shutdown_deadline_seconds`` of real time without raising
    ``TimeoutError`` - the shutdown bound must NOT arm until a shutdown is requested."""

    async def status(self) -> ClientStatus:
        await asyncio.sleep(0.01)  # REAL time: the loop does not occupy the event loop 100%
        return await super().status()


@pytest.mark.asyncio
async def test_normal_run_outlives_shutdown_deadline_without_a_signal(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # Regression (spec §6, DECISION 6): the shutdown bound covers ONLY the shutdown phase. A
    # normal run WITHOUT a signal must run indefinitely - so it must outlive FAR beyond
    # ``shutdown_deadline_seconds`` of REAL time. Before the fix, ``asyncio.timeout`` wrapped
    # all of ``_supervise`` (including the UNBOUNDED wait on the signal) on the REAL clock → the
    # run raised ``TimeoutError`` ~deadline after startup, with no shutdown requested. Here the
    # deadline is tiny (0.2 s) and we let 0.4 s of real time pass: the run must
    # STILL be running, without having raised. Then we request the shutdown → it ends CLEANLY.
    app = _make_app(
        tmp_path,
        matcher_config,
        factory=lambda e: _RealPacedClient(),
        shutdown_deadline=0.2,
    )
    run_task = asyncio.create_task(app.run())
    await asyncio.sleep(0.4)  # REAL time > deadline: if the bound wrapped the run, it would raise
    assert not run_task.done(), "the normal run (no signal) must NOT finish or raise"
    app._on_signal()  # shutdown requested → the bound arms, the clean shutdown is bounded
    await asyncio.wait_for(run_task, timeout=5.0)  # ends without TimeoutError
    assert run_task.exception() is None


def test_default_client_factory_builds_an_amule_client() -> None:
    from mulewatch.adapters.mule_api.client import AmuleApiClient

    endpoint = AmuleEndpoint(name="amule-1", host="gluetun", port=4712, password="secret")
    assert isinstance(default_client_factory(endpoint), AmuleApiClient)


def test_default_download_client_factory_builds_an_amule_client() -> None:
    from mulewatch.adapters.mule_api.client import AmuleApiClient
    from mulewatch.composition.app import default_download_client_factory

    endpoint = AmuleEndpoint(name="dl", host="gluetun", port=4799, password="secret")
    assert isinstance(default_download_client_factory(endpoint), AmuleApiClient)


# A close that drags FAR beyond the armed bound (0.05 s) and FAR beyond the assertion
# threshold (3 s), yet stays BELOW the external guard (30 s). Only the INTERNAL bound
# (armed by ``reschedule``) can cut a close this slow fast enough to land under the
# threshold. If ``reschedule`` regressed, ``aclose`` would block ~10 s then EXIT cleanly
# (no TimeoutError, since the slow close stays below the external guard) and
# ``pytest.raises`` would fail: the test is fail-closed, never "passing" via the guard.
# The 10 s / 3 s separation gives the assertion generous headroom over CI startup jitter
# (``elapsed`` spans the WHOLE run, app setup included), which is why the tighter original
# bounds (1 s / 0.5 s) were flaky under load.
_SLOW_CLOSE_SECONDS = 10.0


class _SlowCloseClient(_ShutdownOnStatusClient):
    """Client whose ``close`` drags beyond the armed bound → the INTERNAL bound cuts it."""

    async def close(self) -> None:
        await asyncio.sleep(_SLOW_CLOSE_SECONDS)  # > armed bound (0.05 s), < external guard (30 s)


@pytest.mark.asyncio
async def test_shutdown_deadline_forces_exit(tmp_path: Path, matcher_config: MatcherConfig) -> None:
    # Close that drags + tiny shutdown deadline → the INTERNAL bound (armed by
    # ``reschedule`` at shutdown) raises TimeoutError (spec §6: the app must NOT appear stuck).
    # ROBUSTNESS: we measure the real elapsed time and require the raise to come FAST (well
    # below the slow close and the external guard) to prove it is the ARMED bound (~0.05 s)
    # that fired, not the external ``wait_for`` (30 s) nor the close end (10 s).
    # A ``reschedule`` regression (bound never armed) would let ``aclose`` exit cleanly
    # after ~10 s WITHOUT TimeoutError → ``pytest.raises`` would fail (fail-closed).
    # ``elapsed`` spans the WHOLE run (app setup included), so the threshold sits at 3 s: far
    # above any plausible CI startup jitter, far below the 10 s slow close.
    app_holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _SlowCloseClient:
        return _SlowCloseClient(app_holder)

    app = _make_app(tmp_path, matcher_config, factory=factory, shutdown_deadline=0.05)
    app_holder["app"] = app
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(app.run(), timeout=30.0)
    elapsed = loop.time() - started
    # < 3 s: well below the slow close (10 s) and the external guard (30 s) → it is indeed the
    # armed bound (~0.05 s) that cut the close, not some other delay.
    assert elapsed < 3.0, f"the armed bound must cut fast, elapsed={elapsed:.3f}s"


@pytest.mark.asyncio
async def test_observations_are_catalogued_during_the_cycle(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    observation = FileObservation(
        file=FileKey(Network.ED2K, "31d6cfe0d16ae931b73c59d7e0c089c0"),
        filename=_DL_NAME,
        size_bytes=234_000_000,
        source_count=3,
        keyword="keroro",
    )
    app_holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(app_holder, results=[(observation,)])

    app = _make_app(tmp_path, matcher_config, factory=factory)
    app_holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)
    catalog = sqlite3.connect(tmp_path / "catalog.db")
    try:
        count = catalog.execute("SELECT count(*) FROM match_decisions").fetchone()[0]
    finally:
        catalog.close()
    assert count == 1


# ---------------------------------------------------------------------------
# Task 6 - startup backfill wiring (policy-fingerprint gate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_runs_and_stores_marker_when_policy_never_set(
    tmp_path: Path, matcher_config: MatcherConfig, caplog: pytest.LogCaptureFixture
) -> None:
    # Fresh local.db (never backfilled before) -> the gate RUNS the backfill (against an
    # EMPTY catalog.db, since it runs BEFORE the first search cycle records anything) and
    # stores the fingerprint, so a LATER restart with the SAME policy would skip it.
    holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(holder)

    app = CrawlerApp(
        crawler_config=_crawler_config(tmp_path),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        policy_fingerprint="fp-first-run",
        client_factory=factory,
    )
    holder["app"] = app
    with caplog.at_level(logging.INFO, logger="mulewatch.composition.app"):
        await asyncio.wait_for(app.run(), timeout=5.0)
    assert any(
        r.getMessage() == "catalogue re-evaluated: 0 files, 0 rows written" for r in caplog.records
    )
    local_conn = sqlite3.connect(tmp_path / "local.db")
    try:
        stored = local_conn.execute(
            "SELECT policy_sha256 FROM backfill_state WHERE id = 1"
        ).fetchone()
    finally:
        local_conn.close()
    assert stored == ("fp-first-run",)


@pytest.mark.asyncio
async def test_backfill_skipped_when_marker_already_matches_fingerprint(
    tmp_path: Path, matcher_config: MatcherConfig, caplog: pytest.LogCaptureFixture
) -> None:
    # Pre-seed local.db with the SAME fingerprint the app is built with -> the gate SKIPS
    # the pass (a restart with an unchanged matcher.yml/targets.yml does no redundant work).
    local_conn = open_local(tmp_path / "local.db")
    try:
        SqliteLocalStateRepository(local_conn).set_last_backfill_policy("fp-unchanged")
    finally:
        local_conn.close()
    holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(holder)

    app = CrawlerApp(
        crawler_config=_crawler_config(tmp_path),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        policy_fingerprint="fp-unchanged",
        client_factory=factory,
    )
    holder["app"] = app
    with caplog.at_level(logging.INFO, logger="mulewatch.composition.app"):
        await asyncio.wait_for(app.run(), timeout=5.0)
    assert any(
        r.getMessage() == "policy unchanged: catalogue re-evaluation skipped"
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# Full mode (download.enabled): wiring of the download loop
# ---------------------------------------------------------------------------


class FakeDownloadClient(FakeMuleClient):
    """Test download client: also satisfies add_link/download_queue (no-op).

    ``queue_calls`` counts the queue polls: proof that a download-loop cycle DID
    run (``download_queue`` is the only network ``await`` of an empty cycle)."""

    def __init__(self) -> None:
        super().__init__()
        self.queue_calls = 0

    async def add_link(self, ed2k_link: str) -> None:
        return None

    async def download_queue(self) -> tuple[DownloadEntry, ...]:
        self.queue_calls += 1
        return ()

    async def shared_files(self) -> tuple[SharedFileEntry, ...]:
        return ()


class _ShutdownOnQueueDownloadClient(FakeDownloadClient):
    """Download client that fires the shutdown on the FIRST ``download_queue`` (1 cycle, stop).

    Bounds the run DETERMINISTICALLY on the DOWNLOAD loop itself: the shutdown is set
    ONLY once the download loop has run a cycle (queue poll) → proves that the
    loop body ran (not just that the task was created), without a timing race
    nor a real ``sleep``. The ``queue_calls`` counter stays readable afterwards."""

    def __init__(self, app_holder: dict[str, CrawlerApp]) -> None:
        super().__init__()
        self._app_holder = app_holder

    async def download_queue(self) -> tuple[DownloadEntry, ...]:
        result = await super().download_queue()
        self._app_holder["app"]._on_signal()  # shutdown AFTER the 1st download cycle
        return result


class _UnreachableDownloadClient(FakeDownloadClient):
    """Download client whose ``connect`` raises ``ClientUnreachableError`` (daemon down at boot)."""

    async def connect(self) -> None:
        raise ClientUnreachableError("download daemon down")


@pytest.mark.asyncio
async def test_observer_mode_runs_without_the_download_loop(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # download absent → observer: starts, runs one cycle, stops; no download client built.
    holder: dict[str, CrawlerApp] = {}
    built: list[AmuleEndpoint] = []

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(holder)

    def download_factory(endpoint: AmuleEndpoint) -> FakeDownloadClient:
        built.append(endpoint)
        return FakeDownloadClient()

    app = CrawlerApp(
        crawler_config=_crawler_config(tmp_path),  # no download → observer
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        policy_fingerprint=_FP,
        client_factory=factory,
        download_client_factory=download_factory,
    )
    holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)
    assert built == []  # observer: no download connection is ever opened


@pytest.mark.asyncio
async def test_full_mode_runs_the_download_loop(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # The shutdown is driven DETERMINISTICALLY by the DOWNLOAD loop itself (the download
    # client fires the shutdown on its 1st queue poll) → we prove that the BODY of the download
    # loop ran (not just that the task was created), without a timing race.
    holder: dict[str, CrawlerApp] = {}
    download_client = _ShutdownOnQueueDownloadClient(holder)

    def search_factory(endpoint: AmuleEndpoint) -> FakeMuleClient:
        return FakeMuleClient()  # does NOT drive the shutdown: the download loop arms it

    app = CrawlerApp(
        crawler_config=_full_crawler_config(tmp_path),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        policy_fingerprint=_FP,
        client_factory=search_factory,
        download_client_factory=lambda endpoint: download_client,
    )
    holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)
    # the download loop ran ≥ 1 cycle (body ran, not just the task created).
    assert download_client.queue_calls >= 1


@pytest.mark.asyncio
async def test_full_mode_tolerates_download_daemon_unreachable_at_startup(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # the download daemon unreachable at startup is TOLERATED (handoff / DV8): we do NOT
    # fail, the loop is armed anyway (its backoff governs the retries).
    holder: dict[str, CrawlerApp] = {}

    def search_factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(holder)

    def download_factory(endpoint: AmuleEndpoint) -> _UnreachableDownloadClient:
        return _UnreachableDownloadClient()

    app = CrawlerApp(
        crawler_config=_full_crawler_config(tmp_path),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        policy_fingerprint=_FP,
        client_factory=search_factory,
        download_client_factory=download_factory,
    )
    holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)  # does not raise: connect tolerated


class _BlockingPollClock(FakeClock):
    """Clock whose LONG sleeps (≥ 5 s) BLOCK for good (on an Event that is never set).

    Models the IN-CYCLE sleep of the download loop (``_sleep_or_nudge``, 30 s poll): it stays
    BLOCKED in ``clock.sleep``, so the loop CANNOT re-test ``self._shutdown`` on its own; only
    an explicit CANCELLATION by ``_supervise`` gets it out. A BARRIER: as soon as the download
    poll has entered a long sleep, we ARM ``self._shutdown`` - the shutdown is thus requested
    while it is blocked. If ``_supervise`` did NOT cancel it, the ``TaskGroup`` would wait
    forever and the armed ``shutdown_deadline`` would fire a ``TimeoutError`` (force-exit): the
    test would
    fail fail-closed. The SHORT sleeps (search inter-keyword pauses) yield immediately
    (determinism, no real time). The search inter-cycle sleep (≥ 5 s) also blocks → it is
    exited by the cancellation of ``loop_task`` (already in place)."""

    def __init__(self, app_holder: dict[str, CrawlerApp]) -> None:
        super().__init__()
        self._app_holder = app_holder
        self._blocked_long_polls: set[float] = set()
        self._never = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        if seconds < 5.0:
            await super().sleep(seconds)  # short pause: yields (instantaneous)
            return
        # Long sleep (in-cycle poll of a loop, or search inter-cycle): we note the pace and,
        # as soon as the download poll (30 s) is blocked, we request the shutdown WHILE it
        # sleeps, then we BLOCK for good.
        self._blocked_long_polls.add(seconds)
        if 30.0 in self._blocked_long_polls:
            self._app_holder["app"]._shutdown.set()
        await self._never.wait()  # NEVER resolves: exit only via cancellation


@pytest.mark.asyncio
async def test_full_mode_shutdown_cancels_the_download_loop_promptly(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # REGRESSION (holistic review): at shutdown, ``_supervise`` must EXPLICITLY cancel the
    # download loop (sibling task of the search ``loop_task``). Without this, it stays
    # blocked in its in-cycle sleep (``_sleep_or_nudge`` does NOT watch ``self._shutdown``),
    # the ``TaskGroup`` waits on its poll (30 s), the ``shutdown_deadline`` fires a
    # ``TimeoutError`` FIRST and the shutdown is FORCED - not clean. Here: a clock whose long
    # sleeps BLOCK, which ARMS the shutdown once the loop is blocked in its poll. If the
    # cancellation happens, ``run()`` RETURNS promptly (without reaching the deadline); otherwise
    # it would ``TimeoutError`` (deadline) or block until the external guard → fail-closed failure.
    holder: dict[str, CrawlerApp] = {}
    clock = _BlockingPollClock(holder)

    # _full_crawler_config: download poll 30 s, shutdown_deadline 30 s.
    app = CrawlerApp(
        crawler_config=_full_crawler_config(tmp_path),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=clock,
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        policy_fingerprint=_FP,
        client_factory=lambda endpoint: FakeMuleClient(),
        download_client_factory=lambda endpoint: FakeDownloadClient(),
    )
    holder["app"] = app
    # The external guard (3 s of REAL time) is WELL below the shutdown_deadline (30 s) AND below
    # the polls (10 s/30 s): it can only fire if the shutdown is NOT prompt. With the
    # cancellation, the run returns within a few event-loop ticks (no real time is consumed).
    await asyncio.wait_for(app.run(), timeout=3.0)


@pytest.mark.asyncio
async def test_full_mode_shutdown_leaves_no_task_leaked(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # T12 - shutdown INVARIANT "no task leak". The ``TaskGroup`` guarantees BY
    # CONSTRUCTION that on exit from ``run()`` neither loop (search/download) survives: its
    # ``__aexit__`` waits for ALL its tasks to finish, and ``_supervise``
    # cancels them ALL explicitly at shutdown. This test LOCKS the invariant: it would fail if
    # a future regression detached a loop from the ``TaskGroup`` (``asyncio.create_task`` outside
    # the group) or forgot to cancel a sibling task - a ``pending`` task would then survive
    # ``run()``. We prove it by DIFFERENCE: the tasks born DURING ``run()`` (full = both
    # loops blocked in their sleep, shutdown armed once blocked) must ALL be
    # finished once ``run()`` has returned. The ``_BlockingPollClock`` forces the worst case: the
    # loops can only exit VIA the explicit cancellation by ``_supervise``.
    holder: dict[str, CrawlerApp] = {}
    clock = _BlockingPollClock(holder)
    app = CrawlerApp(
        crawler_config=_full_crawler_config(tmp_path),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=clock,
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        policy_fingerprint=_FP,
        client_factory=lambda endpoint: FakeMuleClient(),
        download_client_factory=lambda endpoint: FakeDownloadClient(),
    )
    holder["app"] = app
    before = asyncio.all_tasks()  # snapshot BEFORE (the test task + pytest-asyncio infra)
    await asyncio.wait_for(app.run(), timeout=3.0)
    # Tasks born DURING the run (the loops of the TaskGroup): all must be finished.
    # No ``pending`` task must remain - otherwise a loop leaked the lifecycle.
    leaked = [task for task in asyncio.all_tasks() - before if not task.done()]
    assert leaked == [], f"leaked tasks after shutdown: {leaked!r}"


# ---------------------------------------------------------------------------
# Task 8 - metrics + CrawlerStarted + log_level bootstrap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_server_started_when_enabled(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    """/metrics server started when metrics.enabled=True."""
    started: list[int] = []
    holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(holder)

    def metrics_server(port: int, registry: object) -> None:
        started.append(port)

    app = _make_app(
        tmp_path,
        matcher_config,
        factory=factory,
        observability=ObservabilityConfig(
            log_level="INFO",
            metrics=MetricsConfig(enabled=True, port=9123),
            notification_timeout_seconds=5.0,
            notifications=(),
        ),
        metrics_server=metrics_server,
    )
    holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)
    assert started == [9123]  # server started because metrics.enabled=True


@pytest.mark.asyncio
async def test_metrics_server_not_started_when_disabled(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    """/metrics server NOT started when metrics.enabled=False."""
    started: list[int] = []
    holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(holder)

    def metrics_server(port: int, registry: object) -> None:
        started.append(port)

    app = _make_app(
        tmp_path,
        matcher_config,
        factory=factory,
        observability=ObservabilityConfig(
            log_level="INFO",
            metrics=MetricsConfig(enabled=False, port=9123),
            notification_timeout_seconds=5.0,
            notifications=(),
        ),
        metrics_server=metrics_server,
    )
    holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)
    assert started == []  # metrics.enabled=False → no server


@pytest.mark.asyncio
async def test_metrics_server_not_started_when_observability_absent(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    """/metrics server NOT started when observability=None (obs is None)."""
    started: list[int] = []
    holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(holder)

    def metrics_server(port: int, registry: object) -> None:
        started.append(port)

    app = _make_app(
        tmp_path,
        matcher_config,
        factory=factory,
        observability=None,
        metrics_server=metrics_server,
    )
    holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)
    assert started == []  # obs=None → no server


@pytest.mark.asyncio
async def test_emits_crawler_started_observer_mode(
    tmp_path: Path, matcher_config: MatcherConfig, caplog: pytest.LogCaptureFixture
) -> None:
    """CrawlerStarted(mode='observer') emitted at boot in observer mode."""
    holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(holder)

    app = _make_app(tmp_path, matcher_config, factory=factory)
    holder["app"] = app
    with caplog.at_level(logging.INFO, logger="mulewatch.observability"):
        await asyncio.wait_for(app.run(), timeout=5.0)
    assert any("mode observer" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_emits_crawler_started_full_mode(
    tmp_path: Path, matcher_config: MatcherConfig, caplog: pytest.LogCaptureFixture
) -> None:
    """CrawlerStarted(mode='full') emitted at boot in full mode."""
    holder: dict[str, CrawlerApp] = {}
    download_client = _ShutdownOnQueueDownloadClient(holder)

    app = CrawlerApp(
        crawler_config=_full_crawler_config(tmp_path),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        policy_fingerprint=_FP,
        client_factory=lambda e: FakeMuleClient(),
        download_client_factory=lambda endpoint: download_client,
    )
    holder["app"] = app
    with caplog.at_level(logging.INFO, logger="mulewatch.observability"):
        await asyncio.wait_for(app.run(), timeout=5.0)
    assert any("mode full" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Port-sync (High-ID): loop ON / OFF
# ---------------------------------------------------------------------------


class _PortSyncCapableClient(FakeMuleClient):
    """Test port-sync EC client: satisfies get/set_listen_port + network_status (High-ID)."""

    def __init__(self) -> None:
        super().__init__(
            status=NetworkStatus(ed2k_id=0x02000001, ed2k_high=True, kad_status=KadStatus.CONNECTED)
        )
        self.listen_port = 4662
        self.set_ports: list[int] = []

    async def get_listen_port(self) -> int:
        return self.listen_port

    async def set_listen_port(self, port: int) -> None:
        self.set_ports.append(port)
        self.listen_port = port


class _ShutdownOnPollReader:
    """Forwarded-port reader that triggers the shutdown on the FIRST poll (1 cycle then stop).

    Bounds the run DETERMINISTICALLY on the PORT-SYNC loop itself: the shutdown is set
    only once ``forwarded_port`` has run → proves that the loop body has started.
    Returns ``None`` ("not ready") → the loop sleeps without touching the EC (no divergence to
    fix).
    """

    def __init__(self, app_holder: dict[str, CrawlerApp]) -> None:
        self._app_holder = app_holder
        self.calls = 0

    async def forwarded_port(self) -> int | None:
        self.calls += 1
        self._app_holder["app"]._on_signal()  # shutdown AFTER the 1st poll
        return None

    async def aclose(self) -> None:
        return None


class _RecordingRestarter:
    """Test no-op restarter (never called here: the reader returns None → no restart)."""

    def __init__(self) -> None:
        self.calls = 0

    async def restart(self) -> None:
        self.calls += 1

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_port_sync_loop_runs_when_section_present(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # port_sync section present (enabled: true) → the port-sync loop is ARMED. The shutdown is
    # driven by the reader (1st poll → signal) → we prove that the BODY of the loop ran.
    holder: dict[str, CrawlerApp] = {}
    reader = _ShutdownOnPollReader(holder)
    ec_client = _PortSyncCapableClient()

    app = CrawlerApp(
        crawler_config=_port_sync_crawler_config(tmp_path),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        policy_fingerprint=_FP,
        client_factory=lambda endpoint: ec_client,
        port_forwarding_reader_factory=lambda url: reader,
        mule_restarter_factory=lambda: _RecordingRestarter(),
    )
    holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)
    assert reader.calls >= 1  # the body of the port-sync loop did run ≥ 1 cycle


@pytest.mark.asyncio
async def test_port_sync_loop_off_when_no_config(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # No port_sync section → loop OFF (Low-ID tolerated). The factories must NEVER be
    # called: we prove it with factories that would raise if they were.
    holder: dict[str, CrawlerApp] = {}

    def boom_reader(url: str) -> object:
        raise AssertionError("the reader factory must not be called (port-sync OFF)")

    def boom_restarter() -> object:
        raise AssertionError("the restarter factory must not be called (port-sync OFF)")

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(holder)

    app = CrawlerApp(
        crawler_config=_crawler_config(tmp_path),  # no port_sync
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        policy_fingerprint=_FP,
        client_factory=factory,
        port_forwarding_reader_factory=boom_reader,  # type: ignore[arg-type]
        mule_restarter_factory=boom_restarter,  # type: ignore[arg-type]
    )
    holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)  # does not raise (factories never called)


@pytest.mark.asyncio
async def test_port_sync_tolerates_ec_daemon_unreachable_at_startup(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # The dedicated port-sync EC connection unreachable at startup is TOLERATED (R6): we do NOT
    # fail, the loop is armed anyway (the loop's backoff governs).
    holder: dict[str, CrawlerApp] = {}
    reader = _ShutdownOnPollReader(holder)

    class _UnreachableEcClient(_PortSyncCapableClient):
        async def connect(self) -> None:
            raise ClientUnreachableError("port-sync daemon down")

    app = CrawlerApp(
        crawler_config=_port_sync_crawler_config(tmp_path),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        policy_fingerprint=_FP,
        client_factory=lambda endpoint: _UnreachableEcClient(),
        port_forwarding_reader_factory=lambda url: reader,
        mule_restarter_factory=lambda: _RecordingRestarter(),
    )
    holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)  # does not raise: connect tolerated
    assert reader.calls >= 1


def test_default_port_forwarding_reader_factory_builds_a_gluetun_reader() -> None:
    from mulewatch.adapters.gluetun_port import GluetunPortReader
    from mulewatch.composition.app import default_port_forwarding_reader_factory

    reader = default_port_forwarding_reader_factory("http://gluetun:8000")
    assert isinstance(reader, GluetunPortReader)


def test_default_mule_restarter_factory_builds_an_s6_restarter() -> None:
    # amuled is a local s6 service now: the restart takes no URL and no Docker API.
    from mulewatch.adapters.s6_restart import S6MuleRestarter
    from mulewatch.composition.app import default_mule_restarter_factory

    assert isinstance(default_mule_restarter_factory(), S6MuleRestarter)


# ---------------------------------------------------------------------------
# WebUI in-process (spec §5/§17.1): own thread + loop, degrade on crash
# ---------------------------------------------------------------------------


class _FakeWebuiServer:
    """Fake uvicorn-shaped server: ``serve()`` awaits on its OWN loop until ``should_exit`` is
    set (from the main thread by ``_stop_webui``), then returns - mirroring the real graceful
    stop. Records that it started and that it returned, so the test can prove start + stop."""

    def __init__(self) -> None:
        self.should_exit = False
        self.served = threading.Event()
        self.stopped = threading.Event()

    async def serve(self) -> None:
        self.served.set()
        while not self.should_exit:
            await asyncio.sleep(0.01)
        self.stopped.set()


class _CrashingWebuiServer:
    """Fake server whose ``serve()`` raises immediately: models a webui-thread crash. The
    crawler must DEGRADE (log loudly, keep running), not propagate (spec §17.1)."""

    def __init__(self) -> None:
        self.should_exit = False

    async def serve(self) -> None:
        raise RuntimeError("webui boom")


@pytest.mark.asyncio
async def test_webui_starts_on_own_thread_and_stops_at_shutdown(
    tmp_path: Path, matcher_config: MatcherConfig, caplog: pytest.LogCaptureFixture
) -> None:
    # webui.enabled → run() builds the ASGI app, calls the factory with (app,), and serves it on
    # a daemon thread bound to the FIXED 0.0.0.0:8080. At shutdown, _stop_webui sets should_exit
    # + joins the thread.
    holder: dict[str, CrawlerApp] = {}
    server = _FakeWebuiServer()
    captured: dict[str, object] = {}

    def fake_factory(app: Starlette) -> _FakeWebuiServer:
        captured["app"] = app
        return server

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(holder)

    app = CrawlerApp(
        crawler_config=_crawler_config(tmp_path, webui=WebuiConfig(enabled=True)),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        policy_fingerprint=_FP,
        client_factory=factory,
        webui_server_factory=fake_factory,
    )
    holder["app"] = app
    with caplog.at_level(logging.INFO, logger="mulewatch.composition.app"):
        await asyncio.wait_for(app.run(), timeout=5.0)
    assert isinstance(captured["app"], Starlette)  # the built webui ASGI app was passed
    assert any(  # the log reports the FIXED in-container bind
        "webui serving on 0.0.0.0:8080" in r.getMessage() for r in caplog.records
    )
    assert server.served.is_set()  # serve() ran on the webui thread
    assert server.should_exit is True  # _stop_webui asked it to exit at shutdown
    assert server.stopped.is_set()  # serve() returned → the thread joined cleanly


@pytest.mark.asyncio
async def test_webui_not_started_when_disabled(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # webui.enabled=False → the factory is NEVER called and no thread is started. We prove it
    # with a factory that would fail the test if invoked.
    holder: dict[str, CrawlerApp] = {}

    def boom_factory(app: Starlette) -> WebuiServer:
        raise AssertionError("the webui factory must not be called when disabled")

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(holder)

    app = CrawlerApp(
        crawler_config=_crawler_config(tmp_path, webui=WebuiConfig(enabled=False)),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        policy_fingerprint=_FP,
        client_factory=factory,
        webui_server_factory=boom_factory,
    )
    holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)  # does not raise (factory never called)
    assert not any(t.name == "webui" for t in threading.enumerate())  # no webui thread left


@pytest.mark.asyncio
async def test_webui_crash_degrades_and_crawler_shuts_down_cleanly(
    tmp_path: Path, matcher_config: MatcherConfig, caplog: pytest.LogCaptureFixture
) -> None:
    # A webui-thread crash (serve() raises) must NOT propagate out of run(): the crawler logs
    # loudly and keeps crawling to a clean shutdown (DECISION spec §17.1: degrade).
    holder: dict[str, CrawlerApp] = {}
    server = _CrashingWebuiServer()

    def crash_factory(app: Starlette) -> _CrashingWebuiServer:
        return server

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(holder)

    app = CrawlerApp(
        crawler_config=_crawler_config(tmp_path, webui=WebuiConfig(enabled=True)),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        policy_fingerprint=_FP,
        client_factory=factory,
        webui_server_factory=crash_factory,
    )
    holder["app"] = app
    with caplog.at_level(logging.ERROR, logger="mulewatch.composition.app"):
        await asyncio.wait_for(app.run(), timeout=5.0)  # crash does NOT propagate
    assert any("webui thread crashed" in r.getMessage() for r in caplog.records)


def test_default_webui_server_factory_builds_a_uvicorn_server() -> None:
    import uvicorn

    from mulewatch.composition.app import default_webui_server_factory

    server = default_webui_server_factory(Starlette())
    assert isinstance(server, uvicorn.Server)
    assert server.config.host == "0.0.0.0"  # FIXED in-container bind
    assert server.config.port == 8080


# ---------------------------------------------------------------------------
# Runtime controls (phase P6a): the pause gate, the force-cycle race, restart-through-control.
# ---------------------------------------------------------------------------


class _BlockingSleepClock(FakeClock):
    """Clock whose ``sleep`` BLOCKS forever (models an inter-cycle sleep that would not wake on
    its own) - so ``_sleep_or_forced`` can only return via the force-cycle event."""

    async def sleep(self, seconds: float) -> None:
        await asyncio.Event().wait()  # never resolves: exit only via cancellation


@pytest.mark.asyncio
async def test_run_loop_exits_when_shutdown_already_set(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # Deterministic cover of the ``while not self._shutdown.is_set()`` false-exit arc: with the
    # shutdown already set, ``_run_loop`` reads the cycle index once then exits WITHOUT entering
    # the body (no cycle runs), so the per-cycle deps are never touched (hence the casts of None).
    app = _make_app(tmp_path, matcher_config, factory=lambda e: FakeMuleClient())
    app._shutdown.set()
    reads: list[int] = []

    class _SchedulerStub:
        def read_cycle_index(self) -> int:
            reads.append(1)
            return 0

    await asyncio.wait_for(
        app._run_loop(
            workers=(),
            clients=(),
            node_id="n",
            scheduler_state=_SchedulerStub(),  # type: ignore[arg-type]
            backoff=cast(BackoffRegistry, None),
            telemetry=cast(Telemetry, None),
            edge=cast(EdgeState, None),
        ),
        timeout=1.0,
    )
    assert reads == [1]  # read the cycle index once, then exited (the loop body never ran)


@pytest.mark.asyncio
async def test_app_starts_unpaused_with_force_event_clear(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # __init__ arms the run gate (``_resumed`` set = un-paused) and leaves ``_force_cycle`` clear.
    app = _make_app(tmp_path, matcher_config, factory=lambda e: FakeMuleClient())
    assert app._resumed.is_set()
    assert not app._force_cycle.is_set()


@pytest.mark.asyncio
async def test_sleep_or_forced_returns_via_force_and_clears_event(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # FORCED path: the clock sleep would block forever, but ``_force_cycle`` set short-circuits
    # the race; afterwards the event is CLEARED (so one force = exactly one immediate cycle).
    app = _make_app(
        tmp_path, matcher_config, factory=lambda e: FakeMuleClient(), clock=_BlockingSleepClock()
    )
    app._force_cycle.set()
    await asyncio.wait_for(app._sleep_or_forced(100.0), timeout=1.0)
    assert not app._force_cycle.is_set()


@pytest.mark.asyncio
async def test_sleep_or_forced_normal_path_sleeps_and_leaves_event_clear(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # NORMAL path: no force set → the (instant FakeClock) sleep wins; the force event stays clear.
    clock = FakeClock()
    app = _make_app(tmp_path, matcher_config, factory=lambda e: FakeMuleClient(), clock=clock)
    await asyncio.wait_for(app._sleep_or_forced(0.0), timeout=1.0)
    assert not app._force_cycle.is_set()
    assert clock.sleeps == [0.0]  # the clock-sleep branch was taken


@pytest.mark.asyncio
async def test_resumed_gate_blocks_when_cleared_and_releases_when_set(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # Focused gate semantics: cleared ``_resumed`` blocks a waiter; setting it releases it.
    app = _make_app(tmp_path, matcher_config, factory=lambda e: FakeMuleClient())
    app._resumed.clear()  # paused
    gate = asyncio.create_task(app._resumed.wait())
    await asyncio.sleep(0)
    assert not gate.done()
    app._resumed.set()  # resume
    await asyncio.wait_for(gate, timeout=1.0)
    assert gate.done()


class _ShutdownOnSearchClient(FakeMuleClient):
    """Fires the shutdown on the first search (a cycle ran); its status never does."""

    def __init__(self, app_holder: dict[str, CrawlerApp]) -> None:
        super().__init__()
        self._app_holder = app_holder

    async def search(
        self, keyword: str, channel: str, budget_seconds: float
    ) -> tuple[FileObservation, ...]:
        if not self.searches:
            self._app_holder["app"]._on_signal()
        return await super().search(keyword, channel, budget_seconds)


@pytest.mark.asyncio
async def test_pause_gate_blocks_the_cycle_until_resumed(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # WIRING: a paused app (``_resumed`` cleared before run) blocks at the loop's gate BEFORE any
    # cycle - the client is never searched and the run cannot self-shutdown. Resuming releases
    # the gate → a cycle runs, searches, fires the shutdown, exits. Without the gate, the cycle
    # would run immediately and the run would finish before resume.
    holder: dict[str, CrawlerApp] = {}
    client = _ShutdownOnSearchClient(holder)
    app = _make_app(tmp_path, matcher_config, factory=lambda e: client)
    holder["app"] = app
    app._resumed.clear()  # start paused
    run_task = asyncio.create_task(app.run())
    for _ in range(100):  # ample ticks for the ungated cycle to have run + shut down
        await asyncio.sleep(0)
    assert client.searches == []  # paused: no cycle ran
    assert not run_task.done()  # blocked at the gate, no self-shutdown
    app._resumed.set()  # resume → a cycle runs
    await asyncio.wait_for(run_task, timeout=5.0)
    assert client.searches  # a cycle ran after resume


@pytest.mark.asyncio
async def test_restart_control_triggers_graceful_shutdown(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # Driving ``LoopCrawlerControl.restart()`` (bound to the app's own events + this loop) sets
    # ``_shutdown`` on the loop thread → an in-flight cycle is cancelled and ``run()`` exits
    # cleanly, through the control path.
    app = _make_app(tmp_path, matcher_config, factory=lambda e: _BlockingClient())
    run_task = asyncio.create_task(app.run())
    for _ in range(20):  # let the cycle start and block in search
        await asyncio.sleep(0)
    control = LoopCrawlerControl(
        loop=asyncio.get_running_loop(),
        force_cycle=app._force_cycle,
        resumed=app._resumed,
        shutdown=app._shutdown,
    )
    control.restart()
    await asyncio.wait_for(run_task, timeout=5.0)
