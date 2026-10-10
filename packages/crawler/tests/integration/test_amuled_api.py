"""Login, refused login, network status and a search, against a REAL amuleapi.

Dedicated run: uv run pytest -m api_integration --no-cov
Without eD2k access the results are empty: what is validated is the cycle, not their richness.
"""

import pytest

from mulewatch.adapters.clock_asyncio import AsyncioClock
from mulewatch.adapters.mule_api.client import AmuleApiClient
from mulewatch.adapters.mule_api.errors import ApiAuthError, ApiRejectedError
from tests.integration.conftest import ApiEndpoint

pytestmark = pytest.mark.api_integration


@pytest.mark.asyncio
async def test_real_login_succeeds(amuled: ApiEndpoint) -> None:
    client = AmuleApiClient(
        amuled.host, amuled.port, amuled.password, timeout=30.0, clock=AsyncioClock()
    )
    await client.connect()  # the admin password written by --set-admin-pass is accepted
    await client.close()


@pytest.mark.asyncio
async def test_real_login_fails_with_wrong_password(amuled: ApiEndpoint) -> None:
    client = AmuleApiClient(
        amuled.host, amuled.port, "wrong-password", timeout=30.0, clock=AsyncioClock()
    )
    with pytest.raises(ApiAuthError):
        await client.connect()


@pytest.mark.asyncio
async def test_real_status(amuled: ApiEndpoint) -> None:
    client = AmuleApiClient(
        amuled.host, amuled.port, amuled.password, timeout=30.0, clock=AsyncioClock()
    )
    await client.connect()
    try:
        status = await client.status()
        assert [channel.channel for channel in status.channels] == ["ed2k", "kad"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_real_search(amuled: ApiEndpoint) -> None:
    client = AmuleApiClient(
        amuled.host, amuled.port, amuled.password, timeout=30.0, clock=AsyncioClock()
    )
    await client.connect()
    try:
        try:
            results = await client.search("keroro", "ed2k", 30.0)
        except ApiRejectedError as exc:
            # The daemon refused the search cleanly (no eD2k server reachable from the
            # container): the request/response cycle IS validated, with its own message.
            assert str(exc)
            return
        assert isinstance(results, tuple)  # possibly empty: the call is what counts
    finally:
        await client.close()
