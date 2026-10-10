from datetime import UTC

import pytest

from mulewatch.adapters.clock_asyncio import AsyncioClock, SeededRng


def test_asyncio_clock_now_is_aware_utc() -> None:
    assert AsyncioClock().now().tzinfo == UTC


@pytest.mark.asyncio
async def test_asyncio_clock_sleep_zero_returns() -> None:
    await AsyncioClock().sleep(0.0)  # does not raise; no notable wait


def test_seeded_rng_jitter_is_within_span() -> None:
    rng = SeededRng(jitter_seed=42)
    for _ in range(20):
        value = rng.jitter(10.0)
        assert 0.0 <= value < 10.0


def test_seeded_rng_jitter_is_reproducible_for_a_seed() -> None:
    a = [SeededRng(jitter_seed=7).jitter(5.0) for _ in range(3)]
    b = [SeededRng(jitter_seed=7).jitter(5.0) for _ in range(3)]
    assert a == b  # same jitter_seed → same sequence of draws


def test_seeded_rng_jitter_zero_or_negative_span_is_zero() -> None:
    rng = SeededRng(jitter_seed=1)
    assert rng.jitter(0.0) == 0.0
    assert rng.jitter(-3.0) == 0.0
