"""The ports are structural Protocols: a minimal fake satisfies them."""

from mulewatch.domain.observability.events import Event
from mulewatch.domain.observability.policy import (
    Audience,
    MetricInstruction,
    MetricName,
    Severity,
)
from mulewatch.ports.telemetry import MetricsSink, Notifier, Telemetry


class _Sink:
    def apply(self, instruction: MetricInstruction) -> None:
        self.last = instruction


class _Notifier:
    async def notify(self, audience: Audience, title: str, body: str, severity: Severity) -> None:
        self.last = (audience, title, body, severity)


class _Telemetry:
    async def emit(self, event: Event) -> None:
        self.last = event


def test_fakes_satisfy_ports() -> None:
    sink: MetricsSink = _Sink()
    notifier: Notifier = _Notifier()
    telemetry: Telemetry = _Telemetry()
    sink.apply(MetricInstruction(MetricName.OBSERVATIONS, "inc"))
    assert isinstance(notifier, Notifier)
    assert isinstance(telemetry, Telemetry)
    assert isinstance(sink, MetricsSink)
