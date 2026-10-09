# Stage 1: generic file identity and lossless compact observations

- Date: 2026-10-09 (discussion 2026-10-08 and 2026-10-09)
- Status: APPROVED by the operator 2026-10-09
- Tier: Spec (the schema of catalog.db, a webui route and the SQL console's columns change)
- Umbrella: `agents/specs/2026-10-08-multi-network-architecture.md`, stage 1 (D8), which this spec corrects
  on its release and on its metadata bag (D17)
- Evidence: `agents/reference/2026-10-08-sqlite-catalog-migration-spike/` (`REPORT.md`, raw outputs,
  scripts), referred to below as "the spike" with its question number (Q1 to Q7)

## 1. Context and goals

The catalog identifies a file by `files.ed2k_hash`, a 32-hex MD4 that every catalog table references. Stage
5 adds networks whose files have other identities (TTH, SHA-1, infohash), so the catalog needs an identity
that is not eD2k's. Stage 1 makes that change, in today's single container, before any new network exists.

The same rewrite answers the backlog item "Lossless compact observation storage". Counted on the node
through the webui's read-only SQL console on 2026-10-09 (distinct counts summed over the 256 two-hex
prefixes of the hash): `file_observations` holds 11,586,688 rows but only **11,031 distinct variants** in
the sense of D4 (file, filename, size, duration, bitrate, raw_meta, keyword, node, plus the three columns D3
folds into raw_meta); 13,534 once `source_count` is added. `raw_meta` takes 20 distinct values and there is
one node. Per row, only `observed_at` and `source_count` carry information. `dbstat` on 2026-10-08:
`file_observations` 3.23 GB and its three indexes 2.02 GB, of a 5.25 GB file. Since the migration rewrites
every row anyway, it writes them in a compact form.

Goals:

- A file is `(network, native_id)` in the catalog, with a stable `file_id` that is the same on every node.
- Observations are stored without repeating what does not change, with no information lost.
- The node's catalog migrates at boot, in place, within the container's 2 GB memory limit.
- Nothing outside the catalog changes behaviour: search, matching, decisions, notifications and downloads
  work as before.

## 2. Non-goals

- The download side (local.db, the download loop, the amuleapi download ports, the webui node page) keeps
  the eD2k hash until stage 2 (D10).
- No new network, no generic search or status port (stage 2), no source history (stage 4).
- No release (D16).
- No redirect from the old `/files/{ed2k_hash}` URLs, no SQL view for the console (D15).

## 3. Decisions

### D1. `file_id`, a UUID v5 of `(network, native_id)`, is the primary key of `files`

```sql
CREATE TABLE files (
    file_id BLOB PRIMARY KEY,
    network TEXT NOT NULL,
    native_id TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    UNIQUE (network, native_id),
    CHECK (network IN ('ed2k')),
    CHECK (network <> 'ed2k' OR (LENGTH(native_id) = 32 AND native_id NOT GLOB '*[^0-9a-f]*'))
);
```

`file_id = uuid5(NS, f"{network}:{native_id}").bytes`, `NS = uuid5(NAMESPACE_URL,
"https://mission-titar.github.io/mulewatch/file")` = `7d16bb87-5b2f-5aa5-9262-c007b2ab0db4` (spike Q5,
rechecked by the spec review). `network` cannot contain `:`, so the name is unambiguous.

*Reasons. The identity is a natural key: the same file on two nodes is the same `(network, native_id)`. A
UUID derived from it is the same on every node, so `merge` copies `files` rows as it does today, with no id
remapping; a random UUID (v4, v7) would differ per node and merge would still deduplicate on the natural
key. The operator rejected composite keys in the tables that reference a file ("generally painful") and a
prefixed text key (`ed2k:<hash>`), which has to be split to be read. The UUID costs 16 bytes in the small
tables only (`files`, `observation_variants`, `match_decisions`), never per observation (D4). The per-network
CHECK carries over 0001's canonical form of the hash: `file_id` is derived from the `native_id` string, so a
non-canonical spelling of the same hash would mint a second identity for one file. Stage 5 widens both
CHECKs with each network.*

### D2. Kad is not a network of its own: `network = 'ed2k'` for both

The value `ed2k` covers eD2k and Kad, which identify a file by the same MD4 and which amuled already folds
into one file. The Prometheus label `network` (`ed2k` / `kad`, the search channel) is unchanged; the
internal module `application/networks.py` becomes `application/channels.py`.

*Reason: an identity network is what defines `native_id`, a search channel is how a file was found. The
label stays because renaming it breaks the operators' dashboards; the module is renamed so that the word
"network" has one meaning in the code once `Network` exists (D9).*

### D3. Columns: what mulewatch reads stays a column, the rest goes to `raw_meta`

Kept as columns: `filename`, `size_bytes`, `media_length_sec`, `bitrate_kbps`, `source_count`, `keyword`,
`observed_at`, `node_id`. Folded into `raw_meta`: `codec`, `file_type`, `complete_source_count`. Dropped:
`files.aich_hash`.

