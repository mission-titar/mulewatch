"""Apprise notifier: add(url, tag) at setup, node_id in the title, routes by tag, maps
NotifyType, sends markdown with every mention neutralised."""

import apprise
import pytest
from apprise.plugins.discord import USER_ROLE_DETECTION_RE

from mulewatch.adapters.observability.apprise_notifier import AppriseNotifier
from mulewatch.domain.observability.policy import Audience, Severity


class _FakeApprise:
    def __init__(self, asset: apprise.AppriseAsset) -> None:
        self.asset = asset
        self.added: list[tuple[str, str]] = []
        self.sent: list[dict[str, object]] = []

    def add(self, url: str, tag: str) -> bool:
        self.added.append((url, tag))
        return True

    async def async_notify(self, **kwargs: object) -> bool:
        self.sent.append(kwargs)
        return True


def _notifier() -> tuple[AppriseNotifier, _FakeApprise, _FakeApprise]:
    targets = (
        ("discord://x", Audience.COMMUNITY, True),
        ("discord://y", Audience.OPERATIONS, True),
        ("discord://z", Audience.COMMUNITY, False),
    )
    groups: list[_FakeApprise] = []

    def factory(asset: apprise.AppriseAsset) -> _FakeApprise:
        groups.append(_FakeApprise(asset))
        return groups[-1]

    notifier = AppriseNotifier(targets, node_id="titar-node-1", apprise_factory=factory)
    return notifier, groups[0], groups[1]


def test_targets_are_added_with_tags_to_the_prefixed_or_the_bare_group() -> None:
    _, prefixed, bare = _notifier()
    assert prefixed.added == [("discord://x", "community"), ("discord://y", "operations")]
    assert bare.added == [("discord://z", "community")]


def test_both_groups_name_the_node_and_send_no_image() -> None:
    _, prefixed, bare = _notifier()
    for asset in (prefixed.asset, bare.asset):
        assert asset.app_id == "Mulewatch - titar-node-1"
        assert asset.app_url == "https://github.com/mission-titar/mulewatch"
        assert asset.image_url(apprise.NotifyType.INFO) is None
        assert asset.image_url(apprise.NotifyType.INFO, logo=True) is None


@pytest.mark.asyncio
async def test_the_node_prefix_goes_into_the_title_of_the_prefixed_group_only() -> None:
    notifier, prefixed, bare = _notifier()
    await notifier.notify(Audience.COMMUNITY, "📥 Download", "body", Severity.INFO)
    await notifier.notify(Audience.COMMUNITY, "", "online", Severity.INFO)
    assert [(c["tag"], c["title"], c["body"]) for c in prefixed.sent + bare.sent] == [
        ("community", "[titar-node-1] 📥 Download", "body"),
        ("community", "[titar-node-1]", "online"),
        ("community", "📥 Download", "body"),
        ("community", "", "online"),
    ]


@pytest.mark.asyncio
async def test_every_message_is_sent_as_markdown() -> None:
    notifier, prefixed, bare = _notifier()
    await notifier.notify(Audience.OPERATIONS, "", "x", Severity.WARNING)
    calls = prefixed.sent + bare.sent
    assert [c["body_format"] for c in calls] == [apprise.NotifyFormat.MARKDOWN] * 2


@pytest.mark.asyncio
async def test_no_mention_reaches_apprise() -> None:
    hostile = "@everyone @here <@123> <@&456>"
    notifier, prefixed, bare = _notifier()
    await notifier.notify(Audience.COMMUNITY, hostile, hostile, Severity.INFO)
    for call in prefixed.sent + bare.sent:
        for text in (call["title"], call["body"]):
            assert isinstance(text, str)
            assert USER_ROLE_DETECTION_RE.search(text) is None, text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("severity", "notify_type"),
    [
        (Severity.INFO, apprise.NotifyType.INFO),
        (Severity.SUCCESS, apprise.NotifyType.SUCCESS),
        (Severity.WARNING, apprise.NotifyType.WARNING),
        (Severity.ERROR, apprise.NotifyType.FAILURE),
    ],
)
async def test_severity_maps_to_a_notify_type(
    severity: Severity, notify_type: apprise.NotifyType
) -> None:
    notifier, prefixed, _ = _notifier()
    await notifier.notify(Audience.OPERATIONS, "", "x", severity)
    assert prefixed.sent[-1]["notify_type"] == notify_type


@pytest.mark.asyncio
async def test_default_apprise_obj_is_built_from_targets() -> None:
    # Without an injected factory, the notifier builds a real Apprise (no URL → safe no-op).
    notifier = AppriseNotifier((), node_id="n")
    await notifier.notify(Audience.COMMUNITY, "t", "x", Severity.INFO)  # does not raise
