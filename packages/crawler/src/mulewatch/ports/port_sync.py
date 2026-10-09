"""What port-sync reads from aMule: aMule-specific, it leaves the core with port-sync."""

from dataclasses import dataclass
from enum import StrEnum


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
