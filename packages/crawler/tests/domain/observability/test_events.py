"""Events are frozen dataclasses with business fields — construction/freeze test."""

import dataclasses

import pytest

from mulewatch.domain.observability.events import DownloadCompleted, ObservationRecorded


def test_observation_recorded_carries_network() -> None:
    event = ObservationRecorded(network="ed2k")
    assert event.network == "ed2k"


def test_event_is_frozen() -> None:
    event = DownloadCompleted(ed2k_hash="a" * 32, filename="x.avi", targets=(("062A", "t"),))
    # Pass the attribute via a variable to avoid ruff B010 while still
    # triggering FrozenInstanceError at runtime (frozen=True).
    attr = "filename"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(event, attr, "y.avi")
