"""Apprise notifier: routes a notification per AUDIENCE via apprise tags (E-D7).

ADAPTER layer (implements ``Notifier``). At wiring time: ``add(url, tag=audience)`` for each
target, into one of two apprise groups: prefixed or bare (``node_prefix``). ``notify`` sends the
body PREFIXED with the ``node_id`` (instance ID, distributed network) to the first group and as is
to the second, since apprise sends one body to every URL of a tag. No URL → natural no-op (apprise
with no service returns ``None``). ``apprise_factory`` injectable for testing (default: a real
``apprise.Apprise``). The timeout/error absorption live in the dispatcher (E-D13).

DECISION (audit 2026-06-23 / security-network#2): the apprise egress (Slack, Discord, SMTP,
etc. webhooks) traverses the crawler's HOST network — not the VPN. The packaging spec accepts this
tradeoff: the P2P kill-switch stays effective (eD2k blocked outside the VPN, anonymity preserved);
only the notification IP↔webhook CORRELATION remains (an operator who notifies on Slack exposes
their host's IP to Slack, not their P2P traffic). This is a DELIBERATE choice, not a flaw.

No apprise stubs → targeted ``# type: ignore`` (mypy override, Task 9)."""

from collections.abc import Callable, Sequence

import apprise

from mulewatch.domain.observability.policy import Audience, Severity

# (url, audience, node_prefix), from the config's ``observability.notifications``.
NotificationTargets = Sequence[tuple[str, Audience, bool]]

_NOTIFY_TYPES: dict[Severity, object] = {
    Severity.DEBUG: apprise.NotifyType.INFO,
    Severity.INFO: apprise.NotifyType.INFO,
    Severity.WARNING: apprise.NotifyType.WARNING,
    Severity.ERROR: apprise.NotifyType.FAILURE,
}


class AppriseNotifier:
    """``Notifier`` adapter: one apprise channel per audience (tag), prefixed or bare."""

    def __init__(
        self,
        targets: NotificationTargets,
        *,
        node_id: str,
        apprise_factory: Callable[[], object] = apprise.Apprise,
    ) -> None:
        # Typed ``object`` on purpose: the adapter does not depend on apprise's (untyped)
        # surface; ``.add``/``.async_notify`` carry a ``# type: ignore[attr-defined]``.
        self._prefixed: object = apprise_factory()
        self._bare: object = apprise_factory()
        for url, audience, node_prefix in targets:
            group = self._prefixed if node_prefix else self._bare
            group.add(url, tag=audience.value)  # type: ignore[attr-defined]
        self._node_id = node_id

    async def notify(self, audience: Audience, body: str, severity: Severity) -> None:
        for group, text in ((self._prefixed, f"[{self._node_id}] {body}"), (self._bare, body)):
            await group.async_notify(  # type: ignore[attr-defined]
                body=text, notify_type=_NOTIFY_TYPES[severity], tag=audience.value
            )
