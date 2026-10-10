-- local.db — migration 0001: operational state (spec data-model §5; spec MVP §12).
-- NEVER merged: only catalog.db crosses the node boundary (invariant §11).
-- The partial UNIQUE index makes enqueue idempotent (at most ONE active task per hash).

CREATE TABLE node_runtime (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE verification_tasks (
    id INTEGER PRIMARY KEY,
    ed2k_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'in_progress', 'done', 'dead_letter')),
    attempts INTEGER NOT NULL DEFAULT 0,
    enqueued_at TEXT NOT NULL,
    claimed_at TEXT,
    lease_until TEXT
);

CREATE UNIQUE INDEX idx_verification_tasks_active_hash
ON verification_tasks (ed2k_hash)
WHERE status IN ('pending', 'in_progress');

-- The claim scan (oldest pending task) must cost according to the ACTIVE depth
-- of the queue, not according to the all-time history.
CREATE INDEX idx_verification_tasks_pending
ON verification_tasks (enqueued_at)
WHERE status = 'pending';

-- DECISION (audit 2026-06-23 / test-gaps#3): ``state`` has NO CHECK constraining the
-- DownloadState enum. The only reachable writer is ``record_queued`` (insert with 'queued'
-- as a literal) and ``set_state(state.value)`` typed ``DownloadState`` (closed enum). A value
-- outside the enum is therefore unreachable in normal operation. A CHECK CONSTRAINT would be
-- defense-in-depth hardening (out-of-band mutation / corruption / future migration);
-- the present decision is NOT to add it (migration rigidity vs. gain trade-off).
CREATE TABLE downloads (
    ed2k_hash TEXT PRIMARY KEY,
    target_id TEXT NOT NULL,
    state TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE scheduler_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
