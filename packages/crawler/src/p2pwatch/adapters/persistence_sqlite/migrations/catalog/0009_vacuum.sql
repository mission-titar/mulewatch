-- migration: no-transaction
-- catalog.db, migration 0009: give back the pages 0006 to 0008 freed (stage 1 spec, D7 and D8).
-- Outside any transaction, which VACUUM requires; replaying it after a stop is harmless.
-- The sqlite dialect of sqlfluff does not parse VACUUM, hence the noqa.
VACUUM; -- noqa: PRS
-- The crawler holds its connection for good, so the WAL the VACUUM grew would never shrink.
PRAGMA wal_checkpoint(TRUNCATE);
