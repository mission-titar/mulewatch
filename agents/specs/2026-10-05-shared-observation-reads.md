# One module for every observation read, and a merge that never counts a day twice

- Date: 2026-10-05
- Status: DRAFT (awaiting operator review)
- Scope: move every read of `file_observations` / `file_observation_ranges` into one shared
  module, so a reader cannot forget the compacted form; stop `merge` and `compact` from counting
  the same observations twice when both forms of a day meet
- Builds on: PR #90 (every read made range-aware one by one, `test_compacted_catalog.py`)
- Related: `adapters/persistence_sqlite/catalog_repository.py`, `webui/adapters/catalog_read.py`,
  `merge/merger.py`, `compact/compactor.py`, `domain/retention/buckets.py`

## 1. Why

An observation exists in two forms: a raw row in `file_observations`, or, once compacted, a share
of a per-day row in `file_observation_ranges`. Reads were written against the raw table only, and
six of them silently lost the compacted days (PR #90 fixed them one by one). The fix left the
range fallback rule written twice (crawler SQL and webui SQL), held together only by an
end-to-end test. The next reader will make the same mistake unless the two forms stop being
visible to readers at all.

`merge` has the same blind spot on the write side. Merging a catalog `X` with its own compacted
copy `compact(X)` keeps every day before the cutoff twice: raw rows from `X`, ranges from the
copy. The merger dedups each table against itself, never across the two. Compacting that output
again turns the raw rows into byte-identical ranges, so `observation_count` and the source sums
double.

## 2. The model: a sighting

A **sighting** is what a reader wants, whatever the storage form:

| Field | From a raw observation | From a range |
|---|---|---|
| `ed2k_hash` | as stored | as stored |
| `names` | `(filename,)` | `filenames` |
| `observation_count` | 1 | as stored |
| `first_seen`, `last_seen` | `observed_at`, twice | `first_observed_at`, `last_observed_at` |
| `source_count_min`, `_max` | `source_count`, twice | as stored |
| `size_bytes`, media fields | as stored | `files.size_bytes`, no media |
| `compacted` | false | true |

A raw observation is a one-observation range. Readers get sightings and never learn which table
a row came from, except through `compacted` when they want to show it.

## 3. The module

`adapters/persistence_sqlite/sightings.py`, shared by the crawler repository and the webui read
adapter (the webui already imports from `persistence_sqlite`):

- `latest_sighting(conn, hash) -> Sighting | None`: the latest raw observation, else the latest
  range. The fallback rule lives here only.
- `iter_latest_sightings(conn) -> Iterator[Sighting]`: one per file, sorted by hash, for the
  re-evaluation.
- `known_names(conn, hash) -> tuple[str, ...]`: every distinct name, both forms.
- `sightings(conn, hash) -> tuple[Sighting, ...]`: the file's timeline, both forms, oldest first.
- `LATEST_SIGHTING_CTE` and `NAME_MATCH_CLAUSE`: the two SQL fragments the webui's paged list
  composes into its own joins, sort and pagination (a method cannot be joined in SQL). Same
  rule, same module.

Each function writes its own targeted query: index seek on the raw table, ranges only as the
fallback (`COALESCE`), as measured in PR #90. No SQL view: a `UNION ALL` would put the latest
row lookup at the mercy of the planner, and the dedicated query is already proven.

Callers:

- `SqliteCatalogRepository`: `last_observation`, `known_filenames`, `iter_reevaluation_rows`
  delegate to the module. Writes (`record_observation`) stay in the repository.
- `catalog_read.py`: the list, count and tier counts use `LATEST_SIGHTING_CTE`; search uses
  `NAME_MATCH_CLAUSE`; the detail page uses `sightings` and `known_names`.
- The file detail shows **one** timeline of sightings, a compacted day marked as such, instead of
  today's raw table plus "Compacted days" table.

**The rule:** only `sightings.py` reads the two tables, plus the two tools that manipulate the
forms themselves (`compact`, `merge`). Written in `AGENTS.md` (design invariants) and in the
module docstring. No grep gate in the test suite (operator preference against them); the
behavioural guard is `test_compacted_catalog.py`, which keeps running every read on a catalog
built by the real compactor.

## 4. Never twice: merge and compact

A raw observation is **covered** by a range when the range has the same hash, its `bucket` is the
observation's UTC day, and its `node_ids` contains the observation's `node_id`. Compaction only
buckets whole past days, so a covered raw row is the same observation the range already counts.

- `merge`: a raw row covered by a range in the output (from any source) is not copied. The
  merger copies ranges first, then raw rows minus the covered ones, and reports how many it
  skipped.
- `compact`: same rule on its input, which may already hold both forms (a merged catalog): a
  covered raw row is not bucketed again.
- The predicate lives once, in `domain/retention/` (pure), with the SQL form next to the two
  tools' queries; both tools are tested against the double-count scenario of §1.

Why keep the range rather than the raw rows: the tables are append-only, and the range is the
form the operator chose for those days. Nothing is lost: the range already counts them.

Days of **other** nodes are untouched: a range for node A and raw rows of node B on the same day
are different observations, both kept.

## 5. Proof

- TDD, red first: each `sightings.py` function on raw-only, range-only and mixed files; the
  covered predicate (same node, other node, other day, other hash); merge of `X` with
  `compact(X)` equal to `compact(X)` (counts, no duplicate day); compact of that merged output
  with no doubled count.
- `test_compacted_catalog.py` stays green unchanged, apart from the unified timeline.
- Plan tests keep the index seek of the latest-sighting reads (no `SCAN` of the raw table).
- Timing on the node snapshot, same reads as PR #90, no regression.

## 6. Out of scope

- Lossless compact storage by default (`BACKLOG.md`).
- Searching every raw alias (a `LIKE` over 10.9M rows); search keeps the latest name and every
  compacted name.
