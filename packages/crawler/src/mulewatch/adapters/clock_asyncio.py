"""Real adapters for time and randomness (orchestration spec §4).

``AsyncioClock``: ``now()`` = ``datetime.now(UTC)`` (aware), ``sleep`` = ``asyncio.sleep``
(the real event-loop sleep). ``SeededRng``: the ``Rng`` port's jitter draw. Both adapters
are replaced in tests by advanceable/scripted fakes (fully deterministic, spec §3).
"""

import asyncio
import random
from datetime import UTC, datetime


class AsyncioClock:
    """Real ``Clock`` (STRUCTURAL port satisfaction)."""

    def now(self) -> datetime:
        """Current instant, AWARE in UTC (``Clock`` contract)."""
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        """Real event-loop sleep (cancellable at the ``await`` point, spec §6)."""
        await asyncio.sleep(seconds)


class SeededRng:
    """Real ``Rng`` (STRUCTURAL port satisfaction).

    ``jitter``: REAL draw in ``[0, span)`` via a dedicated ``random.Random`` instance,
    seeded at construction (``jitter_seed``, defaults to system entropy) — the backoff
    jitter breaks the thundering-herd between nodes/channels."""

    def __init__(self, *, jitter_seed: int | str | None = None) -> None:
        self._jitter = random.Random(jitter_seed)

    def jitter(self, span: float) -> float:
        """Float in ``[0, span)`` (``[0.0]`` if ``span <= 0``)."""
        if span <= 0:
            return 0.0
        return self._jitter.uniform(0.0, span)
