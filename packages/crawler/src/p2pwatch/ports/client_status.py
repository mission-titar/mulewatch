"""What the crawler expects from any client that reports its status; reachability is
``status()`` not raising ``ClientUnreachableError``. Stubs stay on ONE line."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class ChannelStatus:
    """``connectable``: peers can connect to us; ``None`` when the client cannot tell."""

    channel: str
    on_network: bool
    connectable: bool | None


@dataclass(frozen=True)
class ClientStatus:
    version: str | None
    channels: tuple[ChannelStatus, ...]


class StatusClient(Protocol):
    async def connect(self) -> None: ...

    async def status(self) -> ClientStatus: ...


@dataclass(frozen=True)
class ClientReading:
    read_at: datetime
    status: ClientStatus | None  # None: the client's API did not answer


class StatusReadings(Protocol):
    """Each client's last status reading, as the webui reads it."""

    def readings(self) -> Mapping[str, ClientReading | None]: ...

    def is_current(self, reading: ClientReading) -> bool: ...
