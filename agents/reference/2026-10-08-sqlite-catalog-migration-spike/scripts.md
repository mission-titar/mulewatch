# Spike scripts

The spike's scripts, kept as text so the gate's `ruff check .` does not lint them. Copy one out to
a `.py` file to rerun it; `REPORT.md` gives each one's command.

## `gen_old.py`

```python
"""Build a synthetic OLD-schema catalog.db (migrations 0001..0005) with realistic skew.

usage: python3 gen_old.py OUT.db ROWS
"""

import json
import random
import sqlite3
import sys
import time
import uuid
from bisect import bisect
from datetime import UTC, datetime
from itertools import accumulate
from pathlib import Path

REPO = Path("/home/geoffrey/Repositories/emule-indexer/packages/crawler/src/mulewatch")
MIGR = REPO / "adapters/persistence_sqlite/migrations/catalog"
N_FILES, N_VARIANTS = 1933, 7433
out, rows = Path(sys.argv[1]), int(sys.argv[2])
rng = random.Random(42)

out.unlink(missing_ok=True)
c = sqlite3.connect(out, autocommit=True)
c.execute("PRAGMA journal_mode=WAL")
c.execute("PRAGMA foreign_keys=ON")
for i, f in enumerate(sorted(MIGR.glob("*.sql")), 1):
    c.execute("BEGIN")
    c.executescript(f.read_text())
    c.execute(f"PRAGMA user_version={i}")
    c.execute("COMMIT")
# Indexes stay in place during the load: the real ones grew by appends, so their fill is realistic.
c.execute("PRAGMA cache_size=-1000000")

hashes = [f"{rng.getrandbits(128):032x}" for _ in range(N_FILES)]
sizes = [rng.randint(50_000_000, 400_000_000) for _ in range(N_FILES)]
c.executemany("INSERT INTO files (ed2k_hash, size_bytes) VALUES (?, ?)", zip(hashes, sizes))

raw_metas = []
for k in range(20):
    pairs = [["Filename", "x" * 8], ["Sources", 0], ["Complete", 0], ["Type", "Video"],
             ["Length", 1320 + k], ["Bitrate", 1100], ["Codec", "xvid"], ["Format", "avi"]]
    s = json.dumps(pairs)
    raw_metas.append(s + " " * max(0, 170 - len(s)))
words = ["keroro", "titar", "mission titar", "keroro gunso", "grenouille", "teletoon", "giroro",
         "tamama", "kururu", "dororo", "natsumi", "fuyuki", "keroro vf", "keroro french"]
node = str(uuid.UUID(int=rng.getrandbits(128)))[:8]  # short: calibrates row width to the node

# Every file gets >= 1 variant, the rest land skewed (popular files have more names).
file_w = [rng.lognormvariate(0, 0.6) for _ in range(N_FILES)]  # max file ~6x mean, as on the node
owner = list(range(N_FILES)) + rng.choices(range(N_FILES), weights=file_w, k=N_VARIANTS - N_FILES)
variants = []
for v, f in enumerate(owner):
    name = f"Keroro Mission Titar - {rng.randint(1, 103):03d}{rng.choice('AB')} - " + "".join(
        rng.choice("abcdefghij klmnopqrstuvwxyz") for _ in range(rng.randint(5, 35))
    ) + rng.choice([".avi", ".mkv", ".mp4"])
    has_media = rng.random() < 0.3
    variants.append((
        hashes[f], name, sizes[f], rng.randint(100, 1500) if has_media else None,
        rng.randint(500, 2000) if has_media else None, "xvid" if has_media else None,
        "Video" if rng.random() < 0.8 else None, raw_metas[v % 20], rng.choice(words), node,
        rng.randint(0, 3),  # complete_source_count, constant per variant
    ))
# Observation weight of a variant: its file's popularity, split among its names unevenly.
n_of = [owner.count(f) for f in range(N_FILES)]
var_w = [file_w[f] * rng.random() / n_of[f] for f in owner]
cum = list(accumulate(var_w))
total = cum[-1]

t_us = int(datetime(2026, 6, 10, tzinfo=UTC).timestamp() * 1e6)
step = int(120 * 86400 * 1e6 / rows)
t0 = time.perf_counter()


def gen(n: int):
    global t_us
    for _ in range(n):
        v = variants[bisect(cum, rng.random() * total)]
        t_us += rng.randint(1, 2 * step)
        sc = rng.randint(1, 40)
        yield (v[0], v[1], v[2], sc, min(v[10], sc), v[3], v[4], v[5], v[6], v[7], v[8],
               datetime.fromtimestamp(t_us / 1e6, UTC).isoformat(timespec="microseconds"), v[9])


INSERT = ("INSERT INTO file_observations (ed2k_hash, filename, size_bytes, source_count,"
          " complete_source_count, media_length_sec, bitrate_kbps, codec, file_type, raw_meta,"
          " keyword, observed_at, node_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)")
done = 0
while done < rows:
    n = min(500_000, rows - done)
    c.execute("BEGIN")
    c.executemany(INSERT, gen(n))
    c.execute("COMMIT")
    done += n
print(f"rows {done} in {time.perf_counter() - t0:.0f}s", flush=True)
c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
per_file = c.execute(
    "SELECT max(n), min(n), count(*) FROM (SELECT count(*) n FROM file_observations GROUP BY ed2k_hash)"
).fetchone()
print("obs per file max/min/files:", per_file)
print("distinct variants:", c.execute(
    "SELECT count(*) FROM (SELECT DISTINCT ed2k_hash, filename, size_bytes, media_length_sec,"
    " bitrate_kbps, codec, file_type, raw_meta, keyword, node_id FROM file_observations)"
).fetchone()[0])
print("file bytes:", out.stat().st_size)
```

## `q1_storage.py`

```python
"""Q1: bytes per row of the old vs new observation layouts (dbstat).

usage: python3 q1_storage.py old OLD.db         -> dbstat of file_observations + its indexes
       python3 q1_storage.py new MIGRATED.db    -> builds each new layout in its own file, rows
           appended in observed_at order with the indexes already present (= steady-state growth
           and = the batched migration), then the same after VACUUM (dense lower bound).
"""

import sqlite3
import sys
from pathlib import Path

REAL_ROWS = 11_500_000
mode, src = sys.argv[1], Path(sys.argv[2])


def dbstat(c: sqlite3.Connection, like: str) -> list[tuple[str, int]]:
    return c.execute(
        "SELECT name, sum(pgsize) FROM dbstat WHERE name LIKE ? GROUP BY name ORDER BY 2 DESC",
        (like,),
    ).fetchall()


def show(c: sqlite3.Connection, like: str, rows: int) -> int:
    total = 0
    for name, sz in dbstat(c, like):
        total += sz
        print(f"    {name:44} {sz / rows:6.1f} B/row")
    print(f"    {'TOTAL':44} {total / rows:6.1f} B/row -> {total / rows * REAL_ROWS / 1e9:5.2f} GB at 11.5M")
    return total


if mode == "old":
    c = sqlite3.connect(src)
    rows = c.execute("SELECT count(*) FROM file_observations").fetchone()[0]
    print(f"OLD schema, {rows} rows, sqlite {sqlite3.sqlite_version}")
    show(c, "%file_observations%", rows)
    sys.exit()

LAYOUTS = {
    "a  rowid + (variant_id, observed_at)": [
        "CREATE TABLE observations (variant_id INTEGER NOT NULL, observed_at INTEGER NOT NULL,"
        " source_count INTEGER NOT NULL)",
        "CREATE INDEX i_vo ON observations (variant_id, observed_at)"],
    "b  WITHOUT ROWID PK (variant_id, observed_at, source_count)": [
        "CREATE TABLE observations (variant_id INTEGER NOT NULL, observed_at INTEGER NOT NULL,"
        " source_count INTEGER NOT NULL, PRIMARY KEY (variant_id, observed_at, source_count))"
        " WITHOUT ROWID"],
}
LAYOUTS["c-a  (a) + (observed_at)"] = [*LAYOUTS["a  rowid + (variant_id, observed_at)"],
                                       "CREATE INDEX i_o ON observations (observed_at)"]
LAYOUTS["c-b  (b) + (observed_at)"] = [*LAYOUTS["b  WITHOUT ROWID PK (variant_id, observed_at, source_count)"],
                                       "CREATE INDEX i_o ON observations (observed_at)"]

s = sqlite3.connect(src)
rows = s.execute("SELECT count(*) FROM observations").fetchone()[0]
dups = s.execute("SELECT count(*) FROM (SELECT 1 FROM observations GROUP BY variant_id, observed_at"
                 " HAVING count(*) > 1)").fetchone()[0]
print(f"NEW layouts, {rows} rows, duplicate (variant_id, observed_at) in synthetic data: {dups}")
for i, (label, ddl) in enumerate(LAYOUTS.items()):
    out = src.parent / f"q1_layout_{i}.db"
    out.unlink(missing_ok=True)
    c = sqlite3.connect(out, autocommit=True)
    c.execute("PRAGMA journal_mode=WAL")
    for stmt in ddl:
        c.execute(stmt)
    c.execute("ATTACH ? AS src", (str(src),))
    c.execute("BEGIN")
    c.execute("INSERT INTO observations SELECT variant_id, observed_at, source_count"
              " FROM src.observations ORDER BY observed_at")
    c.execute("COMMIT")
    c.execute("DETACH src")
    print(f"  {label}: appended in time order")
    grown = show(c, "%", rows)
    c.execute("VACUUM")
    print(f"  {label}: after VACUUM")
    dense = show(c, "%", rows)
    print(f"    fill factor of the appended layout: {dense / grown:.0%}")
    c.close()
    out.unlink()
    Path(f"{out}-wal").unlink(missing_ok=True)
    Path(f"{out}-shm").unlink(missing_ok=True)
```

