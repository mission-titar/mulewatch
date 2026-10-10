"""Observability policy: ``describe(event) → Report`` (spec Plan E §3, E-D3).

DOMAIN layer (pure). The ONLY place that decides — for each event — severity, message,
metric(s), audiences. ``describe`` is an EXHAUSTIVE match (``assert_never`` → 100% branch).
The domain knows nothing of ``logging``, Prometheus or apprise: ``Severity``/``Audience``/
``MetricName`` are DOMAIN enums, translated by the adapters (E-D3).

Prometheus GOTCHA: COUNTER names do NOT include ``_total`` here — ``prometheus_client``
adds it at exposition (including it would produce ``…_total_total``). Gauges: name
as-is.
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import Enum, StrEnum, auto
from typing import Literal, assert_never

from catalog_matching.config import TIER_RANK
from catalog_matching.ed2k_link import build_ed2k_link
from mulewatch.domain.file_key import Network
from mulewatch.domain.observability.events import (
    ChannelDegraded,
    ChannelField,
    ChannelRecovered,
    ChannelStatusSampled,
    ClientReachableAgain,
    ClientUnreachableLasting,
    CrawlerStarted,
    DecisionChange,
    DecisionsRecorded,
    DiskSpaceLow,
    DownloadCompleted,
    DownloadQueued,
    Event,
    FreeSpaceSampled,
    HighIdRecovered,
    InstanceUnreachable,
    ObservationRecorded,
    PortMismatchUnresolved,
    PortSyncTriggered,
    SearchExecuted,
    SearchFailed,
    SearchTaskDropped,
)


class Severity(Enum):
    """DOMAIN severity of a fact (translated to a ``logging`` level by the adapter)."""

    DEBUG = auto()
    INFO = auto()
    SUCCESS = auto()
    WARNING = auto()
    ERROR = auto()


class Audience(Enum):
    """Consumer of a notification (E-D7) — the VALUE is the apprise tag."""

    COMMUNITY = "community"
    OPERATIONS = "operations"


class MetricName(StrEnum):
    """Metric names. Counters WITHOUT ``_total`` (added by prometheus_client at exposition)."""

    SEARCHES = "emule_searches"
    OBSERVATIONS = "emule_observations"
    SEARCH_FAILURES = "emule_search_failures"
    SEARCH_TASKS_DROPPED = "emule_search_tasks_dropped"
    MULE_UNREACHABLE = "emule_mule_unreachable"
    DECISIONS = "emule_decisions"
    DOWNLOADS_QUEUED = "emule_downloads_queued"
    DOWNLOADS_COMPLETED = "emule_downloads_completed"
    DISK_FREE_BYTES = "emule_download_disk_free_bytes"
    CRAWLER_UP = "emule_crawler_up"
    PORT_SYNC_TRIGGERED = "emule_port_sync_triggered"
    HIGH_ID_RECOVERED = "emule_high_id_recovered"
    PORT_MISMATCH = "emule_port_mismatch"
    CHANNEL_ON_NETWORK = "p2pwatch_channel_on_network"
    CHANNEL_CONNECTABLE = "p2pwatch_channel_connectable"


MetricKind = Literal["inc", "set", "remove"]


@dataclass(frozen=True)
class MetricInstruction:
    """A metric operation: counter ``inc`` / gauge ``set`` or ``remove``.

    ``labels`` = tuple of ordered (key, value) pairs (hashable → usable in a ``Report``
    equality test). ``value`` = quantity (default 1.0 for ``inc``).
    """

    name: MetricName
    kind: MetricKind
    labels: tuple[tuple[str, str], ...] = ()
    value: float = 1.0


@dataclass(frozen=True)
class Report:
    """How to report an event: severity + message + metric(s) + notif audiences.

    ``metrics`` is a TUPLE (one event can feed several metrics: ``ChannelStatusSampled`` sets
    two gauges). Empty ``audiences`` = no notif.
    ``notification`` is the notified body when it differs from the logged ``message``; ``title``
    heads it.
    """

    severity: Severity
    message: str
    metrics: tuple[MetricInstruction, ...] = ()
    audiences: frozenset[Audience] = frozenset()
    notification: str = ""
    title: str = ""


# The tiers a rise announces, with the heading of the message.
_RISE_HEADINGS = {"download": "📥 Download", "notify": "🔎 Notify"}


def _rank(tier: str | None) -> int:
    """``TIER_RANK``, where none and ``retracted`` rank below every tier."""
    return TIER_RANK.get(tier or "", -1)


def _is_rise(change: DecisionChange) -> bool:
    return change.after in _RISE_HEADINGS and _rank(change.after) > _rank(change.before)


def _targets_section(targets: Iterable[tuple[str, str]]) -> str:
    return "**Targets**\n" + "\n".join(f"{target_id} - {title}" for target_id, title in targets)


def _code_name(filename: str) -> str:
    # A backtick would close the span and let the rest of the name read as markup.
    return "`" + filename.replace("`", "'") + "`"


_DEGRADED: dict[ChannelField, str] = {
    "on_network": "off its network",
    "connectable": "not connectable",
}
_RECOVERED: dict[ChannelField, str] = {
    "on_network": "back on its network",
    "connectable": "connectable again",
}


def _word(value: bool | None) -> str:
    return "unknown" if value is None else "yes" if value else "no"


def _channel_gauge(
    name: MetricName, event: ChannelStatusSampled, value: bool | None
) -> MetricInstruction:
    labels = (("client", event.client), ("network", event.channel))
    if value is None:  # an unknown state has no series, never a stale one
        return MetricInstruction(name, "remove", labels)
    return MetricInstruction(name, "set", labels, float(value))


def _channel_alert(
    severity: Severity, event: ChannelDegraded | ChannelRecovered, what: str
) -> Report:
    return Report(
        severity, f"{event.client} {event.channel}: {what}", (), frozenset({Audience.OPERATIONS})
    )


def _describe_decisions(event: DecisionsRecorded) -> Report:
    changes = ", ".join(f"{c.target_id} {c.before or 'none'} → {c.after}" for c in event.changes)
    report = Report(
        Severity.INFO,
        f"decisions for {event.filename} ({event.file.native_id}): {changes}",
        tuple(
            MetricInstruction(MetricName.DECISIONS, "inc", (("tier", c.after),))
            for c in event.changes
        ),
    )
    risen = [c for c in event.changes if _is_rise(c)]
    if not risen:
        return report
    top = max((c.after for c in risen), key=_rank)
    match event.file.network:
        case Network.ED2K:
            link = build_ed2k_link(event.filename, event.size_bytes, event.file.native_id)
        case _:  # pragma: no cover
            assert_never(event.file.network)
    return replace(
        report,
        audiences=frozenset({Audience.COMMUNITY}),
        notification=(
            _targets_section((c.target_id, c.title) for c in risen)
            + f"\n\n**File**\n{event.size_bytes / 2**20:.1f} MiB - {_code_name(event.filename)}"
            + f"\n\n**ed2k**\n`{link}`"
        ),
        title=_RISE_HEADINGS[top],
    )


def describe(event: Event) -> Report:
    """Map an event to its ``Report`` (EXHAUSTIVE match → 100% branch)."""
    match event:
        case SearchExecuted():
            return Report(
                Severity.DEBUG,
                f"search {event.network}: {event.n_results} result(s)",
                (MetricInstruction(MetricName.SEARCHES, "inc", (("network", event.network),)),),
            )
        case InstanceUnreachable():
            return Report(
                Severity.WARNING,
                "amuled unreachable",
                (MetricInstruction(MetricName.MULE_UNREACHABLE, "inc"),),
            )
        case SearchFailed():
            return Report(
                Severity.WARNING,
                f"search failed on {event.network}",
                (
                    MetricInstruction(
                        MetricName.SEARCH_FAILURES, "inc", (("network", event.network),)
                    ),
                ),
            )
        case SearchTaskDropped():
            return Report(
                Severity.WARNING,
                f"task '{event.keyword}'/{event.network} dropped (all instances in backoff)",
                (
                    MetricInstruction(
                        MetricName.SEARCH_TASKS_DROPPED, "inc", (("network", event.network),)
                    ),
                ),
            )
        case ObservationRecorded():
            return Report(
                Severity.DEBUG,
                f"observation recorded ({event.network})",
                (MetricInstruction(MetricName.OBSERVATIONS, "inc", (("network", event.network),)),),
            )
        case DecisionsRecorded():
            return _describe_decisions(event)
        case DownloadQueued():
            return Report(
                Severity.INFO,
                f"download queued: {event.target_id}",
                (MetricInstruction(MetricName.DOWNLOADS_QUEUED, "inc"),),
            )
        case DownloadCompleted():
            return Report(
                Severity.SUCCESS,
                f"✅ download completed: {', '.join(t for t, _ in event.targets)}",
                (MetricInstruction(MetricName.DOWNLOADS_COMPLETED, "inc"),),
                frozenset({Audience.COMMUNITY}),
                _targets_section(event.targets) + f"\n\n**File**\n{_code_name(event.filename)}",
                "✅ Downloaded",
            )
        case ChannelStatusSampled():
            return Report(
                Severity.DEBUG,
                f"status {event.client} {event.channel}: on network {_word(event.on_network)}, "
                f"connectable {_word(event.connectable)}",
                (
                    _channel_gauge(MetricName.CHANNEL_ON_NETWORK, event, event.on_network),
                    _channel_gauge(MetricName.CHANNEL_CONNECTABLE, event, event.connectable),
                ),
            )
        case ChannelDegraded():
            return _channel_alert(
                Severity.WARNING,
                event,
                f"{_DEGRADED[event.field]} for {event.seconds / 60:.0f} min",
            )
        case ChannelRecovered():
            return _channel_alert(Severity.INFO, event, _RECOVERED[event.field])
        case ClientUnreachableLasting():
            return Report(
                Severity.WARNING,
                f"{event.client} unreachable for {event.seconds / 60:.0f} min",
                (),
                frozenset({Audience.OPERATIONS}),
            )
        case ClientReachableAgain():
            return Report(
                Severity.INFO,
                f"{event.client} reachable again",
                (),
                frozenset({Audience.OPERATIONS}),
            )
        case FreeSpaceSampled():
            return Report(
                Severity.DEBUG,
                f"download disk free: {event.free_bytes} bytes",
                (
                    MetricInstruction(
                        MetricName.DISK_FREE_BYTES, "set", (), float(event.free_bytes)
                    ),
                ),
            )
        case DiskSpaceLow():
            return Report(
                Severity.WARNING,
                f"download disk low: {event.free_bytes / 2**30:.1f} GiB free, "
                f"under the {event.min_free_bytes / 2**30:.1f} GiB floor (no new download)",
                (),
                frozenset({Audience.OPERATIONS}),
            )
        case CrawlerStarted():
            return Report(
                Severity.INFO,
                f"🟢 instance online (mode {event.mode})",
                (MetricInstruction(MetricName.CRAWLER_UP, "set", (), 1.0),),
                frozenset({Audience.OPERATIONS}),
            )
        case PortSyncTriggered():
            return Report(
                Severity.INFO,
                f"port-sync: {event.old} → {event.new} (restart amuled)",
                (MetricInstruction(MetricName.PORT_SYNC_TRIGGERED, "inc"),),
            )
        case HighIdRecovered():
            return Report(
                Severity.INFO,
                f"High-ID recovered on port {event.port}",
                (MetricInstruction(MetricName.HIGH_ID_RECOVERED, "inc"),),
                frozenset({Audience.OPERATIONS}),
            )
        case PortMismatchUnresolved():
            # Fallback alert (DECISION 5): OPERATIONS, edge-triggered (notif on the 1st occurrence
            # only); the metric increments on EVERY occurrence (Prometheus wants the raw state).
            # Wording valid whether or not the port was applied: if `configured` == `live`, the
            # SetPort+restart took but the High-ID has not (yet) come back; otherwise the port
            # could not be applied (restart impossible). No misleading "X ≠ X".
            return Report(
                Severity.WARNING,
                f"High-ID not restored "
                f"(forwarded port {event.live}, amuled port {event.configured})",
                (MetricInstruction(MetricName.PORT_MISMATCH, "inc"),),
                frozenset({Audience.OPERATIONS}) if event.first_occurrence else frozenset(),
            )
        case _:  # pragma: no cover
            assert_never(event)
