import dataclasses
import uuid

import pytest

from mulewatch.domain.file_key import FileKey, Network

_HASH = "31d6cfe0d16ae931b73c59d7e0c089c0"


def test_network_ed2k_is_its_catalog_value() -> None:
    assert Network("ed2k") is Network.ED2K
    assert list(Network) == [Network.ED2K]


def test_file_key_is_frozen() -> None:
    key = FileKey(Network.ED2K, _HASH)
    with pytest.raises(dataclasses.FrozenInstanceError):
        key.native_id = "autre"  # type: ignore[misc]


def test_file_id_is_the_uuid5_of_network_and_native_id_in_the_mulewatch_namespace() -> None:
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, "https://mission-titar.github.io/mulewatch/file")
    assert namespace == uuid.UUID("7d16bb87-5b2f-5aa5-9262-c007b2ab0db4")
    assert FileKey(Network.ED2K, _HASH).file_id == bytes.fromhex("25f66e02cb665d60854b7687954a0c08")


def test_file_id_differs_per_native_id() -> None:
    other = "0" * 32
    assert FileKey(Network.ED2K, _HASH).file_id != FileKey(Network.ED2K, other).file_id