## `q2_index_shrink.py`

```python
"""Q2: do the OLD table's indexes give pages back as rows are deleted by id range?

usage: python3 q2_index_shrink.py OLD.db   (deletes the oldest 25/50/75/100 %, dbstat after each)
"""

import sqlite3
import sys

c = sqlite3.connect(sys.argv[1], autocommit=True)
c.execute("PRAGMA journal_mode=WAL")
c.execute("DROP TRIGGER file_observations_no_delete")
total = c.execute("SELECT max(id) FROM file_observations").fetchone()[0]


def stat(label: str) -> None:
    rows = dict(c.execute("SELECT name, count(*) FROM dbstat WHERE name LIKE '%file_observations%'"
                          " GROUP BY name").fetchall())
    left = c.execute("SELECT count(*) FROM file_observations").fetchone()[0]
    print(f"{label:>5} rows left {left:>8}  freelist {c.execute('PRAGMA freelist_count').fetchone()[0]:>7}"
          "  " + "  ".join(f"{k.replace('idx_file_observations_', 'idx_')}={v}" for k, v in sorted(rows.items())))


print("sqlite", sqlite3.sqlite_version, "(pages per b-tree)")
stat("0%")
for q in (1, 2, 3, 4):
    hi = total * q // 4
    for a in range(total * (q - 1) // 4 + 1, hi + 1, 50_000):
        c.execute("DELETE FROM file_observations WHERE id BETWEEN ? AND ?", (a, min(a + 49_999, hi)))
    stat(f"{q * 25}%")
```

## `q2_migrate.py`

```python
"""Q2/Q3/Q6: batched in-place migration of an OLD catalog.db to the file_id/variants model.

usage: python3 q2_migrate.py DB BATCH CHECKPOINT(0|1) OBS_AT_INDEX(0|1) VACUUM(none|memory|file)
Runs under the runner's pragmas (WAL, foreign_keys=ON, recursive_triggers=ON, temp_store=MEMORY).
Samples DB/WAL/temp-file sizes and RSS every 20 ms in a thread; prints peaks per phase.
"""

import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path

db = Path(sys.argv[1])
BATCH, CKPT, OBS_AT_IDX, VAC = int(sys.argv[2]), sys.argv[3] == "1", sys.argv[4] == "1", sys.argv[5]
JOIN = sys.argv[6] if len(sys.argv) > 6 else "hash"  # hash: UDF content_hash per row; dict: raw-tuple dict
wal = Path(f"{db}-wal")
NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://mission-titar.github.io/mulewatch/file")


def file_id(network: str, native_id: str) -> bytes:
    return uuid.uuid5(NS, f"{network}:{native_id}").bytes


def content_hash(*values: object) -> bytes:
    values = tuple(v.hex() if isinstance(v, bytes) else v for v in values)  # file_id blob
    canon = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.blake2b(canon, digest_size=16).digest()


def fold_meta(raw_meta: str, codec: str | None, file_type: str | None, complete: int) -> str:
    pairs = json.loads(raw_meta)
    pairs += [["codec", codec], ["file_type", file_type], ["complete_source_count", complete]]
    return json.dumps(pairs, ensure_ascii=False)


def size(p: Path) -> int:
    try:
        return p.stat().st_size
    except FileNotFoundError:
        return 0


def temp_bytes() -> int:
    """Sum of the deleted (anonymous) files this process holds open: SQLite's temp files."""
    total = 0
    for fd in os.listdir("/proc/self/fd"):
        try:
            if os.readlink(f"/proc/self/fd/{fd}").endswith("(deleted)"):
                total += os.stat(f"/proc/self/fd/{fd}").st_size
        except OSError:
            pass
    return total


def rss() -> int:
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS"):
            return int(line.split()[1]) * 1024
    return 0


class Peaks:
    def __init__(self) -> None:
        self.reset()
        if os.environ.get("Q2_SAMPLE", "1") == "1":
            threading.Thread(target=self._run, daemon=True).start()

    def reset(self) -> None:
        self.db = self.wal = self.tmp = self.rss = self.sum = 0

    def _run(self) -> None:
        while True:
            d, w, t = size(db), size(wal), temp_bytes()
            self.db, self.wal, self.tmp = max(self.db, d), max(self.wal, w), max(self.tmp, t)
            self.sum, self.rss = max(self.sum, d + w + t), max(self.rss, rss())
            time.sleep(0.02)

    def line(self) -> str:
        return (f"peak db={self.db / 1e9:.2f}GB wal={self.wal / 1e9:.2f}GB tmp={self.tmp / 1e9:.2f}GB"
                f" db+wal+tmp={self.sum / 1e9:.2f}GB rss={self.rss / 1e6:.0f}MB")


c = sqlite3.connect(db, autocommit=True)
assert c.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
c.execute(f"PRAGMA foreign_keys={os.environ.get('Q2_FK', 'ON')}")
c.execute("PRAGMA recursive_triggers=ON")
c.execute("PRAGMA temp_store=MEMORY")
for extra in filter(None, os.environ.get("Q2_PRAGMAS", "").split(";")):  # bind-mount experiments
    print("extra pragma", extra, c.execute(f"PRAGMA {extra}").fetchall())
c.create_function("file_id", 2, file_id, deterministic=True)
c.create_function("content_hash", -1, content_hash, deterministic=True)
c.create_function("fold_meta", 4, fold_meta, deterministic=True)
peaks = Peaks()
time.sleep(0.1)
start_size = size(db) + size(wal)
print(f"sqlite {sqlite3.sqlite_version} start: db={start_size / 1e9:.2f}GB batch={BATCH}"
      f" checkpoint={CKPT} observed_at_index={OBS_AT_IDX} join={JOIN}", flush=True)


def pragma(name: str) -> int:
    return int(c.execute(f"PRAGMA {name}").fetchone()[0])


def phase(label: str, t0: float) -> None:
    print(f"[{label}] {time.perf_counter() - t0:.1f}s {peaks.line()} page_count={pragma('page_count')}"
          f" freelist={pragma('freelist_count')}", flush=True)
    peaks.reset()


# Old row -> variant key: the old columns, with codec/file_type/complete folded into raw_meta.
VCOLS = ["file_id('ed2k', o.ed2k_hash)", "o.filename", "o.size_bytes", "o.media_length_sec",
         "o.bitrate_kbps", "fold_meta(o.raw_meta, o.codec, o.file_type, o.complete_source_count)",
         "o.keyword", "o.node_id"]
VKEY = ", ".join(VCOLS)
VKEY_AS = ", ".join(f"{e} AS {n}" for e, n in zip(VCOLS, "abcdefgh"))
US = "(unixepoch(substr(o.observed_at, 1, 19)) * 1000000 + CAST(substr(o.observed_at, 21, 6) AS INTEGER))"

t0 = time.perf_counter()
bad = c.execute("SELECT count(*) FROM file_observations WHERE length(observed_at) != 32"
                " OR substr(observed_at, 27) != '+00:00'").fetchone()[0]
assert bad == 0, bad
phase("preflight observed_at format scan", t0)

t0 = time.perf_counter()
c.execute("BEGIN")
c.executescript(f"""
    DROP TRIGGER files_no_update; DROP TRIGGER files_no_delete;
    DROP TRIGGER file_observations_no_update; DROP TRIGGER file_observations_no_delete;
    DROP TRIGGER match_decisions_no_update; DROP TRIGGER match_decisions_no_delete;
    DROP TRIGGER source_observations_no_update; DROP TRIGGER source_observations_no_delete;
    DROP TRIGGER file_observation_ranges_no_update; DROP TRIGGER file_observation_ranges_no_delete;
    ALTER TABLE files RENAME TO files_old;
    ALTER TABLE match_decisions RENAME TO match_decisions_old;
    DROP INDEX idx_match_decisions_ed2k_hash; DROP INDEX idx_match_decisions_hash_target_decided;
    CREATE TABLE files (
        file_id BLOB PRIMARY KEY CHECK (length(file_id) = 16),
        network TEXT NOT NULL, native_id TEXT NOT NULL, size_bytes INTEGER NOT NULL,
        UNIQUE (network, native_id));
    CREATE TABLE observation_variants (
        variant_id INTEGER PRIMARY KEY,
        file_id BLOB NOT NULL REFERENCES files (file_id),
        filename TEXT NOT NULL, size_bytes INTEGER NOT NULL,
        media_length_sec INTEGER, bitrate_kbps INTEGER, raw_meta TEXT NOT NULL,
        keyword TEXT NOT NULL, node_id TEXT NOT NULL,
        content_hash BLOB NOT NULL UNIQUE);
    CREATE INDEX idx_observation_variants_file_id ON observation_variants (file_id);
    CREATE TABLE observations (
        variant_id INTEGER NOT NULL REFERENCES observation_variants (variant_id),
        observed_at INTEGER NOT NULL, source_count INTEGER NOT NULL);
    CREATE INDEX idx_observations_variant_observed ON observations (variant_id, observed_at);
    {"CREATE INDEX idx_observations_observed_at ON observations (observed_at);" if OBS_AT_IDX else ""}
    CREATE TABLE match_decisions (
        id INTEGER PRIMARY KEY, file_id BLOB NOT NULL REFERENCES files (file_id),
        target_id TEXT NOT NULL, rule_name TEXT NOT NULL, tier TEXT NOT NULL,
        decided_at TEXT NOT NULL, node_id TEXT NOT NULL);
    CREATE INDEX idx_match_decisions_file_target_decided
        ON match_decisions (file_id, target_id, decided_at);
    INSERT INTO files SELECT file_id('ed2k', ed2k_hash), 'ed2k', ed2k_hash, size_bytes FROM files_old;
    INSERT INTO match_decisions SELECT id, file_id('ed2k', ed2k_hash), target_id, rule_name, tier,
        decided_at, node_id FROM match_decisions_old;
""")
c.execute("COMMIT")
phase("txn A: DDL + files + match_decisions", t0)

t0 = time.perf_counter()
RAW = ("ed2k_hash, filename, size_bytes, media_length_sec, bitrate_kbps, codec, file_type, raw_meta,"
       " complete_source_count, keyword, node_id")
raw_map: dict[tuple[object, ...], int] = {}
c.execute("BEGIN")
if JOIN == "hash":
    c.execute(f"""
        INSERT INTO observation_variants (file_id, filename, size_bytes, media_length_sec,
            bitrate_kbps, raw_meta, keyword, node_id, content_hash)
        SELECT a, b, c, d, e, f, g, h, content_hash(a, b, c, d, e, f, g, h) FROM (
            SELECT DISTINCT {VKEY_AS} FROM file_observations o)""")
else:  # one streaming pass: raw tuple -> variant_id, variants deduped by content_hash
    by_hash: dict[bytes, int] = {}
    for r in c.execute(f"SELECT {RAW} FROM file_observations"):  # streamed, not fetched
        if r in raw_map:
            continue
        h, fn, sz, ml, br, codec, ft, meta, comp, kw, nd = r
        v = (file_id("ed2k", h), fn, sz, ml, br, fold_meta(meta, codec, ft, comp), kw, nd)
        ch = content_hash(*v)
        if ch not in by_hash:
            by_hash[ch] = c.execute(
                "INSERT INTO observation_variants (file_id, filename, size_bytes,"
                " media_length_sec, bitrate_kbps, raw_meta, keyword, node_id, content_hash)"
                " VALUES (?,?,?,?,?,?,?,?,?)", (*v, ch)).lastrowid
        raw_map[r] = by_hash[ch]
    c.create_function("variant_of", 11, lambda *k: raw_map[k], deterministic=True)
c.execute("COMMIT")
print("variants:", c.execute("SELECT count(*) FROM observation_variants").fetchone()[0])
phase(f"txn B: variants ({JOIN})", t0)

lo, hi = c.execute("SELECT min(id), max(id) FROM file_observations").fetchone()
total = c.execute("SELECT count(*) FROM file_observations").fetchone()[0]
t0 = time.perf_counter()
moved, a, report_every, next_report = 0, lo, total // 8, total // 8
while a <= hi:
    b = a + BATCH - 1
    c.execute("BEGIN")
    cur = c.execute(f"""
        INSERT INTO observations (variant_id, observed_at, source_count)
        SELECT v.variant_id, {US}, o.source_count
        FROM file_observations o JOIN observation_variants v
            ON v.content_hash = content_hash({VKEY})
        WHERE o.id BETWEEN ? AND ? ORDER BY o.id""" if JOIN == "hash" else f"""
        INSERT INTO observations (variant_id, observed_at, source_count)
        SELECT variant_of({", ".join("o." + x.strip() for x in RAW.split(","))}), {US}, o.source_count
        FROM file_observations o WHERE o.id BETWEEN ? AND ? ORDER BY o.id""", (a, b))
    n = cur.rowcount
    d = c.execute("DELETE FROM file_observations WHERE id BETWEEN ? AND ?", (a, b)).rowcount
    assert n == d, (n, d)
    c.execute("COMMIT")
    if CKPT:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    moved += n
    a = b + 1
    if moved >= next_report or a > hi:
        el = time.perf_counter() - t0
        print(f"  moved {moved}/{total} {el:.0f}s {moved / el:.0f} rows/s db={size(db) / 1e9:.2f}GB"
              f" wal={size(wal) / 1e9:.2f}GB page_count={pragma('page_count')}"
              f" freelist={pragma('freelist_count')} rss_now={rss() / 1e6:.0f}MB {peaks.line()}", flush=True)
        next_report += report_every
phase(f"batched copy+delete ({moved / (time.perf_counter() - t0):.0f} rows/s)", t0)
c.execute("PRAGMA wal_checkpoint(TRUNCATE)")

t0 = time.perf_counter()
c.execute("BEGIN")
c.executescript("""
    DROP TABLE file_observations; DROP TABLE source_observations; DROP TABLE sources;
    DROP TABLE file_observation_ranges; DROP TABLE match_decisions_old; DROP TABLE files_old;
    CREATE TRIGGER files_no_update BEFORE UPDATE ON files
    BEGIN SELECT RAISE(ABORT, 'files is append-only'); END;
    CREATE TRIGGER files_no_delete BEFORE DELETE ON files
    BEGIN SELECT RAISE(ABORT, 'files is append-only'); END;
    CREATE TRIGGER observations_no_update BEFORE UPDATE ON observations
    BEGIN SELECT RAISE(ABORT, 'observations is append-only'); END;
    CREATE TRIGGER observations_no_delete BEFORE DELETE ON observations
    BEGIN SELECT RAISE(ABORT, 'observations is append-only'); END;
""")
c.execute("COMMIT")
phase("txn D: drop old tables (high-water file)", t0)
c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
print("after drop: db file", size(db), "freelist pages", pragma("freelist_count"), "of", pragma("page_count"))
print("foreign_key_check:", c.execute("PRAGMA foreign_key_check").fetchall())
for name, sz, pages in c.execute(
    "SELECT name, sum(pgsize), count(*) FROM dbstat WHERE name LIKE '%observ%' OR name LIKE '%files%'"
    " GROUP BY name ORDER BY 2 DESC"
):
    print(f"  dbstat {name:40} {sz / 1e6:9.1f} MB {sz / moved:6.1f} B/row")
print("observations:", c.execute("SELECT count(*) FROM observations").fetchone()[0])

if VAC != "none":
    c.execute(f"PRAGMA temp_store={'MEMORY' if VAC == 'memory' else 'FILE'}")
    peaks.reset()
    t0 = time.perf_counter()
    c.execute("VACUUM")
    phase(f"VACUUM temp_store={VAC}", t0)
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    print("after VACUUM + checkpoint: db file", size(db), "wal", size(wal))
c.close()
print("final db file", size(db), "wal", size(wal))
```

