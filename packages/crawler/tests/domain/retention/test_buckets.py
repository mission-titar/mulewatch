"""bucketize (pure): group observations by (ed2k_hash, UTC day), node-agnostic aggregate."""

from mulewatch.domain.retention.buckets import ObservationBucket, ObservationRow, bucketize


def _row(
    *, h: str = "a" * 32, node: str = "n1", name: str = "f.avi", sc: int = 5, csc: int = 1, at: str
) -> ObservationRow:
    return ObservationRow(
        ed2k_hash=h,
        node_id=node,
        filename=name,
        source_count=sc,
        complete_source_count=csc,
        observed_at=at,
    )


def test_empty_input_gives_no_bucket() -> None:
    assert bucketize([]) == []


def test_one_day_one_hash_aggregates() -> None:
    rows = [
        _row(sc=1, csc=0, at="2026-03-01T01:00:00.000000+00:00"),
        _row(sc=9, csc=2, at="2026-03-01T20:00:00.000000+00:00"),
    ]
    (bucket,) = bucketize(rows)
    assert bucket == ObservationBucket(
        ed2k_hash="a" * 32,
        bucket="2026-03-01",
        filenames='["f.avi"]',
        node_ids='["n1"]',
        observation_count=2,
        first_observed_at="2026-03-01T01:00:00.000000+00:00",
        last_observed_at="2026-03-01T20:00:00.000000+00:00",
        source_count_min=1,
        source_count_max=9,
        source_count_sum=10,
        complete_source_count_min=0,
        complete_source_count_max=2,
        complete_source_count_sum=2,
    )


def test_two_days_give_two_buckets() -> None:
    rows = [
        _row(at="2026-03-01T01:00:00.000000+00:00"),
        _row(at="2026-03-02T01:00:00.000000+00:00"),
    ]
    buckets = bucketize(rows)
    assert [b.bucket for b in buckets] == ["2026-03-01", "2026-03-02"]


def test_filenames_are_sorted_distinct_json() -> None:
    rows = [
        _row(name="b.avi", at="2026-03-01T01:00:00.000000+00:00"),
        _row(name="a.avi", at="2026-03-01T02:00:00.000000+00:00"),
        _row(name="b.avi", at="2026-03-01T03:00:00.000000+00:00"),
    ]
    (bucket,) = bucketize(rows)
    assert bucket.filenames == '["a.avi", "b.avi"]'


def test_node_agnostic_two_nodes_one_bucket() -> None:
    rows = [
        _row(node="n2", at="2026-03-01T01:00:00.000000+00:00"),
        _row(node="n1", at="2026-03-01T02:00:00.000000+00:00"),
    ]
    (bucket,) = bucketize(rows)
    assert bucket.node_ids == '["n1", "n2"]'
    assert bucket.observation_count == 2


def test_multiple_hashes_and_days_give_distinct_buckets() -> None:
    rows = [
        _row(h="a" * 32, at="2026-03-01T01:00:00.000000+00:00"),
        _row(h="a" * 32, at="2026-03-02T01:00:00.000000+00:00"),
        _row(h="b" * 32, at="2026-03-01T01:00:00.000000+00:00"),
    ]
    buckets = bucketize(rows)
    assert [(b.ed2k_hash, b.bucket) for b in buckets] == [
        ("a" * 32, "2026-03-01"),
        ("a" * 32, "2026-03-02"),
        ("b" * 32, "2026-03-01"),
    ]


def test_single_observation_min_eq_max_eq_sum() -> None:
    (bucket,) = bucketize([_row(sc=7, csc=3, at="2026-03-01T01:00:00.000000+00:00")])
    assert (bucket.source_count_min, bucket.source_count_max, bucket.source_count_sum) == (7, 7, 7)
    assert bucket.first_observed_at == bucket.last_observed_at
