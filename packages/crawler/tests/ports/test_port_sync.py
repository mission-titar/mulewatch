import dataclasses

import pytest

from mulewatch.ports.port_sync import KadStatus, NetworkStatus


def test_kad_status_is_the_closed_four_state_enum() -> None:
    # Ref. §6: no 0x10 -> off; 0x10 alone -> running; |0x04 -> connected; |0x08 -> firewalled.
    assert {status.value for status in KadStatus} == {"off", "running", "connected", "firewalled"}


def test_network_status_is_frozen_and_holds_fields() -> None:
    status = NetworkStatus(ed2k_id=33554433, ed2k_high=True, kad_status=KadStatus.CONNECTED)
    assert status.ed2k_id == 33554433
    assert status.ed2k_high is True
    assert status.kad_status is KadStatus.CONNECTED
    with pytest.raises(dataclasses.FrozenInstanceError):
        status.ed2k_high = False  # type: ignore[misc]
