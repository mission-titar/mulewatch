-- local.db, migration 0006: the cycle's scheduler state (stage 2 spec, D7, D19).
-- channel_backoff goes too: its amuled:global keys are read by no task, and a reset costs one
-- backoff at most.

DELETE FROM scheduler_state
WHERE key IN ('cycle_index', 'last_full_cycle_at', 'channel_backoff');
