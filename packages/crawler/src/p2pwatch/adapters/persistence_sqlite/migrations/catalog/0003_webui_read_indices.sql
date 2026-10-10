-- catalog.db — migration 0003: composite read-path indices for the webui explorer/dashboard.
-- The webui derives "latest row per group" (decisions per (hash, target), latest verification
-- per hash) with ROW_NUMBER() window functions; these indices let SQLite drive those scans by
-- index instead of the earlier correlated COUNT(*)=0 subqueries (quadratic on accumulated
-- history). file_verifications gets its FIRST ed2k_hash index (it had none, forcing a full
-- table scan on every /files render, the worst offender). file_observations keeps only its
-- existing idx_file_observations_ed2k_hash: the window's per-hash sort is cheap (few
-- observations per file) and a composite there was measured unused by the planner, so it is
-- not added to that hot append path. ADDITIVE DDL: no table rebuilt, the append-only triggers
-- from 0001 are untouched.
--
-- SUPERSEDED (2026-07-16), on file_observations only: the reasoning in the paragraph above is
-- refuted by the real node. There are ~844 observations per file (1183660 rows for 1402
-- files), not "few", and the composite reads as unused only while the query keeps a
-- ROW_NUMBER() window. Migration 0004 adds it and reshapes the read to seek. The indices this
-- migration DOES create are unaffected. See 0004_file_observations_latest_index.sql.

CREATE INDEX idx_match_decisions_hash_target_decided
ON match_decisions (ed2k_hash, target_id, decided_at);

CREATE INDEX idx_file_verifications_hash_verified
ON file_verifications (ed2k_hash, verified_at);
