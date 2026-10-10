"""``SchedulerStateRepository`` port: the scheduler's durable state (spec §4/§7).

PORTS layer, SYNCHRONOUS Protocol (same principle as ``LocalStateRepository``: sub-ms, no
``to_thread`` in the MVP). Persists the per-(instance, channel) BACKOFF (spec §3/§7: it
must survive a restart), as KV in the ``scheduler_state`` table of ``local.db`` (never
merged, invariant §11).

The backoff is serialized as JSON under ONE key (``channel_backoff``): a map
``{ "amule-1:kad": {attempts, retry_after}, "amule-1": {...} }`` — the key is either
``instance:channel`` (a channel failure), or ``instance`` alone (reconnection). ``retry_after``
is a fixed-width ISO-8601 UTC.
``load_channel_backoff`` returns an empty dict if never written (first startup).
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ChannelBackoff:
    """Backoff state of a key (instance, or instance:channel): counter + deadline.

    ``attempts`` = number of CONSECUTIVE failures (used for the exponential computation).
    ``retry_after`` = fixed-width ISO-8601 UTC: as long as ``now < retry_after``, the key is
    SKIPPED. Frozen and JSON-friendly (two scalar fields) → trivial serialization.
    """

    attempts: int
    retry_after: str


class SchedulerStateRepository(Protocol):
    """Sync scheduling-state contract (the backoff).

    ``save_channel_backoff`` ENTIRELY replaces the persisted map (registry snapshot).
    """

    def load_channel_backoff(self) -> dict[str, ChannelBackoff]: ...

    def save_channel_backoff(self, backoff: dict[str, ChannelBackoff]) -> None: ...