## `q2_us_check.py`

```python
"""Check the SQL ISO-text -> epoch-microseconds expression against Python on edge values."""

import random
import sqlite3
from datetime import UTC, datetime, timedelta

US = "(unixepoch(substr(?1, 1, 19)) * 1000000 + CAST(substr(?1, 21, 6) AS INTEGER))"
c = sqlite3.connect(":memory:")
rng = random.Random(1)
base = datetime(1970, 1, 1, tzinfo=UTC)
samples = [base, datetime(2026, 10, 8, 13, 47, 23, 286400, UTC), datetime(2026, 12, 31, 23, 59, 59, 999999, UTC),
           datetime(2028, 2, 29, 0, 0, 0, 1, UTC)]
samples += [base + timedelta(microseconds=rng.randrange(2**62 // 4000)) for _ in range(100_000)]
for d in samples:
    iso = d.isoformat(timespec="microseconds")
    want = (d - base) // timedelta(microseconds=1)
    got = c.execute(f"SELECT {US}", (iso,)).fetchone()[0]
    assert got == want, (iso, got, want)
print(sqlite3.sqlite_version, f"{len(samples)} timestamps: SQL expression == Python exactly")
```

## `q3_creep.py`

```python
"""Q3: which part of the batched copy makes RSS creep? usage: q3_creep.py OLD.db delete|udf|both"""

import sqlite3
import sys

db, mode = sys.argv[1], sys.argv[2]


def rss() -> int:
    return next(int(l.split()[1]) // 1024 for l in open("/proc/self/status") if l.startswith("VmRSS"))


c = sqlite3.connect(db, autocommit=True)
c.execute("PRAGMA journal_mode=WAL")
c.execute("DROP TRIGGER IF EXISTS file_observations_no_delete")
c.execute("CREATE TABLE IF NOT EXISTS sink (v INTEGER, t TEXT, s INTEGER)")
c.create_function("udf", 3, lambda a, b, d: len(a) + len(b) + d, deterministic=True)
hi = c.execute("SELECT max(id) FROM file_observations").fetchone()[0]
print(sqlite3.sqlite_version, mode, "start rss", rss(), "MB")
for a in range(1, hi + 1, 50_000):
    c.execute("BEGIN")
    if mode in ("udf", "both"):
        c.execute("INSERT INTO sink SELECT udf(ed2k_hash, filename, source_count), observed_at,"
                  " source_count FROM file_observations WHERE id BETWEEN ? AND ?", (a, a + 49_999))
    if mode in ("delete", "both"):
        c.execute("DELETE FROM file_observations WHERE id BETWEEN ? AND ?", (a, a + 49_999))
    c.execute("COMMIT")
    if a % 500_000 == 1:
        print(f"  {a + 49_999}: rss {rss()} MB")
print("end rss", rss(), "MB")
```

## `q3_memory.py`

