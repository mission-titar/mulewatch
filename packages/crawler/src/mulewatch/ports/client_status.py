"""What the crawler expects from any client that reports its status; reachability is
``status()`` not raising ``ClientUnreachableError``. Stubs stay on ONE line."""

from dataclasses import dataclass
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
    async def status(self) -> ClientStatus: ...
