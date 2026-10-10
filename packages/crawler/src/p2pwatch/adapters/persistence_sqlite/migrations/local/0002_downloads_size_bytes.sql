-- local.db — migration 0002: application-level disk cap (spec download §7 — DECISION D6).
-- Adds the file size to the downloads table (existing, migration 0001). The cap
-- stays a simple query (sum of size_bytes over ACTIVE downloads). DEFAULT 0 required by
-- ALTER TABLE ADD COLUMN NOT NULL on a possibly non-empty table.

ALTER TABLE downloads ADD COLUMN size_bytes INTEGER NOT NULL DEFAULT 0;
