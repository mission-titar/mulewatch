"""``GluetunPortReader`` adapter: reads gluetun's live forwarded port (port-sync, design §3.2).

``GET {base}/v1/portforward`` → ``{"port": N}`` (route confirmed via the upstream gluetun docs).
DEFENSIVE PARSING (DECISION 7) - ANY failure → ``None`` ("not ready"), NEVER an exception that
propagates: degraded mode (Low-ID) is tolerated (control-server unreachable, PF not yet negotiated,
malformed body). EXACT mirror of ``HttpContentVerifier``'s defensive parsing.

Auth: on the internal ``ec`` network, the gluetun control-server auth is disabled
(``HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE='{"auth":"none"}'``, DECISION D3) → no header to
set. To harden it someday, it's one header (``X-API-Key``/``Authorization``) to add HERE,
loop unchanged. ``aclose`` closes the httpx client (called by composition at shutdown).
"""

import json
import logging

import httpx

_logger = logging.getLogger("p2pwatch.adapters.gluetun_port")


class GluetunPortReader:
    """httpx implementation of the ``PortForwardingReader`` port (STRUCTURAL satisfaction)."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def forwarded_port(self) -> int | None:
        """``GET /v1/portforward``; ``int > 0`` if the PF is alive, else ``None`` (defensive)."""
        try:
            response = await self._client.get("/v1/portforward")
            response.raise_for_status()
        except httpx.HTTPError as error:
            # control-server unreachable / timeout / 4xx / 5xx → "not ready" (Low-ID tolerated).
            _logger.debug("gluetun control-server unavailable (%s): forwarded port unknown", error)
            return None
        return self._parse(response)

    def _parse(self, response: httpx.Response) -> int | None:
        """Defensive parse of a 200: integer ``port`` > 0, else ``None`` (never an exception)."""
        try:
            payload = json.loads(response.content)
        except (json.JSONDecodeError, ValueError):
            _logger.debug("non-JSON /v1/portforward reply: forwarded port unknown")
            return None
        if not isinstance(payload, dict):
            return None
        port = payload.get("port")
        # bool is a subtype of int: excluded explicitly (True must not count as a port).
        if not isinstance(port, int) or isinstance(port, bool) or port <= 0:
            return None
        return port

    async def aclose(self) -> None:
        """Closes the httpx client (called by composition at shutdown)."""
        await self._client.aclose()
