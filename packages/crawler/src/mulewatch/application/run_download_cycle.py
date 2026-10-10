"""The download loop: monitor → completions → new candidates → sleep/nudge (spec §5).

One serial task on the sole download session. ``run_download_cycle`` runs ONE iteration;
``download_loop`` repeats it until shutdown, waiting ``poll_interval`` or the decision nudge.
Four things in it are not obvious:

- **A completion is the client's own signal, never the bytes** (2026-09-02: 065B was stamped
  complete at 20.1 %). ``DownloadStatus.completed`` carries it.
- **Stamp presence BEFORE condemning.** ``last_seen_at`` is written for everything the client
  still lists, and only then does the TTL fail what it has not seen.
- **A download the client does not list is started again**, once per round, until it is listed
  or the TTL fails it: a client may forget its transfers on restart.
- **Step 0 re-connects every iteration.** It is idempotent and nearly free, and it is what makes
  "the client reconnects next round" true (2026-09-04 to 09-11: 7 days of a dead loop).

Errors: ``ClientUnreachableError`` and ``OSError`` on the disk measurement skip the iteration,
``RepositoryError`` is logged and the cycle continues. A stalled download is never abandoned.
"""

import asyncio
import logging
from collections.abc import Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Protocol

from catalog_matching.models import TargetSegment
from mulewatch.application.edge_state import EdgeState
from mulewatch.domain.download.policy import DownloadVerdict, download_policy
from mulewatch.domain.download.states import DownloadState
from mulewatch.domain.file_key import FileKey
from mulewatch.domain.observability.events import (
    DiskSpaceLow,
    DownloadCompleted,
    DownloadQueued,
    FreeSpaceSampled,
)
from mulewatch.ports.catalog_repository import DownloadCandidate, ObservedFile
from mulewatch.ports.client_errors import ClientUnreachableError, DownloadRejectedError
from mulewatch.ports.clock import Clock
from mulewatch.ports.decision_signal import DecisionSignal
from mulewatch.ports.disk_space import DiskSpace
from mulewatch.ports.download_client import (
    DownloadClient,
    DownloadRequest,
    DownloadStatus,
    FailureReason,
)
from mulewatch.ports.repository_errors import RepositoryError
from mulewatch.ports.telemetry import Telemetry

_logger = logging.getLogger("mulewatch.application.run_download_cycle")

# Conventional subject of the download nudge (DECISION D13). D-download subscribes to THIS
# subject; the producer side (the pipeline) calls signal("download").
DOWNLOAD_NUDGE_SUBJECT = "download"


class DownloadRepository(Protocol):
    """STRUCTURAL Protocol of the downloads repo (local typing; the adapter satisfies it).

    Minimal Protocol so the application depends ONLY on what it needs
    (record_queued/set_state/is_downloaded/mark_seen/expire_lost/active_states), without
    importing the adapter. The real ``SqliteDownloadRepository`` (and the test fake) satisfies it
    structurally. Stubs on ONE line (the ``def`` is covered when the class is created).
    """

    def record_queued(self, file: FileKey, target_id: str, size_bytes: int) -> bool: ...

    def set_state(
        self, file: FileKey, state: DownloadState, failure_reason: FailureReason | None = None
    ) -> None: ...

    def is_downloaded(self, file: FileKey) -> bool: ...

    def mark_seen(self, statuses: Iterable[DownloadStatus]) -> None: ...

    def expire_lost(self, max_age_seconds: float) -> tuple[FileKey, ...]: ...

    def active_states(self) -> dict[FileKey, DownloadState]: ...

    def get_target_id(self, file: FileKey) -> str | None: ...


class CatalogReader(Protocol):
    """STRUCTURAL Protocol of the catalog READS the loop needs (DECISION D9).

    Subset of ``CatalogRepository`` (download_decisions, last_observation, best_observation): the
    loop depends ONLY on what it reads, so the minimal test fake satisfies it without implementing
    record_observation/record_decision. The real ``SqliteCatalogRepository``
    satisfies it too. Stubs on ONE line.
    """

    def download_decisions(self) -> tuple[DownloadCandidate, ...]: ...

    def last_observation(self, file: FileKey) -> ObservedFile | None: ...

    def best_observation(self, file: FileKey) -> ObservedFile | None: ...


