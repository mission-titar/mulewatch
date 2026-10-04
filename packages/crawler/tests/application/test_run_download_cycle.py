import asyncio
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta

import pytest

from catalog_matching.engine import DownloadCandidate
from catalog_matching.models import TargetSegment
from mulewatch.application.run_download_cycle import DownloadDeps, run_download_cycle
from mulewatch.domain.download.states import DownloadState
from mulewatch.domain.observability.events import (
    DiskSpaceLow,
    DownloadCompleted,
    DownloadQueued,
    FreeSpaceSampled,
)
from mulewatch.ports.catalog_repository import ObservedFile
from mulewatch.ports.mule_client import (
    KadStatus,
    MuleSearchFailedError,
    MuleUnreachableError,
    NetworkStatus,
)
from mulewatch.ports.mule_download_client import DownloadEntry, SharedFileEntry
from mulewatch.ports.repository_errors import RepositoryError
from tests.application.fakes import RecordingTelemetry

_A = "a" * 32
_B = "b" * 32

_TARGETS = (
    TargetSegment(
        season=2, seasonal_number=11, absolute_number=62, segment="A", title="t", status="lost"
    ),
    TargetSegment(
        season=2,
        seasonal_number=63,
        absolute_number=63,
        segment="A",
        title="t2",
        status="complete",
    ),
)


class FakeDownloadClient:
    """Scripted MuleDownloadClient: SCRIPTED download queue, captures added links.

    ``disconnected`` models the REAL adapter's state before a login: every I/O call raises
    ``MuleUnreachableError`` until ``connect()`` succeeds.
    """

    def __init__(
        self,
        *,
        queue: list[tuple[DownloadEntry, ...]] | None = None,
        shared: list[tuple[SharedFileEntry, ...]] | None = None,
        connect_failures: list[Exception] | None = None,
        queue_failures: list[Exception] | None = None,
        add_failures: list[Exception] | None = None,
        shared_failures: list[Exception] | None = None,
        disconnected: bool = False,
    ) -> None:
        self._queue = list(queue or [()])
        self._shared = list(shared or [()])
        self._connect_failures = list(connect_failures or [])
        self._queue_failures = list(queue_failures or [])
        self._add_failures = list(add_failures or [])
        self._shared_failures = list(shared_failures or [])
        self._disconnected = disconnected
        self.added_links: list[str] = []
        self.connect_calls = 0

    def _require_connected(self) -> None:
        if self._disconnected:
            raise MuleUnreachableError("EC client not connected (call connect() first)")

    async def connect(self) -> None:
        self.connect_calls += 1
        if self._connect_failures:
            raise self._connect_failures.pop(0)
        self._disconnected = False

    async def close(self) -> None:
        return None

    async def add_link(self, ed2k_link: str) -> None:
        self._require_connected()
        if self._add_failures:
            raise self._add_failures.pop(0)
        self.added_links.append(ed2k_link)

    async def download_queue(self) -> tuple[DownloadEntry, ...]:
        self._require_connected()
        if self._queue_failures:
            raise self._queue_failures.pop(0)
        return self._queue.pop(0) if self._queue else ()

    async def shared_files(self) -> tuple[SharedFileEntry, ...]:
        self._require_connected()
        if self._shared_failures:
            raise self._shared_failures.pop(0)
        return self._shared.pop(0) if self._shared else ()

    async def network_status(self) -> NetworkStatus:
        return NetworkStatus(ed2k_id=1, ed2k_high=True, kad_status=KadStatus.CONNECTED)


class FakeDownloadRepo:
    """In-memory downloads repo (the contract of SqliteDownloadRepository, without SQL).

    ``fail_set_state_for``: hashes for which ``set_state`` raises ``RepositoryError`` —
    lets us simulate a mid-cycle repo failure (cf. logic-download#2/error-boundary#2).
    ``fail_active_states``: ``active_states()`` raises — lets us simulate persistence being down
    also at re-read time (every step of the cycle absorbs). ``lost``: hashes ``expire_lost``
    should condemn (scripted, so the fake needs no clock)."""

    def __init__(
        self,
        *,
        fail_record: bool = False,
        fail_set_state_for: set[str] | None = None,
        fail_active_states: bool = False,
        fail_mark_seen: bool = False,
        lost: set[str] | None = None,
    ) -> None:
        self.states: dict[str, DownloadState] = {}
        self.sizes: dict[str, int] = {}
        self.seen: list[set[str]] = []
        self.expired_after: list[float] = []
        self._fail_record = fail_record
        self._fail_set_state_for = fail_set_state_for or set()
        self._fail_active_states = fail_active_states
        self._fail_mark_seen = fail_mark_seen
        self._lost = lost or set()
        self._target_ids: dict[str, str] = {}

    def get_target_id(self, ed2k_hash: str) -> str | None:
        return self._target_ids.get(ed2k_hash)

    def record_queued(self, ed2k_hash: str, target_id: str, size_bytes: int) -> bool:
        if self._fail_record:
            raise RepositoryError("downloads write failed")
        if ed2k_hash in self.states:
            return False
        self.states[ed2k_hash] = DownloadState.QUEUED
        self.sizes[ed2k_hash] = size_bytes
        return True

    def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
        if ed2k_hash in self._fail_set_state_for:
            raise RepositoryError(f"set_state({ed2k_hash}) failed")
        self.states[ed2k_hash] = state

    def is_downloaded(self, ed2k_hash: str) -> bool:
        return ed2k_hash in self.states

    def mark_seen(self, ed2k_hashes: Iterable[str]) -> None:
        if self._fail_mark_seen:
            raise RepositoryError("mark_seen failed")
        self.seen.append(set(ed2k_hashes))

    def expire_lost(self, max_age_seconds: float) -> tuple[str, ...]:
        self.expired_after.append(max_age_seconds)
        condemned = tuple(
            h
            for h in self._lost
            if self.states.get(h) in {DownloadState.QUEUED, DownloadState.DOWNLOADING}
        )
        for ed2k_hash in condemned:
            self.states[ed2k_hash] = DownloadState.FAILED
        return condemned

    def active_states(self) -> dict[str, DownloadState]:
        if self._fail_active_states:
            raise RepositoryError("active_states failed")
        return dict(self.states)


