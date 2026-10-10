import dataclasses

import pytest

from mulewatch.ports.scheduler_state_repository import (
    ChannelBackoff,
    SchedulerStateRepository,
)


class _StubRepository:
    """Satisfies SchedulerStateRepository structurally (without importing it)."""

    def __init__(self) -> None:
        self.backoff: dict[str, ChannelBackoff] = {}

    def load_channel_backoff(self) -> dict[str, ChannelBackoff]:
        return dict(self.backoff)

    def save_channel_backoff(self, backoff: dict[str, ChannelBackoff]) -> None:
        self.backoff = dict(backoff)


def test_channel_backoff_is_frozen_and_holds_fields() -> None:
    state = ChannelBackoff(attempts=2, retry_after="2026-06-12T10:05:00.000000+00:00")
    assert state.attempts == 2
    assert state.retry_after == "2026-06-12T10:05:00.000000+00:00"
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.attempts = 3  # type: ignore[misc]


def test_protocol_is_satisfied_structurally() -> None:
    repository: SchedulerStateRepository = _StubRepository()
    assert repository.load_channel_backoff() == {}
    state = {
        "amule-1:kad": ChannelBackoff(attempts=1, retry_after="2026-06-12T10:00:00.000000+00:00")
    }
    repository.save_channel_backoff(state)
    assert isinstance(repository, _StubRepository)
    assert repository.load_channel_backoff() == state
