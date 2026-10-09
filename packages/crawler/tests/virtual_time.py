"""An event loop whose clock is virtual: whenever every task waits, it jumps to the next timer
instead of sleeping, so concurrent tasks share one simulated time."""

import asyncio
import selectors
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

EPOCH = datetime(2026, 10, 9, tzinfo=UTC)


class _VirtualSelector(selectors.DefaultSelector):
    """Never blocks: the wait the loop asks for advances the virtual time instead."""

    def __init__(self) -> None:
        super().__init__()
        self.time = 0.0

    def select(self, timeout: float | None = None) -> list[tuple[selectors.SelectorKey, int]]:
        events = super().select(0)
        if not events:
            assert timeout is not None, "every task waits and no timer is set: a deadlock"
            self.time += timeout
        return events


class _VirtualLoop(asyncio.SelectorEventLoop):
    def __init__(self) -> None:
        self._virtual = _VirtualSelector()
        super().__init__(self._virtual)

    def time(self) -> float:
        return self._virtual.time


class LoopClock:
    """The running loop's time as the ``Clock`` port."""

    def now(self) -> datetime:
        return EPOCH + timedelta(seconds=asyncio.get_running_loop().time())

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


def run_virtual[T](main: Coroutine[Any, Any, T]) -> T:
    loop = _VirtualLoop()
    try:
        return loop.run_until_complete(main)
    finally:
        loop.close()
