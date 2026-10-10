"""A file's identity across networks: ``(network, native_id)`` and the ``file_id`` it derives.

PURE domain. ``file_id`` is the catalog's key, the same on every node (stage 1 spec D1, D9).
"""

import uuid
from dataclasses import dataclass
from enum import StrEnum

_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://mission-titar.github.io/p2pwatch/file")


class Network(StrEnum):
    """A network that defines a file's ``native_id``; ``ED2K`` covers eD2k and Kad (D2)."""

    ED2K = "ed2k"


@dataclass(frozen=True)
class FileKey:
    """A file as its network names it: for ``ED2K``, the 32 lowercase hex of its MD4."""

    network: Network
    native_id: str

    @property
    def file_id(self) -> bytes:
        """The 16 bytes of ``uuid5(namespace, "<network>:<native_id>")``."""
        return uuid.uuid5(_NAMESPACE, f"{self.network}:{self.native_id}").bytes