```python
"""Q3: peak RSS of (i) CREATE INDEX after fill vs (ii) batched fill with the index pre-created.

usage: python3 q3_memory.py index MIGRATED.db TEMP_STORE(MEMORY|FILE)
       python3 q3_memory.py fill MIGRATED.db BATCH
       python3 q3_memory.py distinct OLD.db   (SQL DISTINCT vs streaming dict, variant building)
Each mode runs in its own process; VmHWM is reset via /proc/self/clear_refs before each step.
"""

import sqlite3
import sys
import time
from pathlib import Path

mode, src = sys.argv[1], Path(sys.argv[2])
tmp = src.parent / "q3_work.db"


def mem(key: str) -> int:
    for line in open("/proc/self/status"):
        if line.startswith(key):
            return int(line.split()[1]) // 1024
    return 0


def reset_hwm() -> None:
    with open("/proc/self/clear_refs", "w") as f:
        f.write("5")


def fresh() -> sqlite3.Connection:
    for p in (tmp, Path(f"{tmp}-wal"), Path(f"{tmp}-shm")):
        p.unlink(missing_ok=True)
    c = sqlite3.connect(tmp, autocommit=True)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE observations (variant_id INTEGER NOT NULL,"
              " observed_at INTEGER NOT NULL, source_count INTEGER NOT NULL)")
    c.execute("ATTACH ? AS src", (str(src),))
    return c


print(f"sqlite {sqlite3.sqlite_version} mode={mode} {sys.argv[3:]} baseline rss={mem('VmRSS')}MB")
if mode == "index":
    c = fresh()
    c.execute("INSERT INTO observations SELECT variant_id, observed_at, source_count"
              " FROM src.observations ORDER BY observed_at")
    rows = c.execute("SELECT count(*) FROM observations").fetchone()[0]
    c.execute(f"PRAGMA temp_store={sys.argv[3]}")
    for cols in ("variant_id, observed_at", "observed_at"):
        reset_hwm()
        before = mem("VmRSS")
        t = time.perf_counter()
        c.execute(f"CREATE INDEX i_{cols.replace(', ', '_')} ON observations ({cols})")
        hwm = mem("VmHWM")
        print(f"  CREATE INDEX ({cols}) on {rows} rows: {time.perf_counter() - t:.1f}s"
              f" peak rss {hwm}MB (+{hwm - before}MB, {(hwm - before) * 1e6 / rows:.0f} B/row)"
              f" -> +{(hwm - before) * 11.5e6 / rows:.0f}MB at 11.5M")
elif mode == "fill":
    batch = int(sys.argv[3])
    c = fresh()
    c.execute("CREATE INDEX i_vo ON observations (variant_id, observed_at)")
    c.execute("CREATE INDEX i_o ON observations (observed_at)")
    rows = c.execute("SELECT count(*) FROM src.observations").fetchone()[0]
    reset_hwm()
    for a in range(0, rows, batch):
        c.execute("BEGIN")
        c.execute("INSERT INTO observations SELECT variant_id, observed_at, source_count"
                  " FROM src.observations WHERE rowid > ? AND rowid <= ?", (a, a + batch))
        c.execute("COMMIT")
        if (a // batch) % max(1, rows // batch // 8) == 0:
            print(f"  filled {a + batch}: rss {mem('VmRSS')}MB hwm {mem('VmHWM')}MB")
    print(f"  final hwm {mem('VmHWM')}MB (cache_size {c.execute('PRAGMA cache_size').fetchone()[0]})")
elif mode == "distinct":
    c = sqlite3.connect(src, autocommit=True)
    c.execute("PRAGMA temp_store=MEMORY")
    cols = ("ed2k_hash, filename, size_bytes, media_length_sec, bitrate_kbps, codec, file_type,"
            " raw_meta, complete_source_count, keyword, node_id")
    reset_hwm()
    t = time.perf_counter()
    n = c.execute(f"SELECT count(*) FROM (SELECT DISTINCT {cols} FROM file_observations)").fetchone()[0]
    print(f"  SQL DISTINCT (temp_store=MEMORY): {n} keys {time.perf_counter() - t:.1f}s"
          f" peak rss {mem('VmHWM')}MB")
    reset_hwm()
    t = time.perf_counter()
    seen: dict[tuple[object, ...], int] = {}
    for r in c.execute(f"SELECT {cols} FROM file_observations"):  # cursor streams rows
        seen.setdefault(r, len(seen))
    print(f"  Python streaming dict (cursor iteration): {len(seen)} keys {time.perf_counter() - t:.1f}s"
          f" peak rss {mem('VmHWM')}MB")
for p in (tmp, Path(f"{tmp}-wal"), Path(f"{tmp}-shm")):
    p.unlink(missing_ok=True)
```

## `q4_fk.py`

```python
"""Q4: foreign keys and table swaps inside the runner's transaction (foreign_keys=ON before BEGIN)."""

import sqlite3
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
_n = 0


def fresh(fk: bool = True) -> sqlite3.Connection:
    global _n
    _n += 1
    c = sqlite3.connect(TMP / f"q4_{_n}.db", autocommit=True)
    c.execute("PRAGMA journal_mode=WAL")
    if fk:
        c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA recursive_triggers=ON")
    c.executescript(
        """
        CREATE TABLE files (ed2k_hash TEXT PRIMARY KEY, size_bytes INTEGER NOT NULL);
        CREATE TABLE file_observations (id INTEGER PRIMARY KEY,
            ed2k_hash TEXT NOT NULL REFERENCES files (ed2k_hash), filename TEXT);
        CREATE INDEX idx_fo_hash ON file_observations (ed2k_hash);
        CREATE TABLE match_decisions (id INTEGER PRIMARY KEY,
            ed2k_hash TEXT NOT NULL REFERENCES files (ed2k_hash), target_id TEXT);
        CREATE INDEX idx_match_decisions_ed2k_hash ON match_decisions (ed2k_hash);
        CREATE TRIGGER files_no_delete BEFORE DELETE ON files
        BEGIN SELECT RAISE(ABORT, 'files is append-only'); END;
        CREATE TRIGGER files_no_update BEFORE UPDATE ON files
        BEGIN SELECT RAISE(ABORT, 'files is append-only'); END;
        CREATE TRIGGER fo_no_delete BEFORE DELETE ON file_observations
        BEGIN SELECT RAISE(ABORT, 'fo is append-only'); END;
        CREATE TRIGGER md_no_delete BEFORE DELETE ON match_decisions
        BEGIN SELECT RAISE(ABORT, 'md is append-only'); END;
        INSERT INTO files VALUES ('a'*1, 10), ('b', 20);
        INSERT INTO file_observations (ed2k_hash, filename) VALUES ('a', 'x'), ('b', 'y');
        INSERT INTO match_decisions (ed2k_hash, target_id) VALUES ('a', '062A');
        """.replace("'a'*1", "'a'")
    )
    return c


def run(label: str, c: sqlite3.Connection, sql: str) -> None:
    try:
        r = c.execute(sql).fetchall()
        print(f"  {label}: OK {r if r else ''}")
    except sqlite3.Error as e:
        print(f"  {label}: ERROR {type(e).__name__}: {e}")


def schema(c: sqlite3.Connection) -> None:
    for t, n, s in c.execute("SELECT type, name, sql FROM sqlite_schema ORDER BY rowid"):
        print(f"    {t:7} {n:32} {' '.join((s or '').split())}")


print("sqlite", sqlite3.sqlite_version, "python", sys.version.split()[0])

print("\n(a) PRAGMA foreign_keys=OFF inside BEGIN")
c = fresh()
c.execute("BEGIN")
run("set OFF", c, "PRAGMA foreign_keys=OFF")
run("read back", c, "PRAGMA foreign_keys")
run("insert orphan child", c, "INSERT INTO match_decisions (ed2k_hash) VALUES ('zz')")
c.execute("ROLLBACK")
run("read back after txn", c, "PRAGMA foreign_keys")

print("\n(b) PRAGMA defer_foreign_keys=ON inside BEGIN")
c = fresh()
c.execute("BEGIN")
run("set defer", c, "PRAGMA defer_foreign_keys=ON")
run("read back", c, "PRAGMA defer_foreign_keys")
run("insert orphan child", c, "INSERT INTO match_decisions (ed2k_hash) VALUES ('zz')")
run("COMMIT with orphan", c, "COMMIT")
print("  in_transaction after failed COMMIT:", c.in_transaction)
run("insert parent zz (resolves)", c, "INSERT INTO files VALUES ('zz', 1)")
run("COMMIT after resolve", c, "COMMIT")
run("defer flag after commit", c, "PRAGMA defer_foreign_keys")
run("foreign_key_check", c, "PRAGMA foreign_key_check")

print("\n(c) DROP TABLE of a parent with referencing rows (triggers present)")
for defer in (False, True):
    c = fresh()
    c.execute("BEGIN")
    if defer:
        c.execute("PRAGMA defer_foreign_keys=ON")
    run(f"defer={defer} DROP TABLE files", c, "DROP TABLE files")
    run(f"defer={defer} COMMIT", c, "COMMIT")
    if c.in_transaction:
        c.execute("ROLLBACK")
print("  (c2) defer=ON, drop parent then drop the children too, then COMMIT")
c = fresh()
c.execute("BEGIN")
c.execute("PRAGMA defer_foreign_keys=ON")
run("DROP files", c, "DROP TABLE files")
run("DROP file_observations", c, "DROP TABLE file_observations")
run("DROP match_decisions", c, "DROP TABLE match_decisions")
run("COMMIT", c, "COMMIT")
print("  (c3) defer=OFF, drop children first then parent (no orphans at any point)")
c = fresh()
c.execute("BEGIN")
run("DROP file_observations", c, "DROP TABLE file_observations")
run("DROP match_decisions", c, "DROP TABLE match_decisions")
run("DROP files", c, "DROP TABLE files")
run("COMMIT", c, "COMMIT")
run("triggers/indexes left", c, "SELECT type, name FROM sqlite_schema")
print("  (c4) parent with NO children rows referencing it, but child table exists (empty)")
c = fresh()
c.execute("BEGIN")
c.execute("DROP TRIGGER fo_no_delete")
c.execute("DROP TRIGGER md_no_delete")
c.execute("DELETE FROM file_observations")
c.execute("DELETE FROM match_decisions")
run("DROP files (children empty)", c, "DROP TABLE files")
run("COMMIT", c, "COMMIT")
run("insert into orphaned child table", c, "INSERT INTO match_decisions (ed2k_hash) VALUES ('a')")

print("\n(d) ALTER TABLE files RENAME TO files_old: REFERENCES in other tables")
for legacy in (False, True):
    for fk in (True, False):
        c = fresh(fk=fk)
        c.execute("BEGIN")
        c.execute(f"PRAGMA legacy_alter_table={'ON' if legacy else 'OFF'}")
        run(f"legacy={legacy} fk={fk} rename", c, "ALTER TABLE files RENAME TO files_old")
        c.execute("COMMIT")
        print("    match_decisions sql:", c.execute(
            "SELECT sql FROM sqlite_schema WHERE name='match_decisions'").fetchone()[0].split(",")[1])
        print("    trigger sql:", c.execute(
            "SELECT tbl_name, sql FROM sqlite_schema WHERE name='files_no_delete'").fetchone())
c.execute("PRAGMA foreign_keys=ON")
run("  last one (legacy ON, fk OFF) then fk ON: insert child", c,
    "INSERT INTO match_decisions (ed2k_hash) VALUES ('a')")
run("  foreign_key_check", c, "PRAGMA foreign_key_check")

print("\n(e) RECIPE: rename-old-away, create-new-clean, batched copy, drop old. FK ON throughout.")
c = fresh()
NS = "6ba7b811-9dad-11d1-80b4-00c04fd430c8"
import uuid

c.create_function("file_id", 2, lambda net, nid: uuid.uuid5(uuid.UUID(NS), f"{net}:{nid}").bytes,
                  deterministic=True)
steps = [
    # txn 1: move old tables out of the way (legacy_alter_table OFF = default: references follow)
    """
    ALTER TABLE files RENAME TO files_old;
    ALTER TABLE match_decisions RENAME TO match_decisions_old;
    DROP INDEX idx_match_decisions_ed2k_hash;
    DROP TRIGGER files_no_delete; DROP TRIGGER files_no_update; DROP TRIGGER md_no_delete;
    CREATE TABLE files (file_id BLOB PRIMARY KEY CHECK (length(file_id) = 16),
        network TEXT NOT NULL, native_id TEXT NOT NULL, size_bytes INTEGER NOT NULL,
        UNIQUE (network, native_id));
    CREATE TABLE match_decisions (id INTEGER PRIMARY KEY,
        file_id BLOB NOT NULL REFERENCES files (file_id), target_id TEXT);
    CREATE INDEX idx_match_decisions_file_id ON match_decisions (file_id);
    CREATE TRIGGER files_no_delete BEFORE DELETE ON files
    BEGIN SELECT RAISE(ABORT, 'files is append-only'); END;
    INSERT INTO files SELECT file_id('ed2k', ed2k_hash), 'ed2k', ed2k_hash, size_bytes FROM files_old;
    INSERT INTO match_decisions (id, file_id, target_id)
        SELECT id, file_id('ed2k', ed2k_hash), target_id FROM match_decisions_old;
    """,
    # txn 2..n: batched copy of the big child would go here (children of files_old stay valid)
    # txn last: drop old children first, then the old parent
    """
    DROP TABLE match_decisions_old;
    DROP TABLE file_observations;
    DROP TABLE files_old;
    """,
]
for i, s in enumerate(steps, 1):
    c.execute("BEGIN")
    try:
        c.executescript(s)
        c.execute("COMMIT")
        print(f"  txn {i}: COMMIT OK")
    except sqlite3.Error as e:
        print(f"  txn {i}: ERROR {e}")
        c.execute("ROLLBACK")
    print("    state:")
    schema(c)
run("foreign_key_check", c, "PRAGMA foreign_key_check")
run("orphan insert rejected?", c, "INSERT INTO match_decisions (file_id) VALUES (zeroblob(16))")
run("integrity_check", c, "PRAGMA integrity_check")
```

