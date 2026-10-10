"""What the crawler expects from any client that searches; its errors are in ``client_errors``.

Protocol stubs stay on ONE line: a body on a second one is an uncovered branch.
"""

from typing import Protocol

from p2pwatch.domain.observation import FileObservation


class SearchClient(Protocol):
    """``channels`` are opaque names the core never tests. ``search`` returns once the client
    signals the end, or ``budget_seconds`` after the search's network start at the latest."""

    channels: tuple[str, ...]

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def search(
        self, keyword: str, channel: str, budget_seconds: float
    ) -> tuple[FileObservation, ...]: ...
