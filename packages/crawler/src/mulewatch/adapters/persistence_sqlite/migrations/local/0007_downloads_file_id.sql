-- local.db, migration 0007: downloads keyed by file_id, the catalog's key (stage 2 spec, D11, D19).
-- network and native_id stay: file_id is one-way, and the loop needs the FileKey back.

CREATE TABLE downloads_new (
    file_id BLOB PRIMARY KEY,
    network TEXT NOT NULL,
    native_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    state TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    completed_at TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT,
    UNIQUE (network, native_id),
    CHECK (network IN ('ed2k')),
    CHECK (network <> 'ed2k' OR (LENGTH(native_id) = 32 AND native_id NOT GLOB '*[^0-9a-f]*'))
);

INSERT INTO downloads_new
SELECT
    FILE_ID('ed2k', ed2k_hash) AS file_id,
    'ed2k' AS network,
    ed2k_hash AS native_id,
    target_id,
    state,
    queued_at,
    completed_at,
    size_bytes,
    last_seen_at
FROM downloads;

DROP TABLE downloads;

ALTER TABLE downloads_new RENAME TO downloads;
