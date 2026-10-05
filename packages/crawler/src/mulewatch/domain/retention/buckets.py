"""Daily bucketing of observations for compaction (spec compaction §4).

PURE domain (no I/O). Groups observations by (ed2k_hash, UTC day) and computes a bucket's
node-agnostic aggregates. The UTC day is the SLICE observed_at[:10] ("YYYY-MM-DD") —
observed_at is fixed-width ISO-8601 UTC (connection.utc_iso), so no timezone math.
filenames/node_ids are CANONICAL JSON arrays (distinct, sorted): two equal sets have the same
text → append-only/merge dedup by row equality works. The mean is derived from
*_sum / observation_count (not stored).
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import groupby


@dataclass(frozen=True)
class ObservationRow:
    """A raw observation read for compaction (fields needed for bucketing)."""

    ed2k_hash: str
    node_id: str
    filename: str
    source_count: int
    complete_source_count: int
    observed_at: str


@dataclass(frozen=True)
class ObservationBucket:
    """A bucket (ed2k_hash, day): the 13 columns of file_observation_ranges except id."""

    ed2k_hash: str
    bucket: str
    filenames: str
    node_ids: str
    observation_count: int
    first_observed_at: str
    last_observed_at: str
    source_count_min: int
    source_count_max: int
    source_count_sum: int
    complete_source_count_min: int
    complete_source_count_max: int
    complete_source_count_sum: int


def _json_sorted_set(values: Sequence[str]) -> str:
    """Canonical JSON array of distinct sorted values (stable text for dedup)."""
    return json.dumps(sorted(set(values)))


def bucketize(observations: Sequence[ObservationRow]) -> list[ObservationBucket]:
    """Group `observations` by (ed2k_hash, UTC day) → one ObservationBucket per group.

    The input MUST be sorted by (ed2k_hash, observed_at, id) — the orchestration (compactor)
    guarantees it via SQL. Each group (same hash, same day) is then contiguous (increasing days).
    Node-agnostic: all observations of a (hash, day), across all nodes, → ONE bucket.
    """
    buckets: list[ObservationBucket] = []
    for (ed2k_hash, day), group_iter in groupby(
        observations, key=lambda row: (row.ed2k_hash, row.observed_at[:10])
    ):
        group = list(group_iter)
        source_counts = [row.source_count for row in group]
        complete_counts = [row.complete_source_count for row in group]
        observed_ats = [row.observed_at for row in group]
        buckets.append(
            ObservationBucket(
                ed2k_hash=ed2k_hash,
                bucket=day,
                filenames=_json_sorted_set([row.filename for row in group]),
                node_ids=_json_sorted_set([row.node_id for row in group]),
                observation_count=len(group),
                first_observed_at=min(observed_ats),
                last_observed_at=max(observed_ats),
                source_count_min=min(source_counts),
                source_count_max=max(source_counts),
                source_count_sum=sum(source_counts),
                complete_source_count_min=min(complete_counts),
                complete_source_count_max=max(complete_counts),
                complete_source_count_sum=sum(complete_counts),
            )
        )
    return buckets
