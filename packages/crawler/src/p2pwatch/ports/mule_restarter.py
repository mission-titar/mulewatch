"""``MuleRestarter`` port: restart the amuled PROCESS (High-ID port-sync, design §4.1/§5).

PORTS layer. amuled does NOT re-bind its listen port at runtime (socket created once at boot):
after an EC ``set_listen_port``, it must be RESTARTED so it re-binds the new port. It shares our
container and is supervised by s6, so the restart is a local ``s6-svc -r`` (``S6MuleRestarter``);
port-sync always needed a PROCESS restart and only ever restarted a CONTAINER because the process
was out of reach (single-container design §9). ``RestarterError`` (the restart fails) is ABSORBED
by the loop (never fatal → edge-triggered alert + backoff). Stub on ONE line (the ``def`` counts
as covered).
"""

from typing import Protocol


class RestarterError(Exception):
    """Restart of amuled failed (s6-svc missing or refusing) → absorbed by the loop.

    The loop catches it without importing this adapter (dependency rule §4): alert + backoff.
    """


class MuleRestarter(Protocol):
    async def restart(self) -> None: ...
