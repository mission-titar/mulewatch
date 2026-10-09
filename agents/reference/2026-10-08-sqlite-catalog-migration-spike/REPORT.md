# SQLite migration spike: file_id / observation_variants / observations

Date 2026-10-08 (Q7 added the same day). All scripts and raw outputs live next to this file
(`agents/reference/2026-10-08-sqlite-catalog-migration-spike/ (outputs/ and scripts.md)`).

## Environment and method

- Host: SQLite **3.53.4**, Python 3.14.7, 12 cores, 30 GB RAM (about 8 GB free during the runs, swap in use).
- Shipped image `ghcr.io/mission-titar/mulewatch:latest` (digest `sha256:6350b2f7...`): SQLite **3.46.1**,
  Python 3.13.5. Docker here is **Docker Desktop** (linuxkit VM), like the live node.
- `deploy/base.compose.yml` today: `mem_limit: 2g`, `pids_limit: 512`, **no tmpfs at all** (the 32m
  `/tmp` tmpfs in the brief is gone; container `/tmp` is the overlay writable layer). The catalog sits on
  the bind mount `./data:/data`.
- **Where the DBs lived (deviation from the brief, flagged to the lead):** the scratchpad is a RAM tmpfs
  with 2.4 GB free, too small for 11.5M rows. The 2M-row runs ran in the scratchpad (so on RAM: their
  timings are optimistic); the 11.5M-row DBs ran in `/home/geoffrey/.cache/mulewatch-spike-db/` (LUKS
  disk), now deleted. Peak spike disk use: about 13 GB.
- Synthetic data: `gen_old.py OUT ROWS` applies the real migrations 0001..0005, then appends rows in
  time order **with the indexes in place** (so their fill is that of an append-grown DB). 1933 files,
  7433 distinct variants (exact at 11.5M), file popularity lognormal: the busiest file gets 37952
  observations at 11.5M (the node: ~38k). 20 distinct `raw_meta` padded to 170 bytes, one node_id,
  strictly increasing microsecond ISO `observed_at`. Command:
  `python3 gen_old.py pristine.db 11500000` (138 s; `gen_11m5.txt`).
- Calibration against the node: synthetic indexes 176 B/row vs node 174 B/row (good); synthetic table
  356 B/row vs node 281 B/row (ours is 27 % fatter: the start size is 6.12 GB instead of 5.25 GB). This
  only affects the "old" side; the new tables do not depend on it.
- `complete_source_count` varies with `source_count` in the synthetic data, so folding it into
  `raw_meta` (as the brief asks) grows the variants from 7433 to **12846** at 11.5M (1.7x). How much
  it grows on the node depends on how `complete_source_count` really varies (synthetic, not measured);
  the brief's "13266 distinct variants+source counts" suggests ~13k variants rather than 7.4k. Count it
  on the node before sizing anything on the variant count.

## Q1. Storage

Script `q1_storage.py`. Commands: `python3 q1_storage.py old pristine.db` (`q1_old_11m5.txt`) and
`python3 q1_storage.py new work.db` on the migrated DB (`q1_new_11m5.txt`). Measured at **11.5M rows**
(no extrapolation; the 2M runs `q1_*_2m.txt` agree within 1 B/row). Each new layout is built in its own
file by appending rows in `observed_at` order with its indexes already present (= what the batched
migration produces and how the table grows afterwards), then VACUUMed for the dense lower bound.

| Layout | B/row, append-grown | GB at 11.5M | B/row after VACUUM |
|---|---|---|---|
| OLD synthetic: table 355.6 + idx hash_observed 82.5 + observed_at 47.4 + ed2k_hash 46.4 | 531.9 | 6.12 | |
| OLD real node (from the brief) | ~455 | 5.23 | |
| (a) rowid table + `(variant_id, observed_at)` | 21.9 + 23.2 = **45.1** | **0.52** | 42.3 |
| (b) `WITHOUT ROWID`, `PRIMARY KEY (variant_id, observed_at, source_count)` | **20.5** | **0.24** | 18.0 |
| (c-a) (a) + index `(observed_at)` | 45.1 + 19.8 = **64.9** | **0.75** | 59.6 |
| (c-b) (b) + index `(observed_at)` | 20.5 + 20.5 = **41.1** | **0.47** | 36.1 |

Plus `observation_variants` 4.8 MB (12846 rows) and its two indexes 0.6 MB, `files` 0.3 MB with its
indexes: negligible.

Uniqueness of `(variant_id, observed_at)`: 0 duplicates in the synthetic data, but **not guaranteed by
construction** on real data. `record_observation` stamps `utc_iso(self._clock())` per insert (one clock
read per row, one transaction per row), so two rows of the same variant would need the same microsecond:
practically impossible in the serial crawler, but a wall clock stepped back by NTP, a frozen test clock,
or the merge tool (variants carry node_id, so no cross-node clash) are the cases to think about. Putting
`source_count` in the key, as in (b), makes a collision a truly identical row; it then needs `INSERT OR
IGNORE` (losing nothing) instead of an IntegrityError that would abort the crawler's insert.

**Conclusion:** the new model is ~10x smaller than today's 5.2 GB: 0.52 GB for (a), 0.24 GB for (b);
an extra `observed_at` index costs +0.23 GB in both cases.

