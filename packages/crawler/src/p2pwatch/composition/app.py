"""Composition root: assembles the clients + UNIQUE repos + engine + loop (spec §4/§6).

COMPOSITION layer (the only one allowed to import adapters AND application). Builds:
- ONE ``SqliteCatalogRepository`` + ONE ``SqliteLocalStateRepository`` +
  ``SqliteSchedulerStateRepository`` (single writer, invariant §11), connections opened
  via ``open_catalog``/``open_local`` (migrations checked at startup, fail-fast §14).
- the ``MatchingEngine`` (once), the ``node_id`` (config override or the one from local.db),
- ONE ``MuleClient`` + ``SearchWorker`` on the container's single amuled (design §6), whose
  session the status loop shares.

Loops: the search tasks (``SearchTasks``), the status loop, and the download loop when
configured.
OBSERVABLE & BOUNDED shutdown (spec §6): ``loop.add_signal_handler`` (NOT ``KeyboardInterrupt``,
which would preempt a sync function mid-write); 1st ^C → human line on stderr +
cancellation of the ``TaskGroup``; 2nd ^C → immediate ``SystemExit``; long-lived resources
are closed by the ``AsyncExitStack`` AFTER the full unwind of the ``TaskGroup`` (no worker
can write anymore), all within a bounded delay (``shutdown_deadline_seconds``).
"""

import asyncio
import logging
import signal
import sqlite3
import sys
import threading
from collections.abc import Callable, Sequence
from contextlib import AsyncExitStack, suppress
from importlib.metadata import version
from pathlib import Path
from typing import Protocol

import uvicorn
from prometheus_client import CollectorRegistry, start_http_server
from starlette.applications import Starlette

import p2pwatch.webui
from catalog_matching.config import MatcherConfig
from catalog_matching.engine import MatchingEngine
from catalog_matching.models import TargetSegment
from p2pwatch.adapters.config.crawler_config import (
    AmuleEndpoint,
    CrawlerConfig,
    DownloadConfig,
)
from p2pwatch.adapters.crawler_control_loop import LoopCrawlerControl
from p2pwatch.adapters.disk_space_shutil import ShutilDiskSpace
from p2pwatch.adapters.mule_api.client import AmuleApiClient
from p2pwatch.adapters.observability.apprise_notifier import AppriseNotifier
from p2pwatch.adapters.observability.dispatcher import ObservabilityDispatcher
from p2pwatch.adapters.observability.prometheus_sink import PrometheusSink
from p2pwatch.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from p2pwatch.adapters.persistence_sqlite.connection import open_catalog, open_local
from p2pwatch.adapters.persistence_sqlite.download_repository import SqliteDownloadRepository
from p2pwatch.adapters.persistence_sqlite.local_state_repository import (
    SqliteLocalStateRepository,
)
from p2pwatch.adapters.persistence_sqlite.scheduler_state_repository import (
    SqliteSchedulerStateRepository,
)
from p2pwatch.application.reevaluate_catalog import reevaluate_catalog
from p2pwatch.application.run_backfill import run_backfill_if_policy_changed
from p2pwatch.application.run_download_cycle import (
    DownloadLoopDeps,
    download_loop,
)
from p2pwatch.application.search_tasks import SearchTasks
from p2pwatch.application.search_worker import (
    BackoffRegistry,
    SearchWorker,
    WorkerDeps,
    WorkerPolicy,
)
from p2pwatch.application.status_loop import StatusBoard, StatusLoopDeps, status_loop
from p2pwatch.domain.observability.events import CrawlerStarted
from p2pwatch.ports.client_errors import ClientUnreachableError
from p2pwatch.ports.clock import Clock, Rng
from p2pwatch.ports.crawler_control import CrawlerControl
from p2pwatch.ports.decision_signal import DecisionSignal
from p2pwatch.ports.download_client import DownloadClient
from p2pwatch.ports.mule_client import MuleClient
from p2pwatch.ports.scheduler_state_repository import SchedulerStateRepository
from p2pwatch.ports.telemetry import Telemetry
from p2pwatch.webui.composition.app import build_app as build_webui_app

_logger = logging.getLogger("p2pwatch.composition.app")

