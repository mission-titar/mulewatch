# Handoff: stage 1, generic identity

Drafted by block 200, corrected by the closing block (210).

## State

Tier Spec lot, spec `agents/specs/2026-10-09-stage1-generic-identity.md` (approved 2026-10-09). A stack of
21 blocks, PRs #103, #104 and #106 to #124 (there is no #105), plus the closing block `docs/stage1-closing` on
top. Nothing is merged and no release follows (D16): the node pulls `latest`, which stays on 4.x.
`catalog.db` is at schema version 9, `local.db` unchanged at 5.

## What was built

- **The runner** (block 10): a `-- migration: no-transaction` first line runs a script outside the
  transaction and stamps after it; migrations sort through temporary files (`temp_store = FILE`); the runner
  restores `secure_delete` and `cache_size` after each script. The rules for writing a migration are in
  `docs/contributing/architecture.md`, section 8.1.
- **What went** (blocks 20 to 90): `networks.py` became `channels.py`; the `compact` command, its engine and
  `domain/retention/`; the ranges in merge, the webui and the sightings; catalog 0006 drops `sources`,
  `source_observations` and `file_observation_ranges`, and refuses a catalog whose ranges hold a row.
- **Test builders** (blocks 100, 110): `tests/catalog_rows.py` seeds every catalog row, and the webui tests
  run on the real schema through `open_catalog`. Blocks 140 and 180 changed the schema under them without
  touching most callers.
- **Variants** (blocks 130, 140): the `raw_meta` fold, `content_hash` and their memoized SQL forms; catalog
  0007 stores observations as `observation_variants` plus `observations` (`WITHOUT ROWID`, integer
  microseconds).
- **Identity** (blocks 150 to 190): `FileKey(network, native_id)` and its `file_id` UUID v5 through the
  catalog port, the decision events and `FileObservation`; catalog 0008 keys `files`, the variants and
  `match_decisions` by `file_id`; the webui serves `/files/{file_id}`. The download side keeps the eD2k hash
  (D10), joined at `download_decisions()`.
- **`VACUUM`** (block 200): catalog 0009, the directive's only consumer; `docs/limits.md` (first boot after
  the upgrade), `docs/operate.md` (growth, merging an older snapshot), `docs/troubleshooting-start.md` (the
  disk check under Docker Desktop).
- **Closing** (block 210): the holistic findings below; 0009 also truncates the WAL; the runner logs
  `migration N: applying` per script; `BACKLOG.md` reconciled; the `/files?q=` accepted limit in the spec's
  section 7.

## Decisions taken during Act

- **Block 180 split at its file bound** into 175 (test preparation, the `file_id()` SQL function) and 180:
  in one piece it measured 697 lines and 23 files, and the spec waived only the line bound. The lead's call
  per the workflow; row 175 carries a `(Corrected: ...)` marker.
- **The `/files` default shape is "orig + skip"**: the first draft failed its D18 bound before block 140
  (real copy 121.7 ms vs `main` 77.2 ms), and the operator chose to try other query shapes rather than stop.
  "skip" drops the latest-sighting join from statements that do not need it (71.9 ms on the real copy, the
  reorder variant was not kept).
- **`/files?q=` is slower and accepted**: 150.8 ms vs 98.0 ms on the real copy at block 140's tip. The
  operator (2026-10-09): not a major path, improve later if needed. Written as an accepted limit in the
  spec's section 7, not in the backlog.
- **The index on `match_decisions (file_id)` is not carried by 0008**: a prefix of the decision index (lead,
  block 180).
- **The fold appends `codec`, `file_type` and `complete_source_count` with their native types** (`null`, an
  integer), not as strings, so an absent codec cannot be confused with one spelled `null` (block 130).

## Measurements (D18, real catalog copy, 2026-10-09)

A copy of the node's catalog (11.65M observations, 5.3 GB), the image over a Docker Desktop bind mount,
`--memory 2g`. Reports in `.reviews/stage1-d18-before-140.md`, `-at-140.md`, `-before-180.md` (gitignored).

| Step | Wall time | Peak `anon` | Peak disk above start |
|---|---|---|---|
| 0006 + 0007 | 169.7 s and 234.3 s (two runs) | 288 MiB | +0.44 GB |
| 0008 | 10.4 s | 324 MiB | +0.24 GB |

Lossless at 0007: `mismatches: 0`, shown failing at 1 (one observation deleted) and 23,296,222 (a fold
without `file_type`). 0008: counts and an observations checksum equal before and after, every `file_id`
equal to a stdlib `uuid5` of the spec's namespace. Pages at block 140's tip: `/files` 85.5 ms vs 88.6 ms
(thin, within run-to-run spread), busiest detail page 377.2 ms vs 554.8 ms.

## Pitfalls

- **Stale `.pyc` after mutation testing**: a mutation that keeps the file's size, reverted within the same
  second, leaves Python trusting the mutated bytecode. Delete `__pycache__` when tests fail unexpectedly.
- **`secure_delete` is compiled in on the image (3.46.1) and the host (3.53.4), not in the gate's
  (3.49.1)**. The migration tests force it `ON` through `older_catalog.forcing_secure_delete`.
