from catalog_matching.models import FileCandidate, TargetSegment


def test_file_candidate_defaults() -> None:
    candidate = FileCandidate(filename="keroro.avi")
    assert candidate.filename == "keroro.avi"
    assert candidate.size_mb is None
    assert candidate.duration_sec is None
    assert candidate.bitrate_kbps is None


def test_file_candidate_with_attributes() -> None:
    candidate = FileCandidate(
        filename="keroro.avi",
        size_mb=120.0,
        duration_sec=1320.0,
        bitrate_kbps=900.0,
    )
    assert candidate.size_mb == 120.0
    assert candidate.duration_sec == 1320.0
    assert candidate.bitrate_kbps == 900.0


def test_target_segment_defaults() -> None:
    target = TargetSegment(
        season=2, seasonal_number=11, absolute_number=62, segment="a", title="Les demoiselles"
    )
    assert target.status == "lost"


def test_target_segment_target_id_pads_and_uppercases() -> None:
    target = TargetSegment(
        season=2, seasonal_number=11, absolute_number=62, segment="a", title="Les demoiselles"
    )
    assert target.target_id == "062A"


def test_target_segment_full_fields() -> None:
    target = TargetSegment(
        season=1,
        seasonal_number=5,
        absolute_number=5,
        segment="b",
        title="Le grand combat",
        status="partial",
    )
    assert target.target_id == "005B"
    assert target.status == "partial"
