"""What the crawler expects from an eMule client; its errors are in ``client_errors``.

Protocol stubs stay on ONE line: a body on a second one is an uncovered branch.
"""

from typing import Protocol

from mulewatch.ports.client_status import StatusClient
from mulewatch.ports.port_sync import NetworkStatus
from mulewatch.ports.search_client import SearchClient


class MuleClient(SearchClient, StatusClient, Protocol):
    """The search, the status, and the network status."""

    async def network_status(self) -> NetworkStatus: ...
