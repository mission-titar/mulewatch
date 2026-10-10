import dataclasses

import pytest

from catalog_matching.models import FileCandidate
from p2pwatch.domain.file_key import FileKey, Network
from p2pwatch.domain.observation import FileObservation, candidate_from_fields, fold_raw_meta

_KEY = FileKey(Network.ED2K, "31d6cfe0d16ae931b73c59d7e0c089c0")


def _full_observation() -> FileObservation:
    return FileObservation(
        file=_KEY,
        filename="Keroro 062A.avi",
        size_bytes=3 * 1024 * 1024,
        source_count=5,
        keyword="keroro",
        media_length_sec=1234,
        bitrate_kbps=1500,
        raw_meta=(("0x0308", "0"), ("codec", None), ("complete_source_count", 2)),
    )


def test_file_observation_is_frozen_and_holds_fields() -> None:
    observation = _full_observation()
    assert observation.file == _KEY
    assert observation.filename == "Keroro 062A.avi"
    assert observation.size_bytes == 3 * 1024 * 1024
    assert observation.source_count == 5
    assert observation.keyword == "keroro"
    assert observation.raw_meta == (("0x0308", "0"), ("codec", None), ("complete_source_count", 2))
    with pytest.raises(dataclasses.FrozenInstanceError):
        observation.filename = "autre"  # type: ignore[misc]


def test_media_fields_and_raw_meta_default_to_absent() -> None:
    observation = FileObservation(
        file=_KEY,
        filename="Keroro 062A.avi",
        size_bytes=100,
        source_count=0,
        keyword="keroro",
    )
    assert observation.media_length_sec is None
    assert observation.bitrate_kbps is None
    assert observation.raw_meta == ()


def test_to_candidate_converts_units_with_media_metadata() -> None:
    # exactly 3 MiB -> size_mb == 3.0 (DECISION 8: 1 MiB = 1024*1024 bytes).
    candidate = _full_observation().to_candidate()
    assert candidate == FileCandidate(
        filename="Keroro 062A.avi",
        size_mb=3.0,
        duration_sec=1234.0,
        bitrate_kbps=1500.0,
    )


def test_to_candidate_maps_absent_media_metadata_to_none() -> None:
    observation = FileObservation(
        file=_KEY,
        filename="Keroro 062A.avi",
        size_bytes=524288,  # 0.5 MiB
        source_count=1,
        keyword="keroro",
    )
    candidate = observation.to_candidate()
    assert candidate == FileCandidate(
        filename="Keroro 062A.avi",
        size_mb=0.5,
        duration_sec=None,
        bitrate_kbps=None,
    )


def test_candidate_from_fields_converts_units_with_media_metadata() -> None:
    # exactly 3 MiB -> size_mb == 3.0 (DECISION 8: 1 MiB = 1024*1024 bytes).
    candidate = candidate_from_fields(
        filename="Keroro 062A.avi",
        size_bytes=3 * 1024 * 1024,
        media_length_sec=1234,
        bitrate_kbps=1500,
    )
    assert candidate == FileCandidate(
        filename="Keroro 062A.avi",
        size_mb=3.0,
        duration_sec=1234.0,
        bitrate_kbps=1500.0,
    )


def test_candidate_from_fields_maps_absent_media_metadata_to_none() -> None:
    candidate = candidate_from_fields(
        filename="Keroro 062A.avi",
        size_bytes=524288,  # 0.5 MiB
        media_length_sec=None,
        bitrate_kbps=None,
    )
    assert candidate == FileCandidate(
        filename="Keroro 062A.avi",
        size_mb=0.5,
        duration_sec=None,
        bitrate_kbps=None,
    )


def test_fold_raw_meta_appends_the_three_values_after_the_pairs() -> None:
    folded = fold_raw_meta((("0x0308", "0"),), "xvid", "Video", 2)
    assert folded == (
        ("0x0308", "0"),
        ("codec", "xvid"),
        ("file_type", "Video"),
        ("complete_source_count", 2),
    )


def test_fold_raw_meta_keeps_an_absent_value_as_none() -> None:
    folded = fold_raw_meta((), None, None, 0)
    assert folded == (("codec", None), ("file_type", None), ("complete_source_count", 0))
