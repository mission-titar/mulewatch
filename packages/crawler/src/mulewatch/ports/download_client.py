"""What the crawler expects from any client that downloads; its errors are in ``client_errors``.

Protocol stubs stay on ONE line: a body on a second one is an uncovered branch.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from mulewatch.domain.file_key import FileKey


class WaitingReason(StrEnum):
    """Why a download waits, shown to the operator: waiting is never failing."""

    DISK_FULL = "disk_full"
    PAUSED = "paused"
    LOCAL = "local"
    NO_SOURCE = "no_source"
    REMOTE_QUEUE = "remote_queue"


class FailureReason(StrEnum):
    """Why a download failed: ``ERROR`` is the client's, ``REJECTED`` and ``LOST`` the core's."""

    ERROR = "error"
    REJECTED = "rejected"
    LOST = "lost"


@dataclass(frozen=True)
class DownloadRequest:
    file: FileKey
    filename: str
    size_bytes: int


@dataclass(frozen=True)
class DownloadStatus:
    """``completed`` is the client's own positive signal, never a byte count."""

    file: FileKey
    bytes_done: int
    bytes_total: int
    completed: bool
    waiting_reason: WaitingReason | None
    failure_reason: FailureReason | None


class DownloadClient(Protocol):
    """``downloads`` returns every download the client knows, completed ones included."""

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def start(self, request: DownloadRequest) -> None: ...

    async def downloads(self) -> tuple[DownloadStatus, ...]: ...
