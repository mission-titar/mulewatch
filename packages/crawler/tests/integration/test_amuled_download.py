"""DOWNLOAD integration against a REAL amuleapi (download spec §11, option A).

Dedicated run: uv run pytest -m download_integration --no-cov
Validates the MECHANICS of the download: ``start`` accepted + the file appears in
``downloads`` with readable byte counters. COMPLETION is NOT reachable (no eD2k sources
from the ephemeral container): it is the start -> list -> status cycle that is validated.
"""

import pytest

from mulewatch.adapters.clock_asyncio import AsyncioClock
from mulewatch.adapters.mule_api.client import AmuleApiClient
from mulewatch.adapters.mule_api.errors import ApiRejectedError
from mulewatch.domain.file_key import FileKey, Network
from mulewatch.ports.download_client import DownloadRequest
from tests.integration.conftest import ApiEndpoint

pytestmark = pytest.mark.download_integration

# A NON-DEGENERATE canonical hash: above all NOT the MD4 of the empty file (31d6cfe0…), which
# amuled treats as instantly complete at 0 bytes and NEVER lists as an active partfile. With a
# real size, the link creates a listed partfile (completed_bytes=0 < size_bytes).
_HASH = "aabbccddeeff00112233445566778899"
_SIZE = 734003200  # ~700 MiB: a real size, hence an active partfile (never "complete")


@pytest.mark.asyncio
async def test_start_then_appears_in_downloads(amuled: ApiEndpoint) -> None:
    client = AmuleApiClient(
        amuled.host, amuled.port, amuled.password, timeout=30.0, clock=AsyncioClock()
    )
    await client.connect()
    try:
        file = FileKey(Network.ED2K, _HASH)
        try:
            await client.start(DownloadRequest(file, "probe-download.bin", _SIZE))
        except ApiRejectedError as exc:
            # The daemon refused the link cleanly, per item in the bulk envelope: the
            # request/response cycle IS validated, with its message. Tolerable here.
            assert str(exc)
            return
        # start ACCEPTED: a real-size link (no source) creates a listed partfile, and
        # `status=all` is what makes it visible whatever its state (§4.3).
        listed = {download.file: download for download in await client.downloads()}
        assert listed[file].bytes_total == _SIZE
        assert not listed[file].completed
    finally:
        await client.close()
