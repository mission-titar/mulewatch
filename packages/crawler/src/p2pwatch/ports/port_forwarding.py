"""``PortForwardingReader`` port: the VPN's LIVE forwarded port (port-sync, design §3.1).

PORTS layer. The crawler reads the forwarded port negotiated by gluetun (NAT-PMP) to align
amuled's listen port on it (High-ID). ``int > 0`` = live port; ``None`` = "not ready" (port 0
/ malformed JSON / control-server unreachable) — DEFENSIVE parsing in the adapter, NEVER an
exception: the degraded mode (Low-ID) is tolerated. Stub on ONE line (the ``def`` counts as
covered).
"""

from typing import Protocol


class PortForwardingReader(Protocol):
    async def forwarded_port(self) -> int | None: ...
