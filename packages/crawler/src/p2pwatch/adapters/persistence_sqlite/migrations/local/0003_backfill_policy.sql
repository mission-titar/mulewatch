-- local.db — migration 0003: policy fingerprint marker (spec §7.1 — startup backfill gate).
-- A single-row table (id=1 enforced by the CHECK) upserted by set_last_backfill_policy.
-- Mutable, unlike catalog.db: the marker is overwritten on every successful backfill pass.

CREATE TABLE backfill_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    policy_sha256 TEXT NOT NULL
);
