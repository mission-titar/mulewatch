-- local.db, migration 0008: the downloads' lifecycle columns (stage 2 spec, D12, D19).
-- bytes_done stays NULL on existing rows: their progress before 0008 is unknown, and a 0 would
-- stamp last_progress_at on the first reading. New rows are queued with 0.

ALTER TABLE downloads ADD COLUMN bytes_done INTEGER;

ALTER TABLE downloads ADD COLUMN last_progress_at TEXT;

ALTER TABLE downloads ADD COLUMN waiting_reason TEXT;

ALTER TABLE downloads ADD COLUMN failure_reason TEXT;
