# Handoff: catalog re-evaluation speed and progress

## State

Branch `fix/catalog-reevaluation-speed`, gate green. Not released.

## Why

Validating the matcher vetoes on the node: the startup re-evaluation took 4 minutes before its first
decision log, and had not finished in 50 minutes on a snapshot. The node's catalog holds 10.9M
observations for 1924 files (4.9 GB in three months), not the 1.18M an older comment states.

## What was built

- `_SELECT_REEVALUATION_ROWS` drives from `files` and seeks each hash's latest observation through
  `idx_file_observations_hash_observed`, like the webui's `latest_obs`. The old correlated
  `COUNT(*)` anti-join was quadratic per hash. On the snapshot: under 0.1 s for all 1924 rows; the
  per-hash `known_filenames` reads then dominate (23 s in total).
- `reevaluate_catalog` logs `catalogue re-evaluation: N/T files, W rows written` every 200 files.
- Every catalog read sees `file_observation_ranges`: re-evaluation rows, `known_filenames`,
  `last_observation` (a range-only download candidate keeps its ed2k link), the webui list, search,
  timeline ("Compacted days") and explanation. A range-only file shows its source count as
  "unknown", never an invented number. `test_compacted_catalog.py` runs the real compactor and
  checks every read, and that the webui and the crawler agree on a veto from a compacted alias.

## Pitfalls

- A plan test asserts no `SCAN` of `file_observations`: the old query also used the index (inside
  its anti-join), so "the index appears" alone proved nothing.
- Search matches the latest raw name and every compacted name, not older raw aliases (a LIKE over
  10.9M rows).

## Leads, not done

- The range fallback rule is written twice (crawler and webui SQL), held together by the
  end-to-end test: the next spec moves every observation read into one shared module.
- `merge` double-counts when it merges a catalog with its own compacted copy (raw rows and ranges
  of the same days, no cross-table dedup); a later compaction doubles the counts. Same spec.
- Storage itself: `BACKLOG.md`, lossless compact observation storage.
