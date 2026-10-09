"""The network label derives from the SearchChannel by construction (E-D6)."""

from mulewatch.application.channels import ED2K, KAD, network_label
from mulewatch.ports.mule_client import SearchChannel


def test_global_is_ed2k() -> None:
    assert network_label(SearchChannel.GLOBAL) == ED2K == "ed2k"


def test_kad_is_kad() -> None:
    assert network_label(SearchChannel.KAD) == KAD == "kad"
