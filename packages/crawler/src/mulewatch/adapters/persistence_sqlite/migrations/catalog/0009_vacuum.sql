-- migration: no-transaction
-- catalog.db, migration 0009: give back the pages 0006 to 0008 freed (stage 1 spec, D7 and D8).
-- Outside any transaction, which VACUUM requires; replaying it after a stop is harmless.
-- sqlfluff's sqlite dialect does not parse VACUUM, hence the noqa.
VACUUM; -- noqa: PRS
