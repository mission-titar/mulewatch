"""``DecisionSignal`` port: the in-process nudge hub (spec orchestration §3).

Coupling by data + nudge (spec §3): the loop persists the decision (append-only = free
reliability, a missing consumer replays from the table) THEN ``signal``s the hub to wake an
in-process consumer immediately. Fallback polling stays the net; a lost nudge is harmless.
The "subject" names what a consumer ``await wait(subject)``s on: today only the download nudge.

ASYNC Protocol. ``signal`` is synchronous (called from the sync post-commit pipeline, must
never block); ``wait`` is async (the consumer sleeps on it). Implemented on the adapter side
by one ``asyncio.Event`` per subject (``adapters/decision_signal_asyncio.py``).
"""

from typing import Protocol


class DecisionSignal(Protocol):
    """In-process wake-up hub (spec §3). ``signal`` wakes every ``wait`` on the same subject."""

    def signal(self, subject: str) -> None: ...

    async def wait(self, subject: str) -> None: ...
