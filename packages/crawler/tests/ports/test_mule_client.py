import pytest

from mulewatch.domain.observation import FileObservation
from mulewatch.ports.client_status import ClientStatus
from mulewatch.ports.mule_client import MuleClient


class _StubClient:
    """Minimal structural implementation: satisfies MuleClient WITHOUT importing it."""

    channels: tuple[str, ...] = ("ed2k", "kad")

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def search(
        self, keyword: str, channel: str, budget_seconds: float
    ) -> tuple[FileObservation, ...]:
        return ()

    async def status(self) -> ClientStatus:
        return ClientStatus(version=None, channels=())


@pytest.mark.asyncio
async def test_stub_client_satisfies_mule_client_protocol() -> None:
    # The `MuleClient` annotation forces mypy to check STRUCTURAL compatibility.
    client: MuleClient = _StubClient()
    await client.connect()
    assert client.channels == ("ed2k", "kad")
    assert await client.search("keroro", "ed2k", 120.0) == ()
    assert await client.status() == ClientStatus(version=None, channels=())
    await client.close()
