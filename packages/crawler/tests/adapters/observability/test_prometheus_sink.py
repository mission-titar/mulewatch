"""Prometheus sink: inc/set/observe on a throwaway CollectorRegistry (get_sample_value)."""

from prometheus_client import CollectorRegistry

from mulewatch.adapters.observability.prometheus_sink import PrometheusSink
from mulewatch.domain.observability.policy import MetricInstruction, MetricName, describe
from tests.domain.observability.test_policy import CASES


def test_counter_inc_with_label() -> None:
    registry = CollectorRegistry()
    sink = PrometheusSink(registry)
    sink.apply(MetricInstruction(MetricName.OBSERVATIONS, "inc", (("network", "ed2k"),)))
    sink.apply(MetricInstruction(MetricName.OBSERVATIONS, "inc", (("network", "ed2k"),)))
    # counter exposed WITH the _total suffix added by prometheus_client
    assert registry.get_sample_value("emule_observations_total", {"network": "ed2k"}) == 2.0


def test_counter_inc_no_label() -> None:
    registry = CollectorRegistry()
    PrometheusSink(registry).apply(MetricInstruction(MetricName.DOWNLOADS_COMPLETED, "inc"))
    assert registry.get_sample_value("emule_downloads_completed_total") == 1.0


def test_gauge_set_with_label() -> None:
    registry = CollectorRegistry()
    PrometheusSink(registry).apply(
        MetricInstruction(
            MetricName.CHANNEL_CONNECTABLE, "set", (("client", "amuled"), ("network", "kad")), 1.0
        )
    )
    labels = {"client": "amuled", "network": "kad"}
    assert registry.get_sample_value("p2pwatch_channel_connectable", labels) == 1.0


def test_gauge_set_no_label() -> None:
    registry = CollectorRegistry()
    PrometheusSink(registry).apply(MetricInstruction(MetricName.CRAWLER_UP, "set", (), 1.0))
    assert registry.get_sample_value("emule_crawler_up") == 1.0


def test_a_channel_series_is_removed_while_unknown_and_only_that_one() -> None:
    registry = CollectorRegistry()
    sink = PrometheusSink(registry)
    amuled = (("client", "amuled"), ("network", "kad"))
    other = (("client", "other"), ("network", "kad"))
    for labels in (amuled, other):
        sink.apply(MetricInstruction(MetricName.CHANNEL_ON_NETWORK, "set", labels, 1.0))
        sink.apply(MetricInstruction(MetricName.CHANNEL_CONNECTABLE, "set", labels, 0.0))
    sink.apply(MetricInstruction(MetricName.CHANNEL_CONNECTABLE, "remove", amuled))
    assert registry.get_sample_value("p2pwatch_channel_connectable", dict(amuled)) is None
    assert registry.get_sample_value("p2pwatch_channel_connectable", dict(other)) == 0.0
    assert registry.get_sample_value("p2pwatch_channel_on_network", dict(amuled)) == 1.0


def test_removing_a_series_never_set_is_a_no_op() -> None:
    # A client unreachable from boot has no series yet.
    registry = CollectorRegistry()
    labels = (("client", "amuled"), ("network", "ed2k"))
    PrometheusSink(registry).apply(
        MetricInstruction(MetricName.CHANNEL_ON_NETWORK, "remove", labels)
    )
    assert registry.get_sample_value("p2pwatch_channel_on_network", dict(labels)) is None


def test_histogram_observe() -> None:
    registry = CollectorRegistry()
    PrometheusSink(registry).apply(
        MetricInstruction(MetricName.SEARCH_CYCLE_DURATION, "observe", (), 2.5)
    )
    assert registry.get_sample_value("emule_search_cycle_duration_seconds_count") == 1.0
    assert registry.get_sample_value("emule_search_cycle_duration_seconds_sum") == 2.5


def test_every_emitted_metric_is_declared_in_the_sink() -> None:
    """STRUCTURAL guardrail: every metric that ``describe`` emits for EACH variant of the
    ``Event`` union must be declared in the sink (otherwise ``apply`` raises ``KeyError``). Closes
    the policy→sink loop, which no pure test covered: a future event adding an undeclared metric
    makes this test fail."""
    registry = CollectorRegistry()
    sink = PrometheusSink(registry)
    for event, _ in CASES:
        for instruction in describe(event).metrics:
            sink.apply(instruction)  # must NEVER raise (declared metric)
