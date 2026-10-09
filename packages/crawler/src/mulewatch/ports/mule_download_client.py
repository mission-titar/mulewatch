"""The DOWNLOAD half of the client, kept apart from ``MuleClient`` (ISP, DECISION D3).

One adapter class satisfies both, but production gives download its own instance and its own
session. The crawler NEVER reads the bytes: what comes back is the daemon's own metadata, and a
completion is the shared list saying so.
"""

from dataclasses import dataclass
from typing import Protocol

from mulewatch.ports.port_sync import NetworkStatus


@dataclass(frozen=True)
class DownloadEntry:
    """One queue entry. A nascent one (``size_full == 0``, the total is not known yet) must
    never read as complete, which is what ``is_complete`` guards."""

    ed2k_hash: str
    size_done: int
    size_full: int

    @property
    def is_complete(self) -> bool:
        """``True`` when amuled holds every byte, which is not yet "completed" (see the loop)."""
        return self.size_full > 0 and self.size_done >= self.size_full

    @property
    def remaining_bytes(self) -> int:
        """Bytes still to come, clamped at 0: a negative term would hand the admission rule
        free space that does not exist."""
        return max(self.size_full - self.size_done, 0)


@dataclass(frozen=True)
class SharedFileEntry:
    """One shared file, hash only: amuled auto-shares what it finishes, which is our POSITIVE
    completion signal, and we never need the on-disk name because we never touch the file."""

    ed2k_hash: str


class MuleDownloadClient(Protocol):
    """UNIT actions only: no sleep, no retry, no loop. ``network_status`` is reused from the
    other port because a HighID is what download in full mode needs."""

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def add_link(self, ed2k_link: str) -> None: ...

    async def download_queue(self) -> tuple[DownloadEntry, ...]: ...

    async def shared_files(self) -> tuple[SharedFileEntry, ...]: ...

    async def network_status(self) -> NetworkStatus: ...
