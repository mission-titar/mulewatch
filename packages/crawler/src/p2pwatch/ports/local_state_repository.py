"""``LocalStateRepository`` port: node identity + backfill marker (spec data-model §4/§6).

SYNCHRONOUS Protocol (same principle as ``CatalogRepository``). ``local.db`` is NEVER merged:
this port does not cross the node boundary (invariant MVP §11).
"""

from typing import Protocol


class LocalStateRepository(Protocol):
    """Sync local-state contract: stable node identity + backfill policy marker."""

    def node_id(self) -> str: ...

    def last_backfill_policy(self) -> str | None: ...

    def set_last_backfill_policy(self, sha256: str) -> None: ...
