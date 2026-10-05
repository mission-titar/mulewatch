# Handoff: one module for every observation read, merge and compact never count twice

## State

Branch `refactor/shared-observation-reads`, gate green. Spec:
`agents/specs/2026-10-05-shared-observation-reads.md` (approved 2026-10-05). Not released.

## What was built

- `adapters/persistence_sqlite/sightings.py`: `latest_sighting`, `iter_latest_sightings`,
  `known_names`, `sightings`, and the `LATEST_SIGHTING_CTE` / `NAME_MATCH_CLAUSE` fragments of the
  webui paged list. The raw-else-range fallback is written there only. `Sighting` lives in
  `domain/observation.py`: the webui domain and the persistence adapter both reach it there.
- The crawler repository and the webui read adapter delegate to it; the file detail shows one
  timeline, a compacted day marked as such. Rule in `AGENTS.md`: only this module reads the two
  tables, besides `compact` and `merge`.
- A raw row is covered by a range (same hash, its UTC day, its node in `node_ids`): `merge` copies
  every source's ranges first and skips covered raw rows (CLI logs the count); `compact` neither
  copies nor buckets a covered raw row.

## Timings on the node snapshot

No regression against PR #90: latest sighting of every file 0.02 s, known names of every file
22 s, webui list page 0.05 s. `merge(X, compact(X))` 4.5 s instead of 8.8 s (fewer inserts).

## Pitfalls

- The covered test is one uncorrelated `IN` over `hash || day || node`: a correlated `EXISTS` made
  merge 11x slower, a row-value `NOT IN` 100x. Hash and day are fixed width, so the key is safe.
- Merge now commits per source and per pass; a failed merge is re-run safely (idempotent).

## Open, for the operator

- `merge --into` an existing output that already holds raw rows, then a source brings ranges
  covering them: both forms stay (append-only) until the next compaction drops the covered rows.
- `covers()` in `domain/retention/` only runs in a parity test; production uses its SQL twin.
- The detail page renders every sighting: 12.9 MB for the heaviest file (38,593 rows). Already
  true before this change.
