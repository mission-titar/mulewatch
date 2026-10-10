"""Edge-triggered state: computes ``first_occurrence`` for notification anti-spam (E-D8).

APPLICATION layer (mutable inter-iteration state, like ``BackoffRegistry``). Owned by
``CrawlerApp``, consulted by the loops (E.2): ``enter(condition)`` returns ``True`` ONLY on the
transition to active (first occurrence of a failure); ``leave(condition)`` rearms on recovery.
The metric, in contrast, increments on EVERY occurrence (Prometheus wants the raw state) — the
edge-trigger only governs notification. Single-threaded on the event loop → no lock. The state
is in-process (not persisted): after a restart, an ongoing failure re-notifies once (acceptable,
E-D8)."""


class EdgeState:
    """Set of conditions currently active (in alert)."""

    def __init__(self) -> None:
        self._active: set[str] = set()

    def enter(self, condition: str) -> bool:
        """Mark ``condition`` active. Returns ``True`` iff this is a TRANSITION (1st occurrence)."""
        if condition in self._active:
            return False
        self._active.add(condition)
        return True

    def leave(self, condition: str) -> bool:
        """Mark ``condition`` inactive. Returns ``True`` iff it was active (rearm)."""
        if condition not in self._active:
            return False
        self._active.discard(condition)
        return True
