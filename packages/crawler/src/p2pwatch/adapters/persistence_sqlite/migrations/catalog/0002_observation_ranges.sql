-- catalog.db — migration 0002: daily rollup of observations (compaction).
-- Written/read ONLY by the compaction tool + the merge; the crawler ignores it.
-- One row = ONE bucket (ed2k_hash, UTC day), node-agnostic: aggregate of ALL
-- observations of that file on that day, across all nodes. source_count and
-- complete_source_count are NOT NULL in file_observations → aggregates always defined.
-- filenames / node_ids: CANONICAL JSON arrays (distinct, sorted). average = sum / count
-- (not stored — exact, associatively combinable). ADDITIVE migration: rebuilds
-- no table from 0001, so it does not touch its triggers.

CREATE TABLE file_observation_ranges (
    id INTEGER PRIMARY KEY,
    ed2k_hash TEXT NOT NULL REFERENCES files (ed2k_hash),
    bucket TEXT NOT NULL,
    filenames TEXT NOT NULL,
    node_ids TEXT NOT NULL,
    observation_count INTEGER NOT NULL,
    first_observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    source_count_min INTEGER NOT NULL,
    source_count_max INTEGER NOT NULL,
    source_count_sum INTEGER NOT NULL,
    complete_source_count_min INTEGER NOT NULL,
    complete_source_count_max INTEGER NOT NULL,
    complete_source_count_sum INTEGER NOT NULL,
    CHECK (observation_count > 0),
    CHECK (first_observed_at <= last_observed_at),
    CHECK (LENGTH(bucket) = 10)
);

CREATE INDEX idx_file_observation_ranges_ed2k_hash
ON file_observation_ranges (ed2k_hash);

CREATE TRIGGER file_observation_ranges_no_update
BEFORE UPDATE ON file_observation_ranges
BEGIN
    SELECT RAISE(ABORT, 'file_observation_ranges is append-only');
END;

CREATE TRIGGER file_observation_ranges_no_delete
BEFORE DELETE ON file_observation_ranges
BEGIN
    SELECT RAISE(ABORT, 'file_observation_ranges is append-only');
END;
