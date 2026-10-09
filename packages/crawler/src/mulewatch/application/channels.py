"""The Prometheus ``network`` label of a search channel: ``"ed2k"`` for GLOBAL, ``"kad"`` for KAD.

A search channel says how a file was found, not which identity network it belongs to."""

from mulewatch.ports.mule_client import SearchChannel

ED2K = "ed2k"
KAD = "kad"

_LABELS = {SearchChannel.GLOBAL: ED2K, SearchChannel.KAD: KAD}


def network_label(channel: SearchChannel) -> str:
    """``"ed2k"`` for GLOBAL, ``"kad"`` for KAD."""
    return _LABELS[channel]