## `q5_functions.py`

```python
"""Q5: Python-registered SQL functions (file_id uuid5, content_hash) inside INSERT..SELECT."""

import hashlib
import json
import sqlite3
import struct
import sys
import time
import uuid

N = 1_000_000
NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://mission-titar.github.io/mulewatch/file")


def file_id(network: str, native_id: str) -> bytes:
    return uuid.uuid5(NS, f"{network}:{native_id}").bytes


def canon_json(*values: object) -> bytes:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()


def canon_lenprefix(*values: object) -> bytes:
    # tag byte per value: N=NULL, I=int (8-byte signed), T=text (4-byte length + utf-8)
    out = bytearray()
    for v in values:
        if v is None:
            out += b"N"
        elif isinstance(v, int):
            out += b"I" + struct.pack(">q", v)
        else:
            b = str(v).encode()
            out += b"T" + struct.pack(">I", len(b)) + b
    return bytes(out)


def h_blake(*v: object) -> bytes:
    return hashlib.blake2b(canon_json(*v), digest_size=16).digest()


def h_sha(*v: object) -> bytes:
    return hashlib.sha256(canon_json(*v)).digest()[:16]


def h_blake_lp(*v: object) -> bytes:
    return hashlib.blake2b(canon_lenprefix(*v), digest_size=16).digest()


# self-checks: NULL-safe and unambiguous
assert canon_json(None, "") != canon_json("", None)
assert canon_json(1, "1") != canon_json("1", 1)
assert canon_json("a,b", "c") != canon_json("a", "b,c")
assert canon_json("ab", "c") != canon_json("a", "bc")
assert canon_lenprefix("ab", "c") != canon_lenprefix("a", "bc")
assert canon_lenprefix(None, "") != canon_lenprefix("", None)
assert canon_json("é") == '["é"]'.encode()

print("sqlite", sqlite3.sqlite_version, "python", sys.version.split()[0])
c = sqlite3.connect(":memory:", autocommit=True)
c.create_function("file_id", 2, file_id, deterministic=True)
for name, f in (("h_blake", h_blake), ("h_sha", h_sha), ("h_blake_lp", h_blake_lp)):
    c.create_function(name, 10, f, deterministic=True)
c.execute(
    "CREATE TABLE src (id INTEGER PRIMARY KEY, h TEXT, filename TEXT, size INT, len INT,"
    " br INT, codec TEXT, ft TEXT, raw TEXT, kw TEXT, node TEXT)"
)
raw = json.dumps([["k", i] for i in range(12)])
c.execute(
    "INSERT INTO src SELECT value, printf('%032x', value % 1933), 'Keroro ' || value || '.avi',"
    " 123456789, CASE WHEN value % 3 THEN 1320 END, 1100, 'xvid', 'Video', ?, 'keroro', 'node-1'"
    " FROM (WITH RECURSIVE g(value) AS (SELECT 1 UNION ALL SELECT value + 1 FROM g WHERE value < ?) SELECT value FROM g)",
    (raw, N),
)


def bench(label: str, sql: str) -> None:
    t = time.perf_counter()
    c.execute(sql)
    dt = time.perf_counter() - t
    print(f"  {label:58} {dt:6.2f} s per 1M rows ({N / dt / 1e3:6.0f} k rows/s)")


cols = "h, filename, size, len, br, codec, ft, raw, kw, node"
bench("baseline: INSERT..SELECT copy, no function", f"CREATE TABLE t0 AS SELECT {cols} FROM src")
bench("file_id('ed2k', h) uuid5", "CREATE TABLE t1 AS SELECT file_id('ed2k', h) FROM src")
bench("content_hash JSON + blake2b-128 (10 cols)", f"CREATE TABLE t2 AS SELECT h_blake({cols}) FROM src")
bench("content_hash JSON + sha256[:16] (10 cols)", f"CREATE TABLE t3 AS SELECT h_sha({cols}) FROM src")
bench("content_hash len-prefix + blake2b-128 (10 cols)", f"CREATE TABLE t4 AS SELECT h_blake_lp({cols}) FROM src")
bench("file_id + content_hash(JSON, blake2b) together",
      f"CREATE TABLE t5 AS SELECT file_id('ed2k', h), h_blake({cols}) FROM src")

t = time.perf_counter()
for row in c.execute(f"SELECT {cols} FROM src"):
    h_blake(*row)
print(f"  {'pure Python stream + hash (no SQL function)':58} {time.perf_counter() - t:6.2f} s per 1M rows")
print("  deterministic file_id example:", file_id("ed2k", "0" * 32).hex(), "NS", NS)
```

## `q7b_single_txn_dict.py`