class FakeCatalogReads:
    """Catalog read side: download_decisions + last_observation scripted."""

    def __init__(
        self,
        *,
        candidates: tuple[DownloadCandidate, ...] = (),
        observations: dict[str, ObservedFile] | None = None,
    ) -> None:
        self._candidates = candidates
        self._observations = observations or {}

    def download_decisions(self) -> tuple[DownloadCandidate, ...]:
        return self._candidates

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None:
        return self._observations.get(ed2k_hash)


class FakeDiskSpace:
    """Scripted free space (the real adapter calls shutil.disk_usage)."""

    def __init__(self, free: int) -> None:
        self.free = free

    def free_bytes(self) -> int:
        return self.free


class FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 6, 13, tzinfo=UTC)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += timedelta(seconds=seconds)
        await asyncio.sleep(0)


def _candidate(hash_hex: str, target_id: str) -> DownloadCandidate:
    return DownloadCandidate(ed2k_hash=hash_hex, target_id=target_id)


def _deps(
    *,
    client: FakeDownloadClient,
    downloads: FakeDownloadRepo,
    catalog: FakeCatalogReads,
    free: int = 1_000_000,
    min_free: int = 0,
    lost_after: float = 86400.0,
    telemetry: RecordingTelemetry | None = None,
    targets: Sequence[TargetSegment] | None = None,
) -> DownloadDeps:
    return DownloadDeps(
        client=client,
        downloads=downloads,
        catalog=catalog,
        targets=targets if targets is not None else _TARGETS,
        disk=FakeDiskSpace(free),
        min_free_bytes=min_free,
        lost_after_seconds=lost_after,
        clock=FakeClock(),
        telemetry=telemetry or RecordingTelemetry(),
    )


@pytest.mark.asyncio
async def test_new_candidate_is_queued_and_link_added() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "062A"),),
        observations={_A: ObservedFile(filename="Keroro.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=catalog,
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.QUEUED
    assert len(client.added_links) == 1
    assert _A in client.added_links[0]


@pytest.mark.asyncio
async def test_already_downloaded_candidate_is_deduped() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING  # already known
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=catalog,
    )
    await run_download_cycle(deps)
    assert client.added_links == []  # dedup: no new link


@pytest.mark.asyncio
async def test_two_segment_candidates_same_hash_dedup_to_one_download() -> None:
    # spec §8: a whole-episode file yields BOTH (hash,062A) and (hash,062B) in
    # download_decisions; is_downloaded(hash) dedups them to ONE physical download.
    #
    # Both targets are DOWNLOAD-eligible ("lost", not "complete") so the ONLY thing that
    # can collapse the two candidates is the `is_downloaded(hash)` guard in
    # _queue_new_candidates — never `_target_status` falling back to its "complete"
    # default for an absent target (that would be an unrelated skip path, spec §6).
    targets = (
        TargetSegment(
            season=2, seasonal_number=11, absolute_number=62, segment="A", title="t", status="lost"
        ),
        TargetSegment(
            season=2, seasonal_number=12, absolute_number=62, segment="B", title="t", status="lost"
        ),
    )
    telemetry = RecordingTelemetry()
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "062A"), _candidate(_A, "062B")),
        observations={_A: ObservedFile(filename="Keroro 062.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=catalog,
        telemetry=telemetry,
        targets=targets,
    )
    await run_download_cycle(deps)
    assert list(downloads.states) == [_A]
    assert downloads.states[_A] is DownloadState.QUEUED
    assert len(client.added_links) == 1 and _A in client.added_links[0]
    # Load-bearing assertion: exactly ONE DownloadQueued event. _queue_new_candidates
    # emits one per NON-skipped candidate (run_download_cycle.py, end of the loop body);
    # both candidates are policy-eligible here, so a SECOND event would only appear if the
    # `is_downloaded` continue-guard were removed — unlike the hash-keyed dict observables
    # above (states/added_links), which stay collapsed regardless thanks to
    # FakeDownloadRepo.record_queued's own idempotency guard.
    queued_events = [e for e in telemetry.events if isinstance(e, DownloadQueued)]
    assert len(queued_events) == 1


@pytest.mark.asyncio
async def test_complete_target_candidate_is_skipped() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_B, "063A"),),  # 063A status=complete
        observations={_B: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=catalog,
    )
    await run_download_cycle(deps)
    assert client.added_links == []
    assert _B not in downloads.states


