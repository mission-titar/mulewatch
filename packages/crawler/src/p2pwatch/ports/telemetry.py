"""Observability ports (spec Plan E §3). ``Telemetry`` (facade emitted by the application) +
sinks ``MetricsSink``/``Notifier`` (wired into the dispatcher). Structural Protocols —
the real adapters AND the test fakes satisfy them without inheritance. Stubs on ONE line."""

from typing import Protocol, runtime_checkable

from p2pwatch.domain.observability.events import Event
from p2pwatch.domain.observability.policy import Audience, MetricInstruction, Severity


@runtime_checkable
class MetricsSink(Protocol):
    def apply(self, instruction: MetricInstruction) -> None: ...


@runtime_checkable
class Notifier(Protocol):
    async def notify(
        self, audience: Audience, title: str, body: str, severity: Severity
    ) -> None: ...


@runtime_checkable
class Telemetry(Protocol):
    async def emit(self, event: Event) -> None: ...
