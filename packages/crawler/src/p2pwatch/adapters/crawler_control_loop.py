"""``LoopCrawlerControl``: thread-safe crawler-control adapter (spec §10, phase P6a).

Concrete ``CrawlerControl`` (satisfied structurally). The webui handlers run on the webui
thread + loop; the crawler owns its ``asyncio.Event``s on its MAIN loop. The load-bearing
primitive is ``loop.call_soon_threadsafe``: ``asyncio.Event.set()``/``.clear()`` are NOT safe to
call directly from another thread (they may need to wake waiters scheduled on the crawler loop,
and the events are affine to that loop), so every control SCHEDULES its mutation to run ON the
crawler loop thread. The call returns immediately; the mutation happens on the next crawler loop
tick.

READ-ONLY BY CONSTRUCTION (spec §4 invariant): this adapter holds NO DB connection and imports
no persistence. Its ONLY effect is scheduling ``asyncio.Event`` mutations on the crawler loop.
That is the structural guarantee that the webui can never write to the database through a
control.
"""

import asyncio


class LoopCrawlerControl:
    """Forwards each control intent onto the crawler loop via ``call_soon_threadsafe``.

    The two events are the crawler's own (``CrawlerApp``): ``resumed`` is the pause gate (set =
    running, clear = paused), and ``shutdown`` is the graceful-shutdown signal.
    """

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        resumed: asyncio.Event,
        shutdown: asyncio.Event,
    ) -> None:
        self._loop = loop
        self._resumed = resumed
        self._shutdown = shutdown

    def pause(self) -> None:
        """Clear the run gate: searches in flight finish, then the crawler idles."""
        self._loop.call_soon_threadsafe(self._resumed.clear)

    def resume(self) -> None:
        """Set the run gate: the crawler searches again."""
        self._loop.call_soon_threadsafe(self._resumed.set)

    def restart(self) -> None:
        """Request the crawler's graceful shutdown (the container restarts it)."""
        self._loop.call_soon_threadsafe(self._shutdown.set)

    def is_paused(self) -> bool:
        """A read, safe from another thread: ``is_set`` only returns a flag."""
        return not self._resumed.is_set()
