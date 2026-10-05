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
- `known_filenames` and the re-evaluation read the names kept in `file_observation_ranges`, so a
  compaction can no longer drop an alias the file-level vetoes need.

## Pitfalls

- A plan test asserts no `SCAN` of `file_observations`: the old query also used the index (inside
  its anti-join), so "the index appears" alone proved nothing.

## Leads, not done

- Other reads still ignore ranges (harmless until the operator compacts, which is not planned):
  `last_observation` (a range-only download candidate gets no ed2k link), the webui list, search,
  timeline and explanation.
- Storage itself: `BACKLOG.md`, lossless compact observation storage.
