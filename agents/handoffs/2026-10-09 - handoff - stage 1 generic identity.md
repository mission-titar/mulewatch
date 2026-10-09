# Handoff: stage 1, generic identity

Draft by block 200, for the closing block (210) to correct.

## State

Tier Spec lot, spec `agents/specs/2026-10-09-stage1-generic-identity.md` (approved 2026-10-09). A stack of
21 blocks, pull requests #103 to #123 plus block 200's, top branch `feat/vacuum-after-migration`; the
closing block `docs/stage1-closing` is not cut yet. Nothing is merged and no release follows (D16): the node
pulls `latest`, which stays on 4.x. `catalog.db` is at schema version 9, `local.db` unchanged at 5.

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

## Decisions taken during Act

- **Block 180 split at its file bound** into 175 (test preparation, the `file_id()` SQL function) and 180:
  in one piece it measured 697 lines and 23 files, and the spec waived only the line bound. The lead's call
  per the workflow; row 175 carries a `(Corrected: ...)` marker.
- **The `/files` default shape is "orig + skip"**: the first draft failed its D18 bound before block 140
  (real copy 121.7 ms vs `main` 77.2 ms), and the operator chose to try other query shapes rather than stop.
  "skip" drops the latest-sighting join from statements that do not need it (71.9 ms on the real copy, the
  reorder variant was not kept).
- **`/files?q=` is slower and accepted**: 150.8 ms vs 98.0 ms on the real copy at block 140's tip. The
  operator (2026-10-09): not a major path, improve later if needed. The closing block writes it as an
  accepted limit in the spec.
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
- **sqlfluff's sqlite dialect does not parse `VACUUM`**: 0009 carries `-- noqa: PRS` on that line.
- `NOTHING` is an SQLite keyword (`SELECT NULL AS nothing` is a syntax error). mypy `--strict` rejects
  `Network.ED2K == "ed2k"` as a non-overlapping comparison.
- **The SQL timestamp rebuild holds for positive instants only**, and `iso_to_micros` refuses a naive stamp.
- **Under Docker Desktop, `docker info`'s `DockerRootDir` is a path inside the VM**: `df` on the host
  measured the host's `/` (49 GB free), while `df` in a container measured the VM's sparse disk image (1.5 TB
  free). The troubleshooting box now measures from a container and points at the host disk holding the image.
- **merge refuses a source at an older schema version** and never migrates it; `merge --into X X` opens X
  with `open_catalog`, which migrates it in place (checked on a catalog stamped 5).
- `Sighting` keeps its aggregate fields (`names`, `observation_count`, min and max), each now one
  observation's value; the webui reads them. Simplify when its reads are next rewritten.
- The list's "Hash" column header stays: it shows the native id's prefix.

## Not validated

- **The top of the stack on the real copy** (D18's second run, the lead's): 0006 to 0009 in one boot,
  0009's duration, memory and disk, the catalog file under 0.5 GB after `VACUUM`, the lossless check through
  0008, the pages again (`/files` passed by a thin margin at block 140). `docs/limits.md` then gains the
  `VACUUM` figure; it gives only the spike's synthetic 0.21 GB final size today.
- The troubleshooting `df` from a container on a native Docker Engine (checked under Docker Desktop only).
- Merging real node catalogs (no integration suite covers merge); the webui pages in a browser.

## Wrap counts

To fill by the closing block: fix-backs, cascaded rebases, runs they re-triggered, unreadable bodies.

## Next

The closing block: the holistic findings with their exits; `BACKLOG.md` reconciled (stage 1 deleted, stage 2
gains the download side's move to `FileKey`, stage 3 loses its `v5.0.0`); the `/files?q=` accepted limit in
the spec. Then the stage 2 spec (generic search and status ports, D10's download side).
