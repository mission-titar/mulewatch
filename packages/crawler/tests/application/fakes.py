"""Deterministic fakes for the application-layer tests (spec §8).

``FakeMuleClient``: results SCRIPTED per ``fetch_results`` call, injectable failures
(``ClientUnreachableError``/``SearchFailedError``) at ``connect``/``start_search``.
``FakeClock``: advanceable clock (``advance`` without I/O) + ``sleep`` that advances WITHOUT
a real wait (determinism). ``FakeRng``: identity shuffle + FIXED jitter (determinism).
``RecordingSignal``: captures nudged subjects. The repos are the REAL SQLite repos
(spec §8: "real repos on tmp_path") — no fakes here.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from mulewatch.domain.observability.events import Event
from mulewatch.domain.observation import FileObservation
from mulewatch.ports.client_errors import ClientUnreachableError, SearchFailedError
from mulewatch.ports.mule_client import KadStatus, NetworkStatus


class FakeClock:
    """Advanceable fake clock + instant sleep (advances now, deterministic)."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 6, 12, tzinfo=UTC)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        """Advance the clock WITHOUT sleeping (to make a ``retry_after`` elapse in a test)."""
        self._now += timedelta(seconds=seconds)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += timedelta(seconds=seconds)
        await asyncio.sleep(0)  # yield control without a real wait


class FakeRng:
    """DETERMINISTIC fake rng: identity shuffle + constant jitter (``jitter_value``).

    The shuffle preserves order (no seed dependency in the tests). ``jitter`` returns
    ``jitter_value`` (0.0 by default → backoff/pause = exact NOMINAL value), but honors
    the port CONTRACT like the real ``SeededRng``: ``span <= 0`` → ``0.0`` (otherwise the
    min==max pause test would lie about real behavior)."""

    def __init__(self, *, jitter_value: float = 0.0) -> None:
        self._jitter_value = jitter_value
        self.jitter_spans: list[float] = []

    def shuffled(self, items: tuple[str, ...], seed: str) -> tuple[str, ...]:
        return items

    def jitter(self, span: float) -> float:
        self.jitter_spans.append(span)
        if span <= 0:
            return 0.0
        return self._jitter_value


class RecordingTelemetry:
    """Fake telemetry: captures emitted events (the test asserts the sequence)."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)


class RecordingSignal:
    """Nudge hub that RECORDS signalled subjects (the test inspects/awaits)."""

    def __init__(self) -> None:
        self.signalled: list[str] = []
        self._events: dict[str, asyncio.Event] = {}

    def signal(self, subject: str) -> None:
        self.signalled.append(subject)
        self._events.setdefault(subject, asyncio.Event()).set()

    async def wait(self, subject: str) -> None:
        event = self._events.setdefault(subject, asyncio.Event())
        await event.wait()
        event.clear()


class FakeMuleClient:
    """Scripted EC client (satisfies MuleClient structurally, spec §8).

    ``results``: list of observation tuples, one per ``fetch_results`` call
    (exhausted → empty tuple). ``connect_failures``: exceptions to raise on the first N
    ``connect`` calls (then success). ``search_failures``: exceptions to raise on the first N
    ``start_search`` calls (then success). ``status``: the ``NetworkStatus`` returned.
    ``widen_answers``: what the next ``widen_search`` calls return or raise (then ``False``).
    """

    def __init__(
        self,
        *,
        results: list[tuple[FileObservation, ...]] | None = None,
        connect_failures: list[Exception] | None = None,
        search_failures: list[Exception] | None = None,
        status: NetworkStatus | None = None,
    ) -> None:
        self._results = list(results or [])
        self._connect_failures = list(connect_failures or [])
        self._search_failures = list(search_failures or [])
        self._status = status or NetworkStatus(
            ed2k_id=1, ed2k_high=True, kad_status=KadStatus.CONNECTED
        )
        self.connect_calls = 0
        self.close_calls = 0
        self.searches: list[tuple[str, str]] = []
        self.fetch_calls = 0
        self.widen_answers: list[bool | Exception] = []
        self.widen_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1
        if self._connect_failures:
            raise self._connect_failures.pop(0)

    async def close(self) -> None:
        self.close_calls += 1

    async def start_search(self, keyword: str, channel: str) -> None:
        self.searches.append((keyword, channel))
        if self._search_failures:
            raise self._search_failures.pop(0)

    async def fetch_results(self) -> tuple[FileObservation, ...]:
        self.fetch_calls += 1
        if not self._results:
            return ()
        return self._results.pop(0)

    async def stop_search(self) -> None:
        return None

    async def search_progress(self) -> int | None:
        return 100  # "done": polling stops immediately (determinism)

    async def widen_search(self) -> bool:
        self.widen_calls += 1
        answer = self.widen_answers.pop(0) if self.widen_answers else False
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def network_status(self) -> NetworkStatus:
        return self._status


class UnreachableStatusClient(FakeMuleClient):
    """Variant whose ``network_status`` raises ``ClientUnreachableError`` (unreachable instance).

    Models the real adapter: a non-connected client raises ``ApiUnreachableError`` (which IS a
    ``ClientUnreachableError``) on a status read. Serves to cover the tolerant branch of
    ``_aggregate_coverage`` (unreachable instance → not search-capable, no crash)."""

    async def network_status(self) -> NetworkStatus:
        raise ClientUnreachableError("client not connected (instance unreachable)")


def make_unreachable(message: str = "down") -> ClientUnreachableError:
    return ClientUnreachableError(message)


def make_search_failed(message: str = "EC_OP_FAILED") -> SearchFailedError:
    return SearchFailedError(message)