@pytest.mark.asyncio
async def test_a_candidate_that_would_break_the_disk_floor_defers() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=500)},
    )
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=catalog,
        free=1_000,
        min_free=600,  # 1000 - 500 < 600 → defers
    )
    await run_download_cycle(deps)
    assert client.added_links == []
    assert _A not in downloads.states


@pytest.mark.asyncio
async def test_outstanding_queue_bytes_are_charged_against_the_floor() -> None:
    # Free space alone would admit this candidate; the 900 bytes amuled still has to fetch
    # for a download already running is what refuses it.
    client = FakeDownloadClient(
        queue=[(DownloadEntry(ed2k_hash=_B, size_done=100, size_full=1000),)]
    )
    downloads = FakeDownloadRepo()
    downloads.states[_B] = DownloadState.DOWNLOADING
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=200)},
    )
    deps = _deps(client=client, downloads=downloads, catalog=catalog, free=1_000, min_free=0)
    await run_download_cycle(deps)
    assert client.added_links == []
    assert _A not in downloads.states


@pytest.mark.asyncio
async def test_a_nascent_queue_entry_commits_nothing() -> None:
    # size_full == 0: amuled does not know the total yet. Charging size_full - size_done would
    # be NEGATIVE and invent free space, so the entry contributes zero and the candidate fits.
    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_B, size_done=50, size_full=0),)])
    downloads = FakeDownloadRepo()
    downloads.states[_B] = DownloadState.DOWNLOADING
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=200)},
    )
    deps = _deps(client=client, downloads=downloads, catalog=catalog, free=1_000, min_free=800)
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.QUEUED


@pytest.mark.asyncio
async def test_candidate_without_observation_is_skipped() -> None:
    # a candidate for which no observation survived (edge case) cannot build a link:
    # we skip it (log), never a crash.
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(candidates=(_candidate(_A, "062A"),), observations={})
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=catalog,
    )
    await run_download_cycle(deps)
    assert client.added_links == []


@pytest.mark.asyncio
async def test_monitor_marks_in_progress_when_not_complete() -> None:
    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_A, size_done=3, size_full=10),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUEUED
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=FakeCatalogReads(),
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.DOWNLOADING


@pytest.mark.asyncio
async def test_monitor_does_not_regress_a_completed_queue_entry() -> None:
    # _monitor: a COMPLETED hash present in the amuled queue MUST NOT regress to DOWNLOADING
    # (the completion notification already fired; re-firing it would be noise).
    class _NoSetStateRepo(FakeDownloadRepo):
        def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
            raise AssertionError("set_state must not be called (completed state)")

    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_A, size_done=3, size_full=10),)])
    repo = _NoSetStateRepo()
    repo.states[_A] = DownloadState.COMPLETED
    deps = _deps(client=client, downloads=repo, catalog=FakeCatalogReads())
    await run_download_cycle(deps)
    assert repo.states[_A] is DownloadState.COMPLETED  # unchanged


@pytest.mark.asyncio
async def test_monitor_resurrects_a_failed_download_back_in_the_queue() -> None:
    # amuled is the authority on what it holds: a hash the TTL condemned (or an add_link
    # amuled rejected) that shows up in the queue is downloading again.
    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_A, size_done=3, size_full=10),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.FAILED
    deps = _deps(client=client, downloads=downloads, catalog=FakeCatalogReads())
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.DOWNLOADING


@pytest.mark.asyncio
async def test_monitor_ignores_unknown_queue_entries() -> None:
    # an entry in the amuled queue but unknown to downloads (started outside crawler) is ignored.
    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_B, size_done=10, size_full=10),)])
    downloads = FakeDownloadRepo()
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=FakeCatalogReads(),
    )
    await run_download_cycle(deps)
    assert _B not in downloads.states