```python
"""Q7 (lead's variant): txn A (DDL) then ONE transaction with no DELETE: variants built by a streamed
Python dict, observations via a dict-backed variant UDF ORDER BY id, DROP old tables; then VACUUM
with temp_store=FILE.

usage: python3 q7b_single_txn_dict.py DB a|b
  a: rowid observations + index (variant_id, observed_at)
  b: WITHOUT ROWID PRIMARY KEY (variant_id, observed_at, source_count), INSERT OR IGNORE
Runner pragmas + Q7_PRAGMAS (e.g. "cache_size=-262144;secure_delete=OFF").
"""

import os
import sqlite3
import sys
import time
from pathlib import Path

from spike_common import Peaks, content_hash, file_id, fold_meta, size

db, layout = Path(sys.argv[1]), sys.argv[2]
wal = Path(f"{db}-wal")
c = sqlite3.connect(db, autocommit=True)
assert c.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
c.execute("PRAGMA foreign_keys=ON")
c.execute("PRAGMA recursive_triggers=ON")
c.execute("PRAGMA temp_store=MEMORY")
for extra in filter(None, os.environ.get("Q7_PRAGMAS", "").split(";")):
    print("extra pragma", extra, c.execute(f"PRAGMA {extra}").fetchall())
c.create_function("file_id", 2, file_id, deterministic=True)
peaks, overall = Peaks(db), Peaks(db)
time.sleep(0.1)
print(f"sqlite {sqlite3.sqlite_version} layout={layout} start: db={size(db) / 1e9:.2f}GB"
      f" secure_delete={c.execute('PRAGMA secure_delete').fetchone()[0]}"
      f" cache_size={c.execute('PRAGMA cache_size').fetchone()[0]}"
      f" SQLITE_TMPDIR={os.environ.get('SQLITE_TMPDIR')}", flush=True)

OBS = {
    "a": """CREATE TABLE observations (
                variant_id INTEGER NOT NULL REFERENCES observation_variants (variant_id),
                observed_at INTEGER NOT NULL, source_count INTEGER NOT NULL);
            CREATE INDEX idx_observations_variant_observed ON observations (variant_id, observed_at);""",
    "b": """CREATE TABLE observations (
                variant_id INTEGER NOT NULL REFERENCES observation_variants (variant_id),
                observed_at INTEGER NOT NULL, source_count INTEGER NOT NULL,
                PRIMARY KEY (variant_id, observed_at, source_count)) WITHOUT ROWID;""",
}[layout]
RAW = ["ed2k_hash", "filename", "size_bytes", "media_length_sec", "bitrate_kbps", "codec",
       "file_type", "raw_meta", "complete_source_count", "keyword", "node_id"]
US = "(unixepoch(substr(o.observed_at, 1, 19)) * 1000000 + CAST(substr(o.observed_at, 21, 6) AS INTEGER))"


def phase(label: str, t0: float) -> None:
    print(f"[{label}] {time.perf_counter() - t0:.1f}s {peaks.line()}", flush=True)
    peaks.reset()


t0 = time.perf_counter()
c.execute("BEGIN")
c.executescript(f"""
    DROP TRIGGER files_no_update; DROP TRIGGER files_no_delete;
    DROP TRIGGER file_observations_no_update; DROP TRIGGER file_observations_no_delete;
    DROP TRIGGER match_decisions_no_update; DROP TRIGGER match_decisions_no_delete;
    DROP TRIGGER source_observations_no_update; DROP TRIGGER source_observations_no_delete;
    DROP TRIGGER file_observation_ranges_no_update; DROP TRIGGER file_observation_ranges_no_delete;
    ALTER TABLE files RENAME TO files_old;
    ALTER TABLE match_decisions RENAME TO match_decisions_old;
    DROP INDEX idx_match_decisions_ed2k_hash; DROP INDEX idx_match_decisions_hash_target_decided;
    CREATE TABLE files (
        file_id BLOB PRIMARY KEY CHECK (length(file_id) = 16),
        network TEXT NOT NULL, native_id TEXT NOT NULL, size_bytes INTEGER NOT NULL,
        UNIQUE (network, native_id));
    CREATE TABLE observation_variants (
        variant_id INTEGER PRIMARY KEY,
        file_id BLOB NOT NULL REFERENCES files (file_id),
        filename TEXT NOT NULL, size_bytes INTEGER NOT NULL,
        media_length_sec INTEGER, bitrate_kbps INTEGER, raw_meta TEXT NOT NULL,
        keyword TEXT NOT NULL, node_id TEXT NOT NULL,
        content_hash BLOB NOT NULL UNIQUE);
    CREATE INDEX idx_observation_variants_file_id ON observation_variants (file_id);
    {OBS}
    CREATE TABLE match_decisions (
        id INTEGER PRIMARY KEY, file_id BLOB NOT NULL REFERENCES files (file_id),
        target_id TEXT NOT NULL, rule_name TEXT NOT NULL, tier TEXT NOT NULL,
        decided_at TEXT NOT NULL, node_id TEXT NOT NULL);
    CREATE INDEX idx_match_decisions_file_target_decided
        ON match_decisions (file_id, target_id, decided_at);
    INSERT INTO files SELECT file_id('ed2k', ed2k_hash), 'ed2k', ed2k_hash, size_bytes FROM files_old;
    INSERT INTO match_decisions SELECT id, file_id('ed2k', ed2k_hash), target_id, rule_name, tier,
        decided_at, node_id FROM match_decisions_old;
""")
c.execute("COMMIT")
phase("txn A: DDL + files + match_decisions (committed)", t0)

overall.reset()
t_all = t0 = time.perf_counter()
c.execute("BEGIN")
raw_map: dict[tuple[object, ...], int] = {}
by_hash: dict[bytes, int] = {}
for r in c.execute(f"SELECT {', '.join(RAW)} FROM file_observations"):
    if r in raw_map:
        continue
    h, fn, sz, ml, br, codec, ft, meta, comp, kw, nd = r
    v = (file_id("ed2k", h), fn, sz, ml, br, fold_meta(meta, codec, ft, comp), kw, nd)
    ch = content_hash(*v)
    if ch not in by_hash:
        by_hash[ch] = c.execute(
            "INSERT INTO observation_variants (file_id, filename, size_bytes, media_length_sec,"
            " bitrate_kbps, raw_meta, keyword, node_id, content_hash) VALUES (?,?,?,?,?,?,?,?,?)",
            (*v, ch)).lastrowid
    raw_map[r] = by_hash[ch]
c.create_function("variant_of", len(RAW), lambda *k: raw_map[k], deterministic=True)
phase(f"variants, streamed dict ({len(by_hash)} variants, {len(raw_map)} raw keys)", t0)

t0 = time.perf_counter()
n = c.execute(f"""
    INSERT {'OR IGNORE ' if layout == 'b' else ''}INTO observations (variant_id, observed_at, source_count)
    SELECT variant_of({', '.join('o.' + x for x in RAW)}), {US}, o.source_count
    FROM file_observations o ORDER BY o.id""").rowcount
phase(f"INSERT {'OR IGNORE ' if layout == 'b' else ''}observations ({n} rows)", t0)

t0 = time.perf_counter()
c.executescript("""
    DROP TABLE file_observations; DROP TABLE source_observations; DROP TABLE sources;
    DROP TABLE file_observation_ranges; DROP TABLE match_decisions_old; DROP TABLE files_old;
    CREATE TRIGGER files_no_update BEFORE UPDATE ON files
    BEGIN SELECT RAISE(ABORT, 'files is append-only'); END;
    CREATE TRIGGER files_no_delete BEFORE DELETE ON files
    BEGIN SELECT RAISE(ABORT, 'files is append-only'); END;
    CREATE TRIGGER observations_no_update BEFORE UPDATE ON observations
    BEGIN SELECT RAISE(ABORT, 'observations is append-only'); END;
    CREATE TRIGGER observations_no_delete BEFORE DELETE ON observations
    BEGIN SELECT RAISE(ABORT, 'observations is append-only'); END;
""")
phase("DROP old tables + triggers", t0)
t0 = time.perf_counter()
c.execute("COMMIT")
phase("COMMIT", t0)
print(f"[whole migration transaction] {time.perf_counter() - t_all:.1f}s {overall.line()}", flush=True)
print(f"after COMMIT: db={size(db)} wal={size(wal)}")
t0 = time.perf_counter()
c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
phase("checkpoint(TRUNCATE)", t0)
pages, free = (c.execute(f"PRAGMA {p}").fetchone()[0] for p in ("page_count", "freelist_count"))
print(f"after checkpoint: db={size(db)} pages={pages} freelist={free}")

c.execute("PRAGMA temp_store=FILE")
t0 = time.perf_counter()
c.execute("VACUUM")
phase("VACUUM temp_store=FILE", t0)
c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
print(f"after VACUUM + checkpoint: db={size(db)} wal={size(wal)}")
print("quick_check:", c.execute("PRAGMA quick_check").fetchone()[0],
      "foreign_key_check:", c.execute("PRAGMA foreign_key_check").fetchall(),
      "observations:", c.execute("SELECT count(*) FROM observations").fetchone()[0],
      "variants:", c.execute("SELECT count(*) FROM observation_variants").fetchone()[0])
for name, sz in c.execute("SELECT name, sum(pgsize) FROM dbstat GROUP BY name ORDER BY 2 DESC LIMIT 4"):
    print(f"  dbstat {name:40} {sz / 1e6:8.1f} MB {sz / n:6.1f} B/row")
```

## `q7_reclaim.py`