- **`older_catalog.open_catalog_at` calls the runner through the module**, so a monkeypatch of
  `connection_module._configure` reaches it; a `from ... import` would silently bind the original.
- **sqlfluff's sqlite dialect does not parse `VACUUM`**: 0009 carries `-- noqa: PRS` on that line. A
  comment starting `-- sqlfluff` is read as an inline config statement, and warns.
- `NOTHING` is an SQLite keyword (`SELECT NULL AS nothing` is a syntax error). mypy `--strict` rejects
  `Network.ED2K == "ed2k"` as a non-overlapping comparison.
- **The SQL timestamp rebuild holds for positive instants only**, and `iso_to_micros` refuses a naive stamp.
- **Under Docker Desktop, `docker info`'s `DockerRootDir` is a path inside the VM**: `df` on the host
  measured the host's `/` (49 GB free), while `df` in a container measured the VM's sparse disk image (1.5 TB
  free). The troubleshooting box now measures from a container and points at the host disk holding the image.
- **merge refuses a source at an older schema version** and never migrates it; `merge --into X X` opens X
  with `open_catalog`, which migrates it in place (checked on a catalog stamped 5). A refusal of
  `open_catalog` on the output exits as a `MergeError`.
- **The SQL aliases `last_seen` and `source_count_max`** of `LATEST_SIGHTING_CTE` stay: the webui's sorts
  and tests name them, though `Sighting` itself is now one observation's fields.
- The list's "Hash" column header stays: it shows the native id's prefix.

## Holistic review

`.reviews/stage1-generic-identity-holistic.md` (gitignored), over the stack at `1886959`: 0 CRITICAL, 2
MAJOR, 13 MINOR (its header counts 12). Each exit:

| Severity | Finding | Exit |
|---|---|---|
| MAJOR | `AGENTS.md`'s merge invariant says it never mutates a DB in place | Fixed, in the reviewer's wording |
| MAJOR | D18's second run, at the top of the stack, not done | PENDING: the lead's run |
| MINOR | merge's CLI lets `open_catalog`'s `MigrationError` out as a traceback | Fixed: wrapped into `MergeError`, test on an output newer than the code |
| MINOR | Two stale merge docstrings (no migration replayed, no domain dependency) | Fixed |
| MINOR | The WAL the `VACUUM` grew stays until the next restart | Fixed: 0009 runs `wal_checkpoint(TRUNCATE)`, test watched failing (WAL 45,352 B, bound one frame) |
| MINOR | No log line during minutes of migration | Fixed: `migration N: applying` per script, named in `docs/limits.md` |
| MINOR | The SQL console shows BLOBs as Python reprs | Fixed: lowercase hex, test; `docs/operate.md` says the console shows `file_id` as the URL takes it |
| MINOR | 0006's refusal absent from the operator docs | Fixed: a box in `docs/troubleshooting-start.md` (pin 4.1.0, open an issue) |
| MINOR | `agents/workflow.md:28` names the `compact` CLI | Fixed |
| MINOR | `Sighting`'s aggregate fields are degenerate | Fixed: `name`, `observed_at`, `source_count`, non-optional `keyword` |
| MINOR | Two catalog log lines lost the `key=value` form | Fixed: `file=%s:%s`, asserted in both failure tests (watched failing) |
| MINOR | Three repository docstrings say "hash" | Fixed, with a fourth ("Every seen hash's") |
| MINOR | The memoized SQL functions stay on the crawler's connection | Fixed by the comment, the smaller change: re-registering uncached needs a test of the uncached state for one memo of about 13.5k keys |
| MINOR | The reader keeps `temp_store = MEMORY`, the runner moved to `FILE` | Fixed on the operator's answer: the webui reader sorts through files too, test watched failing (2 for 1) |
| MINOR | The handoff's pull request count | Fixed |

Class 1 fixes found on the way: the merger's module docstring still said migration `0001` lays the schema;
0009's comment starting `-- sqlfluff` made `sql-lint` warn (both fixed).

## Not validated

- **The top of the stack on the real copy** (D18's second run, the lead's): PENDING. 0006 to 0009 in one
  boot, 0009's duration, memory and disk, the WAL after it, the catalog file under 0.5 GB after `VACUUM`,
  the lossless check through 0008, the pages again (`/files` passed by a thin margin at block 140).
  `docs/limits.md` still gives the spike's synthetic 0.21 GB final size.
- The troubleshooting `df` from a container on a native Docker Engine (checked under Docker Desktop only).
- Merging real node catalogs (no integration suite covers merge); the webui pages in a browser.

## Wrap counts

PENDING the lead's final counts. So far: 0 fix-backs, 0 cascaded rebases, one extra run on #117 for a
docstring dash, no body called unreadable. Block 180 split into 175 and 180 before any push, which cost no
run.

## Next

The operator reviews and merges the stack (`gh stack merge --rebase`), after the lead rebases it onto `main`.
No release (D16). Then the stage 2 spec: generic search and status ports, and the download side's move to
`FileKey` (D10).