# Client factories, injectable in test; the clock is the composition's, so the pacing shares it.
ClientFactory = Callable[[AmuleEndpoint, Clock], MuleClient]
DownloadClientFactory = Callable[[AmuleEndpoint, Clock], DownloadClient]


def default_download_client_factory(endpoint: AmuleEndpoint, clock: Clock) -> DownloadClient:
    """An ``AmuleApiClient`` dedicated to download: its own session."""
    return AmuleApiClient(endpoint.host, endpoint.port, endpoint.password, clock=clock)


MetricsServer = Callable[[int, CollectorRegistry], None]


def default_metrics_server(port: int, registry: CollectorRegistry) -> None:
    """Starts the /metrics HTTP server (daemon thread). Wrapper to fix the argument order."""
    start_http_server(port, registry=registry)  # pragma: no cover


class WebuiServer(Protocol):
    """The ``uvicorn.Server`` shape the crawler drives from the main thread: a ``serve()``
    coroutine (run on the webui thread's OWN loop) and a settable ``should_exit`` flag that
    the serve loop polls - set it True to ask for a graceful return (thread-safe by design)."""

    should_exit: bool

    async def serve(self) -> None: ...  # one line (branch-coverage gotcha, see CLAUDE.md)


# Injectable webui-server factory (fake in test): (built ASGI app) → uvicorn-shaped server. The
# default builds a real ``uvicorn.Server``; unit tests inject a fake (no real HTTP).
WebuiServerFactory = Callable[[Starlette], WebuiServer]

# FIXED in-container webui bind. WHY 0.0.0.0: Docker cannot route a published port to the
# container's internal loopback, so a 127.0.0.1 bind would be unreachable from outside; the
# container's network namespace IS the isolation boundary and exposure is governed by compose
# (published port + networks), not by an app-level bind address. WHY 8080 not 80: port 80 is
# privileged and needs CAP_NET_BIND_SERVICE, which collides with the ``cap_drop: ALL`` + non-root
# hardening floor (an unprivileged process cannot bind it).
_WEBUI_BIND_HOST = "0.0.0.0"
_WEBUI_BIND_PORT = 8080

# Bound on the webui-thread join at shutdown. Set ``should_exit`` then join: uvicorn's serve
# loop polls ``should_exit`` (~0.1 s) and returns with no active connections, so the join
# normally returns well within the bound. The join runs via ``asyncio.to_thread`` (see
# ``_stop_webui``) so the crawler's armed shutdown ``asyncio.timeout`` can still cancel it.
_WEBUI_JOIN_TIMEOUT_SECONDS = 5.0


def default_webui_server_factory(app: Starlette) -> uvicorn.Server:
    """A real ``uvicorn.Server`` bound to the fixed 0.0.0.0:8080 serving the webui ASGI ``app``
    (own thread + loop). Constructing it opens no socket (that happens in ``serve()``)."""
    return uvicorn.Server(
        uvicorn.Config(app, host=_WEBUI_BIND_HOST, port=_WEBUI_BIND_PORT, log_level="info")
    )


def _human(message: str) -> None:
    """Human shutdown line on stderr (spec §6: observable progress, outside logging)."""
    print(message, file=sys.stderr, flush=True)


def _build_policy(config: CrawlerConfig) -> WorkerPolicy:
    """Unpacks the policy config into primitives for the application (dependency rule)."""
    return WorkerPolicy(
        backoff_base_seconds=config.backoff.base_seconds,
        backoff_cap_seconds=config.backoff.cap_seconds,
        backoff_factor=config.backoff.factor,
        backoff_jitter_ratio=config.backoff.jitter_ratio,
    )


def default_client_factory(endpoint: AmuleEndpoint, clock: Clock) -> MuleClient:
    """A real ``AmuleApiClient`` on the given endpoint (default factory, substituted in test)."""
    return AmuleApiClient(endpoint.host, endpoint.port, endpoint.password, clock=clock)


