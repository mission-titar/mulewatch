-- catalog.db, migration 0008: a file is (network, native_id), keyed by file_id (stage 1, D1, D7).
-- observations is rebuilt too: renaming observation_variants away would leave it pointing there.
-- Same pragmas as 0007; the runner restores both after.
PRAGMA secure_delete = OFF;
PRAGMA cache_size = -262144;

-- 1. The old tables' triggers and indexes (index names survive a rename and would collide).
DROP TRIGGER files_no_update;
DROP TRIGGER files_no_delete;
DROP TRIGGER match_decisions_no_update;
DROP TRIGGER match_decisions_no_delete;
DROP TRIGGER observation_variants_no_update;
DROP TRIGGER observation_variants_no_delete;
DROP TRIGGER observations_no_update;
DROP TRIGGER observations_no_delete;
DROP INDEX idx_match_decisions_ed2k_hash;
DROP INDEX idx_match_decisions_hash_target_decided;
DROP INDEX idx_observation_variants_ed2k_hash;

-- 2. Renamed away; each rename rewrites its children's REFERENCES to the new name.
ALTER TABLE files RENAME TO files_old;
ALTER TABLE match_decisions RENAME TO match_decisions_old;
ALTER TABLE observation_variants RENAME TO observation_variants_old;
ALTER TABLE observations RENAME TO observations_old;

-- 3. The new tables under their final names.
CREATE TABLE files (
    file_id BLOB PRIMARY KEY,
    network TEXT NOT NULL,
    native_id TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    UNIQUE (network, native_id),
    CHECK (network IN ('ed2k')),
    CHECK (network <> 'ed2k' OR (LENGTH(native_id) = 32 AND native_id NOT GLOB '*[^0-9a-f]*'))
);

CREATE TABLE match_decisions (
    id INTEGER PRIMARY KEY,
    file_id BLOB NOT NULL REFERENCES files (file_id),
    target_id TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    tier TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    node_id TEXT NOT NULL
);

-- One index: a second on file_id alone would be a prefix of this one.
CREATE INDEX idx_match_decisions_file_target_decided
ON match_decisions (file_id, target_id, decided_at);

CREATE TABLE observation_variants (
    variant_id INTEGER PRIMARY KEY,
    file_id BLOB NOT NULL REFERENCES files (file_id),
    filename TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    media_length_sec INTEGER,
    bitrate_kbps INTEGER,
    raw_meta TEXT NOT NULL,
    keyword TEXT NOT NULL,
    node_id TEXT NOT NULL,
    content_hash BLOB NOT NULL UNIQUE
);

CREATE INDEX idx_observation_variants_file_id ON observation_variants (file_id);

CREATE TABLE observations (
    variant_id INTEGER NOT NULL REFERENCES observation_variants (variant_id),
    observed_at INTEGER NOT NULL,
    source_count INTEGER NOT NULL,
    PRIMARY KEY (variant_id, observed_at, source_count)
) WITHOUT ROWID;

-- 4. Copy. Ids are kept, so observations copy as they are; content_hash now hashes the file_id.
INSERT INTO files (file_id, network, native_id, size_bytes)
SELECT
    FILE_ID('ed2k', ed2k_hash) AS file_id,
    'ed2k' AS network,
    ed2k_hash AS native_id,
    size_bytes
FROM files_old;

INSERT INTO match_decisions (id, file_id, target_id, rule_name, tier, decided_at, node_id)
SELECT
    id,
    FILE_ID('ed2k', ed2k_hash) AS file_id,
    target_id,
    rule_name,
    tier,
    decided_at,
    node_id
FROM match_decisions_old
ORDER BY id;

INSERT INTO observation_variants (
    variant_id, file_id, filename, size_bytes, media_length_sec, bitrate_kbps, raw_meta, keyword,
    node_id, content_hash
)
SELECT
    variant_id,
    file_id,
    filename,
    size_bytes,
    media_length_sec,
    bitrate_kbps,
    raw_meta,
    keyword,
    node_id,
    CONTENT_HASH(
        file_id, filename, size_bytes, media_length_sec, bitrate_kbps, raw_meta, keyword, node_id
    ) AS content_hash
FROM (
    SELECT
        variant_id,
        FILE_ID('ed2k', ed2k_hash) AS file_id,
        filename,
        size_bytes,
        media_length_sec,
        bitrate_kbps,
        raw_meta,
        keyword,
        node_id
    FROM observation_variants_old
) AS keyed
ORDER BY variant_id;

INSERT INTO observations (variant_id, observed_at, source_count)
SELECT
    variant_id,
    observed_at,
    source_count
FROM observations_old
ORDER BY variant_id, observed_at, source_count;

-- 5. Children before their parent.
DROP TABLE observations_old;
DROP TABLE observation_variants_old;
DROP TABLE match_decisions_old;
DROP TABLE files_old;

-- 6. The append-only triggers.
CREATE TRIGGER files_no_update
BEFORE UPDATE ON files
BEGIN
    SELECT RAISE(ABORT, 'files is append-only');
END;

CREATE TRIGGER files_no_delete
BEFORE DELETE ON files
BEGIN
    SELECT RAISE(ABORT, 'files is append-only');
END;

CREATE TRIGGER match_decisions_no_update
BEFORE UPDATE ON match_decisions
BEGIN
    SELECT RAISE(ABORT, 'match_decisions is append-only');
END;

CREATE TRIGGER match_decisions_no_delete
BEFORE DELETE ON match_decisions
BEGIN
    SELECT RAISE(ABORT, 'match_decisions is append-only');
END;

CREATE TRIGGER observation_variants_no_update
BEFORE UPDATE ON observation_variants
BEGIN
    SELECT RAISE(ABORT, 'observation_variants is append-only');
END;

CREATE TRIGGER observation_variants_no_delete
BEFORE DELETE ON observation_variants
BEGIN
    SELECT RAISE(ABORT, 'observation_variants is append-only');
END;

CREATE TRIGGER observations_no_update
BEFORE UPDATE ON observations
BEGIN
    SELECT RAISE(ABORT, 'observations is append-only');
END;

CREATE TRIGGER observations_no_delete
BEFORE DELETE ON observations
BEGIN
    SELECT RAISE(ABORT, 'observations is append-only');
END;