@dataclass
class DownloadDeps:
    """Dependencies of the download loop (composition assembles them once).

    ``targets`` serves the ``target_id → status`` lookup (pure policy). ``disk`` measures the
    free space of the filesystem amuled writes to (metadata only, no file is opened).
    ``catalog`` is typed to
    the NARROW ``CatalogReader`` Protocol above: the loop depends only on the subset it reads
    (consistent with the local ``DownloadRepository`` Protocol), so the minimal test fakes are
    accepted.
    """

    client: DownloadClient
    downloads: DownloadRepository
    catalog: CatalogReader
    targets: Sequence[TargetSegment]
    disk: DiskSpace
    min_free_bytes: int
    lost_after_seconds: float
    clock: Clock
    telemetry: Telemetry
    edge: EdgeState = field(default_factory=EdgeState, kw_only=True)  # low-disk warning


@dataclass
class DownloadLoopDeps(DownloadDeps):
    """``DownloadDeps`` + what it takes to REPEAT (nudge, cadence, shutdown) - DECISION D12."""

    signal: DecisionSignal
    poll_interval_seconds: float
    shutdown: asyncio.Event


def _target_status(targets: Sequence[TargetSegment], target_id: str) -> str:
    """Target status (lookup ``target_id → status``); ``complete`` by default if the target
    has vanished from the config (conservative: do not download for an unknown target)."""
    for target in targets:
        if target.target_id == target_id:
            return target.status
    return "complete"


async def _monitor(
    deps: DownloadDeps,
    states: dict[FileKey, DownloadState],
    listed: tuple[DownloadStatus, ...],
) -> None:
    """Reconciles ``downloads`` with the client's list: a failure it reports, else DOWNLOADING.

    ``FAILED`` is a wall only while the client reports a failure, so an erroneous download does
    not flap back each round; ``COMPLETED`` is one, so its notification never fires twice.
    """
    for status in listed:
        file = status.file
        current = states.get(file)
        if current is None:
            continue  # download outside the crawler: ignored
        if current is DownloadState.COMPLETED:
            continue  # already notified: don't regress and don't re-fire
        failed = status.failure_reason is not None
        target = DownloadState.FAILED if failed else DownloadState.DOWNLOADING
        if current is not target:
            deps.downloads.set_state(file, target, status.failure_reason)
            states[file] = target


async def _record_completion(
    deps: DownloadDeps, file: FileKey, states: dict[FileKey, DownloadState]
) -> None:
    """Marks ``completed`` (stamps completed_at) and notifies (step 2, §5).

    ``completed`` is terminal: the file stays in amuled's IncomingDir, nothing moves it and
    nothing opens it. The caller skips files already ``completed``, so the notification fires
    exactly once per download.
    """
    deps.downloads.set_state(file, DownloadState.COMPLETED)
    states[file] = DownloadState.COMPLETED
    # Every download target of the file, else the one it was queued for (the decision may have
    # dropped since); the native id names a file whose observations are gone.
    decided = [c.target_id for c in deps.catalog.download_decisions() if c.file == file]
    target_ids = decided or [deps.downloads.get_target_id(file) or "unknown"]
    titles = {target.target_id: target.title for target in deps.targets}
    best = deps.catalog.best_observation(file)
    await deps.telemetry.emit(
        DownloadCompleted(
            file,
            file.native_id if best is None else best.filename,
            tuple((target_id, titles.get(target_id, "")) for target_id in target_ids),
        )
    )
    _logger.info("file=%s completed", file.native_id)


async def _handle_completions(
    deps: DownloadDeps,
    states: dict[FileKey, DownloadState],
    listed: tuple[DownloadStatus, ...],
) -> None:
    """Completes each tracked file the client reports ``completed``.

    A ``failed`` file completes too: the TTL can condemn a download that had in fact finished.
    A repo failure on one file skips that file only.
    """
    for status in listed:
        file = status.file
        current = states.get(file)
        if not status.completed or current is None:
            continue  # not completed, or a download outside the crawler: ignored
        if current is DownloadState.COMPLETED:
            continue  # already completed: the notification fired once
        try:
            await _record_completion(deps, file, states)
        except RepositoryError as error:
            _logger.error(
                "completion file=%s repo failure (%s): file skipped, continues",
                file.native_id,
                error,
            )


def _expire_lost(deps: DownloadDeps) -> None:
    """Fails the downloads the client has not listed for ``lost_after_seconds`` (spec §2).

    It bounds a client that accepts ``start()`` without ever listing the download.
    ``is_downloaded`` stays state-blind, so a ``failed`` row still blocks automatic re-queuing;
    the manual retry is deleting the row.
    """
    for file in deps.downloads.expire_lost(deps.lost_after_seconds):
        _logger.warning(
            "file=%s unseen by the client for %ss: marked failed",
            file.native_id,
            deps.lost_after_seconds,
        )