## Q2. Batched in-place migration, disk

Script `q2_migrate.py DB BATCH CHECKPOINT OBS_AT_INDEX VACUUM JOIN`, driven by `run_11m5.sh` and
`run_cache_11m5.sh`, each run from a fresh copy of the 11.5M pristine DB. Runner pragmas (WAL,
`foreign_keys=ON`, `recursive_triggers=ON`, `temp_store=MEMORY`), the Q4 recipe, new indexes created
**before** filling, a loop of `BEGIN; INSERT INTO observations SELECT ... WHERE o.id BETWEEN a AND b
ORDER BY o.id; DELETE FROM file_observations WHERE id BETWEEN a AND b; COMMIT` (+ optional
`wal_checkpoint(TRUNCATE)`). A 20 ms sampler thread records DB, WAL and SQLite temp-file sizes
(deleted files held open, read from `/proc/self/fd`) and RSS. Start file: 6.12 GB.

| Run (11.5M rows) | peak DB file | peak WAL | peak DB+WAL+temp | DB after `DROP` of old tables |
|---|---|---|---|---|
| r1 batch 50k, checkpoint each batch | 6.13 GB | 0.12 GB | 6.24 GB | 6.13 GB (1367481 free of 1495385 pages) |
| r2 batch 50k, no explicit checkpoint | 6.13 GB | 0.12 GB | 6.24 GB | 6.13 GB |
| r3 batch 500k, checkpoint, + `observed_at` index | 6.15 GB | 0.43 GB | 6.59 GB | 6.15 GB |
| r4 batch 500k, no explicit checkpoint | 6.14 GB | 0.42 GB | 6.56 GB | 6.14 GB |

- **The freelist is reused:** the file grows by only 10-30 MB while 0.52-0.75 GB of new data is
  written. Growth is about one batch of new data plus the variants table: each batch INSERTs before it
  DELETEs, so the first batch has no free pages yet. The freelist then rises linearly (r1: 170069 free
  pages at 1.45M rows moved, 678641 at 5.75M, 1367422 at the end).
- **WAL:** bounded by the largest transaction, ~0.12 GB at 50k and ~0.43 GB at 500k, whether or not
  the loop checkpoints: the automatic checkpoint (1000 pages) after each COMMIT already resets the WAL,
  as nothing else reads. An explicit `TRUNCATE` only gives the disk back between batches.
- **Old indexes shrink with the deletes** (`q2_index_shrink.py`, 2M rows, `q2_index_shrink_2m.txt`):
  pages per B-tree after deleting the oldest 25/50/75/100 % by id: table 172016 -> 129027 -> 86020 ->
  43010 -> 1; `idx_ed2k_hash` 22906 -> 18005 -> 12536 -> 6756 -> 1; `idx_hash_observed` 40675 -> 31416
  -> 21632 -> 11460 -> 1; `idx_observed_at` 23004 -> 17264 -> 11510 -> 5756 -> 1. Freed pages go to the
  freelist; the hash-keyed indexes lag ~5 points behind the table, then catch up.
- **`DROP TABLE` keeps the file at its high-water size** (6.13 GB, 91 % freelist) until VACUUM. The
  drop itself takes 0.0-0.2 s (the tables are already empty).
