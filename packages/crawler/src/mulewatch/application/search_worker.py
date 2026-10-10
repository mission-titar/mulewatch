"""One client's searches: a task's search, its observations recorded, and the backoff a failure
earns, per client when unreachable and per channel when refused (stage 2, D4 and D7)."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from catalog_matching.engine import MatchingEngine
from mulewatch.application.record_observations import record_observation
from mulewatch.domain.observability.events import (
    InstanceUnreachable,
    SearchExecuted,
    SearchFailed,
)
from mulewatch.domain.observation import FileObservation
from mulewatch.domain.search.backoff import backoff_delay
from mulewatch.ports.catalog_repository import CatalogRepository
from mulewatch.ports.client_errors import ClientUnreachableError, SearchFailedError
from mulewatch.ports.clock import Clock, Rng
from mulewatch.ports.decision_signal import DecisionSignal
from mulewatch.ports.scheduler_state_repository import ChannelBackoff
from mulewatch.ports.search_client import SearchClient
from mulewatch.ports.telemetry import Telemetry

_logger = logging.getLogger("mulewatch.application.search_worker")

# A ceiling, not a duration: above Kad's 45 s and an ed2k sweep, it guards a search never ending.
SEARCH_BUDGET_SECONDS = 120.0


def _iso(moment: datetime) -> str:
    """Fixed-width ISO-8601 UTC (microseconds ALWAYS written) - same rules as the adapter's
    ``utc_iso`` (which we cannot import: dependency rule §4) so that the
    PERSISTED format is identical to the other timestamps'."""
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class SearchTask:
    """A unit of work: a keyword on a channel (spec §4)."""

    keyword: str
    channel: str


@dataclass(frozen=True)
class WorkerPolicy:
    """A worker's policy parameters, as PRIMITIVES (spec §5; injected by composition).

    ``backoff_jitter_ratio``: fraction of the nominal delay drawn as additional jitter
    (anti-thundering-herd, spec §3) - e.g. 0.3 ⇒ jitter in ``[0, 0.3 * delay)``.
    """

    backoff_base_seconds: float
    backoff_cap_seconds: float
    backoff_factor: float
    backoff_jitter_ratio: float


class BackoffRegistry:
    """SHARED backoff registry keyed (by instance, or "instance:channel"), PERSISTABLE.

    Holds a map ``key → ChannelBackoff(attempts, retry_after)`` (spec §3/§7). ``retry_after``
    is computed on failure: ``clock.now() + backoff_delay(attempts) + jitter`` (jitter drawn from
    the ``Rng`` port, deterministic in test) → fixed-width ISO-8601 UTC. ``is_in_backoff`` skips
    a key while
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
        """Copy of the current map (to persist, spec §7)."""
        return dict(self._states)

    def is_in_backoff(self, key: str) -> bool:
        """``True`` if ``key`` has a ``retry_after`` still in the FUTURE (to skip)."""
        return self.remaining(key) > 0

    def remaining(self, key: str) -> float:
        """Seconds until ``key``'s ``retry_after``, 0 when it has none or it has passed."""
        state = self._states.get(key)
        if state is None:
            return 0.0
        left = datetime.fromisoformat(state.retry_after) - self._clock.now()
        return max(0.0, left.total_seconds())

    def record_failure(self, key: str) -> float:
        """Increments ``attempts``, computes delay+jitter, sets ``retry_after``. Returns the delay.

        The delay is for the LOG; the operational decision is the ``retry_after`` (skip).
        """
        if self.is_in_backoff(key):  # a concurrent search started before it: same round
            return self.remaining(key)
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

    ``backoff`` is the SHARED registry (same instance for all workers + the search tasks,
    which persist it). Single writer on the event loop → no race (spec §3).
    """

    catalog: CatalogRepository
    engine: MatchingEngine
    signal: DecisionSignal
    backoff: "BackoffRegistry"
    telemetry: Telemetry


class SearchWorker:
    """Runs one client's search tasks; never closes the client, which composition owns."""

    def __init__(self, instance_name: str, client: SearchClient, deps: WorkerDeps) -> None:
        self._instance = instance_name
        self._client = client
        self._deps = deps
        self._connected = False

    @property
    def channels(self) -> tuple[str, ...]:
        """The channels its client declares."""
        return self._client.channels

    def seconds_until_ready(self, channel: str) -> float:
        """How long until neither the instance nor ``channel`` is backed off."""
        backoff = self._deps.backoff
        return max(
            backoff.remaining(self._instance), backoff.remaining(f"{self._instance}:{channel}")
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
            await self._deps.telemetry.emit(InstanceUnreachable(self._instance))
            return False
        self._connected = True
        _logger.info("instance %s connected", self._instance)
        return True

    async def _record(self, channel: str, results: tuple[FileObservation, ...]) -> int:
        """Per-obs pipeline; returns the number of CHANGED verdicts (logging). A
        ``RepositoryError`` per obs is ABSORBED inside ``record_observation`` (spec §7)."""
        await self._deps.telemetry.emit(SearchExecuted(self._instance, channel, len(results)))
        changed = 0
        for observation in results:
            if await record_observation(
                observation,
                catalog=self._deps.catalog,
                engine=self._deps.engine,
                signal=self._deps.signal,
                telemetry=self._deps.telemetry,
                client=self._instance,
                network=channel,
            ):
                changed += 1
        return changed

    async def run_task(self, task: SearchTask) -> None:
        """One search, run once ``seconds_until_ready`` is 0. Never raises: backs off and logs."""
        channel_key = f"{self._instance}:{task.channel}"
        if not await self._ensure_connected():
            return
        try:
            results = await self._client.search(task.keyword, task.channel, SEARCH_BUDGET_SECONDS)
        except SearchFailedError as error:
            delay = self._deps.backoff.record_failure(channel_key)
            _logger.warning(
                "instance %s channel %s failed (%s): backoff %.1fs",
                self._instance,
                task.channel,
                error,
                delay,
            )
            await self._deps.telemetry.emit(SearchFailed(self._instance, task.channel))
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
            await self._deps.telemetry.emit(InstanceUnreachable(self._instance))
            return
        changed = await self._record(task.channel, results)
        self._deps.backoff.reset(channel_key)
        self._deps.backoff.reset(self._instance)  # connect() proves nothing: it may not log in
        _logger.info(
            "instance %s: '%s'/%s → %d verdict(s) changed",
            self._instance,
            task.keyword,
            task.channel,
            changed,
        )
