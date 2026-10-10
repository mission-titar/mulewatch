-- catalog.db — migration 0001: full schema (spec data-model §5; spec MVP §11).
-- Append-only ENFORCED BY THE DATABASE: BEFORE UPDATE / BEFORE DELETE triggers on EVERY
-- table → RAISE(ABORT). Holds against UPDATE/DELETE/UPSERT in all cases; against
-- INSERT OR REPLACE only if the connection sets PRAGMA recursive_triggers=ON (our
-- connections do; a third-party tool on the default PRAGMA can bypass it, just as
-- it can DROP TRIGGER). Content key = eD2k hash (lowercase 32-char hex, canonical v0.5.0).
-- Timestamps ISO-8601 UTC as TEXT; raw_meta = JSON list of pairs (order + duplicates).

CREATE TABLE files (
    ed2k_hash TEXT PRIMARY KEY,
    size_bytes INTEGER NOT NULL,
    aich_hash TEXT,
    CHECK (LENGTH(ed2k_hash) = 32 AND ed2k_hash NOT GLOB '*[^0-9a-f]*')
);

CREATE TABLE file_observations (
    id INTEGER PRIMARY KEY,
    ed2k_hash TEXT NOT NULL REFERENCES files (ed2k_hash),
    filename TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    source_count INTEGER NOT NULL,
    complete_source_count INTEGER NOT NULL,
    media_length_sec INTEGER,
    bitrate_kbps INTEGER,
    codec TEXT,
    file_type TEXT,
    raw_meta TEXT NOT NULL,
    keyword TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    node_id TEXT NOT NULL
);

CREATE INDEX idx_file_observations_ed2k_hash ON file_observations (ed2k_hash);
CREATE INDEX idx_file_observations_observed_at ON file_observations (observed_at);

CREATE TABLE sources (
    user_hash TEXT PRIMARY KEY,
    client_name TEXT,
    client_version TEXT
);

CREATE TABLE source_observations (
    id INTEGER PRIMARY KEY,
    user_hash TEXT REFERENCES sources (user_hash),
    ed2k_hash TEXT NOT NULL REFERENCES files (ed2k_hash),
    ip TEXT,
    port INTEGER,
    nickname TEXT,
    client_name TEXT,
    client_version TEXT,
    country TEXT,
    id_type TEXT,
    has_complete_file INTEGER CHECK (has_complete_file IN (0, 1)),
    origin TEXT,
    raw_meta TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    node_id TEXT NOT NULL
);

CREATE INDEX idx_source_observations_ed2k_hash ON source_observations (ed2k_hash);
CREATE INDEX idx_source_observations_user_hash ON source_observations (user_hash);

CREATE TABLE match_decisions (
    id INTEGER PRIMARY KEY,
    ed2k_hash TEXT NOT NULL REFERENCES files (ed2k_hash),
    target_id TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    tier TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    node_id TEXT NOT NULL
);

CREATE INDEX idx_match_decisions_ed2k_hash ON match_decisions (ed2k_hash);

CREATE TABLE file_verifications (
    id INTEGER PRIMARY KEY,
    ed2k_hash TEXT NOT NULL REFERENCES files (ed2k_hash),
    verdict TEXT NOT NULL,
    real_meta TEXT,
    checks TEXT,
    verified_at TEXT NOT NULL,
    node_id TEXT NOT NULL
);

-- WARNING future migrations: a rebuild-style migration (CREATE new table /
-- INSERT … SELECT / DROP / ALTER … RENAME) drops the triggers ALONG WITH the old table —
-- any such migration MUST recreate them (nothing will fail loudly otherwise).

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

CREATE TRIGGER file_observations_no_update
BEFORE UPDATE ON file_observations
BEGIN
    SELECT RAISE(ABORT, 'file_observations is append-only');
END;

CREATE TRIGGER file_observations_no_delete
BEFORE DELETE ON file_observations
BEGIN
    SELECT RAISE(ABORT, 'file_observations is append-only');
END;

CREATE TRIGGER sources_no_update
BEFORE UPDATE ON sources
BEGIN
    SELECT RAISE(ABORT, 'sources is append-only');
END;

CREATE TRIGGER sources_no_delete
BEFORE DELETE ON sources
BEGIN
    SELECT RAISE(ABORT, 'sources is append-only');
END;

CREATE TRIGGER source_observations_no_update
BEFORE UPDATE ON source_observations
BEGIN
    SELECT RAISE(ABORT, 'source_observations is append-only');
END;

CREATE TRIGGER source_observations_no_delete
BEFORE DELETE ON source_observations
BEGIN
    SELECT RAISE(ABORT, 'source_observations is append-only');
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

CREATE TRIGGER file_verifications_no_update
BEFORE UPDATE ON file_verifications
BEGIN
    SELECT RAISE(ABORT, 'file_verifications is append-only');
END;

CREATE TRIGGER file_verifications_no_delete
BEFORE DELETE ON file_verifications
BEGIN
    SELECT RAISE(ABORT, 'file_verifications is append-only');
END;
