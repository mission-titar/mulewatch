-- local.db, migration 0005: last_seen_at on downloads (lost-download TTL spec, 2026-09-13).
--
-- The backfill is load-bearing: expire_lost compares last_seen_at against a cutoff, and
-- NULL < cutoff is NULL, never true. Without it a pre-upgrade row could never expire.

ALTER TABLE downloads ADD COLUMN last_seen_at TEXT;

UPDATE downloads SET last_seen_at = queued_at;