```python
"""Q7: reclaim the freed pages of a migrated DB (high-water file, mostly freelist).

usage: python3 q7_reclaim.py DB memory|file|into|into-file
  memory / file   : VACUUM with temp_store=MEMORY / FILE
  into / into-file: VACUUM INTO 'DB.new' (temp_store MEMORY / FILE), close, rename over DB
Runner pragmas on the connection (WAL, foreign_keys=ON, recursive_triggers=ON).
"""

import os
import sqlite3
import sys
import time
from pathlib import Path

from spike_common import Peaks, size

db, method = Path(sys.argv[1]), sys.argv[2]
new = Path(f"{db}.new")
new.unlink(missing_ok=True)
c = sqlite3.connect(db, autocommit=True)
assert c.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
c.execute("PRAGMA foreign_keys=ON")
c.execute("PRAGMA recursive_triggers=ON")
c.execute(f"PRAGMA temp_store={'FILE' if method.endswith('file') else 'MEMORY'}")
pages, free = (c.execute(f"PRAGMA {p}").fetchone()[0] for p in ("page_count", "freelist_count"))
print(f"sqlite {sqlite3.sqlite_version} {method} start: db={size(db) / 1e9:.2f}GB"
      f" pages={pages} freelist={free} ({free / pages:.0%}) SQLITE_TMPDIR={os.environ.get('SQLITE_TMPDIR')}")
peaks = Peaks(db, new)
time.sleep(0.1)
t0 = time.perf_counter()
if method.startswith("into"):
    c.execute("VACUUM INTO ?", (str(new),))
else:
    c.execute("VACUUM")
print(f"[VACUUM{' INTO' if method.startswith('into') else ''}] {time.perf_counter() - t0:.1f}s {peaks.line()}")
print(f"  right after: db={size(db)} wal={size(Path(f'{db}-wal'))} new={size(new)}")
if method.startswith("into"):
    c.close()  # last connection: SQLite checkpoints and deletes DB-wal / DB-shm
    print(f"  after close: wal exists={Path(f'{db}-wal').exists()} shm exists={Path(f'{db}-shm').exists()}")
    os.replace(new, db)
else:
    t0 = time.perf_counter()
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    print(f"  checkpoint(TRUNCATE) {time.perf_counter() - t0:.1f}s")
    c.close()
r = sqlite3.connect(db, autocommit=True)
print(f"final: db={size(db)} wal={size(Path(f'{db}-wal'))}"
      f" header journal_mode={r.execute('PRAGMA journal_mode').fetchone()[0]}"
      f" header bytes 18/19={open(db, 'rb').read(20)[18:20].hex()} (0202 = WAL, 0101 = rollback)")
r.execute("PRAGMA foreign_keys=ON")
print("  quick_check:", r.execute("PRAGMA quick_check").fetchone()[0],
      "foreign_key_check:", r.execute("PRAGMA foreign_key_check").fetchall(),
      "observations:", r.execute("SELECT count(*) FROM observations").fetchone()[0],
      "triggers:", r.execute("SELECT count(*) FROM sqlite_schema WHERE type='trigger'").fetchone()[0],
      "user_version:", r.execute("PRAGMA user_version").fetchone()[0])
```

## `q7_single_txn.py`

```python
"""Q7 (A): the whole migration in ONE BEGIN..COMMIT: INSERT OR IGNORE variants (dedup by the
content_hash UNIQUE index, no DISTINCT), INSERT observations JOIN on content_hash, DROP old tables.

usage: python3 q7_single_txn.py DB   (runner pragmas: WAL, foreign_keys=ON, recursive_triggers=ON,
temp_store=MEMORY; optional extra pragmas in Q7_PRAGMAS="a=b;c=d")
"""

import os
import sqlite3
import sys
import time
from pathlib import Path

from spike_common import Peaks, content_hash, file_id, fold_meta, size

db = Path(sys.argv[1])
c = sqlite3.connect(db, autocommit=True)
assert c.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
c.execute("PRAGMA foreign_keys=ON")
c.execute("PRAGMA recursive_triggers=ON")
c.execute("PRAGMA temp_store=MEMORY")
for extra in filter(None, os.environ.get("Q7_PRAGMAS", "").split(";")):
    print("extra pragma", extra, c.execute(f"PRAGMA {extra}").fetchall())
c.create_function("file_id", 2, file_id, deterministic=True)
c.create_function("content_hash", -1, content_hash, deterministic=True)
c.create_function("fold_meta", 4, fold_meta, deterministic=True)
peaks = Peaks(db)
time.sleep(0.1)
print(f"sqlite {sqlite3.sqlite_version} start: db={size(db) / 1e9:.2f}GB", flush=True)

VCOLS = ["file_id('ed2k', o.ed2k_hash)", "o.filename", "o.size_bytes", "o.media_length_sec",
         "o.bitrate_kbps", "fold_meta(o.raw_meta, o.codec, o.file_type, o.complete_source_count)",
         "o.keyword", "o.node_id"]
VKEY = ", ".join(VCOLS)
US = "(unixepoch(substr(o.observed_at, 1, 19)) * 1000000 + CAST(substr(o.observed_at, 21, 6) AS INTEGER))"
STEPS = {
    "DDL + files + match_decisions": """
        DROP TRIGGER files_no_update; DROP TRIGGER files_no_delete;
        DROP TRIGGER file_observations_no_update; DROP TRIGGER file_observations_no_delete;
        DROP TRIGGER match_decisions_no_update; DROP TRIGGER match_decisions_no_delete;
        DROP TRIGGER source_observations_no_update; DROP TRIGGER source_observations_no_delete;
        DROP TRIGGER file_observation_ranges_no_update; DROP TRIGGER file_observation_ranges_no_delete;
        ALTER TABLE files RENAME TO files_old;
        ALTER TABLE match_decisions RENAME TO match_decisions_old;
        DROP INDEX idx_match_decisions_ed2k_hash; DROP INDEX idx_match_decisions_hash_target_decided;
        CREATE TABLE files (
            file_id BLOB PRIMARY KEY CHECK (length(file_id) = 16),
            network TEXT NOT NULL, native_id TEXT NOT NULL, size_bytes INTEGER NOT NULL,
            UNIQUE (network, native_id));
        CREATE TABLE observation_variants (
            variant_id INTEGER PRIMARY KEY,
            file_id BLOB NOT NULL REFERENCES files (file_id),
            filename TEXT NOT NULL, size_bytes INTEGER NOT NULL,
            media_length_sec INTEGER, bitrate_kbps INTEGER, raw_meta TEXT NOT NULL,
            keyword TEXT NOT NULL, node_id TEXT NOT NULL,
            content_hash BLOB NOT NULL UNIQUE);
        CREATE INDEX idx_observation_variants_file_id ON observation_variants (file_id);
        CREATE TABLE observations (
            variant_id INTEGER NOT NULL REFERENCES observation_variants (variant_id),
            observed_at INTEGER NOT NULL, source_count INTEGER NOT NULL);
        CREATE INDEX idx_observations_variant_observed ON observations (variant_id, observed_at);
        CREATE TABLE match_decisions (
            id INTEGER PRIMARY KEY, file_id BLOB NOT NULL REFERENCES files (file_id),
            target_id TEXT NOT NULL, rule_name TEXT NOT NULL, tier TEXT NOT NULL,
            decided_at TEXT NOT NULL, node_id TEXT NOT NULL);
        CREATE INDEX idx_match_decisions_file_target_decided
            ON match_decisions (file_id, target_id, decided_at);
        INSERT INTO files SELECT file_id('ed2k', ed2k_hash), 'ed2k', ed2k_hash, size_bytes FROM files_old;
        INSERT INTO match_decisions SELECT id, file_id('ed2k', ed2k_hash), target_id, rule_name, tier,
            decided_at, node_id FROM match_decisions_old;
    """,
    "INSERT OR IGNORE variants (no DISTINCT)": f"""
        INSERT OR IGNORE INTO observation_variants (file_id, filename, size_bytes,
            media_length_sec, bitrate_kbps, raw_meta, keyword, node_id, content_hash)
        SELECT {VKEY}, content_hash({VKEY}) FROM file_observations o ORDER BY o.id;
    """,
    "INSERT observations JOIN on content_hash": f"""
        INSERT INTO observations (variant_id, observed_at, source_count)
        SELECT v.variant_id, {US}, o.source_count
        FROM file_observations o JOIN observation_variants v ON v.content_hash = content_hash({VKEY})
        ORDER BY o.id;
    """,
    "DROP old tables + new triggers": """
        DROP TABLE file_observations; DROP TABLE source_observations; DROP TABLE sources;
        DROP TABLE file_observation_ranges; DROP TABLE match_decisions_old; DROP TABLE files_old;
        CREATE TRIGGER files_no_update BEFORE UPDATE ON files
        BEGIN SELECT RAISE(ABORT, 'files is append-only'); END;
        CREATE TRIGGER files_no_delete BEFORE DELETE ON files
        BEGIN SELECT RAISE(ABORT, 'files is append-only'); END;
        CREATE TRIGGER observations_no_update BEFORE UPDATE ON observations
        BEGIN SELECT RAISE(ABORT, 'observations is append-only'); END;
        CREATE TRIGGER observations_no_delete BEFORE DELETE ON observations
        BEGIN SELECT RAISE(ABORT, 'observations is append-only'); END;
    """,
}

overall = Peaks(db)
t_all = time.perf_counter()
c.execute("BEGIN")
for label, sql in STEPS.items():
    peaks.reset()
    t0 = time.perf_counter()
    c.executescript(sql)
    print(f"[{label}] {time.perf_counter() - t0:.1f}s {peaks.line()}", flush=True)
peaks.reset()
t0 = time.perf_counter()
c.execute("COMMIT")
print(f"[COMMIT] {time.perf_counter() - t0:.1f}s {peaks.line()}", flush=True)
print(f"[whole transaction] {time.perf_counter() - t_all:.1f}s {overall.line()}")
print(f"after COMMIT: db={size(db)} wal={size(Path(f'{db}-wal'))}")
peaks.reset()
t0 = time.perf_counter()
print("checkpoint(TRUNCATE):", c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone(),
      f"{time.perf_counter() - t0:.1f}s {peaks.line()}")
print(f"after checkpoint: db={size(db)} wal={size(Path(f'{db}-wal'))}"
      f" page_count={c.execute('PRAGMA page_count').fetchone()[0]}"
      f" freelist={c.execute('PRAGMA freelist_count').fetchone()[0]}")
print("variants:", c.execute("SELECT count(*) FROM observation_variants").fetchone()[0],
      "observations:", c.execute("SELECT count(*) FROM observations").fetchone()[0],
      "foreign_key_check:", c.execute("PRAGMA foreign_key_check").fetchall())
```

