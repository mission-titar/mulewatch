-- catalog.db, migration 0006: drop the tables nothing writes any more (stage 1 spec, D5 and D6).
-- A catalog holding a compacted day refuses; RAISE only runs in a trigger, hence the guard table.

CREATE TABLE migration_0006_guard (ranges INTEGER NOT NULL);

CREATE TRIGGER migration_0006_guard_refuses_ranges
BEFORE INSERT ON migration_0006_guard
WHEN new.ranges > 0
BEGIN
    SELECT RAISE(
        ABORT,
        'file_observation_ranges holds rows: a compacted day cannot become observations'
    );
END;

INSERT INTO migration_0006_guard (ranges)
SELECT COUNT(*) FROM file_observation_ranges;

DROP TABLE migration_0006_guard;

-- Children before their parent; each DROP takes its table's triggers and indexes with it.
DROP TABLE source_observations;
DROP TABLE sources;
DROP TABLE file_observation_ranges;