@pytest.mark.asyncio
async def test_shared_hash_still_in_the_download_queue_is_not_a_completion() -> None:
    # Field 2026-09-02: 065B was marked COMPLETED (and promoted, forever failing) while amuled
    # reported it at 20.1 %. aMule shares PARTIAL downloads too (standard eMule: you upload what
    # you have), so presence in the shared list is not a completion proof. The discriminator is
    # the download queue: a finished file left ``m_filelist``.
    client = FakeDownloadClient(
        queue=[(DownloadEntry(ed2k_hash=_A, size_done=20, size_full=100),)],
        shared=[(SharedFileEntry(ed2k_hash=_A),)],
    )
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=FakeCatalogReads(),
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.DOWNLOADING  # NOT flipped to COMPLETED


@pytest.mark.asyncio
async def test_a_completed_queue_entry_awaiting_clear_is_still_a_completion() -> None:
    # `download_queue()` asks for `status=all`, so amuled's completed-but-not-yet-cleared
    # entries are IN the snapshot. Reading "present in the queue" as "still transferring" would
    # leave every finished download stuck in `downloading` until the operator cleared it.
    client = FakeDownloadClient(
        queue=[(DownloadEntry(ed2k_hash=_A, size_done=100, size_full=100),)],
        shared=[(SharedFileEntry(ed2k_hash=_A),)],
    )
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=FakeCatalogReads(),
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.COMPLETED


@pytest.mark.asyncio
async def test_shared_hash_absent_from_the_download_queue_is_a_completion() -> None:
    # The positive case of the same rule, with a NON-empty queue (the completing hash is not in
    # it): a finished download is shared and gone from the queue, while other downloads keep
    # running.
    client = FakeDownloadClient(
        queue=[(DownloadEntry(ed2k_hash=_B, size_done=1, size_full=10),)],
        shared=[(SharedFileEntry(ed2k_hash=_A),)],
    )
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=FakeCatalogReads(),
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.COMPLETED


@pytest.mark.asyncio
async def test_cycle_reconnects_a_dead_transport_before_any_io() -> None:
    # Field 2026-09-04: the port-sync restarted amuled, the EC stream died, and the loop then
    # logged "EC client not connected (call connect() first)" every 30 s for 7 days without ever
    # reconnecting: an iteration that aborts at step 1 never reaches the queue/candidate steps,
    # so a node silently stops downloading. ``connect()`` is idempotent (no-op when the stream is
    # live), so re-establishing it at the top of the cycle costs nothing.
    client = FakeDownloadClient(disconnected=True)
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "062A"),),
        observations={_A: ObservedFile(filename="Keroro.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=catalog,
    )
    await run_download_cycle(deps)
    assert client.connect_calls == 1
    assert downloads.states[_A] is DownloadState.QUEUED  # the cycle did its work
    assert len(client.added_links) == 1


@pytest.mark.asyncio
async def test_reconnect_failure_skips_the_iteration_without_raising() -> None:
    client = FakeDownloadClient(
        disconnected=True, connect_failures=[MuleUnreachableError("daemon still down")]
    )
    downloads = FakeDownloadRepo()
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=FakeCatalogReads(candidates=(_candidate(_A, "062A"),)),
    )
    await run_download_cycle(deps)  # does not raise
    assert client.connect_calls == 1  # the reconnect was ATTEMPTED
    assert client.added_links == []
    assert downloads.states == {}


@pytest.mark.asyncio
async def test_unreachable_client_is_tolerated_and_iteration_skipped() -> None:
    client = FakeDownloadClient(queue_failures=[MuleUnreachableError("daemon down")])
    downloads = FakeDownloadRepo()
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=FakeCatalogReads(candidates=(_candidate(_A, "062A"),)),
    )
    await run_download_cycle(deps)  # does not raise
    assert client.added_links == []  # iteration skipped (no candidates processed)


@pytest.mark.asyncio
async def test_repository_error_is_absorbed() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo(fail_record=True)  # record_queued raises RepositoryError
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=catalog,
    )
    await run_download_cycle(deps)  # does not raise (RepositoryError absorbed)


@pytest.mark.asyncio
async def test_monitor_repo_error_still_promotes_completions_in_same_cycle() -> None:
    # Regression logic-download#2: if ``_monitor`` raises ``RepositoryError`` (set_state down on
    # another hash), the old code set ``states={}`` then called ``_handle_completions``
    # → each shared hash → ``states.get(...) is None`` → ignored → NO completion recorded
    # in the whole cycle (latency +1 cycle although we already have the signal). The fix re-reads
    # ``active_states()`` BEFORE ``_handle_completions`` so the completions are seen.
    client = FakeDownloadClient(
        # _A is NOT in the queue: it completed (that is why it is shared and why the completion
        # must be recorded in this very cycle). _B is queued and fails its set_state transition.
        queue=[(DownloadEntry(ed2k_hash=_B, size_done=0, size_full=0),)],
        shared=[(SharedFileEntry(ed2k_hash=_A),)],
    )
    downloads = FakeDownloadRepo(fail_set_state_for={_B})
    # _A already DOWNLOADING (no transition by _monitor); _B QUEUED → _monitor will try
    # set_state(_B, DOWNLOADING) which raises → step 1 crashes, but _A is complete in shared.
    downloads.states[_A] = DownloadState.DOWNLOADING
    downloads.states[_B] = DownloadState.QUEUED
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=FakeCatalogReads(),
    )
    await run_download_cycle(deps)
    # _A's completion is recorded despite the failure of _monitor on _B
    assert downloads.states[_A] is DownloadState.COMPLETED


