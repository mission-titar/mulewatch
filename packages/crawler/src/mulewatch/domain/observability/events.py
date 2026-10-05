"""Observability events: PURE business facts (spec Plan E §3-4).

DOMAIN layer (pure). One FROZEN dataclass per salient observable fact; tagged union
``Event``. Business fields ONLY — no notion of log/metric/notif (that is ``policy.describe``'s
job). Recurring failure facts carry ``first_occurrence`` (computed by the application via
``EdgeState``) for notification anti-spam (E-D8).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchCycleCompleted:
    cycle_index: int
    duration_seconds: float


@dataclass(frozen=True)
class SearchExecuted:
    network: str
    n_results: int


@dataclass(frozen=True)
class InstanceUnreachable:
    pass


@dataclass(frozen=True)
class SearchFailed:
    network: str


@dataclass(frozen=True)
class SearchTaskDropped:
    # All instances in backoff refused this task during the cycle (spec §14):
    # no worker can process it → we drop it and trace it for visibility.
    keyword: str
    network: str


@dataclass(frozen=True)
class AllInstancesBlind:
    first_occurrence: bool


@dataclass(frozen=True)
class ObservationRecorded:
    network: str


@dataclass(frozen=True)
class DecisionChange:
    target_id: str
    title: str
    before: str | None  # the persisted tier, None for a target never judged
    after: str  # the fresh tier, or "retracted"


@dataclass(frozen=True)
class DecisionsRecorded:
    # What one evaluation of a file changed in its judgement, all its targets together.
    ed2k_hash: str
    filename: str  # the clean name, the most sourced
    size_bytes: int
    changes: tuple[DecisionChange, ...]


@dataclass(frozen=True)
class DownloadQueued:
    target_id: str


@dataclass(frozen=True)
class DownloadCompleted:
    ed2k_hash: str
    filename: str  # the clean name, the most sourced
    targets: tuple[tuple[str, str], ...]  # (target_id, title), every download target of the hash


@dataclass(frozen=True)
class ConnectedInstancesSampled:
    network: str
    count: int


@dataclass(frozen=True)
class SearchCapabilitySampled:
    # Current-state sample of "can we search RIGHT NOW?" (at least one instance capable),
    # sampled every cycle → binary gauge. Complements the AllInstancesBlind counter (cumulative,
    # edge-triggered): this one carries the live 0/1 signal Grafana alerts on.
    capable: bool


@dataclass(frozen=True)
class FreeSpaceSampled:
    free_bytes: int  # the download cycle's statvfs on the output directory


@dataclass(frozen=True)
class DiskSpaceLow:
    # Emitted only on the crossing below the floor: the download loop owns the edge.
    free_bytes: int
    min_free_bytes: int


@dataclass(frozen=True)
class CrawlerStarted:
    mode: str


@dataclass(frozen=True)
class PortSyncTriggered:
    old: int  # listen port configured before
    new: int  # targeted forwarded port (the one we align amuled to)


@dataclass(frozen=True)
class HighIdRecovered:
    port: int  # High-ID port confirmed after restart


@dataclass(frozen=True)
class PortMismatchUnresolved:
    first_occurrence: bool  # edge-triggered (E-D8) — computed via EdgeState
    live: int  # live forwarded port (gluetun)
    configured: int  # amuled's listen port (stayed wrong)


type Event = (
    SearchCycleCompleted
    | SearchExecuted
    | InstanceUnreachable
    | SearchFailed
    | SearchTaskDropped
    | AllInstancesBlind
    | ObservationRecorded
    | DecisionsRecorded
    | DownloadQueued
    | DownloadCompleted
    | ConnectedInstancesSampled
    | SearchCapabilitySampled
    | FreeSpaceSampled
    | DiskSpaceLow
    | CrawlerStarted
    | PortSyncTriggered
    | HighIdRecovered
    | PortMismatchUnresolved
)