The fold is one pure function in the domain (`domain/observation.py`), which appends the three values to
the raw_meta pairs. The amuleapi mapper and the migration's SQL function both call it.

*Reasons. The operator's rule: a column that a network may lack and that mulewatch does not need goes to the
per-network bag, which `raw_meta` already is ("we have raw_meta to catch up later"). `codec` and `file_type`
are read by nothing (`grep -rn 'codec\|file_type\|complete_source_count' packages/*/src`: written by the
mapper and the repository, copied by merge and compact, read nowhere else), `complete_source_count` only by
the compaction aggregates that D6 deletes. `aich_hash` is NULL on every row of the node (console, 0 rows
with a value) and by construction (`catalog_repository.py:42` always binds NULL); the webui shows it as
"·". The columns kept are read: the matching engine (duration, bitrate), the webui (source counts), the
provenance of a sighting. The fold lives in the domain because both callers already depend on it: an
amuleapi adapter importing a persistence adapter, or the reverse, would be a new dependency between two
adapters, which nothing does today.*

### D4. Observations are stored as variants plus timestamps

```sql
CREATE TABLE observation_variants (
    variant_id INTEGER PRIMARY KEY,
    file_id BLOB NOT NULL REFERENCES files (file_id),
    filename TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    media_length_sec INTEGER,
    bitrate_kbps INTEGER,
    raw_meta TEXT NOT NULL,
    keyword TEXT NOT NULL,
    node_id TEXT NOT NULL,
    content_hash BLOB NOT NULL UNIQUE
);

CREATE INDEX idx_observation_variants_file_id ON observation_variants (file_id);

CREATE TABLE observations (
    variant_id INTEGER NOT NULL REFERENCES observation_variants (variant_id),
    observed_at INTEGER NOT NULL,
    source_count INTEGER NOT NULL,
    PRIMARY KEY (variant_id, observed_at, source_count)
) WITHOUT ROWID;
```

- **The index on the file reference** serves every read, which starts from a file's variants.
  (Corrected: the DDL above omitted it; 0007 names it `idx_observation_variants_ed2k_hash`.)
- **`content_hash`** is computed in Python. Its input is the variant's columns in a fixed order, serialized
  with `json.dumps(..., ensure_ascii=False, separators=(",", ":"))`, BLOBs written as lowercase hex, and it
  is hashed with `blake2b(digest_size=16)`. The serialization is NULL-safe, type-safe and boundary-safe:
  spike Q5 asserts all three.
- **`raw_meta`** is stored exactly as `record_observation` serializes it today, with
  `json.dumps(pairs, ensure_ascii=False)` and the default separators. The migration writes the same text,
  so a variant written by the crawler and one written by the migration hash alike.
- **`observed_at`** is an integer count of microseconds since the Unix epoch, UTC, which keeps the precision
  of today's `utc_iso` text. The integer would hold until year 294,247, but Python's `datetime` and SQLite's
  `datetime()` stop at 9999-12-31: an accepted limit.
- **Writes** use `INSERT OR IGNORE` on both tables. A variant already known is found by its `content_hash`,
  and two observations equal in all three columns are one.
- **Readers** rebuild `Sighting`'s timestamps as the same `utc_iso` strings, so nothing downstream sees the
  integer.
- **Ties**: two observations of one file at the same instant (only a frozen clock produces them) are ordered
  by `variant_id` descending, then `source_count` descending. This replaces today's tie-break on `id`, which
  `WITHOUT ROWID` removes. Two tests pin the old tie-break and change in block 140:
  `test_sightings.py::test_latest_sighting_breaks_an_observed_at_tie_on_the_highest_id` and
  `test_catalog_repository.py:359`. (Corrected: a third,
  `test_webui_catalog_read.py::test_list_files_observation_tie_break_on_id`, changes too.)

*Reasons, in order. **Variants**: the node's 11.6M rows hold 11,031 variants (section 1). Measured on a
synthetic 11.5M-row catalog: 532 B per observation today, 20.5 B in this layout, so 6.1 GB become 0.24 GB,
0.21 GB after VACUUM (spike Q1, Q7). The catalog then grows about 0.25 GB a quarter instead of 5 GB.
**Integer `variant_id`, not a content UUID**: a UUID derived from the content would freeze its
serialization, since changing a variant's columns later would change every id and rewrite every
observation. With an integer, a serialization change recomputes `content_hash` on ~11k variants and leaves
the observations alone; merge maps variants by `content_hash`, cheap at that count. **`content_hash` rather
than a UNIQUE over the columns**: SQLite treats NULLs as distinct in a UNIQUE constraint, and 8,609 of the
node's 11,031 variants have a NULL duration or bitrate (console, 2026-10-09). **Integer microseconds**: 24 B
saved per observation, about 0.28 GB at 11.5M rows (the spec review measured D4's table after VACUUM at
42.9 B/row with TEXT, 18.7 B/row with INTEGER), with no precision lost. **`WITHOUT ROWID`**: the table is its
own index, half the size of a rowid table plus an index on `(variant_id, observed_at)` (0.24 vs 0.52 GB,
spike Q1). No observation is referenced by anything, so it needs no id. A row identical in all three columns
is indistinguishable from its twin, and `merge` already folds identical rows into one, so dropping it loses
nothing. **The tie-break**: the crawler is a single writer reading a microsecond clock once per row, so real
ties do not happen. The highest `variant_id` is the most recently created variant, the closest available
stand-in for the most recent insert.*