## `spike_common.py`

```python
"""Shared bits for the Q7 scripts: the UDFs of q2_migrate.py and a disk/RSS peak sampler."""

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://mission-titar.github.io/mulewatch/file")


def file_id(network: str, native_id: str) -> bytes:
    return uuid.uuid5(NS, f"{network}:{native_id}").bytes


def content_hash(*values: object) -> bytes:
    values = tuple(v.hex() if isinstance(v, bytes) else v for v in values)
    canon = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.blake2b(canon, digest_size=16).digest()


def fold_meta(raw_meta: str, codec: str | None, file_type: str | None, complete: int) -> str:
    pairs = json.loads(raw_meta)
    pairs += [["codec", codec], ["file_type", file_type], ["complete_source_count", complete]]
    return json.dumps(pairs, ensure_ascii=False)


def size(p: Path) -> int:
    try:
        return p.stat().st_size
    except FileNotFoundError:
        return 0


def temp_bytes() -> int:
    """Deleted files this process holds open: SQLite's temp files."""
    total = 0
    for fd in os.listdir("/proc/self/fd"):
        try:
            if os.readlink(f"/proc/self/fd/{fd}").endswith("(deleted)"):
                total += os.stat(f"/proc/self/fd/{fd}").st_size
        except OSError:
            pass
    return total


def rss() -> int:
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS"):
            return int(line.split()[1]) * 1024
    return 0


class Peaks:
    """Samples every 20 ms: each watched DB file and its -wal, SQLite temp files, RSS."""

    def __init__(self, *dbs: Path) -> None:
        self.dbs = dbs
        self.reset()
        threading.Thread(target=self._run, daemon=True).start()

    def reset(self) -> None:
        self.db = self.wal = self.tmp = self.total = self.rss = 0

    def _run(self) -> None:
        while True:
            d = sum(size(p) for p in self.dbs)
            w = sum(size(Path(f"{p}-wal")) for p in self.dbs)
            t = temp_bytes()
            self.db, self.wal, self.tmp = max(self.db, d), max(self.wal, w), max(self.tmp, t)
            self.total, self.rss = max(self.total, d + w + t), max(self.rss, rss())
            time.sleep(0.02)

    def line(self) -> str:
        return (f"peak db-files={self.db / 1e9:.2f}GB wal={self.wal / 1e9:.2f}GB tmp={self.tmp / 1e9:.2f}GB"
                f" all={self.total / 1e9:.2f}GB rss={self.rss / 1e6:.0f}MB")
```

## `run_11m5.sh`

```bash
#!/bin/sh
# 11.5M-row runs: each migration starts from a fresh copy of the pristine old DB.
set -e
S=$(dirname "$0")
D=/home/geoffrey/.cache/mulewatch-spike-db
export SQLITE_TMPDIR=$D  # temp files (sorter, VACUUM temp_store=FILE) on disk, measurable
run() {  # name batch checkpoint obs_at_index vacuum join
    rm -f $D/work.db*
    cp $D/pristine.db $D/work.db
    python3 $S/q2_migrate.py $D/work.db $2 $3 $4 $5 $6 2>&1 | tee $S/q2_11m5_$1.txt
}
python3 $S/q3_memory.py distinct $D/pristine.db 2>&1 | tee $S/q3_distinct_11m5.txt
rm -f $D/pristine.db-shm $D/pristine.db-wal
run r1_b50k_ck1_vacmem 50000 1 0 memory dict
python3 $S/q1_storage.py new $D/work.db 2>&1 | tee $S/q1_new_11m5.txt
for ts in MEMORY FILE; do python3 $S/q3_memory.py index $D/work.db $ts; done 2>&1 | tee $S/q3_index_11m5.txt
python3 $S/q3_memory.py fill $D/work.db 500000 2>&1 | tee $S/q3_fill_11m5.txt
run r2_b50k_ck0_vacfile 50000 0 0 file dict
run r3_b500k_ck1_obsidx 500000 1 1 none dict
run r4_b500k_ck0_hash 500000 0 0 none hash
rm -f $D/work.db*
echo ALL DONE
```

## `run_cache_11m5.sh`

```bash
#!/bin/sh
# 11.5M: does a bigger page cache fix the batched copy's I/O bound? host, then image over a bind mount.
S=$(dirname "$0")
D=/home/geoffrey/.cache/mulewatch-spike-db
export SQLITE_TMPDIR=$D
rm -f $D/work.db*; cp $D/pristine.db $D/work.db
Q2_PRAGMAS="cache_size=-262144" python3 $S/q2_migrate.py $D/work.db 50000 1 0 none dict 2>&1 | tee $S/q2_11m5_r5_host_cache256m.txt
rm -f $D/work.db*; cp $D/pristine.db $D/work.db
docker run --rm --user "$(id -u):$(id -g)" -e Q2_PRAGMAS="cache_size=-262144" -v "$S":/spike -v "$D":/db \
    --entrypoint python ghcr.io/mission-titar/mulewatch:latest /spike/q2_migrate.py /db/work.db 50000 1 0 none dict \
    2>&1 | tee $S/q2_11m5_r6_image_bindmount_cache256m.txt
rm -f $D/work.db*
echo ALL DONE
```

## `run_q7b.sh`

```bash
#!/bin/sh
# Lead's Q7 variant at 11.5M: single transaction, dict-backed UDF, layouts a and b, host then image bind mount.
S=$(dirname "$0")
D=/home/geoffrey/.cache/mulewatch-spike-db
P="cache_size=-262144;secure_delete=OFF"
cd $S
mkdir -p $D
python3 gen_old.py $D/pristine.db 11500000 > gen_11m5_q7b.txt 2>&1
for l in a b; do
    rm -f $D/work.db*; cp $D/pristine.db $D/work.db
    Q7_PRAGMAS="$P" python3 q7b_single_txn_dict.py $D/work.db $l 2>&1 | tee q7b_11m5_host_layout_$l.txt
done
for l in a b; do
    rm -f $D/work.db*; cp $D/pristine.db $D/work.db
    docker run --rm --user "$(id -u):$(id -g)" -e Q7_PRAGMAS="$P" -v "$S":/spike -v "$D":/db \
        --entrypoint sh ghcr.io/mission-titar/mulewatch:latest -c "cd /spike && python q7b_single_txn_dict.py /db/work.db $l" \
        2>&1 | tee q7b_11m5_image_bindmount_layout_$l.txt
done
rm -rf $D
echo ALL DONE
```

## `run_q7.sh`

```bash
#!/bin/sh
# Q7 at 11.5M: single-transaction migration (A), reclaim options, and batched (B) with secure_delete=OFF.
S=$(dirname "$0")
D=/home/geoffrey/.cache/mulewatch-spike-db
export SQLITE_TMPDIR=$D  # temp files land on disk where the sampler can see them
fresh() { rm -f $D/work.db*; cp $D/pristine.db $D/work.db; }
cd $S

# A, compiled default secure_delete=ON, temp_store=FILE (MEMORY would need ~6 GB RSS: see the 2M runs)
fresh; Q7_PRAGMAS="temp_store=FILE" python3 q7_single_txn.py $D/work.db 2>&1 | tee q7_A_11m5_securedel_on_tempfile.txt
# A, secure_delete=OFF (runner's temp_store=MEMORY)
fresh; Q7_PRAGMAS="secure_delete=OFF" python3 q7_single_txn.py $D/work.db 2>&1 | tee q7_A_11m5_securedel_off.txt
mv $D/work.db $D/postA.db; rm -f $D/work.db-wal $D/work.db-shm
for m in memory file into into-file; do
    rm -f $D/work.db*; cp $D/postA.db $D/work.db
    python3 q7_reclaim.py $D/work.db $m 2>&1 | tee q7_reclaim_afterA_$m.txt
done
rm -f $D/postA.db

# B, batched, secure_delete=OFF (compare r1: 14249 rows/s with it ON), then VACUUM INTO
fresh; Q2_PRAGMAS="secure_delete=OFF" python3 q2_migrate.py $D/work.db 50000 1 0 none dict 2>&1 | tee q7_B_11m5_securedel_off.txt
python3 q7_reclaim.py $D/work.db into 2>&1 | tee q7_reclaim_afterB_into.txt

# B in the image over the bind mount, secure_delete=OFF + 256 MB cache (compare r6: 10017 rows/s)
fresh
docker run --rm --user "$(id -u):$(id -g)" -e Q2_PRAGMAS="secure_delete=OFF;cache_size=-262144" -v "$S":/spike -v "$D":/db \
    --entrypoint python ghcr.io/mission-titar/mulewatch:latest /spike/q2_migrate.py /db/work.db 50000 1 0 none dict \
    2>&1 | tee q7_B_11m5_image_bindmount_securedel_off_cache256m.txt
rm -f $D/work.db*
echo ALL DONE
```
