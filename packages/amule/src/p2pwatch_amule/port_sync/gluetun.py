"""`forwarded_port`: gluetun's forwarded port, or None on any failure, never an exception."""

import http.client
import json
import logging
import urllib.request

_logger = logging.getLogger(__name__)


def forwarded_port(control_url: str) -> int | None:
    """`GET <control_url>/v1/portforward` → `{"port": N}`; None unless N is an integer > 0."""
    url = control_url.rstrip("/") + "/v1/portforward"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            body = response.read()
    except (OSError, http.client.HTTPException) as error:
        # URLError, HTTPError and timeouts are OSErrors; a garbled reply is an HTTPException.
        _logger.warning("gluetun's control server is unavailable (%s)", error)
        return None
    try:
        port = json.loads(body).get("port")
    except (ValueError, AttributeError):
        port = None
    # bool is an int: true must not count as a port.
    if not isinstance(port, int) or isinstance(port, bool) or port <= 0:
        _logger.warning("gluetun reports no forwarded port (%r)", body[:100])
        return None
    return port