### D5. `sources` and `source_observations` are dropped

*Reason: both tables hold 0 rows on the node (console, 2026-10-08 and 2026-10-09), no code writes them, and
they carry `ip`, `port` and `country`, which the umbrella's D10 forbids. Stage 4 creates its own source
tables. Migrating them to `file_id` would rewrite tables meant to disappear.*

### D6. The `compact` tool and `file_observation_ranges` are deleted; a catalog holding ranges refuses to migrate

What goes:

- the tool;
- `domain/retention/`;
- the ranges table;
- every reader of the ranges table: the `sightings` fallback, the `NAME_MATCH_CLAUSE` branch, merge's
  `covered_by_range`, `Sighting.compacted` and the webui's compacted day.

Catalog 0006 aborts with a `MigrationError` naming `file_observation_ranges` when that table holds a row.

*Reason: `compact` answered catalog growth with a lossy daily rollup; D4 answers it losslessly, at about
0.25 GB a quarter. Its readers are what made "only `sightings.py` reads observations" an invariant (two forms
of one observation). The ranges table is empty on the node (console, 0 rows), and the operator knows of one
other node, which they are almost certain never ran `compact`. A range cannot become observations. It keeps
neither the keyword, the duration, the bitrate nor `raw_meta`, and it mixes lists of names and nodes under
shared aggregates. Any conversion would therefore assert combinations that may never have been observed.
Refusing costs a few lines for a case nobody expects; if it ever happens, the conversion gets written against
real data.*

### D7. Four catalog migrations, each one all or nothing

| Version | Script | Effect |
|---|---|---|
| 0006 | `0006_drop_dead_tables.sql` | refuses if ranges hold a row (D6), drops `source_observations`, `sources`, `file_observation_ranges` |
| 0007 | `0007_observation_variants.sql` | builds `observation_variants` and `observations` from `file_observations` (still keyed by `ed2k_hash`), drops `file_observations` |
| 0008 | `0008_file_id.sql` | rebuilds `files`, `match_decisions`, `observation_variants` and `observations` keyed by `file_id` |
| 0009 | `0009_vacuum.sql` | `VACUUM`, outside any transaction (D8) |

