from p2pwatch.domain.search.backoff import backoff_delay


def test_first_attempt_is_the_base_delay() -> None:
    assert backoff_delay(1, base=2.0, cap=60.0, factor=2.0) == 2.0


def test_delay_grows_exponentially_by_factor() -> None:
    assert backoff_delay(2, base=2.0, cap=60.0, factor=2.0) == 4.0
    assert backoff_delay(3, base=2.0, cap=60.0, factor=2.0) == 8.0
    assert backoff_delay(4, base=2.0, cap=60.0, factor=2.0) == 16.0


def test_delay_is_capped() -> None:
    assert backoff_delay(10, base=2.0, cap=30.0, factor=2.0) == 30.0


def test_base_above_cap_is_also_capped_on_first_attempt() -> None:
    # base > cap: even the first attempt is capped (pathological but safe config).
    assert backoff_delay(1, base=100.0, cap=30.0, factor=2.0) == 30.0


def test_attempt_zero_or_negative_is_treated_as_the_first() -> None:
    assert backoff_delay(0, base=2.0, cap=60.0, factor=2.0) == 2.0
    assert backoff_delay(-5, base=2.0, cap=60.0, factor=2.0) == 2.0
