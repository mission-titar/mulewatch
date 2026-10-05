"""Observability dispatcher: routes an ``Event`` to log + metrics + notifications (E-D3/E-D13).

ADAPTER layer. Implements ``Telemetry``. ``emit``: ``describe`` (pure) → log at the mapped level +
``MetricsSink.apply`` for each metric + ``Notifier.notify`` per audience (the report's
``notification`` body, else its message), each notification under
``asyncio.wait_for(timeout)`` with failure/timeout ABSORBED + logged (a broken channel NEVER
breaks the crawl, E-D13). No state (the edge-trigger lives in the application — E-D8)."""

import asyncio
import logging

from mulewatch.domain.observability.events import Event
from mulewatch.domain.observability.policy import Severity, describe
from mulewatch.ports.telemetry import MetricsSink, Notifier

_logger = logging.getLogger("mulewatch.observability")

_LEVELS: dict[Severity, int] = {
    Severity.DEBUG: logging.DEBUG,
    Severity.INFO: logging.INFO,
    Severity.WARNING: logging.WARNING,
    Severity.ERROR: logging.ERROR,
}


class ObservabilityDispatcher:
    """``Telemetry`` adapter: one emission point, three outputs (log/metric/notification)."""

    def __init__(
        self, *, metrics: MetricsSink, notifier: Notifier, notify_timeout_seconds: float
    ) -> None:
        self._metrics = metrics
        self._notifier = notifier
        self._timeout = notify_timeout_seconds

    async def emit(self, event: Event) -> None:
        report = describe(event)
        _logger.log(_LEVELS[report.severity], report.message)
        for instruction in report.metrics:
            self._metrics.apply(instruction)
        for audience in report.audiences:
            try:
                await asyncio.wait_for(
                    self._notifier.notify(
                        audience, report.notification or report.message, report.severity
                    ),
                    timeout=self._timeout,
                )
            except Exception as error:  # noqa: BLE001 — notifications NEVER break the crawl (E-D13)
                _logger.warning("notification %s failed (%s)", audience.value, error)