0007 and 0008 each run in the runner's one transaction. Each sets `PRAGMA secure_delete = OFF` and `PRAGMA
cache_size = -262144` at the top of its script; the runner restores both afterwards (D8).

A table is replaced by the spike's Q4 recipe:

1. drop the old tables' triggers and indexes;
2. rename the old tables away;
3. create the new tables under their final names;
4. copy;
5. drop the old children before the old parent;
6. recreate the append-only triggers.

0008 recomputes `content_hash`, which includes the file reference, and rebuilds `observations` too:
renaming `observation_variants` would leave `observations` pointing at the old table (spike Q4 d).

0007 builds `observations` through a memoized SQL function mapping a row's variant columns to its
`content_hash`. With about 11k distinct keys, each one is hashed once, not 11.6M times.

*Reasons.*

- ***One transaction, not resumable batches.*** The operator first agreed to batches, then to this on the
  spike's numbers. The spike measured, in the shipped image over the Docker Desktop bind mount at 11.5M
  synthetic rows:
  - the single transaction: 120 s with each distinct key hashed once, 535 s when the hash is computed on
    every row;
  - batched copy and delete: 19 to 29 min, dominated by its scattered deletes in the hash-keyed indexes;
  - peak RSS about 325 MB, peak disk 6.61 GB from 6.12 GB (spike Q7).

  A crash rolls back and the migration restarts from an intact catalog, so no intermediate state needs code
  or tests.
- ***The memoized function.*** It is what separates 120 s from 535 s. The SQL migration reaches the
  dict-backed path's speed with it, since only about 11k distinct keys are hashed.
- ***`secure_delete = OFF` is required where SQLite compiles `SECURE_DELETE` in.*** The host (3.53.4) and the
  image (3.46.1) both do, and there the `DROP` of the old table keeps its whole content in the statement
  journal: 1.1 GB RSS at 2M rows, OOM-killed (exit 137) at 2M rows under a 600 MB cap (spike Q7). The gate's
  build (uv's Python 3.13, SQLite 3.49.1) does not compile it in, and its default is 0. The migration tests
  therefore force `PRAGMA secure_delete = ON` first, to stand for the image. Both pragmas take effect inside
  the runner's open transaction: verified 2026-10-09 on 3.46.1 and 3.53.4, and again by the spec review. A
  20 MB `DROP` writes 21.7 MB of WAL by default, and 0.0 MB after the pragma.
- ***The 256 MB cache.*** The copy is I/O bound. On the bind mount, the default 2 MB cache ran the batched copy
  at 1.9k rows/s, against 10k with 256 MB (spike Q6).
- ***Four migrations rather than two.*** One switch would move the migration with every reader and writer of
  the catalog, in one block of about 1,300 lines. Split in two:
  - the storage switch (0007) leaves `files` untouched;
  - the identity switch (0008) rebuilds tables that are small by then (`observations` at about 0.2 GB).

  Each switch then fits in a block about half that size. Cost: `observations` is copied a second time,
  measured before block 180 (D18). Agreed by the operator 2026-10-09.

### D8. The runner: a `-- migration: no-transaction` directive, temporary files on disk, pragmas restored

- **The directive.** A migration script whose first line is exactly `-- migration: no-transaction` runs
  outside the runner's `BEGIN ... COMMIT`, and the runner stamps `user_version` after it succeeds.
  - A first line starting with `-- migration:` with any other value raises `MigrationError` when the
    scripts load.
  - The loaded script carries a `transactional` flag, and the runner executes it through one of two named
    paths.
  - A failing non-transactional script leaves `user_version` unchanged.
  - A non-transactional script must be safe to replay, since a stop between its success and the stamp runs it
    again at the next start.
- **Temporary files.** Migrations run with `temp_store = FILE` instead of `MEMORY`.
- **Pragmas restored.** The runner reads `secure_delete` and `cache_size` before each script and restores
  them after it, as it already restores `temp_store` after the migrations. A script sets what it needs and
  never resets it.
- **Documentation.** The rules for writing a migration go in `docs/contributing/architecture.md`, in a new
  section "Écrire une migration", in French like the rest of `docs/`. The section gathers what lives today
  only in the runner's docstring, plus what the spike found:
  - one transaction per script;
  - no `COMMIT` and no `CREATE TEMP TABLE`;
  - `foreign_keys` cannot be turned off inside the transaction;
  - renaming a table rewrites its children's `REFERENCES`;
  - index names survive a rename;
  - every child of a dropped parent is dropped, even an empty one;
  - the append-only triggers are recreated;
  - `secure_delete` and `cache_size` for large rewrites;
  - the directive.

  The runner's docstring keeps the contract in one line and points to that section.

*Reasons.*

- *`VACUUM` cannot run inside a transaction, and 0009 must still survive a stop.* As its own migration it is
  atomic, since an interrupted `VACUUM` rolls back, and it re-runs while `user_version` is below 9.
- *A flag read from the script is explicit*, where guessing from the content would not be. A typo must never
  silently move a migration in or out of a transaction, hence the load-time error.
- *`temp_store = MEMORY`* was the remedy for `SQLITE_FULL` under a 64 MB tmpfs and `read_only`, both dropped
  on 2026-09-16. It costs an unbounded in-memory sort: +697 MB for an index built after the fact at 11.5M rows
  (spike Q3), while `FILE` costs 2 MB. Stage 3 restores `read_only` on the core, and must then give SQLite a
  writable temporary directory; that stage's spec carries it.
- *Restoring the pragmas in the runner*:
  - a script cannot know the connection's prior value, and the gate's SQLite differs from the image's (D7);
  - it keeps 0009 on the default cache, since a `VACUUM` gives its temporary database the main database's
    cache (540 to 590 MB RSS with 256 MB, spike Q7).

### D9. The domain names a file by a `FileKey`

`domain/file_key.py` holds two types:

- `Network`, a `StrEnum` with one member, `ED2K = "ed2k"`;
- `FileKey(network, native_id)`, frozen, with a `file_id` property that computes D1's UUID.

The catalog port, `FileObservation`, `ReevalRow`, `Sighting` and `DecisionsRecorded` carry a `FileKey`
instead of `ed2k_hash`. The same function is registered as the `file_id()` SQL function that 0008 calls.
The ed2k link is built from `native_id`.

*Reasons.*

- *One value object* keeps the derivation in one place, pure (`uuid` is stdlib, no I/O), shared by the domain
  and the migration.
- *A one-member enum* keeps the stage 1 call sites free of branches. Stage 5 adds members, and `assert_never`
  at the ed2k-link sites makes mypy list what each new network has to answer.

### D10. The download side keeps the eD2k hash until stage 2

The following stay unchanged:

- local.db and `DownloadRepository`;
- the download loop;
- `DownloadEntry` / `SharedFileEntry` and `DownloadCompleted`;
- `catalog_matching`'s `DownloadCandidate`;
- the webui node page;
- `docs/troubleshooting.md`'s `DELETE FROM downloads WHERE ed2k_hash = ?`.

There is one junction. `download_decisions()` returns the `native_id` of `network = 'ed2k'` files, and the
loop wraps a hash in `FileKey(Network.ED2K, hash)` for its two catalog reads.

*Reason: stage 2's D13 rewrites the download port and its lifecycle fields for every network. Adapting the
loop to `FileKey` now (about 300 lines over 15 files) would mean writing that code twice. The operator:
"not rewriting wins, stage 2 will come quickly". Until stage 2, the core speaks `FileKey` on the catalog side
and the eD2k hash on the download side, joined at one explicit point.*

### D11. `FileObservation` becomes network-agnostic

`FileObservation` carries `file: FileKey` and loses `codec`, `file_type` and `complete_source_count`. The
amuleapi mapper folds them into `raw_meta` with D3's function.

*Reason: D3. The mapper's pairs and 0007's must be identical, or every file gets a second variant at its
first sighting after the upgrade. Both call one function, and a cross test compares, through
`record_observation`, the variant a mapped result produces with the one 0007 produces from the same values
stored the old way.*

### D12. The per-file decision signal goes

`record_decisions` stops calling `signal.signal(ed2k_hash)`.

*Reason: nothing waits on a file subject. The only `wait` on the decision signal is
`deps.signal.wait(DOWNLOAD_NUDGE_SUBJECT)` (`application/run_download_cycle.py:413`, `grep -rn "\.wait("`).
Translating a dead call to a `file_id` subject would keep it alive for nothing.*

### D13. The webui addresses a file by `file_id`

The route is `/files/{file_id}`, with the 32 lowercase hex characters of the UUID; a malformed or unknown id
answers 404. The detail page shows the network and the native id instead of "eD2k hash" and "AICH hash",
and still offers the ed2k link for an `ed2k` file.

*Reason: `file_id` is the key, and it is the one identifier that stays unambiguous once two networks exist.
`docs/operate.md` documents the new form.*

### D14. The `AGENTS.md` invariant on observation readers goes

"Only `adapters/persistence_sqlite/sightings.py` reads `file_observations` and
`file_observation_ranges`" is deleted with the ranges' readers.

*Reason: the invariant's stated reason is that an observation had two forms, and a reader that queried one
lost the other. With one form, the rule protects nothing. `sightings.py` stays the natural home of
observation reads, without an invariant to say so.*

### D15. Operator surfaces: accepted changes, no compatibility layer

- The SQL console's tables and columns change.
- The old `/files/{hash}` URLs answer 404.
- `python -m mulewatch.compact` no longer exists.

*Reason: no release ships before the first new network (D16), whose major version gives the migration
notes. The console is for developers and power users, not something to polish (the operator), so a view for
it would be YAGNI.*

### D16. No release until the first new network; a `release/4.x` branch if a fix needs one

The stage 1 lot ships no image beyond `main`. The node pulls `latest` since 2026-10-08, to stay out of this
work.

If a fix must reach 4.x users before the major release (a vulnerability, a broken dependency), the lead
proposes a `release/4.x` branch cut from the last `v4` tag, with the fix cherry-picked onto it and a
`v4.x.y` tag. `release.yml` publishes `latest` on every `v*` tag, so once a 5.x exists, the release carrying
such a fix must also keep `latest` from moving back to 4.x.

*Reason: for an operator, stage 1 is a breaking change with no gain they can see. The catalog cannot be read
by 4.x once migrated, and the console's columns and the file URLs change: "we had aMule, we will have aMule"
(the operator). The first change that justifies the architecture is a second watched network. Whether the
major version comes with the first or the last new network is stage 5's decision. The branch is cut only
when a fix needs it (YAGNI).*

### D17. Corrections to the umbrella spec

Block 10 adds four `(Corrected: ...)` markers to the umbrella spec:

- **its header's "Release" line** (`:7`): the major release moves from stage 3 to the first new network
  (D16);
- **D15** (`:427`): the rename stays in stage 3, but no longer inside a release (D16);
- **section 4, stage 3**: no `v5.0.0` there (D16);
- **D8**: the per-network metadata bag is `raw_meta`, and `FileObservation` keeps its typed columns (D3).

*Reason: the umbrella is approved, and the workflow keeps a marker beside a claim corrected after
approval.*

### D18. Validation on a copy of the node's catalog, never on the node

The real catalog is checked twice:

- **at block 140's tip**, before block 150 is stacked, which settles the claim that decides (section 4)
  while only 0006 and 0007 exist;
- **at the top of the stack**, before the operator merges.

Each time, the lead builds the image from that tip and runs it on a copy of the node's catalog under
`mem_limit: 2g`, with the measurements of section 6 ("It fits", "The migrated catalog loses nothing"). The
copy is a consistent snapshot taken with the operator's agreement: a read from the host while the crawler
writes saw `database disk image is malformed` (2026-10-08, the `-shm` of a Docker Desktop bind mount is not
shared with the host).

Three measurements on the synthetic 11.5M-row catalog (`gen_old.py` in the spike) come before the Act of the
block that relies on them, each with its bound:

- **Before block 140.**
  - 0007, run by the runner as the `.sql` file will run, in the image over the bind mount: under 10 min.
  - The new read shapes: `EXPLAIN QUERY PLAN` of the latest-sighting, timeline, known-names and best-name
    queries shows `SEARCH observations USING PRIMARY KEY (variant_id=?` and no `SCAN observations`.
    (Corrected: the bound read `SEARCH observations USING PRIMARY KEY` alone, which a full-table `max()`
    also prints; known names no longer reads `observations` at all.)
  - The `/files` page and the busiest file's detail page: median of 5 requests no slower than `main` on the
    same catalog (old schema).
- **Before block 180.** 0008 under 5 min, in the image over the bind mount.

A result outside its bound stops the lot for the operator.

*Reasons.*

- *The spike measured synthetic data*, 27 % fatter per row than the node's (356 vs 281 B, spike
  "Calibration"), and with one fused migration. The real catalog decides.
- *Checking at block 140's tip* means a failure reworks two blocks, not ten.
- *The node is never the test bed*, which is why it now pulls `latest`.

## 4. The claim that decides

**0007, in one transaction with `secure_delete = OFF`, rewrites the node's catalog within the container's
2 GB limit and a disk peak within 1 GB of the catalog's starting size.** It is measured only on synthetic
data: 325 MB RSS, and a 6.61 GB peak from 6.12 GB (spike Q7). It is checked on the real catalog at block
140's tip (D18).

If it fails there (OOM, disk), the fallback is the batched copy the spike also measured: bounded memory, a
peak disk of +0.1 GB, slower. It needs a Python-driven multi-transaction step in the runner, which reworks
blocks 10 and 140 before anything is stacked on them. The lot then stops for the operator.

## 5. Blocks

Paths under `packages/crawler/` unless noted. Sizes are estimates from `wc -l` of deleted files and `grep
-c` of the touched sites. Each block measures itself after committing.

| # | Branch | Block | Contents | Est. lines / files |
|---|---|---|---|---|
| 10 | `feat/migration-no-transaction` | Runner directive, temp files, pragmas restored | This spec, the spike reference, the umbrella's markers (D17); `connection.py` (D8), `test_connection.py`; `docs/contributing/architecture.md` "Écrire une migration"; `docs/limits.md` and `docs/troubleshooting-start.md` (in-memory sort, OOM) | 240 / 5 |
| 20 | `refactor/search-channel-label` | The search channel module | `application/networks.py` to `channels.py`, its two importers and its test (D2) | 30 / 4 |
| 30 | `chore/remove-compact-cli` | Remove the `compact` command | `compact/__main__.py`, `tests/compact/test_cli.py`, `docs/operate.md` and `docs/legal.md` mentions, `AGENTS.md` (D6) | 160 / 5 |
| 40 | `refactor/merge-drop-dead-tables` | merge copies neither ranges nor sources | `merge/merger.py`, `merge/__main__.py`, merge tests and helpers, merge's two compaction tests | 285 / 5 |
| 50 | `refactor/webui-drop-compacted-days` | The webui ignores compacted days | webui `catalog_read.py`, `views.py`, `app.py`, `files.html`, the ranges parts of `test_webui_catalog_read.py`, `test_webui_app.py` and `tests/webui/conftest.py`, delete `tests/webui/test_compacted_catalog.py` | 415 / 8 |
| 60 | `chore/remove-compact-engine` | Remove the compaction engine | `compact/compactor.py`, `compact/errors.py`, `tests/compact/test_compactor.py`, `tests/compact/helpers.py` | 470 / 6 |
| 70 | `chore/remove-retention-buckets` | Remove the bucketing domain | `domain/retention/`, `tests/domain/retention/` | 177 / 4 |
| 80 | `refactor/sightings-drop-ranges` | Sightings ignore ranges | `sightings.py` (fallback, `covered_by_range`, `NAME_MATCH_CLAUSE`), `Sighting.compacted`, port and repository docstrings, `test_sightings.py`, `test_catalog_repository.py`, `AGENTS.md` (D14) | 200 / 7 |
| 90 | `feat/drop-dead-catalog-tables` | Catalog 0006 | `0006_drop_dead_tables.sql`, `test_migration_0006.py`, a shared helper building a catalog stamped at N-1, `test_append_only.py`, `test_connection.py`, delete `test_catalog_observation_ranges.py`, architecture diagram | 215 / 6 |
| 100 | `test/catalog-row-builders` | Test row builders, webui on the real schema | `tests/catalog_rows.py` (new), `tests/webui/conftest.py` on `open_catalog` instead of its hand-written DDL, `test_webui_catalog_read.py` | 460 / 3 |
| 110 | `test/catalog-row-builders-rest` | The remaining raw rows through builders | `test_webui_app.py`, `test_sightings.py`, `test_catalog_repository.py`, `test_reader.py`, `test_connection.py`, `test_webui_sql_console.py`, `test_decisions.py`, `test_reevaluate_catalog.py`, `test_search_worker.py`, `tests/composition/test_app.py` | 400 / 10 |
| 120 | `refactor/webui-drop-aich` | The webui drops the AICH hash | `catalog_read.py`, `views.py`, `app.py`, `file_detail.html`, one test (D3) | 20 / 5 |
| 130 | `feat/variant-content-hash` | Variant hash, fold and migration functions | The D3 fold in `domain/observation.py`; `adapters/persistence_sqlite/variants.py` (`content_hash`, its memoized SQL form, ISO to microseconds), their registration on the catalog connection, tests (D4, D7) | 180 / 5 |
| 140 | `feat/observation-variants` | Catalog 0007, observations as variants | `0007_observation_variants.sql`, `record_observation`, `sightings.py` (tie-break, D4), `merger.py`, `test_migration_0007.py`, delete `test_migration_0004.py`, the plan tests, builders, merge helpers, `BACKLOG.md`, webui `catalog_read.py` (Corrected: added, its counters skip the latest sighting without a name filter, D18) (D4, D7) | **700** / 16 |
| 150 | `refactor/file-key` | The catalog port speaks `FileKey` | `domain/file_key.py` and test, `ports/catalog_repository.py`, `catalog_repository.py`, `decisions.py` (D12), `reevaluate_catalog.py`, `record_observations.py`, the download loop's catalog seam (D10), tests (D9) | 250 / 16 |
| 160 | `refactor/events-file-key` | Decision events carry the `FileKey` | `events.py`, `policy.py`, `decisions.py`, their tests | 40 / 6 |
| 170 | `refactor/network-agnostic-observation` | `FileObservation` without eD2k-only fields | `domain/observation.py`, `adapters/mule_api/mapping.py`, `record_observations.py`, the 16 `FileObservation(` sites, `test_mapping.py`, the fold cross test (D11) | 180 / 16 |
| 180 | `feat/file-id-identity` | Catalog 0008, files keyed by `file_id` | `0008_file_id.sql`, `file_id()` registration, `catalog_repository.py` (and `download_decisions`, D10), `sightings.py`, `merger.py`, webui `catalog_read.py` with aliases keeping its surface until 190, `test_migration_0008.py`, delete `test_migration_0003.py`, builders, merge helpers, `test_webui_sql_console.py` | **520** / 17 |
| 190 | `feat/webui-file-id-urls` | File pages addressed by `file_id` | `/files/{file_id}`, views, detail labels, ed2k link from `native_id`, `coverage.py`, `format.py`, webui tests, `docs/operate.md` (D13) | 190 / 11 |
| 200 | `feat/vacuum-after-migration` | Catalog 0009, `VACUUM` | `0009_vacuum.sql` (the directive's consumer), its test, `docs/limits.md` (first boot after the upgrade, with the duration measured in D18), `docs/contributing/architecture.md` (schema, version 9, MD4 and `file_observations` passages), `docs/operate.md` (merge of an older snapshot) | 130 / 5 |
| 210 | `docs/stage1-closing` | Closing | Holistic findings, `BACKLOG.md` reconciled, handoff | n/a |

The deletions run in import order: the command (30), then merge's copy of ranges (40), then the webui's
compacted days (50), then the engine (60), which is the last importer of `domain/retention` and of
`sightings.covered_by_range`, then the bucketing (70), then the sightings fallback (80), and then the table
(90). Between blocks 30 and 80, `main` carries code that is still tested but no longer reachable from a shipped
entry point: an engine without a command, then a bucketing module without an engine, then a fallback that
reads a table nothing fills.

Blocks 140 and 180 pass the 500-line bound. A storage or identity switch moves the migration together with
every writer and reader of the rebuilt tables (`record_observation`, `sightings`, `merge`), or the gate
fails. Their pure parts already left in blocks 130 and 150. The expand and contract alternative (dual write,
switch the reads, then drop) adds about 300 lines of temporary code.

Surfaces whose consumer arrives in a later block:

- the directive (block 10), consumed by 0009 (block 200);
- the functions of block 130, consumed by 0007 and `record_observation` (block 140);
- the webui aliases of block 180, removed by block 190.

No migration file lands before its turn, since a catalog stamped higher skips the lower numbers added later;
hence `VACUUM` comes last. Blocks 140 and 180 wait for their measurements (D18), and block 150 waits for the
copy check at block 140's tip.

**Adjacent backlog items.**

- **"Lossless compact observation storage"**: done by this lot, deleted in block 140.
- **"Multi network", stage 1**: deleted at Wrap. The parent line stays while stages 2 to 5 remain.
- **Stage 2**: stays open, and now also carries the download side's move to `FileKey` (D10). Its text gains
  that point at Wrap.
- **Stage 3**: stays open. Its "`v5.0.0`" goes at Wrap (D16).
- **Stage 4**: stays open. It already creates the source tables, so D5 changes nothing in its text.
- **"Paginate the file detail timeline"**: stays open. It is a paging change to the detail page, which block
  140 does not make. The timeline query changes form, and the page still renders every sighting.

The edits to the Stage 2 and Stage 3 lines rest on the operator's approval of this spec, per `BACKLOG.md`'s
rule.

## 6. Acceptance

Each criterion names the output that would prove it wrong.

- **The migrated catalog loses nothing.** On the copy of the node's catalog (D18), a check script compares
  two exports, each a sorted multiset with exact duplicates removed:
  - before the migration, the old `file_observations` columns as stored: `ed2k_hash`, `filename`,
    `size_bytes`, `source_count`, `complete_source_count`, `media_length_sec`, `bitrate_kbps`, `codec`,
    `file_type`, the `raw_meta` text, `keyword`, the `observed_at` text and `node_id`;
  - after the migration, the same columns rebuilt from `files`, `observation_variants` and `observations`
    the way the readers do: `native_id`, `raw_meta` unfolded back into its three columns and its original
    text, and the integer formatted by `utc_iso`.

  It prints `mismatches: 0`, and any other count fails it. It is shown failing twice first: on a copy with
  one observation deleted after the migration (the trigger dropped for that purpose), and with a fold that
  omits one key.
- **It fits.** On the same copy, run by the image under `mem_limit: 2g`, with a sampler recording `anon`
  from `/sys/fs/cgroup/memory.stat` and the sizes of `catalog.db`, `catalog.db-wal` and SQLite's open
  temporary files every second:
  - `docker inspect -f '{{.State.OOMKilled}}'` prints `false`;
  - the peak `anon` stays under 1 GiB;
  - the peak of the database, WAL and temporary files stays within 1 GB of the starting size;
  - `PRAGMA user_version` prints the stack's top version;
  - the catalog file is under 0.5 GB once 0009 has run.

  It is wrong if `true`, if a peak exceeds its bound, if the version is lower, or if the file is above
  0.5 GB. Not `memory.peak`, which counts the page cache and reached the 2 GB limit while `anon` held 0.25 MB,
  just from reading a 3 GB file (spec review, in the image).
- **No eD2k identity left in catalog.db's schema.** On the migrated copy, `SELECT m.name, p.name FROM
  sqlite_schema AS m, pragma_table_info(m.name) AS p WHERE m.type = 'table' AND (p.name LIKE '%ed2k%' OR
  p.name LIKE 'aich%')` returns no row. `SELECT name FROM sqlite_schema WHERE name IN ('file_observations',
  'file_observation_ranges', 'sources', 'source_observations')` returns no row either.
- **A catalog holding ranges refuses.** `open_catalog` on a catalog stamped 5 with one range row raises
  `MigrationError` whose text contains `file_observation_ranges`, and `user_version` stays 5
  (`test_migration_0006.py`, watched failing first).
- **The directive is exact.** These go through `_load_scripts` and `_apply_migrations` on a temporary
  scripts directory:
  - a first line `-- migration: no-transction` raises `MigrationError` at load;
  - a `VACUUM` script without the directive fails with SQLite's "cannot VACUUM from within a transaction";
  - the same script with the directive runs and stamps its version;
  - a failing script with the directive leaves `user_version` at its previous value.

  Each is a test, watched failing first.
- **Pragmas restored.**
  - After `open_catalog` on a connection set to `secure_delete = 1` and the default cache, both read their
    prior values (the migration tests). A 0, or -262144, fails it.
  - 0007's test, with `secure_delete = ON` forced first, asserts that the WAL after its `DROP` stays below
    the size of the dropped table.
- **The fold does not drift.** The cross test of D11 passes, and fails when the mapper's fold and 0007's
  differ by one pair (watched failing).
- **Webui.** `GET /files/<file_id hex>` of a seeded file answers 200 and shows its `native_id`. `GET
  /files/<its eD2k hash>` and `GET /files/zz` answer 404 (webui tests).
- **Downloads and search untouched (D10, section 2).** `git diff origin/main...origin/<top branch> --stat`
  prints nothing for these paths, under `packages/crawler/src/mulewatch/` unless noted:
  - `adapters/persistence_sqlite/migrations/local`;
  - `adapters/persistence_sqlite/download_repository.py`;
  - `ports/mule_download_client.py`;
  - `ports/mule_client.py`;
  - `packages/matching/src`.

  The compose smoke test passes in CI.
- **Metrics unchanged.** `git diff origin/main...origin/<top branch> --
  packages/crawler/src/mulewatch/adapters/observability/prometheus_sink.py` prints nothing.
- **No release.** `git tag --contains origin/<top branch>` prints nothing when the stack merges.
- **Every block** is green at its tip (`uv run poe check`) and measured by the workflow's command, with its
  numbers in its report. Blocks 140 and 180 carry their justification in their pull request.

## 7. Risks

- **The real catalog is not the synthetic one** (D18): denser rows, more variants once the complete counts
  are folded in. The check at block 140's tip decides.
- **The read shapes are new.** A latest sighting is now the maximum over a file's few variants of a seek on
  the key of `observations`. The bounds of D18 guard the `/files` page.
- **The fold must not drift** between the migration and the mapper (D11). The cross test guards it.
- **`INSERT OR IGNORE` swallows CHECK violations.** The repository keeps validating the eD2k `native_id` in
  Python before writing, as it does for the hash today.
- **merge**: a snapshot from another node at version 5 cannot merge into an output at the stack's top version
  until the new code has opened it, which migrates it in place. `docs/operate.md` says so (block 200).
