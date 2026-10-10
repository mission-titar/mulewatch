"""``describe`` is an exhaustive match: one case per event + each conditional branch
(a rise or not, first_occurrence true/false)."""

from dataclasses import replace

import pytest

from mulewatch.domain.file_key import FileKey, Network
from mulewatch.domain.observability import events as ev
from mulewatch.domain.observability.policy import (
    Audience,
    MetricInstruction,
    MetricName,
    Report,
    Severity,
    describe,
)

_COMMUNITY = frozenset({Audience.COMMUNITY})
_OPERATIONS = frozenset({Audience.OPERATIONS})
_AMULED_KAD = (("client", "amuled"), ("network", "kad"))


CASES: list[tuple[ev.Event, Report]] = [
    (
        ev.SearchExecuted(network="ed2k", n_results=7),
        Report(
            Severity.DEBUG,
            "search ed2k: 7 result(s)",
            (MetricInstruction(MetricName.SEARCHES, "inc", (("network", "ed2k"),)),),
        ),
    ),
    (
        ev.InstanceUnreachable(),
        Report(
            Severity.WARNING,
            "amuled unreachable",
            (MetricInstruction(MetricName.MULE_UNREACHABLE, "inc"),),
        ),
    ),
    (
        ev.SearchFailed(network="kad"),
        Report(
            Severity.WARNING,
            "search failed on kad",
            (MetricInstruction(MetricName.SEARCH_FAILURES, "inc", (("network", "kad"),)),),
        ),
    ),
    (
        ev.SearchTaskDropped(keyword="titar", network="kad"),
        Report(
            Severity.WARNING,
            "task 'titar'/kad dropped (all instances in backoff)",
            (MetricInstruction(MetricName.SEARCH_TASKS_DROPPED, "inc", (("network", "kad"),)),),
        ),
    ),
    (
        ev.ObservationRecorded(network="kad"),
        Report(
            Severity.DEBUG,
            "observation recorded (kad)",
            (MetricInstruction(MetricName.OBSERVATIONS, "inc", (("network", "kad"),)),),
        ),
    ),
    (
        ev.DownloadQueued(target_id="062A"),
        Report(
            Severity.INFO,
            "download queued: 062A",
            (MetricInstruction(MetricName.DOWNLOADS_QUEUED, "inc"),),
        ),
    ),
    (
        ev.ChannelStatusSampled(client="amuled", channel="kad", on_network=True, connectable=False),
        Report(
            Severity.DEBUG,
            "status amuled kad: on network yes, connectable no",
            (
                MetricInstruction(MetricName.CHANNEL_ON_NETWORK, "set", _AMULED_KAD, 1.0),
                MetricInstruction(MetricName.CHANNEL_CONNECTABLE, "set", _AMULED_KAD, 0.0),
            ),
        ),
    ),
    (
        ev.ChannelStatusSampled(client="amuled", channel="kad", on_network=None, connectable=None),
        Report(
            Severity.DEBUG,
            "status amuled kad: on network unknown, connectable unknown",
            (
                MetricInstruction(MetricName.CHANNEL_ON_NETWORK, "remove", _AMULED_KAD),
                MetricInstruction(MetricName.CHANNEL_CONNECTABLE, "remove", _AMULED_KAD),
            ),
        ),
    ),
    (
        ev.ChannelDegraded(client="amuled", channel="ed2k", field="on_network", seconds=300.0),
        Report(Severity.WARNING, "amuled ed2k: off its network for 5 min", (), _OPERATIONS),
    ),
    (
        ev.ChannelDegraded(client="amuled", channel="kad", field="connectable", seconds=300.0),
        Report(Severity.WARNING, "amuled kad: not connectable for 5 min", (), _OPERATIONS),
    ),
    (
        ev.ChannelRecovered(client="amuled", channel="ed2k", field="on_network"),
        Report(Severity.INFO, "amuled ed2k: back on its network", (), _OPERATIONS),
    ),
    (
        ev.ChannelRecovered(client="amuled", channel="kad", field="connectable"),
        Report(Severity.INFO, "amuled kad: connectable again", (), _OPERATIONS),
    ),
    (
        ev.ClientUnreachableLasting(client="amuled", seconds=120.0),
        Report(Severity.WARNING, "amuled unreachable for 2 min", (), _OPERATIONS),
    ),
    (
        ev.ClientReachableAgain(client="amuled"),
        Report(Severity.INFO, "amuled reachable again", (), _OPERATIONS),
    ),
    (
        ev.FreeSpaceSampled(free_bytes=5_000),
        Report(
            Severity.DEBUG,
            "download disk free: 5000 bytes",
            (MetricInstruction(MetricName.DISK_FREE_BYTES, "set", (), 5_000.0),),
        ),
    ),
    (
        ev.DiskSpaceLow(free_bytes=3 * 2**29, min_free_bytes=10 * 2**30),
        Report(
            Severity.WARNING,
            "download disk low: 1.5 GiB free, under the 10.0 GiB floor (no new download)",
            (),
            _OPERATIONS,
        ),
    ),
    (
        ev.CrawlerStarted(mode="full"),
        Report(
            Severity.INFO,
            "🟢 instance online (mode full)",
            (MetricInstruction(MetricName.CRAWLER_UP, "set", (), 1.0),),
            _OPERATIONS,
        ),
    ),
    (
        ev.PortSyncTriggered(old=4662, new=51820),
        Report(
            Severity.INFO,
            "port-sync: 4662 → 51820 (restart amuled)",
            (MetricInstruction(MetricName.PORT_SYNC_TRIGGERED, "inc"),),
        ),
    ),
    (
        ev.HighIdRecovered(port=51820),
        Report(
            Severity.INFO,
            "High-ID recovered on port 51820",
            (MetricInstruction(MetricName.HIGH_ID_RECOVERED, "inc"),),
            _OPERATIONS,
        ),
    ),
    (
        ev.PortMismatchUnresolved(first_occurrence=True, live=51820, configured=4662),
        Report(
            Severity.WARNING,
            "High-ID not restored (forwarded port 51820, amuled port 4662)",
            (MetricInstruction(MetricName.PORT_MISMATCH, "inc"),),
            _OPERATIONS,
        ),
    ),
    (
        ev.PortMismatchUnresolved(first_occurrence=False, live=51820, configured=4662),
        Report(
            Severity.WARNING,
            "High-ID not restored (forwarded port 51820, amuled port 4662)",
            (MetricInstruction(MetricName.PORT_MISMATCH, "inc"),),
        ),
    ),
]


