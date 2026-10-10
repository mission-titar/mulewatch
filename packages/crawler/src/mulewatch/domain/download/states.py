"""A download's lifecycle on the crawler side: ``queued`` → ``downloading`` → ``completed``, or
``failed``; only ``queued`` and ``downloading`` count against the disk cap."""

from enum import StrEnum

# Terminal for the cap: they no longer consume active download quota.
_TERMINAL_STATES = frozenset({"completed", "failed"})


class DownloadState(StrEnum):
    """A download's lifecycle on the crawler side (closed enum, spec §7)."""

    QUEUED = "queued"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"


def is_terminal(state: DownloadState) -> bool:
    """``True`` if the state no longer consumes active download quota (spec §7)."""
    return state.value in _TERMINAL_STATES
