"""Mapping of the amuleapi JSON payloads (spec amuleapi §4.2, D4/D5)."""

from typing import Any

import pytest

from mulewatch.adapters.mule_api.mapping import (
    map_client_status,
    map_download_status,
    map_search_results,
    map_shared_download,
)
from mulewatch.domain.file_key import FileKey, Network
from mulewatch.domain.observation import FileObservation
from mulewatch.ports.client_status import ChannelStatus, ClientStatus
from mulewatch.ports.download_client import DownloadStatus, FailureReason, WaitingReason

_HASH = "8b54a3c20fae9e4b9f7e0c2c8c01b6b1"
_KEY = FileKey(Network.ED2K, _HASH)


def _result(**overrides: Any) -> dict[str, Any]:
    """A result row shaped like GET /search/{id}/results returns one."""
    row: dict[str, Any] = {
        "hash": _HASH,
        "name": "Keroro.095.avi",
        "size_bytes": 3825205248,
        "sources": {"total": 217, "complete": 142},
        "already_downloaded": False,
        "rating": 0,
        "status": "new",
        "file_type": "video",
        "directory": "",
        "media": None,
        "alternate_names": [],
        "comments": [],
    }
    row.update(overrides)
    return row


def _meta(observation: FileObservation) -> dict[str, object]:
    return dict(observation.raw_meta)


def test_maps_a_result_to_an_observation() -> None:
    observations, skipped = map_search_results([_result()], "keroro")

    assert skipped == 0
    (observation,) = observations
    assert observation.file == _KEY
    assert observation.filename == "Keroro.095.avi"
    assert observation.size_bytes == 3825205248
    assert observation.source_count == 217
    assert observation.keyword == "keroro"


def test_the_ed2k_only_fields_are_folded_last_into_raw_meta() -> None:
    (observation,), _ = map_search_results([_result(media={"codec": "H.264"})], "keroro")

    assert observation.raw_meta[-3:] == (
        ("codec", "H.264"),
        ("file_type", "video"),
        ("complete_source_count", 142),
    )


def test_an_uppercase_hash_is_folded_to_lowercase() -> None:
    (observation,), _ = map_search_results([_result(hash=_HASH.upper())], "keroro")

    assert observation.file == _KEY


def test_media_fills_the_columns_that_ec_never_could() -> None:
    media = {
        "duration_seconds": 1440,
        "bitrate_kilobits_per_second": 1500,
        "codec": "H.264",
        "artist": "",
        "album": "",
        "title": "Keroro",
    }
    (observation,), _ = map_search_results([_result(media=media)], "keroro")

    assert observation.media_length_sec == 1440
    assert observation.bitrate_kbps == 1500
    assert _meta(observation)["codec"] == "H.264"
    # artist/album/title have no column: they survive in raw_meta (spec §4.2).
    assert ("media.title", "Keroro") in observation.raw_meta


def test_a_null_media_leaves_the_media_columns_empty() -> None:
    (observation,), _ = map_search_results([_result()], "keroro")

    assert observation.media_length_sec is None
    assert observation.bitrate_kbps is None
    assert _meta(observation)["codec"] is None


def test_a_malformed_media_object_is_tolerated() -> None:
    (observation,), _ = map_search_results([_result(media={"codec": 7})], "keroro")

    assert _meta(observation)["codec"] is None
    assert observation.media_length_sec is None


def test_unmapped_keys_land_in_raw_meta() -> None:
    (observation,), _ = map_search_results([_result(rating=3)], "keroro")

    raw = _meta(observation)
    assert raw["rating"] == "3"
    assert raw["status"] == "new"
    assert raw["already_downloaded"] == "false"
    assert raw["comments"] == "[]"
    # Mapped keys are NOT duplicated into raw_meta.
    assert "hash" not in raw
    assert "alternate_names" not in raw


