"""Apprise notifier: routes a notification per AUDIENCE via apprise tags (E-D7).

ADAPTER layer (implements ``Notifier``). At wiring time: ``add(url, tag=audience)`` for each
target, into one of two apprise groups: prefixed or bare (``node_prefix``). ``notify`` sends the
title PREFIXED with the ``node_id`` (instance ID, distributed network) to the first group and as is
to the second, since apprise sends one message to every URL of a tag. Every message is markdown
(a Discord embed) with every ``@`` neutralised, and both groups share an asset naming the node
(spec discord-embed-notifications §4). No URL → natural no-op (apprise with no service returns
``None``). ``apprise_factory`` injectable for testing (default: a real ``apprise.Apprise``). The
timeout/error absorption live in the dispatcher (E-D13).

DECISION (audit 2026-06-23 / security-network#2): the apprise egress (Slack, Discord, SMTP,
etc. webhooks) traverses the crawler's HOST network — not the VPN. The packaging spec accepts this
tradeoff: the P2P kill-switch stays effective (eD2k blocked outside the VPN, anonymity preserved);
only the notification IP↔webhook CORRELATION remains (an operator who notifies on Slack exposes
their host's IP to Slack, not their P2P traffic). This is a DELIBERATE choice, not a flaw.

No apprise stubs → targeted ``# type: ignore`` (mypy override, Task 9)."""

from collections.abc import Callable, Sequence

import apprise

from p2pwatch.domain.observability.policy import Audience, Severity

# (url, audience, node_prefix), from the config's ``observability.notifications``.
NotificationTargets = Sequence[tuple[str, Audience, bool]]

_NOTIFY_TYPES: dict[Severity, object] = {
    Severity.DEBUG: apprise.NotifyType.INFO,
    Severity.INFO: apprise.NotifyType.INFO,
    Severity.SUCCESS: apprise.NotifyType.SUCCESS,
    Severity.WARNING: apprise.NotifyType.WARNING,
    Severity.ERROR: apprise.NotifyType.FAILURE,
}


# apprise extracts "@word", "<@id>" and "<@&id>" from markdown into pings: a zero-width space breaks
# every one of them, and p2pwatch never mentions anyone.
_NO_MENTION = "@\u200b"


class AppriseNotifier:
    """``Notifier`` adapter: one apprise channel per audience (tag), prefixed or bare."""

    def __init__(
        self,
        targets: NotificationTargets,
        *,
        node_id: str,
        apprise_factory: Callable[..., object] = apprise.Apprise,
    ) -> None:
        # No image: a Discord webhook keeps its own avatar.
        asset = apprise.AppriseAsset(
            app_id=f"p2pwatch - {node_id}",
            app_url="https://github.com/mission-titar/p2pwatch",
            image_url_mask="",
            image_url_logo="",
        )
        # Typed ``object`` on purpose: the adapter does not depend on apprise's (untyped)
        # surface; ``.add``/``.async_notify`` carry a ``# type: ignore[attr-defined]``.
        self._prefixed: object = apprise_factory(asset=asset)
        self._bare: object = apprise_factory(asset=asset)
        for url, audience, node_prefix in targets:
            group = self._prefixed if node_prefix else self._bare
            group.add(url, tag=audience.value)  # type: ignore[attr-defined]
        self._node_id = node_id

    async def notify(self, audience: Audience, title: str, body: str, severity: Severity) -> None:
        prefixed = f"[{self._node_id}] {title}".rstrip()
        for group, heading in ((self._prefixed, prefixed), (self._bare, title)):
            await group.async_notify(  # type: ignore[attr-defined]
                title=heading.replace("@", _NO_MENTION),
                body=body.replace("@", _NO_MENTION),
                body_format=apprise.NotifyFormat.MARKDOWN,
                notify_type=_NOTIFY_TYPES[severity],
                tag=audience.value,
            )
