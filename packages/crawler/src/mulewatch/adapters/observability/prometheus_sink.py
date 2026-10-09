"""Prometheus sink: applies a ``MetricInstruction`` to a DEDICATED ``CollectorRegistry`` (E-D9).

ADAPTER layer (implements ``MetricsSink``). Catalog declared on the INJECTED registry (never
the global registry) → testable on a throwaway registry, no shared state. Three HOMOGENEOUS maps
(counters/gauges/histograms) indexed by ``MetricName`` → ``apply`` routes on ``kind`` across 3
branches. GOTCHA: counters are named WITHOUT ``_total`` (added by the lib at exposition time)."""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from mulewatch.domain.observability.policy import MetricInstruction, MetricName

# (name, doc, labels) of the counters.
_COUNTERS: tuple[tuple[MetricName, str, tuple[str, ...]], ...] = (
    (MetricName.SEARCH_CYCLES, "Search cycles completed", ()),
    (MetricName.SEARCHES, "Searches performed", ("network",)),
    (MetricName.OBSERVATIONS, "Observations recorded", ("network",)),
    (MetricName.SEARCH_FAILURES, "Failed searches", ("network",)),
    (MetricName.SEARCH_TASKS_DROPPED, "Tasks dropped (all in backoff)", ("network",)),
    (MetricName.MULE_UNREACHABLE, "amuled unreachable", ()),
    (MetricName.DECISIONS, "Match decisions recorded", ("tier",)),
    (MetricName.DOWNLOADS_QUEUED, "Downloads queued", ()),
    (MetricName.DOWNLOADS_COMPLETED, "Downloads completed", ()),
    (MetricName.PORT_SYNC_TRIGGERED, "Port syncs triggered", ()),
    (MetricName.HIGH_ID_RECOVERED, "High-IDs recovered", ()),
    (MetricName.PORT_MISMATCH, "High-ID not restored (occurrences)", ()),
)
_GAUGES: tuple[tuple[MetricName, str, tuple[str, ...]], ...] = (
    (MetricName.CRAWLER_UP, "Crawler running (1)", ()),
    (MetricName.DISK_FREE_BYTES, "Free bytes on the download filesystem", ()),
    (MetricName.CHANNEL_ON_NETWORK, "Channel joined its network (1)", ("client", "network")),
    (MetricName.CHANNEL_CONNECTABLE, "Peers can connect to the channel (1)", ("client", "network")),
)
_HISTOGRAMS: tuple[tuple[MetricName, str], ...] = (
    (MetricName.SEARCH_CYCLE_DURATION, "Search cycle duration (s)"),
)


class PrometheusSink:
    """``MetricsSink`` adapter over an injected dedicated registry."""

    def __init__(self, registry: CollectorRegistry) -> None:
        self._counters = {
            name: Counter(name.value, doc, labels, registry=registry)
            for name, doc, labels in _COUNTERS
        }
        self._gauges = {
            name: Gauge(name.value, doc, labels, registry=registry) for name, doc, labels in _GAUGES
        }
        self._histograms = {
            name: Histogram(name.value, doc, registry=registry) for name, doc in _HISTOGRAMS
        }

    def apply(self, instruction: MetricInstruction) -> None:
        labels = dict(instruction.labels)
        if instruction.kind == "inc":
            counter = self._counters[instruction.name]
            (counter.labels(**labels) if labels else counter).inc(instruction.value)
        elif instruction.kind == "set":
            gauge = self._gauges[instruction.name]
            (gauge.labels(**labels) if labels else gauge).set(instruction.value)
        elif instruction.kind == "remove":
            self._gauges[instruction.name].remove_by_labels(labels)
        else:
            self._histograms[instruction.name].observe(instruction.value)
