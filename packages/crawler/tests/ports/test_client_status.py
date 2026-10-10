import dataclasses

import pytest

from p2pwatch.ports.client_status import ChannelStatus, ClientStatus, StatusClient


def test_a_client_status_is_frozen_and_holds_its_channels() -> None:
    kad = ChannelStatus(channel="kad", on_network=True, connectable=None)
    status = ClientStatus(version="3.0.1", channels=(kad,))

    assert status.channels[0].connectable is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        kad.on_network = False  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        status.version = None  # type: ignore[misc]


class _StubClient:
    """Satisfies StatusClient structurally, without importing it."""

    async def connect(self) -> None:
        return None

    async def status(self) -> ClientStatus:
        return ClientStatus(version=None, channels=())


@pytest.mark.asyncio
async def test_stub_client_satisfies_the_status_protocol() -> None:
    # The annotation makes mypy check the structural match.
    client: StatusClient = _StubClient()
    await client.connect()

    assert await client.status() == ClientStatus(version=None, channels=())
