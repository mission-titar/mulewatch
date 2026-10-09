import dataclasses
from dataclasses import FrozenInstanceError

import pytest

from mulewatch.ports.mule_download_client import (
    DownloadEntry,
    MuleDownloadClient,
    SharedFileEntry,
)
from mulewatch.ports.port_sync import KadStatus, NetworkStatus


class _StubDownloadClient:
    """Satisfies MuleDownloadClient structurally (without importing it)."""

    def __init__(self) -> None:
        self.links: list[str] = []
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def add_link(self, ed2k_link: str) -> None:
        self.links.append(ed2k_link)

    async def download_queue(self) -> tuple[DownloadEntry, ...]:
        return (DownloadEntry(ed2k_hash="a" * 32, size_done=5, size_full=10),)

    async def shared_files(self) -> tuple[SharedFileEntry, ...]:
        return ()

    async def network_status(self) -> NetworkStatus:
        return NetworkStatus(ed2k_id=1, ed2k_high=True, kad_status=KadStatus.CONNECTED)


def test_shared_file_entry_carries_the_hash() -> None:
    entry = SharedFileEntry(ed2k_hash="a" * 32)
    assert entry.ed2k_hash == "a" * 32


def test_shared_file_entry_is_frozen() -> None:
    entry = SharedFileEntry(ed2k_hash="a" * 32)
    with pytest.raises(FrozenInstanceError):
        entry.ed2k_hash = "b" * 32  # type: ignore[misc]


def test_download_entry_is_frozen() -> None:
    entry = DownloadEntry(ed2k_hash="a" * 32, size_done=5, size_full=10)
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.size_done = 6  # type: ignore[misc]


def test_is_complete_when_done_reaches_full() -> None:
    assert DownloadEntry(ed2k_hash="a" * 32, size_done=10, size_full=10).is_complete is True
    assert DownloadEntry(ed2k_hash="a" * 32, size_done=11, size_full=10).is_complete is True


def test_is_not_complete_below_full() -> None:
    assert DownloadEntry(ed2k_hash="a" * 32, size_done=9, size_full=10).is_complete is False


def test_zero_full_size_is_never_complete() -> None:
    # size_full == 0 (nascent entry) must NEVER count as complete (otherwise we would
    # promote an empty file). Explicit guard.
    assert DownloadEntry(ed2k_hash="a" * 32, size_done=0, size_full=0).is_complete is False


@pytest.mark.asyncio
async def test_protocol_is_satisfied_structurally() -> None:
    client: MuleDownloadClient = _StubDownloadClient()
    await client.connect()
    await client.add_link("ed2k://|file|x|1|" + "a" * 32 + "|/")
    queue = await client.download_queue()
    status = await client.network_status()
    await client.close()
    assert isinstance(client, _StubDownloadClient)
    assert client.links == ["ed2k://|file|x|1|" + "a" * 32 + "|/"]
    assert queue[0].ed2k_hash == "a" * 32
    assert status.kad_status is KadStatus.CONNECTED


def test_remaining_bytes_is_what_is_left_to_transfer() -> None:
    assert DownloadEntry(ed2k_hash="a" * 32, size_done=3, size_full=10).remaining_bytes == 7


def test_remaining_bytes_of_a_nascent_entry_is_zero() -> None:
    # size_full == 0 means amuled does not know the total yet. Committing a NEGATIVE amount
    # would hand the admission rule free space that does not exist.
    assert DownloadEntry(ed2k_hash="a" * 32, size_done=5, size_full=0).remaining_bytes == 0


def test_remaining_bytes_never_goes_negative_past_completion() -> None:
    assert DownloadEntry(ed2k_hash="a" * 32, size_done=11, size_full=10).remaining_bytes == 0
