-- catalog.db, migration 0005: drop file_verifications (scope-reduction spec, 2026-09-13).
-- Content verification left the project's scope: no writer remains and the live node holds no
-- verdict row. DROP TABLE takes the table's append-only triggers and its index with it
-- (verified empirically, SQLite 3.47), so listing them here would be redundant DDL.

DROP TABLE file_verifications;