@pytest.mark.asyncio
async def test_active_states_repo_failure_is_absorbed_at_step_2() -> None:
    # ``active_states`` that raises IN A LOOP (persistent repo failure): step 1 absorbs, then the
    # re-read of step 2 (logic-download#2) absorbs in turn → the cycle ends without
    # crashing the loop (the next cycle will replay). We exercise HERE the branch
    # ``except RepositoryError`` of step 2 separately from that of step 1.
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo(fail_active_states=True)
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=FakeCatalogReads(),
    )
    await run_download_cycle(deps)  # does not raise (both RepositoryError are absorbed)


@pytest.mark.asyncio
async def test_one_hash_repo_failure_does_not_starve_other_completions() -> None:
    # Regression error-boundary#2: a ``RepositoryError`` in ``_record_completion`` of hash N
    # used to bubble up to the cycle handler, abandoning N+1, N+2 of the same shared_files. The fix
    # isolates PER HASH (try/except around _record_completion), honoring the "isolated per
    # step" intent of the comment (I2).
    client = FakeDownloadClient(
        shared=[
            (
                SharedFileEntry(ed2k_hash=_A),
                SharedFileEntry(ed2k_hash=_B),
            )
        ],
    )
    # _A and _B in DOWNLOADING; set_state(_A, ...) crashes → _record_completion of _A raises;
    # _B must nonetheless be completed (intra-cycle continuity).
    downloads = FakeDownloadRepo(fail_set_state_for={_A})
    downloads.states[_A] = DownloadState.DOWNLOADING
    downloads.states[_B] = DownloadState.DOWNLOADING
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=FakeCatalogReads(),
    )
    await run_download_cycle(deps)
    assert downloads.states[_B] is DownloadState.COMPLETED  # despite the failure on _A


@pytest.mark.asyncio
async def test_intra_cycle_floor_accounts_for_links_added_this_cycle() -> None:
    # two candidates of 600 bytes, 1000 free: the 1st passes, the 2nd defers (600 + 600 > 1000).
    # A candidate admitted this cycle is not in amuled's queue yet, so the outstanding term is
    # carried IN MEMORY over the course of the cycle.
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "062A"), _candidate(_B, "062A")),
        observations={
            _A: ObservedFile(filename="a", size_bytes=600),
            _B: ObservedFile(filename="b", size_bytes=600),
        },
    )
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=catalog,
        free=1000,
        min_free=0,
    )
    await run_download_cycle(deps)
    assert len(client.added_links) == 1  # only one fit above the floor


@pytest.mark.asyncio
async def test_candidate_for_unknown_target_is_treated_as_complete() -> None:
    # _target_status: a candidate whose target_id is ABSENT from _TARGETS → "complete"
    # (conservative) → SKIP_COMPLETE policy → no link, hash not enqueued.
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S9E999Z"),),  # ghost target, absent from _TARGETS
        observations={_A: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=catalog,
    )
    await run_download_cycle(deps)
    assert client.added_links == []
    assert _A not in downloads.states


@pytest.mark.asyncio
async def test_monitor_no_op_when_state_already_matches() -> None:
    # _monitor: in-progress entry (done=3/full=10) and repo already DOWNLOADING → target == current
    # → NO set_state (FALSE branch of `if target != current`).
    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_A, size_done=3, size_full=10),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING

    class _NoSetStateRepo(FakeDownloadRepo):
        def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
            raise AssertionError("set_state must not be called (state already up to date)")

    repo = _NoSetStateRepo()
    repo.states[_A] = DownloadState.DOWNLOADING
    deps = _deps(
        client=client,
        downloads=repo,
        catalog=FakeCatalogReads(),
    )
    await run_download_cycle(deps)
    assert repo.states[_A] is DownloadState.DOWNLOADING


@pytest.mark.asyncio
async def test_queued_download_without_observation_emits_no_link() -> None:
    # _add_links: a QUEUED download in the DB but without observation in the catalog → no link
    # (branch `if observation is None: continue`).
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUEUED
    downloads.sizes[_A] = 100
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=FakeCatalogReads(observations={}),  # no observation
    )
    await run_download_cycle(deps)
    assert client.added_links == []


@pytest.mark.asyncio
async def test_add_link_unreachable_keeps_queued_and_is_tolerated() -> None:
    # add_link raises MuleUnreachableError → tolerated at cycle level; the download stays QUEUED
    # (record_queued already happened) → caught up next round. write-before-network invariant.
    client = FakeDownloadClient(add_failures=[MuleUnreachableError("down")])
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=catalog,
    )
    await run_download_cycle(deps)  # does not raise
    assert downloads.states[_A] is DownloadState.QUEUED  # stays queued → caught up
    assert client.added_links == []