def test_describe_maps_every_event() -> None:
    for event, expected in CASES:
        assert describe(event) == expected, f"wrong Report for {event!r}"


_HASH = "8f3a1c0b9e7d44a2b6c1f0e9d8a7b6c5"


def _decisions(*changes: ev.DecisionChange) -> ev.DecisionsRecorded:
    return ev.DecisionsRecorded(
        file=FileKey(Network.ED2K, _HASH),
        filename="Keroro 062.avi",
        size_bytes=367_185_920,
        changes=changes,
    )


def _change(target_id: str, before: str | None, after: str) -> ev.DecisionChange:
    title = {"062A": "Les demoiselles cambrioleuses", "062B": "Le grand combat sous-marin"}
    return ev.DecisionChange(target_id, title[target_id], before, after)


@pytest.mark.parametrize(
    ("before", "after", "notified"),
    [
        (None, "notify", True),
        (None, "download", True),
        ("retracted", "notify", True),
        ("retracted", "download", True),
        ("notify", "download", True),
        ("catalog", "notify", True),
        ("download", "download", False),
        ("notify", "notify", False),
        ("download", "notify", False),
        ("notify", "catalog", False),
        (None, "catalog", False),
        ("download", "retracted", False),
    ],
)
def test_only_a_rise_to_notify_or_download_reaches_the_community(
    before: str | None, after: str, notified: bool
) -> None:
    report = describe(_decisions(_change("062A", before, after)))
    assert report.audiences == (_COMMUNITY if notified else frozenset())
    assert report.metrics == (MetricInstruction(MetricName.DECISIONS, "inc", (("tier", after),)),)


def test_a_file_with_risen_targets_is_one_message_naming_the_file_and_targets() -> None:
    report = describe(
        _decisions(_change("062A", None, "download"), _change("062B", None, "notify"))
    )
    assert report.title == "📥 Download"
    assert report.notification == (
        "**Targets**\n"
        "062A - Les demoiselles cambrioleuses\n"
        "062B - Le grand combat sous-marin\n"
        "\n"
        "**File**\n"
        "350.2 MiB - `Keroro 062.avi`\n"
        "\n"
        "**ed2k**\n"
        f"`ed2k://|file|Keroro%20062.avi|367185920|{_HASH}|/`"
    )
    assert report.metrics == (
        MetricInstruction(MetricName.DECISIONS, "inc", (("tier", "download"),)),
        MetricInstruction(MetricName.DECISIONS, "inc", (("tier", "notify"),)),
    )


def test_the_message_lists_only_the_risen_targets_under_their_highest_tier() -> None:
    report = describe(
        _decisions(_change("062A", "catalog", "notify"), _change("062B", "notify", "retracted"))
    )
    assert report.title == "🔎 Notify"
    assert report.notification.startswith("**Targets**\n062A - Les demoiselles cambrioleuses\n\n")


def test_decisions_log_one_line_listing_every_change() -> None:
    report = describe(
        _decisions(_change("062A", None, "download"), _change("062B", "notify", "retracted"))
    )
    assert report.severity == Severity.INFO
    assert report.message == (
        f"decisions for Keroro 062.avi ({_HASH}): 062A none → download, 062B notify → retracted"
    )


def test_a_change_without_a_rise_has_no_notification_body() -> None:
    report = describe(_decisions(_change("062A", "download", "notify")))
    assert (report.title, report.notification) == ("", "")


def test_a_backtick_in_the_name_cannot_close_its_code_span() -> None:
    # Discord pings a mention outside code: a name must not escape its span.
    event = replace(_decisions(_change("062A", None, "download")), filename="a`@everyone`b.avi")
    report = describe(event)
    assert "\n350.2 MiB - `a'@everyone'b.avi`\n" in report.notification
    assert "`ed2k://|file|a%60%40everyone%60b.avi|" in report.notification


def _completed(*targets: tuple[str, str]) -> ev.DownloadCompleted:
    return ev.DownloadCompleted(_HASH, "Keroro `062`.avi", targets)


def test_a_completed_download_is_a_green_message_naming_its_targets_and_file() -> None:
    report = describe(
        _completed(
            ("062A", "Les demoiselles cambrioleuses"), ("062B", "Le grand combat sous-marin")
        )
    )
    assert report == Report(
        Severity.SUCCESS,
        "✅ download completed: 062A, 062B",
        (MetricInstruction(MetricName.DOWNLOADS_COMPLETED, "inc"),),
        _COMMUNITY,
        notification=(
            "**Targets**\n"
            "062A - Les demoiselles cambrioleuses\n"
            "062B - Le grand combat sous-marin\n"
            "\n"
            "**File**\n"
            "`Keroro '062'.avi`"
        ),
        title="✅ Downloaded",
    )
