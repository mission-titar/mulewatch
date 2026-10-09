# Stage 2: generic search, download and status ports

- Date: 2026-10-09 (discussion 2026-10-09)
- Status: APPROVED by the operator 2026-10-09
- Tier: Spec (new ports, local.db's schema, Prometheus metric names, config keys, notifications and webui
  routes change)
- Umbrella: `agents/specs/2026-10-08-multi-network-architecture.md`, stage 2 (D9, D13, D14), which this spec
  corrects (D24)
- Evidence: aMule at the pinned commit `909d304d993ee07df6c6f6acf501a6d791d53666` (`AMULE_COMMIT` in
  `packages/crawler/Dockerfile`), its amuleapi reference `docs/api/REFERENCE.md` at the same commit, the
  Lugdunum server documentation (https://lugdunum.shortypower.org/kiten.html), eMule's help topic 229
  (https://www.emule-project.com/home/perl/help.cgi?l=1&topic_id=229&rm=show_topic, fetched 2026-10-09),
  and the handoff `agents/handoffs/2026-09-22 - handoff - amuleapi migration lot 1.md` (point 5)

## 1. Context and goals

The core drives one client, amuled, through two aMule-shaped ports. `MuleClient` (`ports/mule_client.py`)
exposes a four-call search (`start_search`, `search_progress`, `fetch_results`, `widen_search`) over an eD2k
`SearchChannel` enum, plus `network_status()` with eD2k and Kad fields. `MuleDownloadClient`
(`ports/mule_download_client.py`) takes an ed2k link and returns aMule's queue and shared list, and the core
decides completion from them. Search runs in a cycle: every keyword on every channel, in one shared queue,
one cycle every 300 s. local.db keys downloads by `ed2k_hash` (stage 1's D10).

Stage 5 adds clients whose search ends differently (slskd reports a state, AirDC++ has no end), whose
download completes on another signal, and whose status has other fields. Stage 2 makes the three ports
generic, puts aMule's adapter on them, and moves the download side to `FileKey`, in today's single
container, before any new network exists.

Goals:

- **Generic ports.** Search, download and status are ports any client can implement, and their consumers
  never branch on the client or the channel.
- **Search as often as each network allows.** Every channel searches at its own pace, set by the rules of
  its network, with no cycle tying fast channels to slow ones.
- **The download side speaks `FileKey`.** local.db, the download loop and the download port name a file by
  `FileKey` / `file_id`, and a download carries lifecycle fields (D13 of the umbrella).
- **The operator sees each client's status** on the dashboard, and is alerted when a client or a channel
  stays degraded.

## 2. Non-goals

- No persistent or passive search port (D1). They arrive with their first client, in stage 5.
- No new network, no change to matching behaviour (observed: the matching package's golden corpus passes
  unchanged), no source history (stage 4).
- No `retry_after` from an adapter (D7): no client of stage 2 announces a delay.
- Port-sync stays as it is, aMule-specific, until stage 3 moves it into the aMule container (D18).
- No release (D23).

## 3. Decisions

### D1. No persistent or passive search port in stage 2

Stage 2 declares one search port, the bounded one (D3). The umbrella's D9 asked to declare the persistent
(gtk-gnutella) and passive (Bitmagnet) ports too.

*Reason: no client implements them before stage 5, and a port method with no caller breaks the workflow's
"coherent alone" rule. They are declared with their first client (operator, 2026-10-09).*

### D2. A channel is the one unit of search and status

A **channel** is an independent way for a client to search, named by an opaque string the client declares.
aMule declares two: `ed2k` (the global search over eD2k servers) and `kad`. A future slskd would declare one.
Every search runs on a channel, and every status line describes a channel (D14).

`SearchChannel` (`GLOBAL = "global"`, `KAD = "kad"`) and `application/channels.py` (`network_label`) go. The
aMule adapter maps the channel `ed2k` to amuleapi's search type `global`. The Prometheus label `network`
keeps its values `ed2k` / `kad`, which were already the channel's label.

The name is opaque to every consumer: the scheduler, the metrics, the alerts and the webui use it as a label
and never test its value. Channel names are unique per client only, so every consumer pairs them with the
client's name.

*Reasons. aMule's search channel `global` and its status line `ed2k` describe the same thing (operator,
2026-10-09), so one concept serves both. An opaque name keeps the consumers substitutable across clients
(Liskov, operator's question): a test `channel == "kad"` in the core would be an aMule rule in the core.*

### D3. `SearchClient`: one call per search

```python
class SearchClient(Protocol):
    channels: tuple[str, ...]
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def search(
        self, keyword: str, channel: str, budget_seconds: float
    ) -> tuple[FileObservation, ...]: ...
```

- **The budget is a ceiling set by the core**, not a duration. It runs from the network start of the
  search, not from the call: the wait the pacing imposes (D5) comes before it. The adapter returns as soon
  as its client signals the end of the search (aMule: `progress.state == "finished"`), and at the ceiling at
  the latest, with what it has collected.
- **How to wait is the adapter's business**: polling, its interval, and Kad's widening (`POST
  /search/{id}/more`, at most four reasks, `409 kad_more_exhausted` when spent) all move inside the aMule
  adapter. The poll interval becomes an adapter constant (5 s, today's value). **A widening failure is
  absorbed**, the search stands: a reask on a search already finished answers `400 bad_request`, which
  `errors.py` maps to `ApiUnreachableError` (only `amuled_rejected` and `not_found` are operation codes), and
  unabsorbed it would back off the whole client. Today `SearchWorker._widen` absorbs both.
- **`stop_search`, `widen_search`, `search_progress`, `fetch_results` and `network_status` leave the port.**
  `stop_search` was called by nothing outside the adapter. `network_status` moves to the status port (D14)
  and, for port-sync, to its own aMule-specific port (D18).
- **The budget is a core constant of 120 s**, above the natural life of an aMule search: 45 s for Kad
  (`SEARCHKEYWORD_LIFETIME`, `src/kademlia/kademlia/Defines.h:70`), and for ed2k up to 12 s for the
  connected server's answer (`SERVER_ANSWER_TIMEOUT_MS`, `src/SearchList.h:464`) plus 750 ms per other
  server (`src/SearchList.cpp:722`).
- **Accepted loss**: amuled finalizes an ed2k sweep on the timer tick after its last UDP request
  (`src/SearchList.cpp:930-933`), so the last server has 750 ms to answer before `finished`, and a later
  answer is not read.

*Reasons. The core waits for the end, then records everything at once: no partial result is consumed, so
nothing needs three calls (operator: "a simple `search() -> Result[]` unless something justifies more").
Each client signals the end differently (aMule a percentage and a state, slskd a state, AirDC++ nothing),
so a generic polling loop in the core would not even fit AirDC++. `asyncio.timeout` around `search()` would
cancel the coroutine and lose what was collected, hence a parameter. The ceiling guards against a client
that progresses without ever finishing. Counted from the call, the budget would be spent queueing: with K
ed2k keywords, the k-th waits (k-1) x 60 s inside `search()`. Today's 30 s budget cut Kad's reads before its
45 s end and lost its late answers.*

### D4. One task per (channel, keyword), no cycle

```
task (client, channel, keyword), forever:
    wait while the crawl is paused
    wait while (client) or (client, channel) is backed off
    results = await client.search(keyword, channel, SEARCH_BUDGET_SECONDS)
    for each result: record_observation(...)
    await asyncio.sleep(0)
```

- Every task runs concurrently with every other. The tasks of one channel take turns only because the
  adapter makes them (D5).
- **The core sets no interval.** A channel searches as often as its network's rules allow.
- **A task never spins**: it sleeps until a backoff ends instead of calling again, and yields once
  (`asyncio.sleep(0)`) after every search, so a `search()` that returns without suspending (a fake, an
  adapter failing before its first `await`) cannot starve the event loop. A test drives such a fake and
  checks that another coroutine still runs.
- **Results go down the pipeline as each search returns**, through the existing `record_observation`
  (catalog write, matching, decision, download nudge), written once, in the core task. The adapter knows
  neither the catalog nor the matcher.
- **`SearchWorker` stays as the per-client search runner** the tasks call: `run_task` keeps the connection,
  the backoff and the recording. Its cycle-only parts (`SearchTask.skipped_by`, `is_blocked_for`,
  `report_dropped`, `pause_between_items`, `WorkerPolicy.keyword_pause_*`) go with the cycle.
- **The cycle goes**: `run_search_cycle.py` and its shared LIFO queue, the `skipped_by` hand-over between
  interchangeable workers, `domain/search/cycle.py` (the per-cycle keyword shuffle), `cycle_index`, the
  `SearchCycleCompleted` and `SearchTaskDropped` events, and the cycle's coverage sampling (replaced by D15).
- **The pause control stays**: a paused task waits before its next search. A search in flight completes.

*Reasons. A cycle starts again only when its slowest channel is done, so a fast channel idles behind a slow
one; the operator wants a design robust to that, not a setting that happens to hide it. "The important thing
is that we search as often as reasonable" (operator): if ed2k may search three times while Kad searches
once, it must. Catalog writes stay safe: SQLite is synchronous on one asyncio thread, so two tasks never
write at once.*

### D5. The aMule adapter enforces its networks' rules

| Channel | Rule enforced inside `search()` | Source |
|---|---|---|
| `ed2k` | Starts are serialized by an `asyncio.Lock`. A start waits until 60 s have passed since the previous start, and until the previous search reports `finished` or has run past one budget. | aMule's core keeps one ed2k search anchor: a new ed2k start repoints `m_currentSearch`, and a previous search's late answers are misfiled (`src/SearchList.cpp:614-620, 656-660`, "ed2k is single-in-flight" at `:1297-1298`). Lugdunum blacklists an IP that sends "too many requests to the server" for `blacktime`, 3600 s by default, and publishes no threshold. eMule's help: "If you do a lot searches within a short time ... you could trigger this protection and the sever will refuse to communicate with you any further." |
| `kad` | 60 s between two starts on the same **Kad target**. Different targets run in parallel. | Kad keys a keyword search on its first word only: `GetWords` splits on `" ()[]{}<>,._-!?:;\/\""` (`SearchManager.h:128`), skips words under 3 UTF-8 bytes and lowercases the rest (`SearchManager.cpp:283-292`), then hashes `m_words.front()` into the target and refuses a target already searched (`:170-185`). Each Kad node accepts 3 `KADEMLIA2_SEARCH_KEY_REQ` per minute per IP, one per 20 s (`PacketTracking.cpp:134-136, 169`), drops the excess silently, and bans the IP beyond 5 times the limit (`:218-225`) for `CLIENTBANTIME`, 2 h (`src/include/protocol/ed2k/Constants.h:83`). A target's searches reach the same nodes every time. |

- **The Kad target is derived by the adapter** with Kad's tokenizer: the lowercased first word of at least
  3 UTF-8 bytes. `keroro 62` and `keroro mission` share the target `keroro`: they take turns, 60 s apart.
- **A keyword with no such word has no Kad target** (`62`, `ok`): Kad would refuse it ("search keyword too
  short", `SearchManager.cpp:172`) and back off the whole channel. On `kad`, `search()` waits that
  keyword's own 60 s slot, then returns `()` without any network call: no spin, no backoff, no failure
  counted. The adapter logs it once per keyword, at its first search after startup. Test: one simulated
  hour with such a keyword makes at most 60 `search()` returns on `kad` and no `POST /search`.
- **A start waits with one `sleep`** until it is allowed, after reserving its slot (the ed2k lock, the Kad
  target's next start), so two concurrent callers never both see "allowed now". It never refuses and lets
  the core retry.
- **The 60 s are adapter constants, not config**: they are rules of the networks, and a key would invite
  lowering them until the ban.
- **Why 60 s on Kad**, when Kad's own refusal for a search's 45 s life already keeps a target under its
  20 s per packet: a margin over that natural spacing, and the same pace as ed2k. The next lot can weigh
  lowering it to 46 s.
- **Kad's `400 amuled_rejected` "Search keyword is already on search list"** means "not yet": the adapter
  waits one poll interval and retries, for at most one budget counted from the first attempt (no network
  start exists yet to count from), then raises `SearchFailedError`, so the channel backs off. With 60 s
  between two starts of one target and a 45 s life, it only happens when someone else holds that target
  (aMule's own UI, another EC client). Test: a target refused for longer than a budget raises at exactly
  one budget, not before.
- **ed2k's "past one budget"** bounds the wait for a search that never reports `finished`: past it the next
  start goes ahead, at worst misfiling the old search's late answers, which only touches their `keyword`
  provenance.

*Reasons. Pacing (preventing a ban) belongs to whoever knows the network; backoff (reacting to a failure)
stays generic in the core (D7). Enforced inside `search()`, a limit cannot be forgotten by a caller. The
waiting is one `sleep`, not a refusal the core would retry in a loop. ed2k and Kad searches can be in flight
together: "starting one never disturbs the other" (REFERENCE.md, `POST /search`). Each other server of an
ed2k sweep receives one UDP packet per search, so the risk sits on the connected server. Keyed per keyword,
the Kad rule would let two keywords sharing a first word hit the same nodes twice as often.*

### D6. aMule manages its searches' lifetime: no stop, no delete

The adapter never sends `POST /search/{id}/stop` nor `DELETE /search/{id}`. A Kad search ends and is deleted
by Kad itself at 45 s (`SearchManager.cpp:324-326`); an ed2k search ends with its sweep.

amuled keeps a bounded ring of 20 searches, finished ones included, and evicts the oldest (REFERENCE.md,
`GET /search/{id}/results`). With K distinct Kad targets, at most 1 + K of our searches are in flight, and
at most 2 + 2K start within one 120 s budget (one ed2k and one Kad per target every 60 s). A search is
evicted while still read once 2 + 2K exceeds 20: **the ceiling is 9 distinct Kad targets**, an accepted
limit (today: 2).

This removes the stop-before-start of 2026-09-22 (`client.py:90-100`). It was added because the smoke
config's 5 s cycle relaunched a keyword on Kad within its 45 s and got `Search keyword is already on search
list` on every cycle. The stop took the keyword off Kad's list, which also removed aMule's own protection
against relaunching a keyword too fast: harmless at 300 s between two searches of a keyword, a straight
road to the 2 h ban once searches chain.

*Reason: the operator prefers relying on aMule where it can. Kad's own expiry means no state of ours can
leave a target blocked for more than 45 s, and D5's 60 s replaces the protection the stop bypassed.*

### D7. Backoff stays generic in the core, saved at each change

The core keeps today's exponential backoff (`backoff.*` config) per client and per (client, channel), keys
`amuled` and `amuled:ed2k` / `amuled:kad`. It is saved to `scheduler_state.channel_backoff` at each change
(failure or reset) instead of at the end of a cycle that no longer exists. The old keys (`amuled:global`)
are not carried over: local 0006 deletes the stored map (D19), which costs at most one reset backoff.

`SearchFailed` carries no `retry_after`.

*Reason: for a search, amuleapi never sends a delay (`400 amuled_rejected`, `503 ec_unavailable`; its
`Retry-After` only accompanies the auth lockout, SSE streams and file responses), and a stopped container
answers nothing. A field no client fills is YAGNI; slskd adds it in stage 5 if it announces one.*

### D8. Generic client errors

`ports/client_errors.py`: `ClientError`, `ClientUnreachableError` (the client's API does not answer: the
caller degrades and backs off the client), `SearchFailedError` (a search refused: the channel backs off),
`DownloadRejectedError` (a download refused: that download fails). They replace `MuleClientError`,
`MuleUnreachableError` and `MuleSearchFailedError`; the amuleapi errors re-parent onto them, and
`ApiAuthError` stays outside the contract (fail fast).

*Reason: the download loop treats a rejected link through `MuleSearchFailedError`, an error named after
search. Each port names its own failure, and the consumers catch a generic type.*

### D9. `DownloadClient`: `start` and `downloads`

```python
@dataclass(frozen=True)
class DownloadRequest:
    file: FileKey
    filename: str
    size_bytes: int

@dataclass(frozen=True)
class DownloadStatus:
    file: FileKey
    bytes_done: int
    bytes_total: int
    completed: bool
    waiting_reason: WaitingReason | None
    failure_reason: FailureReason | None

class DownloadClient(Protocol):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def start(self, request: DownloadRequest) -> None: ...
    async def downloads(self) -> tuple[DownloadStatus, ...]: ...
```

- **The core builds a `DownloadRequest`** from the catalog's last observation, as it builds the link today.
  The aMule adapter builds the ed2k link (`build_ed2k_link` stays in `catalog_matching`, also used by the
  webui and the decision notification).
- **`downloads()` returns every download the client knows**: for aMule, `GET /downloads?status=all` plus the
  files of `GET /shared` the queue no longer lists, as completed. The core ignores files it did not queue,
  as today.
- **`shared_files`, `download_queue`, `add_link` and `network_status` leave the port.** `/shared` becomes an
  aMule detail of `completed`.

*Reasons. An observation is the wrong value to hand a download client: it is a past fact (seen at a time,
by a keyword, with a source count), a file has thousands of them under several names, and it lacks what
some networks need (Gnutella's token, Soulseek's username), so it would also couple the download port to
`FileObservation`. A name and a size are what every network has, and the disk cap needs the size anyway.
What a network needs beyond them arrives with that network (operator, 2026-10-09).*

### D10. Completion is the adapter's positive signal

`completed` is computed by the adapter from its client's own signal, never from bytes. For aMule, a file is
completed when its queue entry has `status == "completed"` (the one status amuleapi reserves for "moved,
awaiting clear", `src/webapi/Refresher.cpp:320-324`), or when it is in `GET /shared` and absent from the
queue (completed then cleared). A queue entry with any other status is not completed, even when it is listed
in `/shared`: amuled shares partfiles too.

Its test: a download at `completed_bytes == size_bytes`, status `completing`, **present** in `/shared`, is
not completed.

*Reason: all bytes received is not the file verified and in place. aMule hashes then moves the file and
re-downloads a corrupt part, so `bytes_done` can go back down; slskd marks `Succeeded` before the move,
qBittorrent can still be `moving`, AirDC++'s rename can fail; a BitTorrent download before its metadata has
`0 == 0` (umbrella D13). Today's rule is a byte count (`DownloadEntry.is_complete`, `size_done >=
size_full`) combined with `/shared`, which a full partfile listed in `/shared` passes: the 2026-09-02 20 %
case's family. Rule 3 of the umbrella's D13 stands: a positive signal, checked against a false positive.*

### D11. The download side speaks `FileKey`

- `DownloadCandidate` leaves `catalog_matching.engine` for the crawler's catalog port, with `file:
  FileKey` instead of `ed2k_hash`. Only the crawler uses it, and `FileKey` lives in the crawler.
- `download_decisions()` returns `FileKey`s; the download repository, the loop, `DownloadCompleted` and the
  webui's node page take a `FileKey` / `file_id`. Stage 1's junction (`FileKey(Network.ED2K, hash)` rebuilt
  in the loop) goes.

*Reason: stage 1's D10 deferred this to stage 2, so the download port is rewritten once.*

### D12. Lifecycle fields

| Field | Who uses it | For what | aMule source |
|---|---|---|---|
| `failure_reason` | the download loop | the download becomes `failed`, with its cause | status `erroneous`: `error`; a refused `start`: `rejected`; the TTL: `lost` |
| `last_progress_at` | the operator, on `/node` | see that a download has not moved for days | computed by the core: stamped when `bytes_done` grows |
| `waiting_reason` | the operator, on `/node` | see why it waits | first match: status `insufficient_disk`: `disk_full`; `paused` or `stopped`: `paused`; `waiting` (`PS_WAITING_FOR_HASH`), `hashing` or `allocating`: `local`; `sources.total == 0`: `no_source`; `sources.total > 0` and `sources.transferring == 0`: `remote_queue`; otherwise none |

- amuleapi's `waiting` is a local hashing wait (`Refresher.cpp:333-345`), not a remote queue: aMule has no
  partfile-level remote-queue status, a remote queue is per source, hence `remote_queue` derived from the
  `/downloads` row's `sources: {total, unavailable, transferring, a4af}`.
- Only `failure_reason` changes the core's behaviour. **Waiting is not failing** (umbrella D13, rule 1): a
  download with no source for months stays `queued` or `downloading` and shows its stall; it becomes
  `failed` only on a failure its client reports, a rejected start, or the existing `lost` TTL.
- **A listed download with a client-reported `failure_reason` stays `failed`.** Today `_monitor` moves any
  listed, non-completed row back to `downloading` (`run_download_cycle.py:142-158`, `FAILED` is no wall); it
  now promotes a `failed` row only when its status carries no `failure_reason`, so an `erroneous` partfile
  does not flap each round. A failed row can still complete, as today.

*Reason: today an `erroneous` partfile is ignored and stays in progress forever, a real gap; the two display
fields make the umbrella's rule 1 visible to the operator (operator, 2026-10-09).*

### D13. A download the client forgot is started again

Each round, a `queued` or `downloading` download absent from `downloads()` receives `start()` again. The
`lost` TTL (`download.lost_after_seconds`) keeps its meaning: after that long without the client showing it,
the download becomes `failed` with `lost`.

At most one `start()` per download per round (`download.poll_interval_seconds`, 30 s), sent to our own
client, not to the network, and stopping as soon as the client lists it again. A client that accepts the
start without ever listing the download is bounded by the TTL.

- **Accepted limit: a download cancelled by hand in aMule's UI comes back within a round.** amuled accepts
  a link for a cancelled file (`IsFileExisting` is false for a file neither shared nor queued,
  `src/DownloadQueue.cpp:377-420`). The remedy is to cancel it in aMule and mark its row `failed`, which no
  round restarts (the relaunch covers `queued` and `downloading` only): `UPDATE downloads SET state =
  'failed' WHERE native_id = ?`. It runs the way `docs/troubleshooting.md` already runs its retry write
  (`DELETE FROM downloads ...`, which would do the opposite here: the next round re-queues the decision):
  the crawler alone stopped with `s6-svc -d`, the write through `docker compose exec --user amule ...
  python -c "import sqlite3; ..."` (the webui console is read-only), the crawler started again with
  `s6-svc -u`. Block 180 writes this procedure into `docs/troubleshooting.md`.
- **Not handled: a completed file moved out of Incoming before the loop saw it** would be downloaded again
  in full. A lost-media watch keeps sharing what it finds, so its Incoming is not emptied under it; the
  `docs/troubleshooting.md` passage that names this cause of absence is rewritten in block 180.

*Reason: aMule keeps its downloads across a restart, so the gap (a `downloading` row is never re-sent) never
showed; slskd drops every transfer on restart (umbrella D13, rule 4). One rule covers both (operator).*

### D14. `StatusClient`: per channel, `on_network` and `connectable`

```python
@dataclass(frozen=True)
class ChannelStatus:
    channel: str
    on_network: bool
    connectable: bool | None

@dataclass(frozen=True)
class ClientStatus:
    version: str | None
    channels: tuple[ChannelStatus, ...]

class StatusClient(Protocol):
    async def status(self) -> ClientStatus: ...
```

- **API reachable** is `status()` not raising `ClientUnreachableError`; no field carries it.
- **`on_network`**: the channel has joined its network (an eD2k server, Kad, a Soulseek login).
- **`connectable`**: peers can connect to us. `False` is a finding (Low-ID, firewalled); `None` means the
  client cannot tell (slskd tests nothing, Bitmagnet reports nothing).

| aMule channel | `on_network` (`GET /status`) | `connectable` |
|---|---|---|
| `ed2k` | `ed2k.state == "connected"` | `ed2k.high_id` when on the network, else `None` |
| `kad` | `kad.state == "connected"` | `not kad.firewalled_tcp` when on the network, else `None` |

- **`ec_connected` not `true` raises `ClientUnreachableError`.** Once a first snapshot exists, `/status`
  answers `200` from amuleapi's cache with `ec_connected: false` and the last states
  (`src/webapi/Api.cpp:2301-2320`; REFERENCE.md: "`ec_connected` is `false` while amuleapi can't reach the
  underlying amuled"). Mapped as states, a dead amuled would read "on network, connectable". Test with
  `ec_connected: false` and stale `connected` states.
- `version` is `daemon_version` from `GET /api/v1/version`, and `None` when it is `""` (EC down).
- The status never carries an address (`public_ip`, `server_ip` are dropped).

*Reasons. aMule needs three things watched: the ed2k server connection, High-ID and Kad's firewall
(operator); per channel, one shape covers aMule, gtk-gnutella's G1 and G2, and BitTorrent's two clients,
each its own client with its own lines. The names were chosen with the operator: `connectable` is
BitTorrent's word; `connected` beside it would read as "could become connectable", and `joined` left open
what is joined. `ed2k.high_id` read without `state` meant "no id yet" as well as Low-ID (REFERENCE.md,
`GET /status`).*

### D15. A status loop per client, alerts on a degraded state that lasts

```
status loop, per client, every 60 s (core constant):
    status() -> gauges per channel, alert clocks, last status kept for the webui (D21)
    ClientUnreachableError -> InstanceUnreachable (log and counter), alert clock of the client
```

- **An alert fires when a degraded state has lasted**, whatever came before it and whenever it began,
  boot included:
  - `on_network = False` or `connectable = False` on a channel for 5 min or more;
  - the client's API unreachable for 2 min or more.

  The durations are core constants. "Recovered" is sent when the state turns good again (`True`, or the API
  answering), only if an alert had fired. `None` is not degraded: it stops the clock without sending a
  recovery.
- **The state is only "degraded since when"**, per (client, channel, field) and per client, in memory.
- **While the API is unreachable, every channel of that client is unknown** (`None`): their clocks stop,
  and only the API alert can fire. A dead amuled sends one alert at 2 min, not a second one per channel at
  5 min. Test: an unreachable client with channels `False` before it alerts on the API only, and the
  channel clocks restart from zero when it answers again.
- **Client unreachable becomes a notification** (OPERATIONS) with its recovery. Today `InstanceUnreachable`
  is only a log line and a counter (`policy.py` gives it no audience); it stays so, for each failed
  attempt, and the alert is a new event.
- **What this covers**: a channel that never joins its network after boot (what `AllInstancesBlind` did),
  no alert for Kad's firewalled window after each reconnect or port-sync restart, and an alert for a node
  back in Low-ID after a reboot. Kad reads firewalled until two peers confirm
  (`src/kademlia/kademlia/Prefs.cpp:158-170`); the check asks peers to connect back
  (`KADEMLIA_FIREWALLED_REQ`, `FirewalledCheck`, `src/kademlia/net/KademliaUDPListener.cpp:174-190`), and no
  constant bounds how long their answers take. aMule's own estimate is 5 min: it delays its buddy search "the first 5mins of starting the
  client ... just in case it takes a bit for the client to determine firewall status"
  (`src/kademlia/kademlia/Kademlia.cpp:108-110`). The hourly recheck (`:107`) does not reopen the window:
  during a recheck `GetFirewalled` reports the last known state (`Prefs.cpp:160-165`). The 5 min threshold
  equals aMule's estimate, unmeasured on the node (section 7).
- **What goes**: the core's rule "an instance can search if High-ID or Kad connected"
  (`run_search_cycle._is_search_capable`), `domain/search/coverage.py`, and the `AllInstancesBlind`,
  `ConnectedInstancesSampled` and `SearchCapabilitySampled` events. Every channel keeps searching and backs
  off on failure.
- `InstanceUnreachable`'s message names the client generically (`f"{client} unreachable"`, still "amuled
  unreachable" for aMule).

*Reasons. The operator's rule: alert on a degraded state that persists, independent of the previous state
and of boot. A transition rule either alerts on every Kad reconnection or never on a node that boots blind;
a duration does neither. The rule "High-ID or Kad" is aMule's, coded in the core: it would break
substitutability for any other client. The status was sampled at the start of each cycle, which D4 removes,
so it needs a loop of its own.*

### D16. Metrics renamed `p2pwatch_*`

| Before | After |
|---|---|
| `emule_searches{network}`, `emule_observations{network}`, `emule_search_failures{network}` | `p2pwatch_searches{client, network}`, `p2pwatch_observations{client, network}`, `p2pwatch_search_failures{client, network}` |
| `emule_decisions{tier}` | `p2pwatch_decisions{tier}` |
| `emule_downloads_queued`, `emule_downloads_completed`, `emule_download_disk_free_bytes` | `p2pwatch_downloads_queued`, `p2pwatch_downloads_completed`, `p2pwatch_download_disk_free_bytes` |
| `emule_crawler_up` | `p2pwatch_crawler_up` |
| `emule_mule_unreachable` | `p2pwatch_client_unreachable` |
| `emule_search_cycles`, `emule_search_cycle_duration_seconds`, `emule_search_blind_cycles`, `emule_search_tasks_dropped` | removed (no cycle, no shared queue) |
| `emule_connected_instances{network}`, `emule_search_capable` | removed, replaced by `p2pwatch_channel_on_network{client, network}` and `p2pwatch_channel_connectable{client, network}` (1 or 0; the series is removed while unknown) |
| `emule_port_sync_triggered`, `emule_high_id_recovered`, `emule_port_mismatch` | unchanged |

*Reasons. The project generalizes now, and no release ships between stage 2 and stage 3 (D23), so renaming
here breaks the operators' dashboards once, as late as renaming in stage 3 would. `p2pwatch` is the
project's final name (umbrella D15). The `client` label lands now, on the two gauges and on the three
counters labelled by channel, because channel names are unique per client only (D2): stage 5's two
BitTorrent clients could share one, their series would merge, and adding the label then would break the
dashboards a second time. The three port-sync metrics leave the core in stage 3 with port-sync
(umbrella D7), so renaming them would serve nothing.*

### D17. Five config keys removed, and refused

`cycle_interval_seconds`, `keyword_pause_min_seconds`, `keyword_pause_max_seconds`,
`search_poll_interval_seconds` and `search_poll_budget_seconds` are removed. Today an unknown top-level key
is ignored silently (`parse_crawler_config` reads the keys it knows), so the parser gains an explicit list of
these five: a `crawler.yml` carrying one is refused at startup with a `ConfigError` naming it.
`deploy/crawler.yml` and `tests/smoke/crawler.yml` drop each key in the block that stops reading it.

| Key | What it did | Becomes |
|---|---|---|
| `cycle_interval_seconds` (300) | time between two cycle starts | nothing (no cycle, D4) |
| `keyword_pause_min/max_seconds` (1 to 4) | random pause between two searches, eD2k anti-ban | the adapter's rules (D5) |
| `search_poll_interval_seconds` (5) | how often to ask aMule for progress | an adapter constant (D3): local traffic with our own client |
| `search_poll_budget_seconds` (30) | how long to wait for one search's results | a core constant of 120 s (D3), only a guard |

*Reason: none of the five is an operator's choice any more (operator, 2026-10-09). A removed key ignored
silently would let an operator believe a setting still applies.*

### D18. Port-sync keeps its own aMule reads

`NetworkStatus` and `KadStatus` move from `ports/mule_client.py` to a port-sync port module, read only by
`PortPreferences` (`application/port_sync_loop.py`). `AmuleApiClient` keeps `network_status()`,
`get_listen_port()` and `set_listen_port()` for it. `server_name` and `server_addr`, read by nothing, go.

*Reason: port-sync is aMule-specific and moves into the aMule container in stage 3 (umbrella D7); widening
the generic status port with High-ID details for it would undo D14.*

### D19. local.db: three migrations

- **0006, scheduler state** (with D4): deletes the `cycle_index`, `last_full_cycle_at` and `channel_backoff`
  rows of `scheduler_state`.
- **0007, downloads keyed by `file_id`** (with D11):

```sql
CREATE TABLE downloads_new (
    file_id BLOB PRIMARY KEY,
    network TEXT NOT NULL,
    native_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    state TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    completed_at TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT,
    UNIQUE (network, native_id),
    CHECK (network IN ('ed2k')),
    CHECK (network <> 'ed2k' OR (LENGTH(native_id) = 32 AND native_id NOT GLOB '*[^0-9a-f]*'))
);
INSERT INTO downloads_new
SELECT file_id('ed2k', ed2k_hash), 'ed2k', ed2k_hash, target_id, state, queued_at, completed_at,
       size_bytes, last_seen_at
FROM downloads;
DROP TABLE downloads;
ALTER TABLE downloads_new RENAME TO downloads;
```

  The `file_id()` SQL function of stage 1 is registered on the local connection too (`open_local`).
- **0008, lifecycle columns** (with D12): `bytes_done INTEGER NOT NULL DEFAULT 0` (the last value seen, from
  which `last_progress_at` is computed), `last_progress_at TEXT`, `waiting_reason TEXT`, `failure_reason
  TEXT`.

Their tests are `test_local_migration_0006.py`, `_0007.py` and `_0008.py`: the catalog's
`test_migration_0006.py` to `_0008.py` already exist.

`docs/troubleshooting.md`'s manual retry becomes `DELETE FROM downloads WHERE native_id = ?`.

*Reasons. `file_id` is the catalog's key, so one file has one name in both databases; `network` and
`native_id` stay because `file_id` is a one-way hash and the loop needs the `FileKey` back to build a
`DownloadRequest` (operator). The CHECKs carry over the catalog's (stage 1's D1). `cycle_index` and
`last_full_cycle_at` are read by nothing once the cycle goes, and the webui's node page would show them
stale. `channel_backoff` is still read (D7): it is deleted to drop the `amuled:global` keys, which no task
reads again. Three migrations, because each lands with the block that needs it.*

### D20. The webui merges `/controls` into the dashboard and shows the status

- **Dashboard (`/`)**: a "Clients" section, one line per client (name, version, API reachable) and per
  channel (`on network`, `connectable`: yes, no, unknown), from the status loop's last reading, kept in
  memory (D15). A reading older than two status periods, or none, shows as such, never the last values as
  current. The webui never calls a client.
- **Controls**: the "Force a cycle now" button and `CrawlerControl.force_cycle` go (no cycle, and the
  operator does not want a "search now" in its place). Pause, resume and restart move to the dashboard,
  which shows whether the crawl is paused (`CrawlerControl.is_paused()`, new). The `POST /controls/*` routes
  stay and redirect to `/`; the `/controls` page and its menu entry go.
- **`/node`**: the downloads table shows `native_id` instead of `ed2k_hash`, plus `last_progress_at`,
  `waiting_reason` and `failure_reason`.

*Reason: without the force button, `/controls` holds three buttons and does not even show whether the crawl
is paused; next to the client status, they read in context (operator).*

### D21. The status reaches the webui through an in-memory snapshot

The status loop publishes an immutable snapshot (per client: time of the reading, `ClientStatus` or
unreachable) to a holder the webui reads on its own thread. Replacing a reference to an immutable value is
atomic, so no lock is needed.

*Reason: the webui runs on its own thread and reads local state only; a second session to the client from
the webui would double the API traffic and the 401 lockout risk.*

### D22. What the core no longer knows about aMule

After stage 2, `application/` and `domain/` name none of `MuleClient`, `MuleDownloadClient`, `ed2k_hash`,
`SearchChannel`, `KadStatus` or `NetworkStatus`, except `port_sync_loop.py` (D18).
`domain/download/policy.py`'s docstring, which names `NetworkStatus`, loses the name in block 70.

*Reason: the acceptance criterion that proves the ports generic (section 6).*

### D23. No release

Stage 2 ships no image beyond `main`, as stage 1's D16 decided until the first new network.

*Reason: the operator-visible breaks (metric names, config keys, `/controls`, local.db) reach operators once,
with the first new network's major version.*

### D24. Corrections to the umbrella spec

Block 10 adds `(Corrected: ...)` markers to the umbrella spec:

- **D9**: the persistent and passive search ports are declared with their first client in stage 5, not in
  stage 2 (D1). The bounded-search port is `search(keyword, channel, budget)` (D3), and there is no stop:
  the client manages its searches' lifetime (D6), against its table's "start, collect until done or a
  timeout, stop".
- **D14**: "as Low-ID does today" is wrong, no Low-ID edge alert exists (the edge keys are `coverage_blind`,
  `port_mismatch` and `disk_low`); the fields are per channel, `on_network` and `connectable: bool | None`
  plus the client's `version`, API reachability is `status()` not raising, and alerts fire on a degraded
  state that lasts (D14, D15 here).
- **Section 4, stage 2**: "persistent and passive search ports declared" goes.

*Reason: the umbrella is approved, and the workflow keeps a marker beside a claim corrected after approval.*

## 4. The claim that decides

**The scheduler never exceeds the networks' limits.** Over any simulated hour:

- **ed2k**: starts are at least 60 s apart, and no start happens while the previous search reports
  `running`, unless that search has run past one budget;
- **Kad**: starts on one target are at least 60 s apart, whatever keywords share it.

It is the only new behaviour that can harm the node (a 1 h server blacklist, a 2 h Kad ban), and it cannot be
watched on the node before a release. It is proved by tests driving all tasks against `FakeAmuleApi` with a
fake clock for one simulated hour, recording each `POST /search` with its time, type and query:

- a three-keyword config, two of them sharing a first word (`keroro`, `keroro mission`, `titar`): ed2k starts
  60 s apart or more, Kad starts per target 60 s apart or more, and each search's budget counted from its
  network start;
- a fake whose ed2k search never finishes: the second ed2k start happens at exactly one budget after the
  first, not before.

Each is watched failing with each rule disabled in turn (block 30, and again through the composition at
block 110).

## 5. Blocks

Paths under `packages/crawler/` unless noted. Sizes are estimates from `wc -l` of the deleted files and
`grep -c` of the touched sites. Each block measures itself after committing.

| # | Branch | Block | Contents | Est. lines / files |
|---|---|---|---|---|
| 10 | `refactor/generic-client-errors` | Generic client errors | This spec, the umbrella's markers (D24); `ports/client_errors.py`; `MuleClientError`, `MuleUnreachableError`, `MuleSearchFailedError` replaced at every site (`search_worker.py`, `run_search_cycle.py`, `run_download_cycle.py`, `port_sync_loop.py`, `composition/app.py`), amuleapi `errors.py` re-parented, `ports/test_mule_client_errors.py` and the sites' tests (D8) | 180 / 16 |
| 20 | `feat/amule-search-call` | aMule's `search()` | `AmuleApiClient.search(keyword, channel, budget)`: polling to `finished` or the budget from the network start, Kad widening inside with its failures absorbed (the `400` test), no stop or delete (D6), channel `ed2k` to type `global`; `test_client.py`, `api_fakes.py` (records calls with their time) | 300 / 3 |
| 30 | `feat/amule-search-pacing` | The pacing rules | D5 in the adapter: the ed2k lock, 60 s and the past-one-budget hatch, the Kad target (Kad's tokenizer) and its 60 s, slots reserved before sleeping, the `already on search list` retry, the no-target keyword; an injected `Clock`; the one-hour tests of section 4 | 380 / 3 |
| 40 | `refactor/channel-names` | Channels as names | `SearchChannel` and `channels.py` (with its test) go; `SearchTask.channel`, the backoff keys, the metric labels and the adapter take the names `ed2k` / `kad`; `search_worker.py`, `run_search_cycle.py`, fakes and their tests (D2) | 220 / 10 |
| 50 | `refactor/search-client-port` | The `SearchClient` port | `ports/search_client.py` (D3); `SearchWorker` calls `search()` (its polling loop and `_widen` go); the budget constant; `search_poll_*` keys removed and refused (D17); `tests/application/fakes.py`, `test_search_worker.py`, `test_run_search_cycle.py`, `test_crawler_config.py`, `deploy/crawler.yml`, `tests/smoke/crawler.yml` | 470 / 12 |
| 60 | `chore/remove-old-search-calls` | Remove the four-call search | `start_search`, `search_progress`, `fetch_results`, `widen_search`, `stop_search` from the adapter (`client.py:90-140`) and their tests (`test_client.py:255-458`); the old search protocol in `ports/mule_client.py` and `tests/ports/test_mule_client.py` | 400 / 5 |
| 70 | `feat/client-status-read` | The status port and aMule's `status()` | `ports/client_status.py` (D14); `AmuleApiClient.status()` (`/status` with `ec_connected`, `/version`), mapping and tests; `NetworkStatus`/`KadStatus` to the port-sync port module (D18); `domain/download/policy.py`'s docstring (D22) (Corrected: split at its file bound, D18's move went to block 75) | 290 / 9 |
| 75 | `refactor/port-sync-status-module` | Port-sync's own status module | `NetworkStatus` and `KadStatus` from `ports/mule_client.py` to `ports/port_sync.py`, every importer repointed, `server_name` and `server_addr` removed (D18); `tests/ports/test_port_sync.py` | 150 / 19 |
| 80 | `feat/status-alerts` | Status loop and alerts | `application/status_loop.py`: the degraded-since clocks, the 5 min and 2 min constants, alerts and recoveries (D15); events and policy (OPERATIONS audience); the two gauges with `client` (D16) in `prometheus_sink.py`; tests | 380 / 8 |
| 90 | `refactor/status-loop-wiring` | The composition runs the status loop | `composition/app.py` and `test_app.py`; `_aggregate_coverage`, `coverage.py` and its test, `AllInstancesBlind`, `ConnectedInstancesSampled`, `SearchCapabilitySampled` and their metrics removed | 330 / 10 |
| 100 | `feat/channel-search-tasks` | The per (channel, keyword) tasks | `application/search_tasks.py` (D4): task loop over `SearchWorker.run_task`, pause gate, sleep until a backoff ends, the `sleep(0)` floor and its test, backoff saved at each change (D7); tests | 380 / 4 |
| 110 | `refactor/search-tasks-wiring` | The composition runs the tasks | `composition/app.py` and `test_app.py` off the cycle; `CrawlerControl.force_cycle`, its webui button and route removed; `cycle_interval_seconds` removed and refused (D17); local 0006 and `test_local_migration_0006.py` (D19); `deploy/crawler.yml`, `tests/smoke/crawler.yml`; `docs/operate.md` and `docs/troubleshooting.md` cycle passages | 490 / 18 |
| 120 | `chore/remove-search-cycle` | Remove the cycle | `run_search_cycle.py` and `test_run_search_cycle.py`, `domain/search/cycle.py` and its test, the `SearchCycleCompleted` event and its two metrics with their tests | **1070** / 8 |
| 130 | `chore/remove-worker-cycle-parts` | Remove the worker's cycle parts | `SearchWorker`'s cycle-only parts (`SearchTask.skipped_by`, `is_blocked_for`, `report_dropped`, `pause_between_items`) and their tests (D4), the `SearchTaskDropped` event and its metric, `keyword_pause_*` removed and refused with `WorkerPolicy`'s fields (D17), `deploy/crawler.yml`, `tests/smoke/crawler.yml` | 260 / 9 |
| 140 | `refactor/metrics-p2pwatch` | Metrics renamed | `MetricName` values (D16), `InstanceUnreachable`'s generic message (D15), `test_prometheus_sink.py`, `test_policy.py`, `docs/operate.md`, `docs/high-id.md` | 130 / 7 |
| 150 | `refactor/download-loop-file-key` | The download loop on `FileKey` | `DownloadCandidate` to the crawler's catalog port with a `FileKey`; `download_decisions()`; the repository protocol and `SqliteDownloadRepository` take `FileKey` (still stored as the hash); `run_download_cycle.py`, `DownloadCompleted`; `catalog_matching/engine.py`; tests (D11) | 450 / 12 |
| 160 | `feat/local-downloads-file-id` | local 0007, downloads keyed by `file_id` | `0007_downloads_file_id.sql` and `test_local_migration_0007.py`, `file_id()` registered by `open_local`, `download_repository.py`, webui `local_read.py`, `views.py`, `node.html`, `docs/troubleshooting.md`, `tests/integration/test_compose_smoke.py`'s SQL (D19) | 330 / 12 |
| 170 | `feat/amule-download-calls` | aMule's `start()` and `downloads()` | `ports/download_client.py` types and protocol (D9); `AmuleApiClient.start()`, `downloads()` with `completed` (D10) and the reasons' mapping (D12); the false-positive test; `test_client.py`, `test_mapping.py`, `api_fakes.py` | 400 / 6 |
| 180 | `refactor/download-client-port` | The loop on `DownloadClient` | `run_download_cycle.py` on `start()` / `downloads()`, completion from `completed`, the restart of forgotten downloads (D13), the disk cap from `bytes_total - bytes_done`; `MuleDownloadClient` and its port test removed; `FakeDownloadClient`; composition; `tests/integration/test_compose_smoke.py`'s `shared_files()` call; in `docs/troubleshooting.md`, the absence passage and the cancel procedure (D13) | 490 / 12 |
| 190 | `feat/download-lifecycle-fields` | Lifecycle fields | local 0008 and `test_local_migration_0008.py`; the loop persists `bytes_done`, `last_progress_at`, `waiting_reason`, `failure_reason`, fails on a client error and keeps it failed (D12); `/node` shows them (D20); `docs/operate.md` | 420 / 12 |
| 200 | `feat/webui-dashboard-status` | Dashboard status and controls | The status snapshot holder (D21); the dashboard's "Clients" section; pause, resume, restart on the dashboard with the paused state, `CrawlerControl.is_paused`; `/controls` page and menu entry removed, POSTs redirect to `/` (D20); `docs/operate.md`, `docs/install.md`, `docs/troubleshooting.md` | 400 / 14 |
| 210 | `docs/stage2-closing` | Closing | The holistic findings, `BACKLOG.md` reconciled, handoff | 80 / 6 |

Block 120 passes the 500-line bound: it deletes `run_search_cycle.py` (210 lines) with its test (730) and
`cycle.py` with its test, and a module cannot leave without its test (an untested module fails the coverage
gate, a test without its module fails to import). What could leave on its own did, in block 130.

**Order.**

- 10 first: every later block raises or catches the generic errors.
- 20 before 30 before 50 before 60, 70 before 80 before 90, 100 before 110 before 120 before 130, 170 before
  180 before 190: each adapter or module lands with its tests before the block that switches its caller, and the old
  path leaves after it.
- 40 before 50: the port takes channel names.
- 90 before 110: the cycle samples the status until the status loop runs, so removing the cycle first would
  leave a window with no status metric and no alert.
- 150 before 160: the loop speaks `FileKey` before the table changes key under it.
- local 0006 (110), 0007 (160) and 0008 (190) land in order: a local.db stamped higher skips the lower
  numbers added later.
- 200 after 90 (the status loop it reads) and 110 (the force button already gone).

**Surfaces whose consumer arrives in a later block.**

- `AmuleApiClient.search()` (blocks 20, 30), consumed by `SearchWorker` in block 50.
- `AmuleApiClient.status()` and the status port (block 70), consumed by the status loop in block 80.
- `status_loop.py` (block 80), consumed by the composition in block 90.
- `search_tasks.py` (block 100), consumed by the composition in block 110.
- `AmuleApiClient.start()` / `downloads()` and the download port (block 170), consumed by the loop in block
  180.

Between blocks 50 and 60, the adapter carries the four-call search tested but called by nothing; between
blocks 110 and 120, `main` carries `run_search_cycle.py`, and until block 130 the worker's cycle parts and
the `keyword_pause_*` keys, tested but unreachable from a shipped entry point, as stage 1 did between its blocks 30 and 80.

**Adjacent backlog items.**

- **"Multi network", stage 2**: deleted at Wrap.
- **Stage 3**: stays open. Its text gains that port-sync's metrics and `NetworkStatus` leave the core with it
  (D16, D18).
- **Stage 4**: stays open; nothing here touches sources or events.
- **Stage 5**: stays open. Its text gains that the persistent and passive search ports arrive with their
  first client (D1), and `SearchFailed` a `retry_after` if slskd announces one (D7).
- **"Paginate the file detail timeline"**: stays open; the file pages are not touched.

The edits to the Stage 3 and Stage 5 lines rest on the operator's approval of this spec, per `BACKLOG.md`'s
rule.

## 6. Acceptance

Each criterion names the output that would prove it wrong.

- **The pacing holds** (section 4). In the three-keyword hour, the smallest gap between two ed2k starts is
  60 s or more, no ed2k start falls while the previous search reports `running` before its budget, and the
  smallest gap between two Kad starts on `keroro` is 60 s or more (counting `keroro` and `keroro mission`
  together). In the never-finishing case, the second ed2k start is at exactly one budget. Disabling each rule
  in turn makes its count fail (watched failing, each).
- **The budget runs from the network start.** In the three-keyword hour, the third ed2k keyword's search
  gets its whole budget after waiting for the first two. A budget counted from the call fails it.
- **No stop, no delete.** Over the same hour, `FakeAmuleApi` records no `POST /search/{id}/stop` and no
  `DELETE /search/{id}`. One recorded call fails it.
- **A keyword without a Kad target does not spin.** Over one simulated hour, `search()` on `kad` for `62`
  returns at most 60 times and sends no `POST /search` (watched failing without the slot wait).
- **A held Kad target gives up after one budget.** A target refused with `already on search list` for
  longer than a budget raises `SearchFailedError` at exactly one budget from the first attempt.
- **A widening failure is absorbed.** A `400 bad_request` on `POST /search/{id}/more` leaves `search()`
  returning its results, with no backoff (watched failing).
- **Channels do not wait for each other.** With a fake client whose `kad` search takes 10 times longer than
  its `ed2k` one, the `ed2k` tasks complete at least 5 times as many searches over the same simulated time.
  Equal counts fail it.
- **No task starves the loop.** With a fake `search()` that returns without suspending, a concurrent
  coroutine still runs (watched failing without the `sleep(0)` floor).
- **Completion is not a byte count.** The aMule adapter reports `completed = False` for a download at
  `completed_bytes == size_bytes`, status `completing`, present in `/shared` (watched failing with today's
  rule).
- **Waiting is not failing.** A download with `sources.total == 0` for a simulated year stays `downloading`
  with `waiting_reason = no_source` while the client lists it. `failed` fails it.
- **A client failure stays failed.** A listed download reported `erroneous` stays `failed` with `error` over
  several rounds. A return to `downloading` fails it.
- **A forgotten download is started again.** A `downloading` row absent from `downloads()` gets one
  `start()` per round, none once listed again, and becomes `failed` / `lost` after the TTL (tests).
- **A dead amuled is unreachable.** `status()` on `ec_connected: false` with stale `connected` states raises
  `ClientUnreachableError` (watched failing).
- **Alerts on a degraded state that lasts.** Tests, each watched failing:
  - `connectable = False` from the first reading alerts at 5 min, not before; one lasting 4 min alerts never;
  - `False` then `True` after an alert sends a recovery, `True` without an alert sends nothing;
  - `None` between two `False` readings restarts the clock;
  - the client unreachable alerts at 2 min, and recovers when `status()` answers;
  - while it is unreachable, channels that read `False` before send no channel alert, and their clocks
    restart from zero when it answers again.
- **Consumers are client-blind.** A fake client declaring a channel `x` goes through the search tasks, the
  status loop, the gauges and the dashboard with no code change. Separately, `grep -rnE
  'MuleClient|MuleDownloadClient|ed2k_hash|SearchChannel|KadStatus|NetworkStatus'
  packages/crawler/src/mulewatch/application packages/crawler/src/mulewatch/domain` prints only
  `port_sync_loop.py` lines (D22). Any other line fails it.
- **Removed keys are refused.** `parse_crawler_config` on a config with `cycle_interval_seconds` raises a
  `ConfigError` whose text contains `cycle_interval_seconds`, and so for each of the five (watched failing).
- **Metric names.** In `packages/crawler/src/mulewatch/domain/observability/policy.py`, `grep -c '"emule_'`
  prints `3` and `grep -c '"p2pwatch_'` prints `11` (9 renamed, 2 new). Any other count fails it.
- **Matching unchanged.** The matching package's golden corpus passes with no change to its fixtures.
- **local.db migrates losslessly.** On a copy of the node's local.db, after 0006 to 0008: the row count of
  `downloads` is unchanged, every `native_id` equals the old `ed2k_hash`, `file_id` equals the catalog's
  `file_id` for the same file (`SELECT COUNT(*)` of mismatches prints 0), and `scheduler_state` holds no
  `cycle_index`. A mismatch or a leftover row fails it.
- **Webui.** `GET /` shows the "Clients" section and the pause state; `GET /controls` answers 404; `POST
  /controls/pause` redirects to `/` (webui tests).
- **The compose smoke test passes in CI**, on both architectures.
- **No release.** `git tag -l 'v*'` prints the same list before the lot's first block and after the stack
  merges. A new tag fails it.
- **Every block** is green at its tip (`uv run poe check`) and measured by the workflow's command, with its
  numbers in its report. Block 120 carries its justification in its pull request.

## 7. Risks and accepted limits

- **Accepted limit: a crawler restart restarts the alert clocks.** They live in memory, so a degraded state
  ongoing across a restart alerts at most one delay later.
- **Accepted limit: a download cancelled by hand in aMule's UI comes back within a round** (D13).
- **Accepted limit: at most 9 distinct Kad targets** before amuled's ring of 20 evicts a search still read
  (D6).
- **Accepted loss: an ed2k answer later than 750 ms after the sweep's last request is not read** (D3).
- **The catalog grows faster.** Searches become about 2.5 times more frequent on ed2k (one every 60 s,
  against 2 per 300 s) and 5 times on Kad (each target every 60 s, against every 300 s), and each search
  adds one `observations` row per result. Stage 1 made those rows small (0.21 GB for 11.65M observations),
  so the growth rate rises from a small base. The gain may be small too: servers' and Kad's indexes move
  with peers' connections and republications, not by the second.
- **Lugdunum's threshold is unpublished.** 60 s between ed2k searches is a human pace and far from the only
  open server's limits (https://github.com/danim7/ed2k-server, `src/server/bot_detector.rs`: a 24 h ban past
  600 UDP searches per minute), but the connected server's real threshold is unknown; a blacklist lasts at
  least an hour and is per server.
- **The 120 s budget assumes a sweep shorter than it.** An ed2k sweep lasts about 12 s plus 750 ms per
  server: past about 140 servers in the list, its last answers arrive after the reads stop and are lost. The
  node's server count is to check before the top of the stack.
- **Kad's firewalled window is assumed under 5 min.** No constant bounds it; the threshold equals aMule's
  own estimate (D15). To watch on the node: the time from `kad.state` turning `connected` to
  `firewalled_tcp` turning `false` on `GET /status`, across a few amuled restarts. Over 5 min, each restart
  or port-sync restart sends a Kad alert and its recovery.
- **Concurrent use of one amuleapi session.** Several tasks and the status loop share one session; two
  concurrent `401`s would each re-log in once, which stays under the 30-per-minute lockout.
- **The smoke test meets the 60 s rules.** Its second search on a keyword now waits 60 s, so a smoke
  assertion that needs two searches of one keyword takes longer.
