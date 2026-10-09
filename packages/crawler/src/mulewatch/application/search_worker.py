"""Search worker: owns 1 ``MuleClient``, drains the queue (spec §4).

APPLICATION layer. One worker per ``amuled`` instance (spec §3: N workers = N
sessions = real parallelism; degenerates to a sequential loop at N=1). Per item
``(keyword, channel)`` pulled from the shared queue:

  consults the backoff (SKIPS the item if the instance OR the channel is backed off until its
  ``retry_after``) → ensures the connection (per-instance reconnection if down) →
  ``start_search`` → bounded polling (config budget) → ``fetch_results`` →
  ``record_observation`` for EACH obs.

Error handling (spec §7, "the client signals, Plan C decides") - the application catches
ONLY PORT exceptions (never an adapter's, dependency rule §4):
- ``ClientUnreachableError`` (the daemon is out of reach: refused, timed out, or degraded) →
  instance DOWN: we drop the client, PER-INSTANCE reconnection BACKOFF (``retry_after``
  set); the other workers continue; the item is ABANDONED.
- ``SearchFailedError`` (application failure of a channel) → BACKOFF PER (instance, channel).
- ``RepositoryError`` on an obs → logged and counted by ``record_observations``.

The backoff is exponential + jitter (spec §3), REMEMBERED in a SHARED ``BackoffRegistry``
(a single instance for ALL workers + the cycle) and PERSISTED in
``scheduler_state`` at the end of the cycle (spec §3/§7: it survives a restart). "Skip until
``retry_after``" replaces the old "sleep for the delay": a backed-off channel is skipped, not
waited on - the event loop stays available. Mutations of the shared registry happen between
two ``await`` (single-threaded event loop, single writer) → no lock needed (spec §3).
The worker NEVER closes the client (ownership = composition root, §6).
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from catalog_matching.engine import MatchingEngine
from mulewatch.application.channels import network_label
from mulewatch.application.record_observations import record_observation
from mulewatch.domain.observability.events import (
    InstanceUnreachable,
    SearchExecuted,
    SearchFailed,
    SearchTaskDropped,
)
from mulewatch.domain.search.backoff import backoff_delay
from mulewatch.ports.catalog_repository import CatalogRepository
from mulewatch.ports.client_errors import ClientUnreachableError, SearchFailedError
from mulewatch.ports.clock import Clock, Rng
from mulewatch.ports.decision_signal import DecisionSignal
from mulewatch.ports.mule_client import MuleClient, SearchChannel
from mulewatch.ports.scheduler_state_repository import ChannelBackoff
from mulewatch.ports.telemetry import Telemetry

_logger = logging.getLogger("mulewatch.application.search_worker")

_PROGRESS_DONE = 100  # search_progress() at 100 % → we stop polling


def _iso(moment: datetime) -> str:
    """Fixed-width ISO-8601 UTC (microseconds ALWAYS written) - same rules as the adapter's
    ``utc_iso`` (which we cannot import: dependency rule §4) so that the
    ``now < retry_after`` comparison is lexicographic == chronological, and the
    PERSISTED format is identical to the other timestamps'."""
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class SearchTask:
    """A unit of work: a keyword on a channel (spec §4).

    ``skipped_by`` remembers the ``instance_name`` values that have already refused this task
    during the cycle (instance or channel backoff). A re-enqueued task carries this trace to
    enable termination: when ALL instances have refused, the loop drops the task with
    a telemetry trace (spec §14: visibility rather than silence). ``frozenset`` →
    idempotent union, hashable (compatible with ``dataclass(frozen=True)``)."""

    keyword: str
    channel: SearchChannel
    skipped_by: frozenset[str] = frozenset()


@dataclass(frozen=True)
class WorkerPolicy:
    """A worker's policy parameters, as PRIMITIVES (spec §5; injected by composition).

    ``backoff_jitter_ratio``: fraction of the nominal delay drawn as additional jitter
    (anti-thundering-herd, spec §3) - e.g. 0.3 ⇒ jitter in ``[0, 0.3 * delay)``.
    ``keyword_pause_min_seconds``/``keyword_pause_max_seconds``: bounds (min ≤ max) of the
    JITTERED inter-keyword PAUSE (spec §5/§7, eD2k anti-rate-limit) - a delay
    ``min + rng.jitter(max - min)`` is slept BETWEEN two items of the same worker.
    """

    backoff_base_seconds: float
    backoff_cap_seconds: float
    backoff_factor: float
    backoff_jitter_ratio: float
    poll_budget_seconds: float
    poll_interval_seconds: float
    keyword_pause_min_seconds: float
    keyword_pause_max_seconds: float


