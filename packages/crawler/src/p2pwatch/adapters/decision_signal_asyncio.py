"""Real nudge-hub adapter: one ``asyncio.Event`` per subject (orchestration spec §3/§4).

STRUCTURALLY implements the ``DecisionSignal`` port. ``signal(subject)`` wakes any
in-flight ``wait(subject)`` then re-arms the event (``set`` followed by the ``clear`` by the
woken waiter): a consumer sleeping on the subject resumes immediately, then goes back to
sleep on the next nudge. A ``signal`` with NO waiter is harmless (the event stays armed
until the next ``wait``, which consumes it right away) — consistent with "a lost nudge is
harmless, the fallback polling is the safety net" (spec §3).

Single-thread/event-loop: all accesses go through the event loop (the repos are called
synchronously on that same loop, no races). No lock needed.
"""

import asyncio


class AsyncioDecisionSignal:
    """In-process nudge hub (one ``asyncio.Event`` per subject, created on demand)."""

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}

    def _event(self, subject: str) -> asyncio.Event:
        return self._events.setdefault(subject, asyncio.Event())

    def signal(self, subject: str) -> None:
        """Wakes the subject's ``wait`` calls (synchronous: called post-commit, does not block)."""
        self._event(subject).set()

    async def wait(self, subject: str) -> None:
        """Sleeps until the subject's next ``signal``, then re-arms for the following one."""
        event = self._event(subject)
        await event.wait()
        event.clear()