@pytest.mark.asyncio
async def test_add_link_rejected_marks_failed_and_does_not_crash() -> None:
    # add_link raises MuleSearchFailedError (the daemon replied EC_OP_FAILED — link rejected):
    # THIS hash is marked FAILED (spec §9 "failed + log"), the loop continues, does not raise.
    client = FakeDownloadClient(add_failures=[MuleSearchFailedError("rejected")])
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=catalog,
    )
    await run_download_cycle(deps)  # does not raise (application failure tolerated per hash)
    assert downloads.states[_A] is DownloadState.FAILED  # link rejected → marked failed
    assert client.added_links == []


@pytest.mark.asyncio
async def test_add_link_rejected_for_one_hash_does_not_block_the_next() -> None:
    # add_link rejected (EC_OP_FAILED) for _A, accepted for _B: _A → FAILED, _B → link emitted and
    # stays QUEUED. The break does not abort the loop (continues to the next hash).
    client = FakeDownloadClient(add_failures=[MuleSearchFailedError("rejected")])
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "062A"), _candidate(_B, "062A")),
        observations={
            _A: ObservedFile(filename="a", size_bytes=1),
            _B: ObservedFile(filename="b", size_bytes=1),
        },
    )
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=catalog,
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.FAILED  # rejected
    assert downloads.states[_B] is DownloadState.QUEUED  # accepted (link emitted)
    assert any(_B in link for link in client.added_links)
    assert all(_A not in link for link in client.added_links)


@pytest.mark.asyncio
async def test_completion_and_new_candidate_in_the_same_cycle() -> None:
    # _A completed via the SHARED files (recorded this cycle); _B is a new candidate
    # (queued + link).
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    downloads.sizes[_A] = 10
    catalog = FakeCatalogReads(
        candidates=(_candidate(_B, "062A"),),
        observations={_B: ObservedFile(filename="b.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=catalog,
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.COMPLETED  # shared and out of the queue
    assert downloads.states[_B] is DownloadState.QUEUED  # new → enqueued
    assert any(_B in link for link in client.added_links)  # + link emitted


@pytest.mark.asyncio
async def test_emits_download_queued() -> None:
    telemetry = RecordingTelemetry()
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "062A"),),
        observations={_A: ObservedFile(filename="Keroro.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=catalog,
        telemetry=telemetry,
    )
    await run_download_cycle(deps)
    assert any(type(e).__name__ == "DownloadQueued" for e in telemetry.events)


@pytest.mark.asyncio
async def test_emits_download_completed() -> None:
    telemetry = RecordingTelemetry()
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A),)])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    downloads._target_ids[_A] = "062A"
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        telemetry=telemetry,
    )
    await run_download_cycle(deps)
    assert any(isinstance(e, DownloadCompleted) and e.target_id == "062A" for e in telemetry.events)