def test_every_alternate_name_becomes_its_own_observation() -> None:
    """D4: amuled folds the same file's other filenames under the parent; we unfold them."""
    alternates = [
        {"ecid": 621, "name": "[Keroro].095.avi", "sources": {"total": 40, "complete": 22}},
        {"ecid": 622, "name": "sgt-frog-095.avi", "sources": {"total": 10, "complete": 3}},
    ]
    observations, skipped = map_search_results([_result(alternate_names=alternates)], "keroro")

    assert skipped == 0
    assert [observation.filename for observation in observations] == [
        "Keroro.095.avi",
        "[Keroro].095.avi",
        "sgt-frog-095.avi",
    ]
    # The hash and the size come from the parent, the source counts from each entry.
    assert {observation.file for observation in observations} == {_KEY}
    assert {observation.size_bytes for observation in observations} == {3825205248}
    assert [observation.source_count for observation in observations] == [217, 40, 10]
    assert [_meta(observation)["complete_source_count"] for observation in observations] == [
        142,
        22,
        3,
    ]


def test_an_alternate_never_carries_the_volatile_ecid() -> None:
    alternate = {"ecid": 621, "name": "[Keroro].095.avi", "sources": {"total": 40}}
    observations, _ = map_search_results([_result(alternate_names=[alternate])], "keroro")

    assert all("621" not in str(value) for _, value in observations[1].raw_meta)


def test_a_result_without_a_usable_hash_is_skipped_and_counted() -> None:
    observations, skipped = map_search_results([_result(hash="not-a-hash")], "keroro")

    assert observations == ()
    assert skipped == 1


def test_a_result_without_a_usable_size_is_skipped_and_counted() -> None:
    observations, skipped = map_search_results([_result(size_bytes=True)], "keroro")

    assert observations == ()
    assert skipped == 1


def test_a_result_that_is_not_an_object_is_skipped_and_counted() -> None:
    observations, skipped = map_search_results(["nonsense"], "keroro")

    assert observations == ()
    assert skipped == 1


def test_an_unusable_name_costs_only_its_own_observation() -> None:
    alternates = [{"name": 7}, "nonsense", {"name": "sgt-frog-095.avi"}]
    observations, skipped = map_search_results([_result(alternate_names=alternates)], "keroro")

    assert [observation.filename for observation in observations] == [
        "Keroro.095.avi",
        "sgt-frog-095.avi",
    ]
    assert skipped == 2


def test_a_nameless_parent_does_not_lose_its_alternates() -> None:
    alternates = [{"name": "sgt-frog-095.avi"}]
    observations, skipped = map_search_results(
        [_result(name="", alternate_names=alternates)], "keroro"
    )

    assert [observation.filename for observation in observations] == ["sgt-frog-095.avi"]
    assert skipped == 1


def test_a_malformed_alternate_names_list_is_ignored() -> None:
    observations, skipped = map_search_results([_result(alternate_names="nope")], "keroro")

    assert len(observations) == 1
    assert skipped == 0


def test_missing_source_counts_read_as_zero() -> None:
    (observation,), _ = map_search_results([_result(sources="nope")], "keroro")

    assert observation.source_count == 0
    assert _meta(observation)["complete_source_count"] == 0


def test_a_non_integer_source_count_reads_as_zero() -> None:
    (observation,), _ = map_search_results([_result(sources={"total": "many"})], "keroro")

    assert observation.source_count == 0


def test_a_non_string_file_type_is_dropped() -> None:
    (observation,), _ = map_search_results([_result(file_type=7)], "keroro")

    assert _meta(observation)["file_type"] is None


def test_a_results_payload_that_is_not_a_list_yields_nothing() -> None:
    assert map_search_results("nope", "keroro") == ((), 0)


def _queued(status: str, **overrides: Any) -> dict[str, Any]:
    """A row shaped like GET /downloads returns one."""
    row: dict[str, Any] = {
        "hash": _HASH,
        "size_bytes": 1000,
        "completed_bytes": 400,
        "status": status,
        "sources": {"total": 5, "unavailable": 0, "transferring": 2, "a4af": 0},
    }
    row.update(overrides)
    return row


def _download(row: object) -> DownloadStatus:
    status = map_download_status(row)
    assert status is not None
    return status


def test_maps_a_download_row_to_its_status() -> None:
    assert _download(_queued("downloading")) == DownloadStatus(
        file=_KEY,
        bytes_done=400,
        bytes_total=1000,
        completed=False,
        waiting_reason=None,
        failure_reason=None,
    )


def test_only_the_completed_status_completes_a_download() -> None:
    # Every byte received is not the file verified and moved (spec stage 2, D10).
    statuses = ("completed", "completing", "downloading")
    completed = [_download(_queued(status, completed_bytes=1000)).completed for status in statuses]

    assert completed == [True, False, False]


