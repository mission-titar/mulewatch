"""Apprise notifier: add(url, tag) at setup, prefixes node_id, routes by tag, maps NotifyType."""

import apprise
import pytest

from mulewatch.adapters.observability.apprise_notifier import AppriseNotifier
from mulewatch.domain.observability.policy import Audience, Severity


class _FakeApprise:
    def __init__(self) -> None:
        self.added: list[tuple[str, str]] = []
        self.sent: list[dict[str, object]] = []

    def add(self, url: str, tag: str) -> bool:
        self.added.append((url, tag))
        return True

    async def async_notify(self, **kwargs: object) -> bool:
        self.sent.append(kwargs)
        return True


def _notifier(prefixed: _FakeApprise, bare: _FakeApprise | None = None) -> AppriseNotifier:
    targets = (
        ("discord://x", Audience.COMMUNITY, True),
        ("discord://y", Audience.OPERATIONS, True),
        ("discord://z", Audience.COMMUNITY, False),
    )
    groups = iter((prefixed, bare or _FakeApprise()))
    return AppriseNotifier(targets, node_id="titar-node-1", apprise_factory=lambda: next(groups))


def test_targets_are_added_with_tags_to_the_prefixed_or_the_bare_group() -> None:
    prefixed, bare = _FakeApprise(), _FakeApprise()
    _notifier(prefixed, bare)
    assert prefixed.added == [("discord://x", "community"), ("discord://y", "operations")]
    assert bare.added == [("discord://z", "community")]


@pytest.mark.asyncio
async def test_notify_sends_the_prefixed_body_and_the_bare_one_to_the_tag() -> None:
    prefixed, bare = _FakeApprise(), _FakeApprise()
    await _notifier(prefixed, bare).notify(Audience.COMMUNITY, "episode found", Severity.INFO)
    assert [(c["tag"], c["body"]) for c in prefixed.sent + bare.sent] == [
        ("community", "[titar-node-1] episode found"),
        ("community", "episode found"),
    ]
    assert prefixed.sent[-1]["notify_type"] == apprise.NotifyType.INFO


@pytest.mark.asyncio
async def test_notify_keeps_the_apprise_passthrough_format() -> None:
    # A declared MARKDOWN turns Discord mentions in a hostile filename into real pings.
    prefixed, bare = _FakeApprise(), _FakeApprise()
    await _notifier(prefixed, bare).notify(Audience.COMMUNITY, "x", Severity.INFO)
    assert all("body_format" not in call for call in prefixed.sent + bare.sent)


@pytest.mark.asyncio
async def test_severity_maps_to_failure() -> None:
    fake = _FakeApprise()
    await _notifier(fake).notify(Audience.OPERATIONS, "panne", Severity.ERROR)
    assert fake.sent[-1]["notify_type"] == apprise.NotifyType.FAILURE


@pytest.mark.asyncio
async def test_default_apprise_obj_is_built_from_targets() -> None:
    # Without an injected apprise_obj, the notifier builds a real Apprise (no URL → safe no-op).
    notifier = AppriseNotifier((), node_id="n")
    await notifier.notify(Audience.COMMUNITY, "x", Severity.INFO)  # does not raise