- **VACUUM at the end** (final DB 0.49 GB):
  - `temp_store=MEMORY` (r1): 11.1 s, **peak RSS 557 MB** (the temp copy of the DB lives in RAM),
    **peak WAL 0.49 GB** (in WAL mode VACUUM writes the whole new DB through the WAL), peak disk
    6.62 GB = high-water + one final-DB-sized WAL.
  - `temp_store=FILE` (r2): 7.3 s, RSS 45 MB, temp file 0.49 GB in `SQLITE_TMPDIR` (unset in the
    image: `/var/tmp`, then `/tmp`, i.e. the container's overlay layer, not `/data`) + WAL 0.49 GB:
    peak disk 7.11 GB = high-water + 2x the final DB.
  - After `wal_checkpoint(TRUNCATE)`: file 491597824 bytes, WAL 0.
  - 2M run for scale: VACUUM MEMORY peaks at 117 MB RSS for an 88 MB final DB.

**Conclusion:** the batched in-place migration needs no extra disk beyond ~one batch (+0.12 GB WAL at
50k); the space comes back only with a final VACUUM, which costs ~1x the final DB in RAM
(`temp_store=MEMORY`) or on the container's own filesystem (`FILE`), plus the same again in WAL.

## Q3. Memory

Script `q3_memory.py` (one process per mode, `VmHWM` reset via `/proc/self/clear_refs` before each
step). 11.5M figures are measured, not extrapolated.

| Step | 2M rows | 11.5M rows |
|---|---|---|
| (i) `CREATE INDEX (variant_id, observed_at)` after fill, `temp_store=MEMORY` | +120 MB (60 B/row), 1.5 s | **+697 MB** (61 B/row), 14.5 s |
| (i) `CREATE INDEX (observed_at)` after fill, `MEMORY` | +87 MB (44 B/row) | (reused the freed sorter memory) |
| (i) same two with `temp_store=FILE` | +2 MB / +0 MB | +2 MB / +0 MB (4.4 s, 2.6 s) |
| (ii) batched fill with both indexes pre-created, batch 50k or 500k | flat 18 MB | **flat 18 MB** (`cache_size` -2000) |
| variant building: `SELECT DISTINCT` of the 11 old columns, `temp_store=MEMORY` | 20 MB, 1.1 s | **19 MB**, 13.9 s, 12846 keys |
| variant building: Python dict streamed from a cursor | 27 MB, 2.5 s | **28 MB**, 17.6 s |
| whole `q2_migrate.py` copy loop (default cache) | | 85-117 MB peak RSS |
| same, `cache_size=-262144` | | 367 MB peak RSS |

Commands: `python3 q3_memory.py index work.db MEMORY|FILE`, `... fill work.db 500000`,
`... distinct pristine.db` (outputs `q3_index_*.txt`, `q3_fill_*.txt`, `q3_distinct_*.txt`).

- (i) is linear in rows, ~61 B/row for this 2-integer key: an in-memory sort is not bounded by
  `cache_size` (matches the runner's docstring warning; 116 B/row there for the old text keys). At
  11.5M it is 0.7 GB of the 2 GB limit, and it grows with the table.
- (ii) is bounded by `cache_size` regardless of rows. Pre-creating the indexes and filling batch by
  batch is the bounded option.
- `SELECT DISTINCT` builds its ephemeral index of **distinct keys only**: memory bounded by the
  variant count (~13k), not by rows. No need for a Python dict to stay bounded. A Python dict streamed
  from a cursor is just as bounded; `fetchall()` of 100k-row pages is what costs memory (183-198 MB
  measured with an earlier version of the scripts that paged with `LIMIT 100000` + `fetchall()`;
  `q2_2m_b50k_ck1_dict.txt` comes from that version, the current scripts iterate the cursor).
- RSS during the copy loop drifts by tens of MB and comes back (40 -> 85 MB -> 40 MB); `q3_creep.py`
  shows that neither the DELETE nor a Python UDF alone causes it: allocator noise, bounded.

**Conclusion:** fill pre-indexed tables batch by batch (memory flat at the cache size); avoid any
`CREATE INDEX` on the big table under `temp_store=MEMORY` (0.7 GB at 11.5M and growing); `SELECT
DISTINCT` for the variants is bounded by the ~13k distinct keys.

## Q4. Foreign keys and table swaps inside the runner's transaction

Script `q4_fk.py`. Command: `python3 q4_fk.py` (host, SQLite 3.53.4) and
`docker run --rm -v $PWD:/spike:ro --entrypoint python ghcr.io/mission-titar/mulewatch:latest /spike/q4_fk.py`
(image, SQLite 3.46.1). Outputs `q4_host.txt`, `q4_image.txt`: **identical apart from the version line.**
Every connection opens like the runner (WAL, `foreign_keys=ON`, `recursive_triggers=ON`) and the old
schema carries the append-only triggers.

| Case | Measured |
|---|---|
| (a) `PRAGMA foreign_keys=OFF` after `BEGIN` | silently ignored: reads back `1`, an orphan insert still fails `FOREIGN KEY constraint failed`. |
| (b) `PRAGMA defer_foreign_keys=ON` after `BEGIN` | works: reads back `1`, orphan insert OK, `COMMIT` fails `FOREIGN KEY constraint failed` **and the transaction stays open** (`in_transaction=True`), inserting the missing parent then lets `COMMIT` succeed; the flag resets to `0` after `COMMIT`. |
| (c) `DROP TABLE files` with child rows, immediate FKs | fails `FOREIGN KEY constraint failed` (the implicit `DELETE FROM` checks children; it does **not** fire the `BEFORE DELETE` append-only trigger). |
| (c) same, `defer_foreign_keys=ON` | `DROP` succeeds, `COMMIT` fails (orphans counted). |
| (c2) deferred: drop parent, then drop both children, `COMMIT` | OK (dropping the child tables removes the counted violations). |
| (c3) immediate: drop children first, then the parent | OK. Dropping a table with append-only triggers works: the implicit delete bypasses triggers, and `DROP TABLE` removes the table's triggers and indexes (schema empty afterwards). |
| (c4) drop a parent whose child table exists but is empty | `DROP` OK, but the child keeps `REFERENCES files` to a now-missing table: every later insert into it fails `no such table: main.files`. A trap for an "empty, keep it" child. |
| (d) `ALTER TABLE files RENAME TO files_old`, `legacy_alter_table=OFF` (default), fk ON or OFF | children's `REFERENCES` rewritten to `"files_old"`; triggers follow the table (`ON "files_old"`). |
| (d) `legacy_alter_table=ON`, fk ON | rewritten as well. |
| (d) `legacy_alter_table=ON`, fk OFF | **not** rewritten: children keep `REFERENCES files`, the next child insert fails `no such table: main.files` and `foreign_key_check` lists every child row. |

**(e) Working recipe** ("rename the old ones away, create the new ones under their final names"),
verified end to end in `q4_fk.py` and at 11.5M rows in `q2_migrate.py`, every step a committed
transaction with `foreign_keys=ON` and no `defer_foreign_keys`:

1. txn A (DDL, small): drop the old tables' append-only triggers; `ALTER TABLE files RENAME TO files_old`
   (all old children now say `REFERENCES "files_old"`); `ALTER TABLE match_decisions RENAME TO
   match_decisions_old` and drop its indexes (index names survive a rename, so they would collide);
   `CREATE TABLE files (file_id BLOB PRIMARY KEY ...)`, `observation_variants`, `observations`,
   `match_decisions` with clean `REFERENCES files (file_id)` text, their indexes and triggers under the
   final names; copy `files` and `match_decisions`.
2. txn B: build `observation_variants`.
3. txn C1..Cn: batched copy + delete of `file_observations`. Each COMMIT is FK-valid: new children point
   at new parents, old children at `files_old`.
4. txn D: drop the old children first (`file_observations`, `source_observations`,
   `file_observation_ranges`, `match_decisions_old`), then `files_old`; recreate the append-only triggers.

End state (`q4_host.txt`, last block): the schema text reads exactly
`REFERENCES files (file_id)` (no quoted or rewritten names), `PRAGMA foreign_key_check` is empty, an
orphan insert into `match_decisions` fails, `integrity_check` = ok. The only quoted names appear on
the *old* tables in the intermediate state, which are dropped.

**Conclusion:** never rely on `foreign_keys=OFF` inside the runner (ignored); rename the old tables
away and create the new ones under their final names, dropping old children before the old parent:
no `defer_foreign_keys` and no `legacy_alter_table` needed, and every intermediate COMMIT passes.

## Q5. Python-registered SQL functions

Script `q5_functions.py`. Command: `python3 q5_functions.py` (host) and the same in the image
(`q5_host.txt`, `q5_image.txt`). 1M source rows in `:memory:`, each function evaluated inside an
`INSERT .. SELECT` (`CREATE TABLE AS SELECT`).

| Per 1M rows | host 3.53.4 / py3.14 | image 3.46.1 / py3.13 |
|---|---|---|
| baseline copy, no function | 0.54 s | 0.48 s |
| `file_id('ed2k', h)` = uuid5 bytes | 1.48 s | 1.74 s |
| `content_hash` 10 cols, JSON + blake2b-128 | 3.16 s | 3.13 s |
| `content_hash` 10 cols, JSON + sha256[:16] | 2.98 s | 3.11 s |
| `content_hash` 10 cols, length-prefixed + blake2b-128 | 3.07 s | 2.50 s |
| `file_id` + `content_hash` in one statement | 4.69 s | 5.07 s |

- `create_function(name, n, f, deterministic=True)` works inside `INSERT..SELECT` and in
  `JOIN .. ON v.content_hash = content_hash(...)` under both versions.
- **file_id encoding proposed:** `uuid5(NS, f"{network}:{native_id}").bytes` (16-byte BLOB), with
  `NS = uuid5(NAMESPACE_URL, "https://mission-titar.github.io/mulewatch/file")` =
  `7d16bb87-5b2f-5aa5-9262-c007b2ab0db4`. Unambiguous as long as `network` cannot contain `:` (a
  `CHECK (network GLOB '[a-z0-9]*' ...)` or a closed enum); `native_id` is the canonical lowercase hex.
  Example: `file_id('ed2k', '0'*32)` = `b0ddd181bce15158bd5bfcd8568bd48c`, same on both versions.
- **content_hash canonical form proposed:** `json.dumps(values, ensure_ascii=False,
  separators=(",", ":"))` of the column tuple in a fixed order, BLOBs (the file_id) as lowercase hex,
  hashed with `blake2b(digest_size=16)`. NULL-safe (`null` vs `""`), type-safe (`1` vs `"1"`) and
  boundary-safe (`["a,b","c"]` vs `["a","b,c"]`): asserted in the script. JSON is as fast as the
  length-prefixed form within noise and readable when debugging; blake2b-128 and truncated sha256 cost
  the same, blake2b is the native 16-byte digest.
- The cost that matters is per old row during the copy (11.5M): a per-row `content_hash` join costs
  ~3 s/M (~35 s at 11.5M on the host). The faster path (Q6) avoids it: hash only the ~12.8k distinct
  raw keys once and map raw key -> variant_id through a dict-backed UDF.

**Conclusion:** registered deterministic UDFs are usable in migrations under 3.46.1; uuid5 file_id
~1.5 µs/row, content_hash ~3 µs/row: negligible next to I/O, but only hash distinct keys, not rows.

## Q6. Time

Throughput of the batched copy+delete phase (rows/s), plus the full-table read passes.

| Run | Where | rows/s | copy phase |
|---|---|---|---|
| 2M, batch 50k, dict join | host, **tmpfs (RAM)** | 72312 | 28 s |
| 2M, batch 50k, per-row `content_hash` join | host, tmpfs | 37978 | 53 s |
| 2M, batch 50k | image, container overlay FS (inside the VM) | 26188 / 26800 (checkpoint / not) | 75 s |
| 2M, batch 50k | **image, Docker Desktop bind mount** (= the live node's setup) | **1916** | 1044 s |
| 11.5M r1, batch 50k, checkpoint | host, LUKS disk | 14249 | 807 s |
| 11.5M r2, batch 50k, no checkpoint | host, LUKS | 19871 | 579 s |
| 11.5M r3, batch 500k, + observed_at index | host, LUKS | 18350 | 627 s |
| 11.5M r4, batch 500k, per-row hash join | host, LUKS | 21359 | 538 s |
| 11.5M r5, batch 50k, **`cache_size=-262144`** | host, LUKS | **35811** | 321 s |
| 11.5M r6, batch 50k, **`cache_size=-262144`** | **image, bind mount** | **10017** | **1148 s (19 min)** |
| 11.5M (Q7), batch 50k, `secure_delete=OFF` | host, LUKS | 22843 | 503 s |
| 11.5M (Q7), batch 50k, `secure_delete=OFF` + 256 MB cache | image, bind mount | 6596 | 1744 s (noisy, see Q7) |
| 11.5M (Q7), **single transaction (A)**, `secure_delete=OFF` + 256 MB cache | image, bind mount | (whole migration) | **535 s** |

Full-table read passes at 11.5M: the `observed_at` format preflight scan 11-20 s host / 95 s image on
the bind mount; the variant pass (dict) 17-21 s host / 89 s on the bind mount; with per-row UDF
`content_hash` in `SELECT DISTINCT` 86 s host.

- On disk at 11.5M the copy is **I/O bound**: the join strategy and the batch size change little (14-21k
  rows/s, run-to-run noise from the OS page cache included), while the SQLite page cache matters a
  lot: 256 MB lets each batch's working set (the ~13k variants' leaves in the new index, plus the
  ~1933-way scattered deletes in the two hash-keyed old indexes) stay in cache: 2.5x on the host.
- **Over the Docker Desktop bind mount the default 2 MB cache is pathological**: 1916 rows/s at 2M,
  i.e. >= 100 min for 11.5M (extrapolated from 2M; it was still slowing down). Experiments at 200k
  rows (`q2_bindmount_pragmas.txt`): `synchronous=NORMAL` 2559 rows/s, `locking_mode=EXCLUSIVE` 3128,
  both 3658, `mmap_size=1G` 2531, **`cache_size=-262144` 41538** (that one fits the whole 110 MB DB in
  cache, so the 11.5M r6 run is the honest number: 10017 rows/s, 19 min).
- Expected on the node (Docker Desktop, bind mount, 2 GB limit): ~20-25 min for the copy with a
  256 MB cache plus ~3 min of full scans, versus well over an hour with the default cache. Indicative
  only: the live node's disk and VM differ from this host's.

**Conclusion:** set a large `cache_size` (256 MB measured, costs ~300 MB RSS) on the migration
connection; batch size and explicit checkpoints barely matter for speed. Q7 shows the single
transaction is faster still over the bind mount (535 s for everything).

## Q7. Is a resumable batched copy needed at all?

The first subsection answers the question as specified; the rest records the earlier exploration
(per-row `content_hash` join variant, the secure_delete trap, reclaim options), still valid.

Scripts: `q7_single_txn.py DB` (A: the whole migration in ONE `BEGIN..COMMIT`, runner pragmas, extra
pragmas via `Q7_PRAGMAS`), `q7_reclaim.py DB memory|file|into|into-file`, `spike_common.py` (UDFs +
sampler), driver `run_q7.sh`. 11.5M rows from a regenerated pristine DB (same seed, same stats:
`gen_11m5_q7.txt`). (A) is exactly the brief: DDL of the Q4 recipe, `INSERT OR IGNORE INTO
observation_variants SELECT <8 cols>, content_hash(<8 cols>) FROM file_observations` (dedup by the
UNIQUE index, no DISTINCT), `INSERT INTO observations SELECT ... JOIN observation_variants ON
v.content_hash = content_hash(...)`, `DROP` of every old table, new triggers, `COMMIT`.

### Q7 as specified: one transaction, no DELETE, dict-backed variant UDF (the main answer)

Script `q7b_single_txn_dict.py DB a|b`, driver `run_q7b.sh` (fresh `pristine.db`, 11.5M rows,
`gen_11m5_q7b.txt`; deleted afterwards). Txn A (the Q4 recipe's DDL + `files` + `match_decisions`)
commits first; then ONE `BEGIN..COMMIT`: variants built by streaming `file_observations` into a
Python dict (12846 variants), `INSERT [OR IGNORE] INTO observations SELECT variant_of(<11 raw
cols>), <iso->us>, source_count FROM file_observations o ORDER BY o.id`, `DROP` of the old children
then `files_old`, new triggers, `COMMIT`; then `wal_checkpoint(TRUNCATE)` and `VACUUM` with
`temp_store=FILE`. Every run: `PRAGMA cache_size=-262144` **and `PRAGMA secure_delete=OFF`**. The
compiled-in secure_delete is a hard requirement for any single-transaction DROP: without it the DROP's
statement journal is the whole old table in RAM (see "The trap" below, OOM reproduced at 2M under a
600m cap). Layout (a) = rowid + `(variant_id, observed_at)`; layout (b) = `WITHOUT ROWID PRIMARY KEY
(variant_id, observed_at, source_count)` with `INSERT OR IGNORE`, no `observed_at` index.

| 11.5M rows | host LUKS (a) | host LUKS (b) | image, bind mount (a) | image, bind mount (b) |
|---|---|---|---|---|
| variants pass | 15.8 s | 17.5 s | 32.7 s | 29.7 s |
| INSERT observations | 30.2 s | 29.1 s | 73.0 s | 39.3 s |
| DROP old tables | 32.0 s | 35.8 s | 23.4 s | 31.3 s |
| COMMIT | 1.9 s | 1.2 s | 54.8 s | 19.7 s |
| **whole transaction** | **79.9 s** | **83.6 s** | **183.9 s** | **120.0 s** |
| peak RSS | 325 MB | 324 MB | 326 MB | 326 MB |
| peak WAL | 0.53 GB | 0.25 GB | 0.53 GB | 0.25 GB |
| peak DB file | 6.64 GB | 6.36 GB | 6.64 GB | 6.36 GB |
| **peak DB+WAL+temp** | **7.17 GB** | **6.61 GB** | 7.17 GB | 6.61 GB |
| after checkpoint | 6.64 GB, 92 % freelist | 6.36 GB, 96 % freelist | same as host | same as host |
| VACUUM (`temp_store=FILE`): time | 4.7 s | 2.6 s | 50.6 s | 6.3 s |
| VACUUM: peak RSS | 591 MB | 540 MB | 591 MB | 540 MB |
| VACUUM: peak DB+WAL+temp | 7.63 GB (WAL 0.49 + temp 0.49) | 6.57 GB (WAL 0.21, temp 0) | 7.63 GB | 6.57 GB |
| final DB | 491597824 B | **213037056 B** | 491597824 B | 213037056 B |

Outputs `q7b_11m5_host_layout_{a,b}.txt`, `q7b_11m5_image_bindmount_layout_{a,b}.txt`. All four:
`quick_check` ok, `foreign_key_check` empty, 11500000 observations, 12846 variants. Layout (b)
after VACUUM: `observations` 207.3 MB = 18.0 B/row (matches Q1). `INSERT OR IGNORE` ignored no row
(rowcount 11500000), as expected with strictly increasing synthetic timestamps.

- **Fast**: no per-row hashing (the dict maps the raw 11-column tuple straight to `variant_id`), no
  DELETE (no secure-delete zeroing, no scattered deletes in the hash-keyed old indexes). 1.5-3 min in
  the image over the bind mount versus 19-29 min for the batched copy (Q6/Q7 B) and 9 min for the
  per-row `content_hash` join variant.
- **Memory**: ~325 MB = the 256 MB page cache + Python; flat in rows (the dict holds 12846 keys).
  Well under the 2 GB limit.
- **Disk**: the new data is appended before the DROP frees anything: +0.52 GB (a) / +0.24 GB (b), plus a
  WAL of the same size at COMMIT. Layout (b) peaks 0.56 GB lower than (a).
- **VACUUM's RSS with `temp_store=FILE` is not small here**: VACUUM gives its temporary database the
  main database's `cache_size`, so with 256 MB set, the temp DB stays in that cache before spilling to a
  file (for (b), whose 0.21 GB result fits, it never spilled: temp 0). Reset `cache_size` to the default
  before VACUUM to get the 25-45 MB RSS of Q2/Q7 below, or use `VACUUM INTO` (24 MB, see below).

**Conclusion:** no resumable batched copy needed. With `secure_delete=OFF` and a 256 MB cache, the
whole migration is one transaction of 2-3 min on the node's setup, ~325 MB RSS, and a peak of 6.6 GB
(b) / 7.2 GB (a) on disk; then VACUUM (or `VACUUM INTO`) brings layout (b) to 0.21 GB.

### The trap that decides it: SQLITE_SECURE_DELETE is compiled in

Both builds report `PRAGMA secure_delete` = 1 and `SECURE_DELETE` in `compile_options` (host Arch
3.53.4 and the image's Debian 3.46.1). Every freed page is overwritten with zeros, so the `DROP` of the
old tables inside a multi-statement transaction (1) writes the whole old table back as zero pages into
the WAL and (2) records the original pages in the **statement journal**, which lives in `temp_store`:
RAM under the runner's `temp_store=MEMORY`.

| A, single transaction | peak RSS | peak WAL | peak temp | peak DB file | peak DB+WAL+temp | time |
|---|---|---|---|---|---|---|
| 2M, defaults (secure_delete ON, temp MEMORY), host | **1111 MB** | 1.16 GB | in RAM | 1.15 GB | 2.31 GB | 82 s |
| 2M, same, **image, `--memory=600m`** | **killed: exit 137 at the DROP** | | | | | |
| 2M, secure_delete ON, temp_store=FILE | 25 MB | 1.16 GB | 1.06 GB | 1.15 GB | 3.28 GB | 76 s |
| 2M, secure_delete OFF (either temp_store) | 24 MB | 0.10 GB | 0 | 1.15 GB | 1.25 GB | 70 s |
| 11.5M, defaults | **not run: ~6.1 GB of statement journal in RAM** (extrapolated: the FILE run below puts 6.12 GB in temp; the 2M run puts it in RSS) | | | | | |
| 11.5M, secure_delete ON, temp_store=FILE | 39 MB | **6.68 GB** | **6.12 GB** | 6.64 GB | **18.92 GB** | 681 s |
| 11.5M, **secure_delete OFF**, temp MEMORY | **31 MB** | **0.53 GB** | 0 | **6.64 GB** | **7.17 GB** | 655 s |
| 11.5M, secure_delete OFF + cache 256 MB, **image over the bind mount** | 314 MB | 0.53 GB | 0 | 6.64 GB | 7.17 GB | **535 s** |

Outputs: `q7_A_2m_pragmas.txt`, `q7_A_2m_image_mem600m.txt`, `q7_A_11m5_securedel_on_tempfile.txt`,
`q7_A_11m5_securedel_off.txt`, `q7_A_11m5_image_bindmount_securedel_off_cache256m.txt`.

- With `secure_delete=OFF` (a per-connection pragma, settable before `BEGIN`), A is cheap: RSS flat,
  WAL = the new data (0.53 GB, all of it in the one transaction), file grows 6.12 -> 6.64 GB because
  the new rows are appended before the DROP frees anything. Phases at 11.5M on the host: variants
  194 s (per-row `fold_meta` + `file_id` + `content_hash`, twice per row as written), observations
  300 s, DROP 157 s (it walks every page of the old B-trees), COMMIT 4 s.
- With the compiled default it is not viable in the container: the statement journal of the DROP is the
  whole old table (6.1 GB) in RAM, the OOM kill is reproduced at 2M under a 600m cap, and with
  `temp_store=FILE` it moves to disk instead: 18.9 GB peak (old DB + 6.7 GB WAL + 6.1 GB temp file in
  `SQLITE_TMPDIR`, i.e. the container's overlay, not `/data`).

### B, the batched copy, under the same light

| B, batched, batch 50k, checkpoint each batch | peak RSS | peak WAL | peak DB+WAL | copy rows/s |
|---|---|---|---|---|
| 11.5M host, secure_delete ON (= r1 of Q2) | 85 MB | 0.12 GB | 6.24 GB | 14249 |
| 11.5M host, **secure_delete OFF** | 90 MB | 0.11 GB | 6.23 GB | **22843** |
| 11.5M image bind mount, ON + cache 256 MB (= r6) | 368 MB | 0.12 GB | 6.24 GB | 10017 |
| 11.5M image bind mount, OFF + cache 256 MB | 368 MB | 0.11 GB | 6.23 GB | 6596 (see below) |

(`q7_B_11m5_securedel_off.txt`, `q7_B_11m5_image_bindmount_securedel_off_cache256m.txt`.) B's
statement journal is one batch's deleted pages, so secure_delete never hurts its memory; turning it off
saves the zero-page writes (1.6x on the host). The two bind-mount runs disagree (6.6k vs 10k rows/s) for
no reason I could isolate: the host load average was ~4 from other work during the second, and its rate
fell steadily from 19k to 6k rows/s. Treat bind-mount timings as +/- 50 %.

### A vs B

| | A (one transaction, secure_delete OFF) | B (batched, any setting) |
|---|---|---|
| peak RSS | 31 MB (314 MB with a 256 MB cache) | 85-117 MB (368 MB with a 256 MB cache) |
| peak WAL | 0.53 GB (the whole new data) | ~one batch: 0.12 GB at 50k, 0.43 GB at 500k |
| peak DB file | 6.64 GB (+0.52) | 6.13-6.15 GB (+0.01-0.03) |
| peak disk total | 7.17 GB | 6.24 GB at 50k |
| time, host | 655 s | 503-807 s + ~40 s of passes |
| time, image over the bind mount | **535 s** | 1148-1744 s + ~60-120 s of passes |
| crash midway | ROLLBACK: the DB is untouched, the migration restarts from scratch | must resume from the committed batches: resumable code, more states to test |
| fits the runner (one script = one transaction) | yes, as one migration with registered UDFs | no: needs a Python-driven multi-transaction step |

**A needs only ~0.9 GB more peak disk than B (7.17 vs 6.24 GB), is not slower (faster over the bind
mount, as it is mostly sequential scans while B's scattered deletes thrash the hash-keyed indexes), and
has a trivial failure mode.** Its one hard requirement is `PRAGMA secure_delete=OFF` on the migration
connection (or, equivalently for the DROP, `temp_store=FILE` plus ~12 GB of extra disk: worse).

### Reclaiming the freed space afterwards (after A: 6.64 GB file, 92 % freelist; final DB 0.49 GB)

| Method | time (host) | peak RSS | peak disk (DB + WAL + temp + new) | result |
|---|---|---|---|---|
| `VACUUM`, `temp_store=MEMORY` | 4.7 s | **550 MB** (the temp copy) | 7.14 GB (6.64 + 0.49 WAL) | WAL-mode file, 491597824 bytes |
| `VACUUM`, `temp_store=FILE` | 4.1 s | 25 MB | 7.63 GB (6.64 + 0.49 WAL + 0.49 temp in `SQLITE_TMPDIR`) | same |
| `VACUUM INTO 'catalog.db.new'` (either temp_store) + close + `os.replace` | **1.4 s** | **24 MB** | **7.13 GB** (6.64 + 0.49 new file, no WAL, no temp) | **rollback-journal file** (header bytes 18/19 = `0101`), 491597824 bytes |

After B (6.13 GB file): `VACUUM INTO` 1.5 s, 24 MB RSS, 6.62 GB peak (`q7_reclaim_afterB_into.txt`);
VACUUM MEMORY/FILE as in Q2 (557 MB RSS / 45 MB RSS + 0.49 GB temp). Outputs
`q7_reclaim_afterA_{memory,file,into,into-file}.txt`.

Every result passes `quick_check`, `foreign_key_check` (empty), keeps 11.5M observations, the 4
triggers and `user_version` 5. Notes on `VACUUM INTO`:
- it builds straight into the target file: no temp database, so `temp_store` is irrelevant, and no
  WAL on the target. Its peak RSS is the cache, not the DB size: the only reclaim that is cheap in
  memory **and** disk.
- the output is in **rollback-journal mode** (`journal_mode=delete`): the runner's `_configure`
  (`PRAGMA journal_mode=WAL` on every open) switches it back, but nothing else should open it first.
- the swap must happen with no other connection open: closing the last connection checkpoints and
  deletes `catalog.db-wal`/`-shm` (verified: both gone after `close()`), then `os.replace` is atomic on
  the same filesystem. A stale `-wal` left next to the new file could be replayed into it (the
  "rename a database while in use" corruption case of SQLite's how-to-corrupt page; not tested here):
  never rename over a DB that still has a WAL.
- the target must be on the same filesystem as `catalog.db` (`/data`) for an atomic rename.

**Conclusion:** no, the resumable batched copy is not needed. One transaction (A) with
`PRAGMA secure_delete=OFF` peaks at 31 MB RSS, 0.53 GB WAL and 7.17 GB disk at 11.5M, runs in ~9 min
over the bind mount, and rolls back cleanly. Without that pragma it is OOM-killed (statement journal
in RAM). Reclaim afterwards with `VACUUM INTO` + rename (1.4 s, 24 MB RSS, +0.49 GB disk), not
in-place `VACUUM` (550 MB RSS under the runner's `temp_store=MEMORY`).

## Surprises and traps

0. **Both SQLite builds have `SQLITE_SECURE_DELETE` compiled in** (`PRAGMA secure_delete` = 1 on host
   and image). Every freed page is zero-filled and written: a `DROP` or a large `DELETE` inside a
   multi-statement transaction writes the whole freed volume into the WAL and keeps the original pages
   in the statement journal, in RAM under `temp_store=MEMORY`. That OOM-kills a single-transaction
   migration (reproduced at 2M under a 600m cap; 6.1 GB at 11.5M). `PRAGMA secure_delete=OFF` on the
   migration connection removes the problem entirely (Q7), and speeds up the batched copy 1.6x.
1. **`PRAGMA foreign_keys=OFF` inside the runner's transaction is silently ignored** (no error, reads
   back 1). The SQLite "12-step" recipe that relies on it cannot run inside `_run_scripts`.
2. **Renaming a parent rewrites the children's `REFERENCES`** (`legacy_alter_table=OFF`, the default):
   rename-then-recreate is the clean way. With `legacy_alter_table=ON` + `foreign_keys=OFF` the children
   keep a dangling `REFERENCES files` and every later insert fails `no such table: main.files`.
3. **Dropping a parent whose child table is empty leaves that child unusable** (`no such table:
   main.files` on insert): drop or rebuild every child, even the empty ones (`source_observations`,
   `file_observation_ranges`, `sources`).
4. **`DROP TABLE` with FKs on runs an implicit `DELETE` that bypasses the append-only triggers**, fails
   on child rows when FKs are immediate, and is deferred (then fails at COMMIT) under
   `defer_foreign_keys`. A failed deferred COMMIT leaves the transaction **open**: the runner's
   ROLLBACK path must still run.
5. **Index names survive `ALTER TABLE .. RENAME`**: recreating an index with the old name on the new
   table collides unless the old one is dropped first.
6. **Docker Desktop bind mount + default `cache_size` = 13x slower than the container's own FS** for
   this workload (1916 vs 26188 rows/s at 2M). Not fsync, not the shm (`synchronous`/`locking_mode`
   barely help): page re-reads, which a 256 MB `cache_size` mostly removes.
7. **VACUUM in WAL mode writes the whole new DB into the WAL** (0.49 GB) on top of the temp copy
   (RAM under `temp_store=MEMORY`: 557 MB RSS at 11.5M; or a temp file outside `/data` under FILE).
8. **The 2M timings on the scratchpad are RAM timings** (tmpfs): 72k rows/s there vs 14-20k rows/s on
   disk at 11.5M. Do not extrapolate time linearly from small runs.
9. **Folding `complete_source_count` into the variant multiplies the variants** (7433 -> 12846
   synthetic, ~13k expected on the node): harmless for size, but the variant count is not the 7.4k of
   the brief.
10. The ISO text -> integer microseconds conversion in pure SQL,
    `unixepoch(substr(t,1,19)) * 1000000 + CAST(substr(t,21,6) AS INTEGER)`, equals Python exactly on
    100004 timestamps under both 3.53.4 and 3.46.1 (`q2_us_check.py`), given the preflight check that
    every value is 32 chars ending in `+00:00` (true by `utc_iso`).
