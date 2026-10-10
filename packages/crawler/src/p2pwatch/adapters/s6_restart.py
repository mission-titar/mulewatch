"""Restart amuled with ``s6-svc``: it runs in this container, under the same supervisor.

``s6-svc`` returns as soon as the signal is queued, so there is nothing to wait for. Anything
that goes wrong raises ``RestarterError``; the port-sync loop alerts and retries next cycle.
"""

import asyncio
import logging
from collections.abc import Sequence

from p2pwatch.ports.mule_restarter import RestarterError

_logger = logging.getLogger("p2pwatch.adapters.s6_restart")

_RESTART_COMMAND: tuple[str, ...] = ("s6-svc", "-r", "/etc/services.d/amuled")


class S6MuleRestarter:
    """``MuleRestarter`` implementation over ``s6-svc`` (STRUCTURAL satisfaction)."""

    def __init__(self, *, command: Sequence[str] = _RESTART_COMMAND) -> None:
        self._command = tuple(command)

    async def restart(self) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                *self._command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise RestarterError(f"cannot run {self._command[0]} ({error})") from error
        _, stderr = await process.communicate()
        if process.returncode != 0:
            detail = stderr.decode(errors="replace").strip()
            raise RestarterError(f"{self._command[0]} exited {process.returncode}: {detail}")
        _logger.info("amuled restart requested (%s)", " ".join(self._command))
