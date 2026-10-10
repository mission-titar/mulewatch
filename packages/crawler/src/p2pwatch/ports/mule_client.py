"""What the crawler expects from an eMule client; its errors are in ``client_errors``.

Protocol stubs stay on ONE line: a body on a second one is an uncovered branch.
"""

from typing import Protocol

from p2pwatch.ports.client_status import StatusClient
from p2pwatch.ports.search_client import SearchClient


class MuleClient(SearchClient, StatusClient, Protocol):
    """The search, and the status."""
