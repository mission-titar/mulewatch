"""The dispatcher: log + metrics always; notification by audience, failure/timeout absorbed."""

import asyncio
import logging

import pytest

from mulewatch.adapters.observability.dispatcher import ObservabilityDispatcher
from mulewatch.domain.file_key import FileKey, Network
from mulewatch.domain.observability import events as ev
from mulewatch.domain.observability.policy import Audience, MetricInstruction, Severity
from mulewatch.ports.telemetry import MetricsSink, Notifier


class _RecordingSink:
    def __init__(self) -> None:
        self.applied: list[MetricInstruction] = []

    def apply(self, instruction: MetricInstruction) -> None:
        self.applied.append(instruction)


class _RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[Audience, str, str, Severity]] = []

    async def notify(self, audience: Audience, title: str, body: str, severity: Severity) -> None:
        self.calls.append((audience, title, body, severity))


class _RaisingNotifier:
    async def notify(self, audience: Audience, title: str, body: str, severity: Severity) -> None:
        raise RuntimeError("canal mort")


class _HangingNotifier:
    async def notify(self, audience: Audience, title: str, body: str, severity: Severity) -> None:
        await asyncio.sleep(10)  # exceeds the test's short timeout


_COMPLETED = ev.DownloadCompleted(
    FileKey(Network.ED2K, "a" * 32), "Keroro 062.avi", (("062A", "t"),)
)


def _dispatcher(
    sink: MetricsSink, notifier: Notifier, timeout: float = 5.0
) -> ObservabilityDispatcher:
    # _RecordingSink/_RecordingNotifier/… structurally satisfy MetricsSink/Notifier.
    return ObservabilityDispatcher(metrics=sink, notifier=notifier, notify_timeout_seconds=timeout)


@pytest.mark.asyncio
async def test_logs_and_applies_metrics_no_audience() -> None:
    sink, notifier = _RecordingSink(), _RecordingNotifier()
    await _dispatcher(sink, notifier).emit(ev.ObservationRecorded(client="amuled", network="ed2k"))
    assert [m.name.value for m in sink.applied] == ["p2pwatch_observations"]
    assert notifier.calls == []  # ObservationRecorded has no audience


@pytest.mark.asyncio
async def test_two_metrics_one_event() -> None:
    sink, notifier = _RecordingSink(), _RecordingNotifier()
    await _dispatcher(sink, notifier).emit(
        ev.ChannelStatusSampled(client="amuled", channel="kad", on_network=True, connectable=True)
    )
    assert [m.name.value for m in sink.applied] == [
        "p2pwatch_channel_on_network",
        "p2pwatch_channel_connectable",
    ]


@pytest.mark.asyncio
async def test_notifies_the_message_when_there_is_no_separate_body() -> None:
    sink, notifier = _RecordingSink(), _RecordingNotifier()
    await _dispatcher(sink, notifier).emit(ev.CrawlerStarted(mode="full"))
    assert notifier.calls == [
        (Audience.OPERATIONS, "", "🟢 instance online (mode full)", Severity.INFO)
    ]


@pytest.mark.asyncio
async def test_logs_the_message_but_notifies_the_body(caplog: pytest.LogCaptureFixture) -> None:
    sink, notifier = _RecordingSink(), _RecordingNotifier()
    change = ev.DecisionChange("062A", "Les demoiselles cambrioleuses", None, "download")
    with caplog.at_level(logging.INFO, logger="mulewatch.observability"):
        await _dispatcher(sink, notifier).emit(
            ev.DecisionsRecorded(FileKey(Network.ED2K, "a" * 32), "Keroro 062.avi", 1024, (change,))
        )
    assert caplog.records[-1].getMessage().startswith("decisions for Keroro 062.avi")
    assert [(a, t, b.splitlines()[0]) for a, t, b, _ in notifier.calls] == [
        (Audience.COMMUNITY, "📥 Download", "**Targets**")
    ]


@pytest.mark.asyncio
async def test_log_level_matches_severity(caplog: pytest.LogCaptureFixture) -> None:
    sink, notifier = _RecordingSink(), _RecordingNotifier()
    with caplog.at_level(logging.DEBUG, logger="mulewatch.observability"):
        await _dispatcher(sink, notifier).emit(ev.InstanceUnreachable("amuled"))
    assert caplog.records[-1].levelno == logging.WARNING


@pytest.mark.asyncio
async def test_a_success_logs_as_info(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="mulewatch.observability"):
        await _dispatcher(_RecordingSink(), _RecordingNotifier()).emit(_COMPLETED)
    assert caplog.records[-1].levelno == logging.INFO


@pytest.mark.asyncio
async def test_notification_failure_is_absorbed(caplog: pytest.LogCaptureFixture) -> None:
    sink = _RecordingSink()
    with caplog.at_level(logging.WARNING, logger="mulewatch.observability"):
        await _dispatcher(sink, _RaisingNotifier()).emit(_COMPLETED)
    assert sink.applied  # the metric went through despite the notification failure
    assert any("failed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_notification_timeout_is_absorbed() -> None:
    sink = _RecordingSink()
    # short timeout + hanging notifier → wait_for raises TimeoutError, absorbed.
    await _dispatcher(sink, _HangingNotifier(), timeout=0.01).emit(_COMPLETED)
    assert sink.applied