class BackoffRegistry:
    """SHARED backoff registry keyed (by instance, or "instance:channel"), PERSISTABLE.

    Holds a map ``key → ChannelBackoff(attempts, retry_after)`` (spec §3/§7). ``retry_after``
    is computed on failure: ``clock.now() + backoff_delay(attempts) + jitter`` (jitter drawn from
    the ``Rng`` port, deterministic in test) → fixed-width ISO-8601 UTC (lexicographic ==
    chronological comparison). ``is_in_backoff`` skips a key while
    ``now < retry_after``. ``snapshot``/``load_from`` bridge to ``scheduler_state``
    (the persistence survives a restart). Deterministic logic (injected clock/rng).
    """

    def __init__(self, policy: WorkerPolicy, clock: Clock, rng: Rng) -> None:
        self._policy = policy
        self._clock = clock
        self._rng = rng
        self._states: dict[str, ChannelBackoff] = {}

    def load_from(self, states: dict[str, ChannelBackoff]) -> None:
        """Reloads the registry from a persisted snapshot (recovery after crash, spec §7)."""
        self._states = dict(states)

    def snapshot(self) -> dict[str, ChannelBackoff]:
        """Copy of the current map (to persist at the end of the cycle, spec §7)."""
        return dict(self._states)

    def is_in_backoff(self, key: str) -> bool:
        """``True`` if ``key`` has a ``retry_after`` still in the FUTURE (to skip)."""
        state = self._states.get(key)
        if state is None:
            return False
        return _iso(self._clock.now()) < state.retry_after

    def record_failure(self, key: str) -> float:
        """Increments ``attempts``, computes delay+jitter, sets ``retry_after``. Returns the delay.

        The delay is for the LOG; the operational decision is the ``retry_after`` (skip).
        """
        attempts = self._states[key].attempts + 1 if key in self._states else 1
        delay = backoff_delay(
            attempts,
            base=self._policy.backoff_base_seconds,
            cap=self._policy.backoff_cap_seconds,
            factor=self._policy.backoff_factor,
        )
        delay += self._rng.jitter(self._policy.backoff_jitter_ratio * delay)
        retry_after = _iso(self._clock.now() + timedelta(seconds=delay))
        self._states[key] = ChannelBackoff(attempts=attempts, retry_after=retry_after)
        return delay

    def reset(self, key: str) -> None:
        """Clears the backoff of a key (success)."""
        self._states.pop(key, None)


@dataclass
class WorkerDeps:
    """A worker's shared dependencies (composition assembles them once).

    ``backoff`` is the SHARED registry (same instance for all workers + the cycle,
    which persists it). ``rng`` serves the inter-keyword pause jitter (the backoff has its
    own RNG access via the registry; both point to the same shared instance).
    Single writer on the event loop → no race (spec §3).
    """

    catalog: CatalogRepository
    engine: MatchingEngine
    signal: DecisionSignal
    clock: Clock
    rng: Rng
    policy: WorkerPolicy
    backoff: "BackoffRegistry"
    telemetry: Telemetry


