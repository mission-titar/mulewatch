"""``CrawlerControl`` port: the webui to crawler-loop control channel (spec §10, phase P6a).

The webui runs on its OWN thread with its OWN event loop (``composition/app.py`` starts it via
``_start_webui``/``_serve_webui``), while the crawler drives its asyncio loop on the MAIN
thread. A webui request handler that wants to influence the running crawler cannot touch the
crawler's ``asyncio.Event``s directly: ``Event.set()``/``.clear()`` may have to wake waiters
registered on the crawler loop, and those events are affine to that loop. So this port is the
seam: each method is a synchronous, non-blocking, fire-and-forget "intent accepted" call the
webui makes; the concrete adapter (``adapters/crawler_control_loop.py``) forwards the mutation
onto the crawler loop thread via ``loop.call_soon_threadsafe``.

Methods (all called from the webui thread, all return immediately):
- ``pause`` clears the run gate: searches in flight finish, then the crawler idles.
- ``resume`` sets the run gate: the crawler searches again.
- ``restart`` requests the crawler's graceful shutdown (the container's ``restart:
  unless-stopped`` brings it back).
- ``is_paused`` reads the run gate, for the dashboard.

Deliberately NOT here (deferred to phase P6b): re-evaluate, requeue a download.
"""

from typing import Protocol


class CrawlerControl(Protocol):
    """Webui to crawler-loop control intents (spec §10). Every call is thread-safe,
    non-blocking, fire-and-forget: it schedules a mutation on the crawler loop and returns at
    once. Stub bodies are ONE line (a two-line ``...`` body is a branch-coverage gotcha, see
    CLAUDE.md)."""

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def restart(self) -> None: ...

    def is_paused(self) -> bool: ...
