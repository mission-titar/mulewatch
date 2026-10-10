"""Observability dispatcher: routes an ``Event`` to log + metrics + notifications (E-D3/E-D13).

ADAPTER layer. Implements ``Telemetry``. ``emit``: ``describe`` (pure) → log at the mapped level +
``MetricsSink.apply`` for each metric + ``Notifier.notify`` per audience (the report's title and
``notification`` body, else its message), each notification under
``asyncio.wait_for(timeout)`` with failure/timeout ABSORBED + logged (a broken channel NEVER
breaks the crawl, E-D13). No state (the edge-trigger lives in the application — E-D8)."""

import asyncio
import logging

from p2pwatch.domain.observability.events import Event
from p2pwatch.domain.observability.policy import Severity, describe
from p2pwatch.ports.telemetry import MetricsSink, Notifier

_logger = logging.getLogger("p2pwatch.observability")

_LEVELS: dict[Severity, int] = {
    Severity.DEBUG: logging.DEBUG,
    Severity.INFO: logging.INFO,
    Severity.SUCCESS: logging.INFO,
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
                        audience,
                        report.title,
                        report.notification or report.message,
                        report.severity,
                    ),
                    timeout=self._timeout,
                )
            except Exception as error:  # noqa: BLE001 — notifications NEVER break the crawl (E-D13)
                _logger.warning("notification %s failed (%s)", audience.value, error)