# ---------------------------------------------------------------------------
# I2 — per-STEP error granularity (anti-starvation): a RepositoryError in one
# step (completions / new candidates) must NOT prevent the OTHER from running.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completion_repo_failure_does_not_starve_new_candidates() -> None:
    # _handle_completions raises RepositoryError (set_state fails on the shared hash _A)
    # → _queue_new_candidates AND _add_links run ANYWAY for _B:
    # a step-2 repo failure does not starve step 3 (anti-starvation, I2).
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A),)])
    downloads = FakeDownloadRepo(fail_set_state_for={_A})  # step 2 raises RepositoryError
    downloads.states[_A] = DownloadState.DOWNLOADING  # shared → completed step 2 (set_state raises)
    downloads.sizes[_A] = 10
    catalog = FakeCatalogReads(
        candidates=(_candidate(_B, "062A"),),  # new candidate step 3
        observations={_B: ObservedFile(filename="b.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=catalog,
    )
    await run_download_cycle(deps)  # does not raise
    # Observable effect of step 3: _B enqueued AND its link emitted despite the step-2 failure.
    assert downloads.states[_B] is DownloadState.QUEUED
    assert any(_B in link for link in client.added_links)
    # _A stays DOWNLOADING (the set_state failure left step 2 incomplete → retry next round).
    assert downloads.states[_A] is DownloadState.DOWNLOADING


@pytest.mark.asyncio
async def test_candidate_repo_failure_does_not_starve_completions() -> None:
    # Symmetric: _queue_new_candidates raises RepositoryError (record_queued fails) → the
    # step-2 completions were recorded ANYWAY (observable effect). The failure of
    # step 3 does not starve step 2.
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A),)])
    downloads = FakeDownloadRepo(fail_record=True)  # step 3 raises RepositoryError
    downloads.states[_A] = DownloadState.DOWNLOADING  # _A shared → completed step 2
    downloads.sizes[_A] = 10
    catalog = FakeCatalogReads(
        candidates=(_candidate(_B, "062A"),),  # new candidate → record_queued will raise
        observations={_B: ObservedFile(filename="b.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=catalog,
    )
    await run_download_cycle(deps)  # does not raise
    # Observable effect of step 2: _A completed despite the step-3 failure.
    assert downloads.states[_A] is DownloadState.COMPLETED
    # _B was NOT enqueued (record_queued raised) → no link emitted for it.
    assert _B not in downloads.states
    assert client.added_links == []


@pytest.mark.asyncio
async def test_monitor_unreachable_aborts_subsequent_steps() -> None:
    # MuleUnreachableError in _monitor (download_queue) = dead daemon → ABORT of the iteration:
    # neither the completions (step 2) nor the new candidates (step 3) must run.
    # (Doctrine "a dead daemon fails everything" — distinct from RepositoryError isolation.)
    client = FakeDownloadClient(queue_failures=[MuleUnreachableError("daemon down")])
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.COMPLETED  # a pending completion (step 2)
    downloads.sizes[_A] = 10
    catalog = FakeCatalogReads(
        candidates=(_candidate(_B, "062A"),),  # a candidate (step 3)
        observations={_B: ObservedFile(filename="b.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=catalog,
    )
    await run_download_cycle(deps)  # does not raise (tolerated) but EVERYTHING is skipped
    assert downloads.states[_A] is DownloadState.COMPLETED  # unchanged
    assert _B not in downloads.states  # step 3 NOT executed
    assert client.added_links == []


@pytest.mark.asyncio
async def test_monitor_repo_failure_is_isolated_and_does_not_starve_candidates() -> None:
    # _monitor raises RepositoryError (set_state fails during reconciliation) → step 1 is
    # ISOLATED (log + continue), it does NOT starve step 3: _B is enqueued anyway.
    class _MonitorFailRepo(FakeDownloadRepo):
        def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
            raise RepositoryError("set_state monitor failed")

    client = FakeDownloadClient(queue=[(DownloadEntry(ed2k_hash=_A, size_done=10, size_full=10),)])
    downloads = _MonitorFailRepo()
    downloads.states[_A] = DownloadState.QUEUED  # reconciled → set_state(COMPLETED) will raise
    catalog = FakeCatalogReads(
        candidates=(_candidate(_B, "062A"),),
        observations={_B: ObservedFile(filename="b.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=catalog,
    )
    await run_download_cycle(deps)  # does not raise
    # Step 3 ran despite the step-1 failure: _B enqueued + link emitted.
    assert downloads.states[_B] is DownloadState.QUEUED
    assert any(_B in link for link in client.added_links)


@pytest.mark.asyncio
async def test_add_links_repo_failure_is_tolerated_and_does_not_raise() -> None:
    # _add_links raises RepositoryError (set_state fails while marking a rejected link FAILED) →
    # tolerated (log), run_download_cycle does not raise. Contract "never raises".
    class _AddLinkSetStateFailRepo(FakeDownloadRepo):
        def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
            if state is DownloadState.FAILED:
                raise RepositoryError("set_state(FAILED) failed")
            super().set_state(ed2k_hash, state)

    # add_link rejected (EC_OP_FAILED) → _add_links tries set_state(FAILED), which raises
    # RepositoryError.
    client = FakeDownloadClient(add_failures=[MuleSearchFailedError("rejected")])
    downloads = _AddLinkSetStateFailRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=catalog,
    )
    await run_download_cycle(deps)  # does NOT raise (step-4 RepositoryError tolerated)


# ---------------------------------------------------------------------------
# Completion via the EC SHARED files (positive signal).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shared_file_for_tracked_hash_is_completed() -> None:
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A),)])
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=FakeCatalogReads(),
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.COMPLETED


@pytest.mark.asyncio
async def test_shared_file_for_untracked_hash_is_ignored() -> None:
    downloads = FakeDownloadRepo()  # _A not tracked by the crawler
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A),)])
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=FakeCatalogReads(),
    )
    await run_download_cycle(deps)
    assert _A not in downloads.states


@pytest.mark.asyncio
async def test_already_completed_shared_hash_is_not_recompleted() -> None:
    # A completed file stays in amuled's shared list forever. Without the terminal skip, every
    # cycle would re-stamp it and re-fire the community notification.
    class _NoSetStateRepo(FakeDownloadRepo):
        def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
            raise AssertionError("must not re-stamp an already completed download")

    downloads = _NoSetStateRepo()
    downloads.states[_A] = DownloadState.COMPLETED
    telemetry = RecordingTelemetry()
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A),)])
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        telemetry=telemetry,
    )
    await run_download_cycle(deps)
    assert not any(isinstance(e, DownloadCompleted) for e in telemetry.events)


