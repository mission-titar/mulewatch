import dataclasses

import pytest

from mulewatch.domain.observation import FileObservation
from mulewatch.ports.mule_client import (
    KadStatus,
    MuleClient,
    NetworkStatus,
)


def test_kad_status_is_the_closed_four_state_enum() -> None:
    # Ref. §6: no 0x10 -> off; 0x10 alone -> running; |0x04 -> connected; |0x08 -> firewalled.
    assert {status.value for status in KadStatus} == {"off", "running", "connected", "firewalled"}


def test_network_status_is_frozen_and_holds_fields() -> None:
    status = NetworkStatus(
        ed2k_id=33554433,
        ed2k_high=True,
        kad_status=KadStatus.CONNECTED,
        server_name="TestServer",
        server_addr="1.2.3.4:4661",
    )
    assert status.ed2k_id == 33554433
    assert status.ed2k_high is True
    assert status.kad_status is KadStatus.CONNECTED
    assert status.server_name == "TestServer"
    assert status.server_addr == "1.2.3.4:4661"
    with pytest.raises(dataclasses.FrozenInstanceError):
        status.ed2k_high = False  # type: ignore[misc]


def test_network_status_server_fields_default_to_none() -> None:
    status = NetworkStatus(ed2k_id=None, ed2k_high=False, kad_status=KadStatus.OFF)
    assert status.server_name is None
    assert status.server_addr is None


class _StubClient:
    """Minimal structural implementation: satisfies MuleClient WITHOUT importing it."""

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def start_search(self, keyword: str, channel: str) -> None:
        return None

    async def fetch_results(self) -> tuple[FileObservation, ...]:
        return ()

    async def stop_search(self) -> None:
        return None

    async def search_progress(self) -> int | None:
        return None

    async def widen_search(self) -> bool:
        return False

    async def network_status(self) -> NetworkStatus:
        return NetworkStatus(ed2k_id=None, ed2k_high=False, kad_status=KadStatus.OFF)


@pytest.mark.asyncio
async def test_stub_client_satisfies_mule_client_protocol() -> None:
    # The `MuleClient` annotation forces mypy to check STRUCTURAL compatibility.
    client: MuleClient = _StubClient()
    await client.connect()
    await client.start_search("keroro", "ed2k")
    assert await client.fetch_results() == ()
    assert await client.search_progress() is None
    assert await client.widen_search() is False
    assert (await client.network_status()).kad_status is KadStatus.OFF
    await client.stop_search()
    await client.close()
