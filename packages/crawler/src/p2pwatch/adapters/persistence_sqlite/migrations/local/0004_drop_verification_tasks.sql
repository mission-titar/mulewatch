-- local.db, migration 0004: drop the verification queue (scope-reduction spec, 2026-09-13).
-- DROP TABLE takes the two partial indices with it.
--
-- The UPDATE is load-bearing, not cosmetic: DownloadState lost its QUARANTINED member, so
-- download_repository's DownloadState(row[1]) would raise ValueError on any surviving row and
-- wedge the download loop. Both values are terminal for the disk cap: no behaviour changes.

DROP TABLE verification_tasks;

UPDATE downloads SET state = 'completed'
WHERE state = 'quarantined';