@pytest.mark.asyncio
async def test_failed_shared_hash_is_resurrected_and_notified() -> None:
    # The TTL can condemn a download that had in fact completed and been moved out of
    # IncomingDir. Finding it shared afterwards is amuled saying it exists: complete + notify.
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.FAILED
    telemetry = RecordingTelemetry()
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A),)])
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        telemetry=telemetry,
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.COMPLETED
    assert any(isinstance(event, DownloadCompleted) for event in telemetry.events)


@pytest.mark.asyncio
async def test_monitor_moves_queued_to_downloading_not_completed() -> None:
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUEUED
    client = FakeDownloadClient(
        queue=[(DownloadEntry(ed2k_hash=_A, size_done=10, size_full=10),)],
        shared=[()],  # not yet shared → no completion
    )
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=FakeCatalogReads(),
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.DOWNLOADING  # NOT completed (bytes ignored)


@pytest.mark.asyncio
async def test_shared_files_unreachable_aborts_iteration_gracefully() -> None:
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    client = FakeDownloadClient(shared_failures=[MuleUnreachableError("dead stream")])
    deps = _deps(
        client=client,
        downloads=downloads,
        catalog=FakeCatalogReads(),
    )
    await run_download_cycle(deps)  # does not raise
    assert downloads.states[_A] is DownloadState.DOWNLOADING


@pytest.mark.asyncio
async def test_marks_seen_the_hashes_of_both_the_queue_and_the_shared_files() -> None:
    # Stamping must happen BEFORE the TTL is evaluated, and from BOTH sources: a file that
    # completed and left the queue is only visible in the shared list.
    client = FakeDownloadClient(
        queue=[(DownloadEntry(ed2k_hash=_A, size_done=3, size_full=10),)],
        shared=[(SharedFileEntry(ed2k_hash=_B),)],
    )
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    downloads.states[_B] = DownloadState.COMPLETED
    deps = _deps(client=client, downloads=downloads, catalog=FakeCatalogReads())
    await run_download_cycle(deps)
    assert downloads.seen == [{_A, _B}]


@pytest.mark.asyncio
async def test_a_download_amuled_no_longer_knows_becomes_failed() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo(lost={_A})
    downloads.states[_A] = DownloadState.DOWNLOADING
    deps = _deps(client=client, downloads=downloads, catalog=FakeCatalogReads(), lost_after=3600.0)
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.FAILED
    assert downloads.expired_after == [3600.0]


@pytest.mark.asyncio
async def test_presence_repo_failure_is_absorbed_and_candidates_still_run() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo(fail_mark_seen=True)
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=100)},
    )
    deps = _deps(client=client, downloads=downloads, catalog=catalog)
    await run_download_cycle(deps)  # does not raise
    assert downloads.states[_A] is DownloadState.QUEUED


@pytest.mark.asyncio
async def test_an_unmeasurable_output_directory_admits_nothing_and_does_not_raise() -> None:
    # A missing output mount must not take the crawler down: it also catalogues, which is the
    # primary mission. Refusing every candidate is the conservative degradation.
    class _BrokenDisk:
        def free_bytes(self) -> int:
            raise FileNotFoundError("/data/downloads")

    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=100)},
    )
    telemetry = RecordingTelemetry()
    deps = _deps(client=client, downloads=downloads, catalog=catalog, telemetry=telemetry)
    deps.disk = _BrokenDisk()
    await run_download_cycle(deps)
    assert client.added_links == []
    assert _A not in downloads.states
    assert not [e for e in telemetry.events if isinstance(e, FreeSpaceSampled | DiskSpaceLow)]


@pytest.mark.asyncio
async def test_each_cycle_reports_the_measured_free_space() -> None:
    telemetry = RecordingTelemetry()
    deps = _deps(
        client=FakeDownloadClient(),
        downloads=FakeDownloadRepo(),
        catalog=FakeCatalogReads(),
        free=4_242,
        telemetry=telemetry,
    )
    await run_download_cycle(deps)
    assert FreeSpaceSampled(free_bytes=4_242) in telemetry.events


@pytest.mark.asyncio
async def test_low_disk_warns_once_per_crossing_and_rearms_at_the_floor() -> None:
    telemetry = RecordingTelemetry()
    disk = FakeDiskSpace(500)
    deps = _deps(
        client=FakeDownloadClient(),
        downloads=FakeDownloadRepo(),
        catalog=FakeCatalogReads(),
        min_free=600,
        telemetry=telemetry,
    )
    deps.disk = disk
    warnings_per_cycle: list[int] = []
    for free in (500, 400, 600, 599):  # cross down, stay low, back AT the floor, cross again
        disk.free = free
        before = len(telemetry.events)
        await run_download_cycle(deps)
        warnings_per_cycle.append(
            sum(isinstance(e, DiskSpaceLow) for e in telemetry.events[before:])
        )
    assert warnings_per_cycle == [1, 0, 0, 1]
    assert DiskSpaceLow(free_bytes=500, min_free_bytes=600) in telemetry.events