class SearchWorker:
    """Drives ONE ``amuled`` to drain ``SearchTask`` objects (spec §3/§4)."""

    def __init__(self, instance_name: str, client: MuleClient, deps: WorkerDeps) -> None:
        self._instance = instance_name
        self._client = client
        self._deps = deps
        self._connected = False

    @property
    def instance_name(self) -> str:
        """Logical name of the driven instance (backoff key + ``skipped_by`` identifier)."""
        return self._instance

    def is_blocked_for(self, task: SearchTask) -> bool:
        """``True`` if the task's instance OR channel is backed off (skip+re-enqueue, §14)."""
        if self._deps.backoff.is_in_backoff(self._instance):
            return True
        return self._deps.backoff.is_in_backoff(f"{self._instance}:{task.channel}")

    async def report_dropped(self, task: SearchTask) -> None:
        """Traces the drop of a task refused by ALL instances (spec §14)."""
        _logger.warning(
            "task '%s'/%s dropped (all instances in backoff)",
            task.keyword,
            task.channel,
        )
        await self._deps.telemetry.emit(
            SearchTaskDropped(keyword=task.keyword, network=network_label(task.channel))
        )

    async def _ensure_connected(self) -> bool:
        """Connects the client if needed. Returns ``False`` if the instance stays down."""
        if self._connected:
            return True
        try:
            await self._client.connect()
        except ClientUnreachableError as error:
            delay = self._deps.backoff.record_failure(self._instance)
            _logger.warning(
                "instance %s unreachable (%s): reconnect backoff %.1fs",
                self._instance,
                error,
                delay,
            )
            await self._deps.telemetry.emit(InstanceUnreachable())
            return False
        self._connected = True
        self._deps.backoff.reset(self._instance)
        _logger.info("instance %s connected", self._instance)
        return True

    async def _poll_then_fetch(self, channel: SearchChannel) -> int:
        """Bounded polling (config budget) then ``fetch_results`` → per-obs pipeline.

        Returns the number of CHANGED verdicts (logging). Polling stops at 100 % or when the
        budget is exhausted; ``fetch_results`` returns the cumulative snapshot. A
        ``RepositoryError`` per obs is ABSORBED (logged + counted) INSIDE
        ``record_observation`` → the cycle continues (spec §7), a single corrupt obs does not
        bring down the whole sweep. Emits ``SearchExecuted`` (network label + number of
        results) then ``ObservationRecorded``/``DecisionsRecorded`` via ``record_observation``.
        """
        waited = 0.0
        widen = channel is SearchChannel.KAD
        while waited < self._deps.policy.poll_budget_seconds:
            progress = await self._client.search_progress()
            if progress is not None and progress >= _PROGRESS_DONE:
                break
            if widen and waited > 0:  # tick 1: Kad has queried nobody yet, a reask is wasted
                widen = await self._widen()
            await self._deps.clock.sleep(self._deps.policy.poll_interval_seconds)
            waited += self._deps.policy.poll_interval_seconds
        results = await self._client.fetch_results()
        network = network_label(channel)
        await self._deps.telemetry.emit(SearchExecuted(network=network, n_results=len(results)))
        changed = 0
        for observation in results:
            if await record_observation(
                observation,
                catalog=self._deps.catalog,
                engine=self._deps.engine,
                signal=self._deps.signal,
                telemetry=self._deps.telemetry,
                network=network,
            ):
                changed += 1
        return changed

    async def _widen(self) -> bool:
        """Re-asks Kad for more results; ``False`` once it is exhausted or failed (not retried).
        A failure is absorbed: the search itself still stands."""
        try:
            return not await self._client.widen_search()
        except (SearchFailedError, ClientUnreachableError) as error:
            _logger.info("instance %s: widening the Kad search failed (%s)", self._instance, error)
            return False

    async def run_task(self, task: SearchTask) -> None:
        """Runs ONE ``SearchTask`` (spec §4). Never raises: signals via backoff/log.

        SKIPS the item if the instance OR the channel is backed off (future ``retry_after``,
        spec §7).
        """
        channel_key = f"{self._instance}:{task.channel}"
        if self._deps.backoff.is_in_backoff(self._instance):
            _logger.info("instance %s in backoff: item '%s' skipped", self._instance, task.keyword)
            return
        if self._deps.backoff.is_in_backoff(channel_key):
            _logger.info(
                "instance %s channel %s in backoff: item '%s' skipped",
                self._instance,
                task.channel,
                task.keyword,
            )
            return
        if not await self._ensure_connected():
            return
        try:
            await self._client.start_search(task.keyword, task.channel)
            changed = await self._poll_then_fetch(task.channel)
        except SearchFailedError as error:
            delay = self._deps.backoff.record_failure(channel_key)
            _logger.warning(
                "instance %s channel %s failed (%s): backoff %.1fs",
                self._instance,
                task.channel,
                error,
                delay,
            )
            await self._deps.telemetry.emit(SearchFailed(network=network_label(task.channel)))
            return
        except ClientUnreachableError as error:
            self._connected = False
            delay = self._deps.backoff.record_failure(self._instance)
            _logger.warning(
                "instance %s unreachable (%s), instance down, backoff %.1fs",
                self._instance,
                error,
                delay,
            )
            await self._deps.telemetry.emit(InstanceUnreachable())
            return
        self._deps.backoff.reset(channel_key)
        _logger.info(
            "instance %s: '%s'/%s → %d verdict(s) changed",
            self._instance,
            task.keyword,
            task.channel,
            changed,
        )

    async def pause_between_items(self) -> None:
        """Sleeps a JITTERED inter-keyword PAUSE (spec §5/§7, eD2k anti-rate-limit).

        Delay = ``keyword_pause_min + rng.jitter(keyword_pause_max - keyword_pause_min)``
        (reuses the ``Rng.jitter`` contract: ``[0, span)``; ``span ≤ 0`` when min == max
        → zero jitter → FIXED pause = min). Spaces out one worker's searches to
        avoid ``amuled`` getting banned from an eD2k server (spec §7). Called by the
        drain BETWEEN two items, never after the last (the caller skips the emptied queue).
        """
        policy = self._deps.policy
        span = policy.keyword_pause_max_seconds - policy.keyword_pause_min_seconds
        delay = policy.keyword_pause_min_seconds + self._deps.rng.jitter(span)
        await self._deps.clock.sleep(delay)
