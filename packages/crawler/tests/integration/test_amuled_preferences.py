"""Listen-port get/set against a REAL amuleapi (High-ID port-sync).

Dedicated run: uv run pytest -m api_integration --no-cov
Proves the round-trip only: writing the preference is not a rebind, and the real restart and
the real High-ID belong to the e2e suite.
"""

import pytest

from p2pwatch.adapters.clock_asyncio import AsyncioClock
from p2pwatch.adapters.mule_api.client import AmuleApiClient
from tests.integration.conftest import ApiEndpoint

pytestmark = pytest.mark.api_integration


@pytest.mark.asyncio
async def test_real_get_listen_port_reads_a_plausible_port(amuled: ApiEndpoint) -> None:
    client = AmuleApiClient(
        amuled.host, amuled.port, amuled.password, timeout=30.0, clock=AsyncioClock()
    )
    await client.connect()
    try:
        listen_port = await client.get_listen_port()
        assert 0 < listen_port < 65536  # a plausible listen port (aMule's default: 4662)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_real_set_then_get_round_trips_the_port(amuled: ApiEndpoint) -> None:
    # set_listen_port(N) writes the preference; a later get must return N. The actual re-bind
    # (socket) requires a restart: NOT tested here (covered by the layer-B e2e).
    client = AmuleApiClient(
        amuled.host, amuled.port, amuled.password, timeout=30.0, clock=AsyncioClock()
    )
    await client.connect()
    try:
        original = await client.get_listen_port()
        target = 51820 if original != 51820 else 51821
        await client.set_listen_port(target)
        assert await client.get_listen_port() == target
        # courtesy: we restore the original port (the preference is persisted by amuled).
        await client.set_listen_port(original)
    finally:
        await client.close()
