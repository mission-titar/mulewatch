from datetime import UTC, datetime

import pytest

from p2pwatch.ports.clock import Clock, Rng


class _StubClock:
    """Satisfies Clock structurally (without importing it)."""

    def now(self) -> datetime:
        return datetime(2026, 6, 12, tzinfo=UTC)

    async def sleep(self, seconds: float) -> None:
        return None


class _StubRng:
    def jitter(self, span: float) -> float:
        return 0.0


def test_clock_protocol_is_satisfied_structurally() -> None:
    clock: Clock = _StubClock()
    assert clock.now() == datetime(2026, 6, 12, tzinfo=UTC)


@pytest.mark.asyncio
async def test_clock_sleep_is_awaitable() -> None:
    clock: Clock = _StubClock()
    await clock.sleep(1.0)  # does not raise; returns None (contract)


def test_rng_protocol_is_satisfied_structurally() -> None:
    rng: Rng = _StubRng()
    assert rng.jitter(5.0) == 0.0
