"""What the crawler expects from an eMule client; its errors are in ``client_errors``.

Protocol stubs stay on ONE line: a body on a second one is an uncovered branch.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from mulewatch.domain.observation import FileObservation


class SearchChannel(StrEnum):
    """eD2k servers or Kad. The values are the tokens ``POST /search`` takes verbatim."""

    GLOBAL = "global"
    KAD = "kad"


class KadStatus(StrEnum):
    """Kad state (closed enum), read from ``GET /status``'s ``kad`` object."""

    OFF = "off"
    RUNNING = "running"
    CONNECTED = "connected"
    FIREWALLED = "firewalled"


@dataclass(frozen=True)
class NetworkStatus:
    """``ed2k_id`` is ``None`` while not connected to a server, which is what keeps a false
    ``ed2k_high`` (LowID, id < 16777216) apart from "no id yet"."""

    ed2k_id: int | None
    ed2k_high: bool
    kad_status: KadStatus
    server_name: str | None = None
    server_addr: str | None = None


class MuleClient(Protocol):
    """UNIT actions only: no sleep, no retry, no loop. ``fetch_results`` returns the daemon's
    CUMULATIVE snapshot, ``search_progress`` is ``None`` when it reports no percentage, and
    ``widen_search`` (Kad only) is ``True`` once the search can no longer be widened."""

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def start_search(self, keyword: str, channel: SearchChannel) -> None: ...

    async def fetch_results(self) -> tuple[FileObservation, ...]: ...

    async def stop_search(self) -> None: ...

    async def search_progress(self) -> int | None: ...

    async def widen_search(self) -> bool: ...

    async def network_status(self) -> NetworkStatus: ...