async def _queue_new_candidates(deps: DownloadDeps, outstanding: int) -> None:
    """Replays tier=download decisions missing from ``downloads`` (step 3, spec §5).

    ``outstanding`` is what the client still has to transfer, measured from the cycle's
    snapshot; ``free`` is read once here. Both are MEASURED, never declared.
    """
    free = deps.disk.free_bytes()
    await deps.telemetry.emit(FreeSpaceSampled(free_bytes=free))
    if free >= deps.min_free_bytes:
        deps.edge.leave("disk_low")
    elif deps.edge.enter("disk_low"):
        await deps.telemetry.emit(DiskSpaceLow(free_bytes=free, min_free_bytes=deps.min_free_bytes))
    for candidate in deps.catalog.download_decisions():
        if deps.downloads.is_downloaded(candidate.file):
            continue
        observation = deps.catalog.last_observation(candidate.file)
        if observation is None:
            _logger.warning(
                "candidate file=%s without observation: link impossible, skipped",
                candidate.file.native_id,
            )
            continue
        verdict = download_policy(
            tier="download",
            target_status=_target_status(deps.targets, candidate.target_id),
            already_downloaded=False,
            free_bytes=free,
            outstanding_bytes=outstanding,
            file_size=observation.size_bytes,
            min_free_bytes=deps.min_free_bytes,
        )
        if verdict is not DownloadVerdict.DOWNLOAD:
            _logger.info(
                "candidate file=%s → %s (skipped/deferred)", candidate.file.native_id, verdict.value
            )
            continue
        # record_queued ONLY here (sync DB write); _start_unlisted sends it (network I/O): the
        # write precedes the network, and a start that raises leaves it 'queued' for next round.
        deps.downloads.record_queued(candidate.file, candidate.target_id, observation.size_bytes)
        outstanding += observation.size_bytes  # not listed by the client yet: carried in memory
        _logger.info("candidate file=%s queued for download", candidate.file.native_id)
        await deps.telemetry.emit(DownloadQueued(target_id=candidate.target_id))


async def _start_unlisted(deps: DownloadDeps, listed: frozenset[FileKey]) -> None:
    """Starts every ``queued`` or ``downloading`` download the client does not list (D13).

    A new download and a forgotten one are the same case. ``DownloadRejectedError`` fails THAT
    download, since retrying would resend a refused request; ``ClientUnreachableError``
    propagates and the caller skips the iteration.
    """
    # FRESH re-read: _queue_new_candidates wrote new QUEUED rows this cycle.
    states = deps.downloads.active_states()
    for file, state in states.items():
        if state not in {DownloadState.QUEUED, DownloadState.DOWNLOADING} or file in listed:
            continue
        observation = deps.catalog.last_observation(file)
        if observation is None:
            continue
        try:
            await deps.client.start(
                DownloadRequest(file, observation.filename, observation.size_bytes)
            )
        except DownloadRejectedError as error:
            deps.downloads.set_state(file, DownloadState.FAILED, FailureReason.REJECTED)
            _logger.warning(
                "start rejected by the client for file=%s (%s): marked failed",
                file.native_id,
                error,
            )