class CrawlerApp:
    """Assembles and runs the crawler (composition root, spec §4/§6)."""

    def __init__(
        self,
        *,
        crawler_config: CrawlerConfig,
        targets: Sequence[TargetSegment],
        matcher_config: MatcherConfig,
        clock: Clock,
        rng: Rng,
        signal_hub: DecisionSignal,
        policy_fingerprint: str,
        client_factory: ClientFactory = default_client_factory,
        download_client_factory: DownloadClientFactory = default_download_client_factory,
        metrics_server: MetricsServer = default_metrics_server,
        webui_server_factory: WebuiServerFactory = default_webui_server_factory,
    ) -> None:
        self._crawler_config = crawler_config
        self._targets = tuple(targets)
        self._matcher_config = matcher_config
        self._clock = clock
        self._rng = rng
        self._signal = signal_hub
        self._policy_fingerprint = policy_fingerprint
        self._client_factory = client_factory
        self._download_client_factory = download_client_factory
        self._metrics_server = metrics_server
        self._webui_server_factory = webui_server_factory
        self._shutdown = asyncio.Event()
        # The pause gate (phase P6a), armed (set = un-paused) so the crawler starts running. The
        # webui mutates it thread-safely via ``LoopCrawlerControl`` (spec §10).
        self._resumed = asyncio.Event()
        self._resumed.set()
        self._signal_count = 0

    def _on_signal(self) -> None:
        """Loop handler (never preempts a sync function, spec §6)."""
        self._signal_count += 1
        if self._signal_count == 1:
            _human(
                "Shutdown requested: finishing in-flight searches, clean close… "
                "(Ctrl-C again to force)"
            )
            self._shutdown.set()
        else:
            _human("Forced shutdown.")
            raise SystemExit(1)

    async def _build_download_loop(
        self,
        *,
        download_config: DownloadConfig,
        stack: AsyncExitStack,
        catalog_repo: SqliteCatalogRepository,
        local_conn: sqlite3.Connection,
        telemetry: Telemetry,
    ) -> DownloadLoopDeps:
        """Assemble the download loop deps (download mode, spec §7).

        SHARED single repos (``catalog_repo`` already built; a ``SqliteDownloadRepository`` on
        the SAME ``local_conn`` - single writer on the event loop, no race). Its own session to
        the daemon, connected tolerating ``ClientUnreachableError`` (a daemon not yet listening
        at startup does not kill the crawler; the loop's backoff governs).
        """
        download_client = self._download_client_factory(
            self._crawler_config.amule_endpoint, self._clock
        )
        stack.push_async_callback(download_client.close)
        try:
            await download_client.connect()
        except ClientUnreachableError as error:
            _logger.warning(
                "download daemon unreachable at startup (%s): tolerated, retry by the loop",
                error,
            )
        return DownloadLoopDeps(
            client=download_client,
            downloads=SqliteDownloadRepository(local_conn),
            catalog=catalog_repo,
            targets=self._targets,
            disk=ShutilDiskSpace(download_config.output_dir),
            min_free_bytes=download_config.min_free_bytes,
            lost_after_seconds=download_config.lost_after_seconds,
            clock=self._clock,
            telemetry=telemetry,
            signal=self._signal,
            poll_interval_seconds=download_config.poll_interval_seconds,
            shutdown=self._shutdown,
        )

    async def _supervise(
        self,
        *,
        shutdown_timeout: asyncio.Timeout,
        workers: Sequence[SearchWorker],
        scheduler_state: SchedulerStateRepository,
        backoff: BackoffRegistry,
        status_deps: StatusLoopDeps,
        download_deps: DownloadLoopDeps | None,
    ) -> None:
        """Launch the loops, wait for shutdown (UNBOUNDED), ARM the bound, cancel ALL and unwind.

        Waiting on the shutdown signal is FREE (``shutdown_timeout`` enters here DISARMED -
        deadline ``None`` - so the crawler runs until stopped, over an unbounded
        span). AS SOON AS shutdown is requested, we ARM the bound (``reschedule`` to ``now +
        shutdown_deadline_seconds``) BEFORE cancelling: thus the ``TaskGroup`` unwind (the ``await``
        of the cancelled tasks on exit of the ``async with``) THEN the LIFO stack close
        (in ``run``) are both bounded - the app CANNOT appear stuck at shutdown.
        Cancellation lands at the next network ``await`` (never mid DB write, sync repos,
        spec §6).
        PROMPT SHUTDOWN OF ALL LOOPS: each sibling task must be cancelled EXPLICITLY -
        cancelling the search tasks does NOT cancel the status/download loops, which are
        their siblings in the ``TaskGroup``. Without this, shutdown would wait on each loop's
        in-cycle sleep (``_sleep_or_nudge`` of the download watches ONLY poll/nudge, not
        ``self._shutdown``), and the ``shutdown_deadline`` armed
        above would fire a ``TimeoutError`` FIRST - a routine Ctrl-C would then force the
        exit instead of a clean shutdown. So we cancel the ENTIRE set of created tasks.
        EMPIRICAL VERIFICATION: cancelling the children of a ``TaskGroup`` (the group itself
        not being cancelled) does NOT propagate a ``CancelledError`` on exit of the ``async with``
        - the unwind is CLEAN. So we print the progress AFTER the block, without ``except*``
        (which would be dead code). A real worker exception, however, would propagate as an
        ``ExceptionGroup`` - we don't mask it.
        """
        async with asyncio.TaskGroup() as group:
            tasks = [
                group.create_task(
                    SearchTasks(
                        workers=workers,
                        keywords=self._crawler_config.search_keywords,
                        resumed=self._resumed,
                        backoff=backoff,
                        scheduler_state=scheduler_state,
                        clock=self._clock,
                    ).run()
                ),
                group.create_task(status_loop(status_deps)),
            ]
            if download_deps is not None:
                tasks.append(group.create_task(download_loop(download_deps)))
            await self._shutdown.wait()  # UNBOUNDED (the bound is disarmed while running)
            shutdown_timeout.reschedule(
                asyncio.get_running_loop().time() + self._crawler_config.shutdown_deadline_seconds
            )
            for task in tasks:
                task.cancel()
        _human("Workers stopped.")

    def _start_webui(self, stack: AsyncExitStack, status: StatusBoard) -> None:
        """Start the read-only webui on its OWN thread + loop (spec §5), sharing only IMMUTABLE
        state with the crawler (the parsed matcher/targets, the DB paths). It reads through its
        OWN ``ReaderProvider`` (inside ``build_webui_app``); nothing here touches the crawler's
        event loop nor its writer connections. The thread is a daemon (never blocks process
        exit); its graceful stop is registered on ``stack`` so it runs during the normal LIFO
        unwind at shutdown, AFTER ``_supervise`` returns (see ``_stop_webui``)."""
        webui_pkg_dir = Path(p2pwatch.webui.__file__).parent
        # Runtime-control channel (phase P6a): bound to the crawler's OWN loop + events, so the
        # webui thread hands off intents thread-safely (spec §10). ``_start_webui`` runs within
        # ``run()``'s loop, so ``get_running_loop()`` is the crawler loop. The webui receives the
        # PORT (``CrawlerControl``); this concrete adapter holds no DB connection.
        control: CrawlerControl = LoopCrawlerControl(
            loop=asyncio.get_running_loop(),
            resumed=self._resumed,
            shutdown=self._shutdown,
        )
        app = build_webui_app(
            catalog_db=Path(self._crawler_config.catalog_db_path),
            local_db=Path(self._crawler_config.local_db_path),
            matcher_config=self._matcher_config,
            targets=self._targets,
            templates_dir=webui_pkg_dir / "adapters" / "templates",
            static_dir=webui_pkg_dir / "adapters" / "static",
            control=control,
            status=status,
            amule_url=self._crawler_config.webui.amule_url,
        )
        server = self._webui_server_factory(app)
        thread = threading.Thread(
            target=self._serve_webui, args=(server,), name="webui", daemon=True
        )
        thread.start()
        stack.push_async_callback(self._stop_webui, server, thread)
        _logger.info("webui serving on %s:%d (own thread)", _WEBUI_BIND_HOST, _WEBUI_BIND_PORT)

    def _serve_webui(self, server: WebuiServer) -> None:
        """Webui thread body: run the server on a FRESH loop in THIS thread. A crash DEGRADES
        (spec §17.1): log loudly and return - a webui failure must NOT stop the crawler. We catch
        ``Exception`` only (a ``KeyboardInterrupt``/``SystemExit`` still propagates), never
        ``BaseException``."""
        try:
            asyncio.run(server.serve())
        except Exception:
            _logger.exception("webui thread crashed: crawler continues (degraded, no HTTP)")

    async def _stop_webui(self, server: WebuiServer, thread: threading.Thread) -> None:
        """Graceful webui stop (runs during the LIFO stack unwind at shutdown): ask the serve
        loop to exit (``should_exit`` is polled by ``serve()``), then join the thread OFF the
        event loop via ``asyncio.to_thread`` so the crawler's armed shutdown ``asyncio.timeout``
        stays able to cancel it (the thread is a daemon, so a truly-hung webui dies at exit)."""
        server.should_exit = True
        await asyncio.to_thread(thread.join, _WEBUI_JOIN_TIMEOUT_SECONDS)

    async def run(self) -> None:
        """Async entry point: opens the resources, installs the signals, loops (§6).

        Ownership (spec §6): the ``AsyncExitStack`` owns the long-lived resources (daemon clients +
        2 connections). The shutdown bound is an ``asyncio.timeout`` ENTERED DISARMED (deadline
        ``None``): the steady-state run (waiting on the signal, searches) is UNBOUNDED - otherwise
        the crawler would die after ``shutdown_deadline_seconds`` of normal operation. ONLY the
        SHUTDOWN PHASE is bounded: ``_supervise`` ARMS the bound (``reschedule``) as soon as
        shutdown is requested, so the ``TaskGroup`` unwind THEN the LIFO stack close below fall
        under the deadline - the app CANNOT appear stuck at shutdown. An overrun raises
        ``TimeoutError`` (forced exit); the ``finally`` then attempts a best-effort close
        (suppress) so as not to re-block indefinitely. The bound NEVER arms without a requested
        shutdown → a ``TimeoutError`` can only hit a close that drags.
        """
        # Startup version line (spec 2026-07-10-git-driven-versioning): the number baked into the
        # installed wheel by the build (setuptools-scm / SETUPTOOLS_SCM_PRETEND_VERSION), so an
        # operator can correlate a running node to a release.
        _logger.info("p2pwatch version %s", version("p2pwatch"))
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, self._on_signal)
        loop.add_signal_handler(signal.SIGTERM, self._on_signal)
        stack = AsyncExitStack()
        try:
            catalog_conn = open_catalog(self._crawler_config.catalog_db_path)
            stack.callback(catalog_conn.close)
            local_conn = open_local(self._crawler_config.local_db_path)
            stack.callback(local_conn.close)

            local_repo = SqliteLocalStateRepository(local_conn)
            node_id = self._crawler_config.node_id or local_repo.node_id()
            obs = self._crawler_config.observability
            notifications = obs.notifications if obs is not None else ()
            registry = CollectorRegistry()
            notifier = AppriseNotifier(
                tuple((t.url, t.tag, t.node_prefix) for t in notifications),
                node_id=node_id,
            )
            telemetry = ObservabilityDispatcher(
                metrics=PrometheusSink(registry),
                notifier=notifier,
                notify_timeout_seconds=(
                    obs.notification_timeout_seconds if obs is not None else 5.0
                ),
            )
            if obs is not None and obs.metrics is not None and obs.metrics.enabled:
                self._metrics_server(obs.metrics.port, registry)
            catalog_repo = SqliteCatalogRepository(catalog_conn, node_id)
            scheduler_state = SqliteSchedulerStateRepository(local_conn)
            engine = MatchingEngine(self._matcher_config, self._targets)
            endpoint = self._crawler_config.amule_endpoint
            board = StatusBoard((endpoint.name,), self._clock)
            # In-process webui (spec §5): own thread + loop, started EARLY (before the daemon client
            # + startup backfill) so it is up promptly and stays isolated from the crawler's
            # synchronous work. Gated by ``webui.enabled``; a crash degrades (spec §17.1). Its
            # graceful stop is on ``stack`` → runs at the normal shutdown unwind (after DB conns
            # are pushed, so it stops the thread before those close during LIFO teardown).
            if self._crawler_config.webui.enabled:
                self._start_webui(stack, board)
            # SHARED backoff registry: built ONCE, RELOADED from scheduler_state
            # (backoff survives restart, spec §3/§7), injected into ALL workers
            # + passed to the search tasks that persist it. Single writer on the event loop.
            policy = _build_policy(self._crawler_config)
            backoff = BackoffRegistry(policy, self._clock, self._rng)
            backoff.load_from(scheduler_state.load_channel_backoff())
            deps = WorkerDeps(
                catalog=catalog_repo,
                engine=engine,
                signal=self._signal,
                backoff=backoff,
                telemetry=telemetry,
            )

            client = self._client_factory(endpoint, self._clock)
            stack.push_async_callback(client.close)
            # CONNECT at setup. A daemon not yet listening must NOT bring
            # the crawler down, and in one container that is the NORMAL case, not the exception:
            # the crawler and amuled start together under s6, and amuled starts amuleapi itself,
            # so the crawler routinely knocks first (design §4). We TOLERATE the
            # ClientUnreachableError and CONTINUE - the worker's reconnection backoff governs the
            # retries. connect() is idempotent → the worker's later _ensure_connected() stays a
            # no-op. We do NOT catch broader: ApiAuthError (wrong password) is NOT a
            # ClientUnreachableError → it keeps propagating (fail-fast config, spec §14).
            try:
                await client.connect()
            except ClientUnreachableError as error:
                _logger.warning(
                    "amuled unreachable at startup (%s): tolerated, the searches back off", error
                )
            workers = [SearchWorker(endpoint.name, client, deps)]
            # The status loop shares the search session (one session per container's amuled).
            status_deps = StatusLoopDeps(
                endpoint.name, client, self._clock, telemetry, self._shutdown, board
            )

            _logger.info("crawler started: node_id=%s", node_id)

            download_deps: DownloadLoopDeps | None = None
            # FULL mode ⟺ the ``download`` section is present (``enabled: true``). The unified
            # parser then guarantees the wiring is complete (endpoint) - no more
            # ``_require_full_config`` gate at composition.
            download_config = self._crawler_config.download
            if download_config is not None:
                download_deps = await self._build_download_loop(
                    download_config=download_config,
                    stack=stack,
                    catalog_repo=catalog_repo,
                    local_conn=local_conn,
                    telemetry=telemetry,
                )
                _logger.info("full mode: download loop armed")

            mode = "full" if download_config is not None else "observer"
            await telemetry.emit(CrawlerStarted(mode=mode))

            # Startup backfill (spec §7/§7.1): re-evaluate the WHOLE catalogue against the
            # current matcher, gated by a policy fingerprint stored in local.db (a
            # comment/whitespace-only edit to matcher.yml/targets.yml still triggers one
            # harmless extra pass, which then writes nothing before the marker updates).
            # Runs to completion BEFORE the loops so tier actions (download nudge, notify)
            # fire for the very first search, not a search later.
            summary = await run_backfill_if_policy_changed(
                fingerprint=self._policy_fingerprint,
                local_repo=local_repo,
                run_backfill=lambda: reevaluate_catalog(
                    catalog=catalog_repo, engine=engine, signal=self._signal, telemetry=telemetry
                ),
            )
            if summary is None:
                _logger.info("policy unchanged: catalogue re-evaluation skipped")
            else:
                _logger.info(
                    "catalogue re-evaluated: %d files, %d rows written",
                    summary.evaluated,
                    summary.written,
                )

            # Bound ENTERED DISARMED (None): the steady state is unbounded; ``_supervise`` arms it
            # (reschedule) as soon as shutdown is requested → only the shutdown phase + the aclose
            # below are bounded. (Verified empirically: timeout(None) does not fire; reschedule
            # from inside arms; a slow op after arming raises TimeoutError, a fast one does not.)
            async with asyncio.timeout(None) as shutdown_timeout:
                await self._supervise(
                    shutdown_timeout=shutdown_timeout,
                    workers=workers,
                    scheduler_state=scheduler_state,
                    backoff=backoff,
                    status_deps=status_deps,
                    download_deps=download_deps,
                )
                _human(f"{len(workers)} amuled session(s) closing…")
                await stack.aclose()
                _human("Databases closed: exiting.")
        finally:
            # Best-effort if the bounded shutdown failed (TimeoutError) or if setup raised:
            # close what remains WITHOUT ever re-blocking (suppress any failure/cancellation).
            with suppress(BaseException):
                await stack.aclose()
            loop.remove_signal_handler(signal.SIGINT)
            loop.remove_signal_handler(signal.SIGTERM)
