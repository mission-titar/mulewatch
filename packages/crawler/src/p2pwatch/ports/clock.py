"""``Clock`` and ``Rng`` ports: time and randomness, injectable (spec orchestration §3).

TOTAL determinism (spec §3): the application never reads the system clock nor a global
``random`` directly — it goes through these ports, which tests replace with advanceable/
seeded fake implementations (zero flakiness, every run replayable).

``Clock`` carries an AWARE ``now()`` (UTC) AND an ASYNC ``sleep`` (a task sleeps until its
backoff ends): the two faces of time the orchestration needs. The ``sleep`` is on the
port so a fake can advance it WITHOUT a real wait.
"""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Time, injectable: aware ``now()`` (UTC) + async ``sleep`` (spec §3).

    Implemented on the adapter side by ``datetime.now(UTC)`` + ``asyncio.sleep``; replaced
    in tests by an advanceable fake clock (the ``sleep`` advances ``now`` without waiting).
    """

    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...


class Rng(Protocol):
    """Injectable randomness: ``jitter`` returns a float in ``[0, span)`` (backoff jitter)."""

    def jitter(self, span: float) -> float: ...