async def run_download_cycle(deps: DownloadDeps) -> None:
    """ONE iteration of the download loop (spec §5). Never raises: tolerates/absorbs.

    Two distinct error DOCTRINES (item I2 - anti-starvation):

    - ``ClientUnreachableError`` (client out of reach, from step 0 or ``_start_unlisted``) =
      dead client → ABORT the iteration ("a dead client makes everything fail"). We skip the
      rest; the next iteration retries.
    - ``RepositoryError`` (persistence failure, NO client I/O) → ISOLATED PER STEP: a repo
      failure in one step must NOT starve the others. Each step that can raise
      ``RepositoryError`` (monitor/completions/candidates) is wrapped separately (log +
      ``continue`` to the next step). ``_start_unlisted`` re-reads ``active_states`` FRESHLY, so it
      runs even if an upstream step partially failed. "NEVER abandon a stalled download": the
      1→2→3→4 order is preserved, each step is best-effort.

    The repos are sync → cancellation (shutdown) lands at the network ``await``, never mid-write.

    DECISION (audit 2026-06-23 / observability#5): a ``ClientUnreachableError`` here does NOT
    emit ``InstanceUnreachable`` (unlike the search tasks). The E-D5 taxonomy files this
    event under SEARCH only; the download loop is single-instance and the label
    ``instance=...`` would be meaningless (counter shared with the search workers). The
    unavailability is handled by the next cycle's retry + the warning log. Intentional
    asymmetry.
    """
    # Step 0 - CONNECT + SNAPSHOT: client I/O → ClientUnreachableError = dead daemon = ABORT.
    # ``connect()`` is IDEMPOTENT (the adapter no-ops when its transport is live) and is the ONLY
    # thing that re-arms a stream the adapter discarded after a failed read. SKIPPING IT WEDGES
    # THE LOOP: amuled is restarted by the port-sync on every VPN renegotiation, and nothing else
    # in this loop ever reconnects (field, 2026-09-04: 7 days of "client not connected").
    # Same guard as ``SearchWorker._ensure_connected`` and ``run_port_sync_cycle``.
    try:
        await deps.client.connect()
        listed = await deps.client.downloads()
    except ClientUnreachableError as error:
        _logger.warning("download daemon unreachable (%s): iteration skipped, retry", error)
        return
    listed_files = frozenset(status.file for status in listed)
    # Clamped at 0: a nascent download (total not known yet) must not invent free space.
    outstanding = sum(max(status.bytes_total - status.bytes_done, 0) for status in listed)
    # Step 1 - MONITOR: NO client I/O left (the list came from step 0) → only RepositoryError.
    # Every step shares that ONE snapshot: a second read could only contradict the first.
    try:
        states = deps.downloads.active_states()
        await _monitor(deps, states, listed)
    except RepositoryError as error:
        _logger.error("download monitor repo failure (%s): step skipped, continues", error)
    # Step 2 - COMPLETIONS: we RE-READ ``active_states`` FRESHLY (logic-download#2). Without it,
    # a failure in step 1 left ``states`` frozen/empty → every completed file → ``states.get is
    # None`` → ignored → NO completion recorded the entire cycle. The re-read is also better
    # aligned than ``states={}`` with the nominal case (fresh states), at the cost of one extra
    # repo call (idempotent). A failure of the re-read itself is caught downstream.
    # Steps 2 & 3 - NO client I/O → only RepositoryError possible, ISOLATED per step (I2):
    # a repo failure in one must NOT prevent the other from running.
    try:
        fresh_states = deps.downloads.active_states()
        await _handle_completions(deps, fresh_states, listed)
    except RepositoryError as error:
        _logger.error("download completions repo failure (%s): step skipped, continues", error)
    # Step 2b - PRESENCE + TTL: stamp FIRST, condemn after. The reverse order would fail a row
    # the client is showing us right now.
    try:
        deps.downloads.mark_seen(listed)
        _expire_lost(deps)
    except RepositoryError as error:
        _logger.error("download presence repo failure (%s): step skipped, continues", error)
    try:
        await _queue_new_candidates(deps, outstanding)
    except RepositoryError as error:
        _logger.error("download candidates repo failure (%s): step skipped, continues", error)
    except OSError as error:
        # A missing or unreadable output mount: refuse to admit anything rather than take down
        # the crawler, which also catalogues. Conservative, loud, and retried next cycle.
        _logger.error("output directory unmeasurable (%s): no candidate admitted, retry", error)
    # Step 4 - START: client I/O → ClientUnreachableError = dead daemon = ABORT. Re-reads
    # ``active_states`` FRESHLY, so it runs even if step 3 partially failed.
    try:
        await _start_unlisted(deps, listed_files)
    except ClientUnreachableError as error:
        _logger.warning("download daemon unreachable (%s): iteration skipped, retry", error)
    except RepositoryError as error:
        _logger.error("download start repo failure (%s): step skipped, retry", error)


async def _sleep_or_nudge(deps: DownloadLoopDeps) -> None:
    """Waits ``poll_interval`` OR the ``download`` nudge, whichever comes FIRST (spec §5).

    ``asyncio.wait(FIRST_COMPLETED)`` then cancel the loser: a decision change (nudge) wakes the
    loop immediately; otherwise the fallback poll wakes it at the cadence.
    Shutdown cancellation lands HERE (an ``await``), never mid DB write (sync).
    """
    sleep_task = asyncio.ensure_future(deps.clock.sleep(deps.poll_interval_seconds))
    nudge_task = asyncio.ensure_future(deps.signal.wait(DOWNLOAD_NUDGE_SUBJECT))
    try:
        await asyncio.wait({sleep_task, nudge_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (sleep_task, nudge_task):
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


async def download_loop(deps: DownloadLoopDeps) -> None:
    """Repeats ``run_download_cycle`` then waits (poll/nudge) until shutdown (DECISION D12).

    Wired by ``CrawlerApp`` into the ``TaskGroup``; cancellation (shutdown) lands
    at the next ``await`` (a poll or the sleep/nudge wait), never mid DB write.
    """
    while not deps.shutdown.is_set():
        await run_download_cycle(deps)
        if deps.shutdown.is_set():
            break
        await _sleep_or_nudge(deps)
