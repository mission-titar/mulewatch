# Handoff: stage 2, generic search, download and status ports

Drafted by block 200, corrected by the closing block (210).

## State

Tier Spec lot, spec `agents/specs/2026-10-09-stage2-search-download-status-ports.md` (approved 2026-10-09). A
stack of 28 blocks: PRs #127, #128 and #130 to #154 (there is no #129), then the closing block 210
(`docs/stage2-closing`) on top. Nothing is merged and no release follows (D23): the node pulls `latest`, which
stays on 4.1.0. `catalog.db` is unchanged at schema version 9, `local.db` goes from 5 to 8 (0006 to 0008).

After the stack, `application/` and `domain/` name no aMule type outside `port_sync_loop.py` (D22's grep prints
two `port_sync_loop.py` lines and nothing else, checked at block 210's tip). `git tag -l 'v*'` still ends at
`v4.1.0`. `ports/mule_client.py` survives as `MuleClient(SearchClient,
StatusClient)`, the composition's type for the one aMule session the search tasks and the status loop share.

## What was built

- **Generic errors** (block 10): `ports/client_errors.py`, `ClientUnreachableError`, `SearchFailedError`,
  `DownloadRejectedError` under `ClientError`. amuleapi's `ApiRejectedError` is both a `SearchFailedError` and a
  `DownloadRejectedError` (one `400 amuled_rejected` for both); `ApiAuthError` stays under `ClientError` but
  outside the three, so it fails fast.
- **aMule's `search()`** (blocks 20, 30): one call per search, polling to `finished` or a budget counted from the
  network start, Kad widening absorbed, no stop or delete. The pacing lives in the adapter: ed2k starts
  serialized and 60 s apart, with a one-budget hatch for a search that never finishes; Kad 60 s apart per
  target (Kad's tokenizer: first word of 3 UTF-8 bytes, lowercased); a keyword without a Kad target waits its
  slot and returns nothing; "already on search list" retried for one budget. The client takes an injected
  `Clock`.
- **Channels and the search port** (blocks 40 to 60): `SearchChannel` and `channels.py` went; a channel is an
  opaque name (`ed2k`, `kad`) the client declares in `channels`. `SearchClient` and the 120 s core budget;
  `search_poll_*` removed and refused; the four-call search removed.
- **Status** (blocks 70 to 96): `StatusClient` (`connect()`, `status()`), per channel `on_network` and
  `connectable: bool | None`, `ec_connected` not `true` raising `ClientUnreachableError`. `NetworkStatus` and
  `KadStatus` moved to `ports/port_sync.py` (D18). The status loop reads every 60 s and alerts on a degraded
  state that lasts (5 min per channel field, 2 min for the API), with the two `p2pwatch_channel_*` gauges. The
  cycle's coverage readout, `coverage.py` and the three coverage events and metrics went.
- **Search tasks** (blocks 100 to 130): one task per (client, channel, keyword), pause gate, sleep until a
  backoff ends, the `sleep(0)` floor, backoff saved at each change. The force-cycle control went (105), then
  the cycle itself (110 wiring, 120 removal, 130 the worker's cycle parts). `cycle_interval_seconds` and
  `keyword_pause_*` removed and refused. local 0006 deletes the cycle state and the stored backoff map.
  `tests/virtual_time.py`: the virtual-clock event loop that runs an hour of concurrent tasks in about a second.
- **Metrics** (block 140): `emule_*` renamed `p2pwatch_*`, a `client` label on the three search counters,
  `InstanceUnreachable` names its client. Port-sync's three metrics keep their names.
- **Download side** (blocks 150 to 190): `DownloadCandidate` with a `FileKey` in the crawler's catalog port;
  local 0007 keys `downloads` by `file_id` (`network`, `native_id` kept); `DownloadClient` (`start`,
  `downloads`) with completion from the client's positive signal, never bytes; the loop on it, restarting a
  download the client forgot (D13); `MuleDownloadClient` and the old calls removed; local 0008 and the
  lifecycle fields (`bytes_done`, `last_progress_at`, `waiting_reason`, `failure_reason`), a reported failure
  staying failed.
- **Dashboard** (block 200): the status loop publishes each reading to a `StatusBoard`, which the webui reads on
  its own thread through the `StatusReadings` port (`ports/client_status.py`, block 207); the dashboard shows each client and channel from a current reading only, the crawl's paused
  state and the pause, resume and restart buttons. The `/controls` page and its menu entry went; the
  `POST /controls/*` routes redirect to `/`.
- **Holistic fixes** (blocks 205, 207, 210): the client backoff grows until a search succeeds; `connect()`
  logs in once under a lock; the status loop reads any client error but a refused password
  (`ClientAuthError`, new in the port contract) as unreachable; `p2pwatch_client_unreachable` carries
  `client`; `AmuleApiClient` paces on the composition's clock; `start()` refuses a file of another network;
  a list answered without its envelope is unreachable; the simulated year runs against the real TTL; the
  spec, `AGENTS.md` and the living docs caught up (table below).

## Decisions taken during Act

- **Five splits, all at a bound and before any push** (the lead's call per the workflow), each with its rows and
  `(Corrected: ...)` markers in the spec:
  - block 70 into 70 and 75 (329 lines, 23 files as specced: the estimate missed the 18 importers of
    `NetworkStatus` / `KadStatus`); D18's move went to 75;
  - block 90 into 90, 93 and 96 (756 lines, 22 files): the cycle's coverage to 93, the three events to 96;
  - block 110 into 105 and 110 (540 lines, 25 files): the force-cycle control to 105;
  - block 180 into 180 and 185 (702 lines, 16 files): the old download port's removal to 185;
  - the closing work into 205 (core and adapter findings), 207 (ports, tests, docstrings) and 210 (spec,
    `AGENTS.md`, `docs/`, `BACKLOG.md`, this handoff), at the file bound.
- **Block 120 passes the line bound** (902 lines, 19 files), by the spec's stated reason: a module cannot leave
  without its test.
- **Class 2, block 90: the status loop never connected its client.** The boot connect routinely fails in one
  container, only the search worker reconnected, and with a paused crawl nothing would: a false 2 min alert on
  each cold boot, permanent while paused. Fix: `connect()` on `StatusClient`, called before each reading.
  Operator: "correctif accepté".
- **Class 2, block 120: `SchedulerStateRepository.read_cycle_index` / `write_cycle_state`** lost their last
  caller and no block removed them. Operator: "on les retire oui"; they went in block 130, since block 120
  would have reached 21 files.
- **Block 100: `BackoffRegistry.record_failure` ignores a failure on a key already backed off.** Not in the
  spec. Several searches of one channel can now fail together; each counting an attempt would jump six tasks'
  backoff from 2 s to 64 s at once. It keeps D7's "today's exponential backoff". `is_in_backoff` derives from
  the new `remaining()`, so the task's sleep and the worker's skip cannot disagree.
- **Block 120 also moved `Rng` to `ports/clock.py` without `shuffled`**, and removed the `observe` metric kind
  with the only histogram (class 1, agreed by the lead); `SearchWorker`'s `is_blocked_for`, `report_dropped`
  and `instance_name` left there, since only the cycle's test covered them.
- **Block 205: `ClientAuthError` joins the port contract** (`ApiAuthError` re-parents onto it): the status
  loop lets a refused password through while it reads every other client error as unreachable, and the
  application cannot name `ApiAuthError`. D8 and D15 carry markers. The client factories take
  `(endpoint, clock)`, and a list answered without its envelope now fails a search as unreachable (the
  client backs off) where it used to return nothing.
- **Block 205 established D13's open question** from aMule's source at `909d304`: amuled never refuses a
  link for a file it still holds (`AddED2KLink`, `src/DownloadQueue.cpp:1515`, merges and answers 202),
  whatever the entry's state; the one refusal (complete, shared, out of the queue) fails the row once with
  `rejected`, then the next `/shared` listing completes it. No code needed; D13 carries the fact.
- **Block 190: `bytes_done` is NULL on migrated rows**, 0 on rows queued after: a default 0 would stamp
  `last_progress_at` on every running download at the first round after the upgrade. D19 carries the marker.
- **Smaller departures**, each in its block's report: `connectable` is `None` when the aMule flag is not a
  boolean, and a missing `ec_connected` is unreachable (70); a completed, failed or `completing` download gets
  no `no_source` / `remote_queue` waiting reason, `completing` reads `local` (170); the adapter reads
  `/downloads` before `/shared` (170); `open_local` registers all the pure SQL functions (160); the board holds
  the freshness rule and is seeded with the client names, so a client without a reading shows "No reading
  yet.", an out-of-date reading shows no value at all, and a reading is stamped when published (200);
  `ClientReading` lives in `ports/client_status.py` beside the port, and the 120 s / 121 s freshness boundary
  is tested on `StatusBoard` only (207).

## Wrap counts

- Fix-backs: 1 (block 110's CI failure, below). Cascaded rebases: 1. Runs re-triggered by them: 1.
- The four splits rewrote unpushed branches only: no run re-triggered.
- Bodies called unreadable: none so far; the operator has not reviewed the stack yet.

Under the workflow's threshold (more runs re-triggered than blocks), stacking cost less than pull requests in
series.

**Friction with `gh stack` 0.1.1.** It cannot insert a block mid-stack: the lead edited `.git/gh-stack` by hand
to insert 105. During block 110's fix cascade, `gh stack push` pushed an empty block 120 branch at a stale tip,
re-pointed by hand.

**Block 110's CI failure.** Run 38002591038 on `bca6dd3` failed on both architectures in
`test_real_loop_runs_one_search_and_stops` (`assert 0 >= 1`). A fresh daemon is on no eD2k server, so it refuses
every ed2k search at once while Kad's search takes about 25 s; the test fired the shutdown on the third
`search()` call, the ed2k retry after its 2 s backoff, before Kad returned. Fixed in `733753c`: the wrapper fires
the shutdown when a channel whose search returned comes back for its next search, and refused searches never
count. Reproduced locally against `ghcr.io/mission-titar/mulewatch:main` on host port 14711 (old test failed
with CI's line, fixed one passed in 30 s); second run 38003265253.

## Pitfalls

- **Concurrent tasks need `tests/virtual_time.py`, not `FakeClock`**: `FakeClock.sleep` advances shared time by
  each sleeper's own delay, so concurrent sleeps add up. The virtual loop asserts "deadlock" when every task
  waits with no timer set. One task (the status loop) is fine on `FakeClock`, which then advances 60 s per
  reading: a test whose timing matters gets a client whose `status()` never answers.
- **A shutdown trigger shared by concurrent tasks must fire once**: a second `_on_signal()` raises `SystemExit`.
- **A fresh daemon refuses ed2k while Kad works**: a loop test against a real daemon must not count refused
  searches as searches done.
- **Read `/downloads` before `/shared`**: the other order loses a download completed and cleared between the two
  reads, and D13 would start it again.
- **"Already on search list" matches amuled's English message** (gettext); the image sets no `LANG`.
- **A Kad start delayed by those retries can land under 60 s before its target's next reserved slot.** Kad's own
  refusal (45 s life, no stop) still spaces them 45 s or more; no rule added. Spec section 4 carries the
  exception.
- **At a backoff's end every task of the channel wakes and calls `search()`**; the adapter's pacing serializes
  them.
- **A starvation test must keep its concurrent coroutine running until the cancellation lands**: the search task
  gets one more turn after `cancel()`.
- **Jinja renders an unknown attribute as empty**: `node.html` reading a renamed field fails no test unless one
  asserts the value (block 160 added one).
- **`registry.collect()` reports a counter's family name without `_total`.**
- **Zensical keeps accents in heading anchors** (`#suivre-un-téléchargement`); an ASCII anchor fails
  `docs-build --strict`.
- **`gh stack rebase --upstack` refuses while a stack branch has no remote yet**; block 110 was rebased onto 105
  with `git rebase --onto`.
- **`git rm` stages at once**: a later `git add <spec>` and commit carried the deletions into the docs commit
  (block 120, redone before pushing).
- **The compose smoke's seed computes `file_id` by importing `mulewatch.domain.file_key`** in the container: a
  plain `sqlite3` connection has no `file_id()` function.
- **A composition test that GETs the captured webui app leaks the webui's `ReaderProvider` connections** into a
  `ResourceWarning` in the next test; block 200's wiring test captures `build_webui_app`'s arguments instead.
- **The fakes never suspend**, so concurrency faults stay invisible to them (holistic findings 1 and 2): block
  205's `connect()` test uses an async `MockTransport` handler that yields once.
- **A mutation restored within the same second leaves a stale `.pyc`** that keeps it alive: delete
  `__pycache__` after restoring.
- `agents/reference/2026-06-23-codebase-audit-findings.md` still names `MuleUnreachableError`; a dated record,
  left as is.

## Holistic review

`.reviews/stage2-holistic.md` (gitignored), over `origin/main...origin/feat/webui-dashboard-status`: CRITICAL 0,
MAJOR 2, MINOR 15. Every finding was fixed in the lot except 16, an accepted limit.

| # | Severity | Finding | Exit |
|---|---|---|---|
| 1 | MAJOR | The client backoff never grew: a no-op `connect()` reset it | fixed, block 205 (#153) |
| 2 | MAJOR | Concurrent `connect()` calls each logged in, leaking sessions | fixed, block 205 (#153) |
| 3 | MINOR | The status loop died on any client error but unreachable | fixed, block 205 (#153) |
| 4 | MINOR | The D13 restart on an under-reported list; amuled's answer for a queued file unknown | fixed, block 205 (#153): envelope-less list unreachable, the fact written into D13 by block 210 |
| 5 | MINOR | Section 4's Kad exception unmarked | fixed, block 210: marker with its 45 s bound |
| 6 | MINOR | D14's `connect()` and D7's backed-off failure rule unmarked | fixed, block 210: markers |
| 7 | MINOR | `p2pwatch_client_unreachable` without `client` | fixed, block 205 (#153); D16 marker by block 210 |
| 8 | MINOR | The webui took the concrete `StatusBoard` | fixed, block 207 (#154): `StatusReadings` port |
| 9 | MINOR | `run_task`'s unreachable skips | fixed, block 205 (#153): deleted |
| 10 | MINOR | Stale docstrings (cycle, amuled, "D3") | fixed, block 207 (#154) |
| 11 | MINOR | `AGENTS.md` described the removed code | fixed, block 210 |
| 12 | MINOR | Living docs left behind; operate.md missed the D15 alerts | fixed, block 210 |
| 13 | MINOR | The node's `crawler.yml` carries keys D17 refuses | fixed, block 210: written under Next |
| 14 | MINOR | The adapter picked its own clock | fixed, block 205 (#153) |
| 15 | MINOR | `start()` would send any network's file as an ed2k link | fixed, block 205 (#153) |
| 16 | MINOR | A pause lets searches waiting their network slot start | accepted limit, spec section 7; the banner and `docs/operate.md` say so (block 210) |
| 17 | MINOR | The simulated year did not exercise the TTL | fixed, block 207 (#154) |

## Not validated

- **Nothing of this lot ran against a real amuled on the node**: the paced `search()`, `status()`, `start()` and
  `downloads()` are tested against `FakeAmuleApi`; the compose smoke test in CI is the only real daemon they
  meet. The integration suites do not run on this machine; block 205's `AmuleApiClient(..., clock=...)`
  change to them is type-checked by mypy only.
- **The 60 s pacing against Lugdunum's unpublished threshold** (spec section 7).
- **The node's eD2k server count**: the 120 s budget assumes a sweep under it, about 140 servers (spec section 7
  asks for it before the top of the stack).
- **Kad's firewalled window under 5 min**: to watch on the node across a few amuled restarts (spec section 7).
- **local.db 0006 to 0008 on the node's data**: run on `backup()` copies of the node's local.db (version 5, 25
  rows) by blocks 160 and 190: rows and identities preserved, the four new columns NULL. The acceptance's SQL
  comparison against the catalog's `file_id` could not run: the node's catalog is still at version 5.
- **The D13 restart against a client that forgets its transfers**: none exists before stage 5.
- The dashboard and `/node` in a browser.

## Next

The operator reviews and merges the stack (`gh stack merge --rebase`) after the lead rebases it onto `main`. No
release (D23). Then stage 3: port-sync and its metrics move into the aMule container.

**Before the node pulls the next image**, its `/home/geoffrey/Projets/mulewatch/crawler.yml` must lose the five
keys D17 refuses: it carries all of them (`cycle_interval_seconds`, `search_poll_budget_seconds`,
`search_poll_interval_seconds`, `keyword_pause_min_seconds`, `keyword_pause_max_seconds`, grep 2026-10-10),
and the crawler would refuse to start with a `ConfigError` naming the first. Whoever cuts the next release
tells the operator first.
