-- catalog.db, migration 0007: observations as variants plus timestamps (stage 1 spec, D4 and D7).
-- One transaction over every observation: secure_delete OFF keeps the DROP's freed pages out of the
-- statement journal, the 256 MB cache keeps the copy off the disk; the runner restores both after.
PRAGMA secure_delete = OFF;
PRAGMA cache_size = -262144;

DROP TRIGGER file_observations_no_update;
DROP TRIGGER file_observations_no_delete;
DROP INDEX idx_file_observations_ed2k_hash;
DROP INDEX idx_file_observations_observed_at;
DROP INDEX idx_file_observations_hash_observed;

CREATE TABLE observation_variants (
    variant_id INTEGER PRIMARY KEY,
    ed2k_hash TEXT NOT NULL REFERENCES files (ed2k_hash),
    filename TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    media_length_sec INTEGER,
    bitrate_kbps INTEGER,
    raw_meta TEXT NOT NULL,
    keyword TEXT NOT NULL,
    node_id TEXT NOT NULL,
    content_hash BLOB NOT NULL UNIQUE
);

CREATE INDEX idx_observation_variants_ed2k_hash ON observation_variants (ed2k_hash);

CREATE TABLE observations (
    variant_id INTEGER NOT NULL REFERENCES observation_variants (variant_id),
    observed_at INTEGER NOT NULL,
    source_count INTEGER NOT NULL,
    PRIMARY KEY (variant_id, observed_at, source_count)
) WITHOUT ROWID;

-- DISTINCT keeps one entry per raw key (~13k), so each is folded and hashed once.
INSERT OR IGNORE INTO observation_variants (
    ed2k_hash, filename, size_bytes, media_length_sec, bitrate_kbps, raw_meta, keyword, node_id,
    content_hash
)
SELECT
    ed2k_hash,
    filename,
    size_bytes,
    media_length_sec,
    bitrate_kbps,
    folded,
    keyword,
    node_id,
    content_hash(
        ed2k_hash, filename, size_bytes, media_length_sec, bitrate_kbps, folded, keyword, node_id
    ) AS content_hash
FROM (
    SELECT DISTINCT
        ed2k_hash,
        filename,
        size_bytes,
        media_length_sec,
        bitrate_kbps,
        fold_raw_meta(raw_meta, codec, file_type, complete_source_count) AS folded,
        keyword,
        node_id
    FROM file_observations
) AS raw_keys;

-- CROSS JOIN pins the scan on file_observations; content_hash is memoized per distinct key.
INSERT OR IGNORE INTO observations (variant_id, observed_at, source_count)
SELECT
    v.variant_id,
    iso_to_micros(o.observed_at) AS observed_at,
    o.source_count
FROM file_observations AS o
CROSS JOIN observation_variants AS v
WHERE
    v.content_hash = content_hash(
        o.ed2k_hash, o.filename, o.size_bytes, o.media_length_sec, o.bitrate_kbps,
        fold_raw_meta(o.raw_meta, o.codec, o.file_type, o.complete_source_count),
        o.keyword, o.node_id
    )
ORDER BY o.id;

DROP TABLE file_observations;

CREATE TRIGGER observation_variants_no_update
BEFORE UPDATE ON observation_variants
BEGIN
    SELECT raise(ABORT, 'observation_variants is append-only');
END;

CREATE TRIGGER observation_variants_no_delete
BEFORE DELETE ON observation_variants
BEGIN
    SELECT raise(ABORT, 'observation_variants is append-only');
END;

CREATE TRIGGER observations_no_update
BEFORE UPDATE ON observations
BEGIN
    SELECT raise(ABORT, 'observations is append-only');
END;

CREATE TRIGGER observations_no_delete
BEFORE DELETE ON observations
BEGIN
    SELECT raise(ABORT, 'observations is append-only');
END;
