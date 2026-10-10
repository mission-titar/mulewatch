-- catalog.db, migration 0004: seek index for the webui's latest-observation read.
--
-- Supersedes migration 0003's decision to leave file_observations with only its single-column
-- idx_file_observations_ed2k_hash. 0003 justified that with "the window's per-hash sort is
-- cheap (few observations per file)" and "a composite there was measured unused by the
-- planner". The real node refutes both: file_observations is append-only and re-observes every
-- file on each cycle, reaching 1183660 rows for 1402 files (~844 observations per file, not
-- "few"). The composite reads as unused only while the query keeps a ROW_NUMBER() window,
-- which must number every row and so walks the whole table whatever indices exist (measured:
-- 2.8s per query, ~10s per /files render). Paired with the seek form of latest_obs in
-- webui/adapters/catalog_read.py, the planner does use it, as a covering index seek: the two
-- changes only pay off together, which is why 0003's isolated measurement looked negative.
--
-- (ed2k_hash, observed_at) is deliberately 2 columns: id is INTEGER PRIMARY KEY, hence the
-- rowid, which SQLite already stores as every index's implicit trailing key, so it serves the
-- "ORDER BY observed_at DESC, id DESC" tiebreak without being named (measured: same covering
-- seek, a smaller index on this hot append path).
--
-- ADDITIVE DDL: no table rebuilt, the append-only triggers from 0001 are untouched.

CREATE INDEX idx_file_observations_hash_observed
ON file_observations (ed2k_hash, observed_at);