def test_an_erroneous_download_failed_with_an_error() -> None:
    assert _download(_queued("erroneous")).failure_reason is FailureReason.ERROR


@pytest.mark.parametrize(
    ("status", "sources", "reason"),
    [
        ("insufficient_disk", {"total": 0}, WaitingReason.DISK_FULL),
        ("paused", {"total": 0}, WaitingReason.PAUSED),
        ("stopped", {"total": 0}, WaitingReason.PAUSED),
        ("waiting", {"total": 0}, WaitingReason.LOCAL),
        ("hashing", {"total": 0}, WaitingReason.LOCAL),
        ("allocating", {"total": 0}, WaitingReason.LOCAL),
        ("completing", {"total": 0}, WaitingReason.LOCAL),
        ("downloading", {"total": 0}, WaitingReason.NO_SOURCE),
        ("downloading", {}, WaitingReason.NO_SOURCE),
        ("downloading", {"total": 3, "transferring": 0}, WaitingReason.REMOTE_QUEUE),
        ("downloading", {"total": 3, "transferring": 1}, None),
        ("completed", {"total": 0}, None),
        ("erroneous", {"total": 0}, None),
    ],
)
def test_a_download_waits_for_the_first_reason_that_applies(
    status: str, sources: dict[str, int], reason: WaitingReason | None
) -> None:
    assert _download(_queued(status, sources=sources)).waiting_reason is reason


def test_a_status_that_is_not_a_string_waits_on_its_sources() -> None:
    # An unhashable status must not reach the status table as a key.
    row = _queued("downloading", sources={"total": 0})
    row["status"] = ["paused"]

    assert _download(row).waiting_reason is WaitingReason.NO_SOURCE


def test_a_download_status_row_without_a_usable_hash_is_dropped() -> None:
    assert map_download_status({"size_bytes": 1000, "status": "completed"}) is None
    assert map_download_status("nonsense") is None


def test_a_shared_row_reads_as_a_completed_download() -> None:
    assert map_shared_download({"hash": _HASH, "size_bytes": 10}) == DownloadStatus(
        file=_KEY,
        bytes_done=10,
        bytes_total=10,
        completed=True,
        waiting_reason=None,
        failure_reason=None,
    )
    assert map_shared_download({"name": "Keroro.095.avi"}) is None
    assert map_shared_download("nonsense") is None


def test_a_connected_high_id_and_open_kad_are_connectable() -> None:
    status = map_client_status(
        {
            "ed2k": {"state": "connected", "high_id": True, "public_ip": "210.2.150.73"},
            "kad": {"state": "connected", "firewalled_tcp": False},
        },
        {"daemon_version": "3.0.1"},
    )

    assert status == ClientStatus(
        version="3.0.1",
        channels=(
            ChannelStatus(channel="ed2k", on_network=True, connectable=True),
            ChannelStatus(channel="kad", on_network=True, connectable=True),
        ),
    )


def test_a_low_id_and_a_firewalled_kad_are_not_connectable() -> None:
    status = map_client_status(
        {
            "ed2k": {"state": "connected", "high_id": False},
            "kad": {"state": "connected", "firewalled_tcp": True},
        },
        {"daemon_version": "3.0.1"},
    )

    assert [channel.connectable for channel in status.channels] == [False, False]


def test_connectable_is_unknown_off_the_network() -> None:
    # Off the network, high_id false means "no id yet", not Low-ID.
    status = map_client_status(
        {
            "ed2k": {"state": "connecting", "high_id": False},
            "kad": {"state": "connecting", "firewalled_tcp": None},
        },
        {"daemon_version": "3.0.1"},
    )

    assert [channel.on_network for channel in status.channels] == [False, False]
    assert [channel.connectable for channel in status.channels] == [None, None]


def test_a_missing_reachability_flag_on_the_network_is_unknown() -> None:
    status = map_client_status({"ed2k": {"state": "connected"}, "kad": {"state": "connected"}}, {})

    assert [channel.connectable for channel in status.channels] == [None, None]


def test_an_empty_version_and_status_degrade_to_unknown() -> None:
    # daemon_version is "" while EC is down.
    status = map_client_status("nonsense", {"daemon_version": ""})

    assert status.version is None
    assert [channel.channel for channel in status.channels] == ["ed2k", "kad"]
    assert [channel.on_network for channel in status.channels] == [False, False]
