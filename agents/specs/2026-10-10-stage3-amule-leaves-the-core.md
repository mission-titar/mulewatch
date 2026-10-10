# Stage 3: aMule leaves the core, renamed p2pwatch

- Date: 2026-10-10 (discussion 2026-10-10)
- Status: APPROVED (operator sign-off 2026-10-11)
- Tier: Spec (the image names, the compose layout, the config keys, the metrics and the notifications
  change)
- Umbrella: `agents/specs/2026-10-08-multi-network-architecture.md`, stage 3 (D1, D4, D5, D6, D7, D15 for
  aMule), which this spec corrects (D2)
- Evidence:
  - aMule at the pinned commit `909d304d993ee07df6c6f6acf501a6d791d53666` (`AMULE_COMMIT` in
    `packages/crawler/Dockerfile`), fetched with `gh api repos/amule-org/amule/contents/<path>?ref=<commit>`;
  - the s6 manual page for `s6-svc` (https://skarnet.org/software/s6/s6-svc.html, fetched 2026-10-10);
  - Docker's `include` reference (https://docs.docker.com/reference/compose-file/include/, through
    Context7 2026-10-10);
  - gluetun's wiki, "Inter-containers networking"
    (https://github.com/qdm12/gluetun-wiki/blob/main/setup/inter-containers-networking.md, through
    Context7 2026-10-10).

## At a glance

```
Before (4.x): one container, mulewatch
  +------------------------------------------------------------------+
  | s6                                                               |
  |   crawler + webui ---127.0.0.1:4711---> amuleapi <--- amuled     |
  |   port-sync, inside the crawler: gluetun port -> PATCH           |
  |     /preferences, then s6-svc -r amuled                          |
  |   writes: data/ (crawler), amule/ and downloads/ (amuled)        |
  +------------------------------------------------------------------+

After (stage 3): two containers
  +-----------------------------+            +-------------------------------------+
  | p2pwatch (core, no s6)      |            | ed2k (p2pwatch-amule, s6)           |
  |   crawler + webui ----------+-ed2k:4711->|   amuled ---> amuleapi :4711        |
  |   runs as PUID:PGID         |            |   port-sync: gluetun port ->        |
  |   writes: data/             |            |     amule.conf, s6-svc -d / -u      |
  |   reads:  ed2k/downloads/   |            |   writes: ed2k/amule/,              |
  |           (ro, statvfs)     |            |           ed2k/downloads/           |
  +-----------------------------+            +-------------------------------------+
  vpn variant: ed2k shares ed2k-gluetun's network; ed2k-gluetun carries the alias ed2k.
  The core stays outside the tunnel.
```

The 15 blocks:

- **10**: rename everything to p2pwatch, merged alone; then the repository is renamed.
- **20**: the `packages/amule/` member, with the boot step moved into it.
- **30**: port-sync's parts (config file, gluetun read, variables).
- **35**: port-sync's loop and its s6 service, in today's single image.
- **40**: the core stops running port-sync; `port_sync:` refused.
- **50**: remove the core's port-sync loop.
- **60**: remove its adapters and ports.
- **65**: remove its events, metrics and listen-port calls.
- **70**: the `p2pwatch-amule` image.
- **80**: the core image without aMule; the smoke stack on two images.
- **90**: the include layout in `deploy/`.
- **100**: publish and scan two images; split the VEX.
- **110**: operator docs.
- **120**: contributor and troubleshooting docs.
- **130**: closing.

## 1. Context and goals

Since the 2026-09-16 single-container design, one image runs the crawler and amuled under s6, and amuled
starts amuleapi. The crawler reaches amuleapi on `127.0.0.1:4711`.

Port-sync runs inside the crawler: it reads gluetun's forwarded port, writes it with `PATCH /preferences`
and restarts amuled with `s6-svc -r`.

Stage 2 made the search, download and status ports generic. So the core no longer needs aMule beside it,
only an amuleapi to talk to.

Stage 5 adds clients with lifecycles of their own. Stage 3 moves aMule to its own image and container now,
with the one network the project already has. It renames the project before the first new image is
published (umbrella D15).

Goals:

- **The project is p2pwatch**: repository, Python package and dist, image, docs site.
- **aMule runs in its own image**, `p2pwatch-amule`, with its boot step and its port-sync. The core image
  holds the crawler and its webui only.
- **Port-sync belongs to the client**: it runs beside amuled, and the core knows nothing of it.
- **One compose layout for every network**: a base `compose.yml` and one `include:` per network, in a
  `direct` or a `vpn` variant.

## 2. Non-goals

- **No hardening of the core.** No `read_only`, no `cap_drop: ALL`, no `USER` in the image. The core gets
  `user:` for ownership (D10) and keeps `no-new-privileges`, and nothing else from umbrella D1's list.
  Why (operator, 2026-10-10):
  - `read_only` would sabotage SQLite's large operations: a migration or a compaction spills temporary data.
  - The tmpfs a read-only root needs already ran out once (the webui's `SQLITE_FULL` of 2026-07-06).
  - `cap_drop: ALL` and an image `USER` buy little for a process that already runs as the operator's uid.
- **No release.** Stage 3 ships no image beyond `main`, as stage 1's D16 decided until the first new
  network. The node keeps pulling `latest`, which stays on 4.1.0. `main` pushes publish `p2pwatch:main` and
  `p2pwatch-amule:main`, which the operator can test the migration with.
  Why: the layout change reaches operators once, with the first new network's major version.
- No new network, no change to search, matching, downloads or the webui beyond the client's address (D9).
- No source history or new event (stage 4).
- No per-client free-space check: the core keeps measuring one output directory (D10).

## 3. Decisions

### Rename

#### D1. The rename lands first, as a block merged alone

**In short: block 10 renames everything to p2pwatch and merges alone, then the operator renames the
repository.**

What block 10 renames, from `mulewatch` to `p2pwatch`:

- the Python package (`packages/crawler/src/p2pwatch/`) and the dist;
- the image, `ghcr.io/mission-titar/p2pwatch`, and the compose project name;
- the test variables, `MULEWATCH_TEST_API_*` to `P2PWATCH_TEST_API_*`;
- the docs site's `site_name`, `site_url`, `repo_url` and `repo_name`;
- the VEX document's `@id` and product, the apprise `app_url`, the webui's titles;
- the living docs, `AGENTS.md` and `README.md`;
- the `file_id` namespace URL, from `https://mission-titar.github.io/mulewatch/file` to
  `https://mission-titar.github.io/p2pwatch/file` (`file_key.py`, `test_file_key.py`);
- `grype-scan.yml`, which scans `p2pwatch:latest` from this block (D13).

The umbrella's markers (D2) and this spec ship in it.

Kept on purpose. These are the only files `git grep -il mulewatch -- ':/' ':/!agents'` may still print:

| File | Why | From |
|---|---|---|
| `docs/migration-1x.md`, `docs/migration-2x.md` | They describe migrating to versions that shipped as `mulewatch`. | block 10 |
| `docs/migration-4x.md` | It names what the operator migrates from. | block 110 |

Then, in order:

1. The operator merges block 10 alone (`gh pr merge --rebase`).
2. The operator renames the GitHub repository to `mission-titar/p2pwatch`.
3. The lead runs `git remote set-url origin git@github.com:mission-titar/p2pwatch.git`.
4. The rest of the stack is cut from the new `main`.
5. The lead records in the handoff the `main` sha block 10 was cut from. The lot's holistic review runs over
   `git diff <that sha>...origin/<top branch>`, so it reads the rename too, although block 10 merged before
   the stack exists.

Why:

- Everything stage 3 creates (`packages/amule/`, `deploy/ed2k/`, the docs) is born under its final name,
  instead of being renamed a second time (operator, 2026-10-10).
- Merging alone lets the operator review one mechanical diff by itself, and rename the repository before
  the rest is written, so the rest of the stack never rebases onto a block still in review (operator,
  2026-10-10).
- This order is a choice, not a constraint: GitHub keeps open pull requests across a repository rename.
- The namespace URL seeds every `file_id` (stage 1 D1). No catalog in production carries one yet, since
  stage 1's migration ships with the v5 release (operator, 2026-10-10). After that release it can never
  change.
- The workflow's holistic diff starts at the merge base with `main`, which block 10's early merge would
  move past it. The recorded sha keeps the review over the whole lot, as `AGENTS.md` requires.

#### D2. Corrections to the umbrella spec and to stage 1's spec

**In short: block 10 adds `(Corrected: ...)` markers to the umbrella and to stage 1's D1.**

Markers in the umbrella spec:

- **D1**: "the core can finally run as a non-root `USER`, `read_only`, with `cap_drop: ALL`" goes. The core
  runs as `PUID:PGID` through compose's `user:` for ownership only (Non-goals here).
- **D4**: `packages/amule/` holds `amule_config` (moved) and a new stdlib port-sync. `port_sync_loop.py`,
  `gluetun_port.py` and `s6_restart.py` are deleted, not moved (D4, D7).
- **D5**: services are prefixed by their network, a network's data lives under its directory, and
  `ed2k-gluetun` carries the alias `ed2k`. The `../data/ed2k` pitfall does not arise (D10).
  The shared fragment is `service.compose.yml`, not `service.yml`: today's fragment is `base.compose.yml`,
  and editors only apply Compose's schema to `*compose*.yml` (operator, 2026-10-10).
- **D7**: the aMule row writes `amule.conf` and restarts amuled through s6, with no amuleapi (D4).
  "The core keeps the alerting" goes: the three port-sync events are removed, and Low-ID is the status
  loop's 5 min alert on `connectable` (D7).
- **D15**: the count "153 `.py` files" is 158 on 2026-10-10 (`git grep -l mulewatch -- '*.py' | wc -l`).
- **Section 4, stage 3**: "core hardening" goes.

Marker in stage 1's spec (`agents/specs/2026-10-09-stage1-generic-identity.md`), D1: the namespace URL
becomes `https://mission-titar.github.io/p2pwatch/file`, UUID `d30da1ca-776a-5106-b046-daf77302c1af`
(D1 here).

Why:

- The umbrella is approved, and the workflow keeps a marker beside a claim corrected after approval.

### aMule image and port-sync

#### D3. `packages/amule/`: a stdlib-only workspace member

**In short: a new member, dist `p2pwatch-amule`, package `p2pwatch_amule`, holds the boot step and
port-sync, with no third-party dependency.**

- It is under the same gate as the others: 100 % branch coverage in its own pytest run, `mypy --strict`
  over `src` and `tests`, ruff.
- `p2pwatch_amule.config`: today's `mulewatch.amule_config`, moved unchanged but for its name. It is the
  boot step: the `amule` user, mount point ownership, `amule.conf` reconciled, amuleapi's admin password.
  It also validates port-sync's variables (D5).
- `p2pwatch_amule.port_sync`: the port-sync of D4.
- It depends on nothing outside the standard library.
- The core never imports it, and it never imports the core.

Why:

- Umbrella D4: a directory under `packages/` per image we build, and Python shipped in an image is under
  the gate.
- The boot step already uses only the standard library.
- Port-sync needs one HTTP GET (`urllib.request`), so no third-party package lands in the aMule image's
  SBOM for it. Today's `GluetunPortReader` uses httpx only because the crawler already had it.

#### D4. Port-sync edits `amule.conf` and restarts amuled through s6

**In short: beside amuled, port-sync compares gluetun's port to `amule.conf`, and on a difference stops
amuled, writes the file, and always starts amuled again.**

One loop, every `PORT_SYNC_POLL_SECONDS`:

1. Read gluetun's forwarded port: `GET <GLUETUN_CONTROL_URL>/v1/portforward` with a 10 s timeout,
   `{"port": N}`. Any failure, a non-JSON body, or a missing, non-integer, boolean or non-positive `port`
   reads as "no port" (today's defensive parse, `adapters/gluetun_port.py`). The loop does nothing this
   round.
2. Read amuled's port from `amule.conf`, `[eMule] Port`, 4662 when absent (aMule's `DEFAULT_TCP_PORT`).
3. Same port, or a restart less than `PORT_SYNC_RESTART_MIN_SECONDS` ago: nothing.
4. Otherwise:
   1. `s6-svc -wD -T 60000 -d /etc/services.d/amuled`;
   2. when it exits 0, write `[eMule] Port` and `[eMule] UDPPort` to the forwarded port;
   3. then **always** `s6-svc -u /etc/services.d/amuled`, whatever the down wait or the write did;
   4. record the restart's time.

Also:

- At its start, before the first round, port-sync sends `s6-svc -u /etc/services.d/amuled`.
- It logs each change and each failure. It emits no event and no metric.
- It writes both ports to one value, as `AmuleApiClient.set_listen_port` does today (`PATCH /preferences`
  with `tcp_port` and `udp_port`): one forwarded port is all a tunnel gives.
- It runs as `amule`. Its run script grants that group two rights on amuled (D6): control (`-d`, `-u`) and
  event subscription.

Why:

- Port-sync is the client's own business (operator, 2026-10-10). Beside amuled it reaches the config file
  and the supervisor.
- So it needs neither amuleapi, nor a password, nor the `401` discipline of an HTTP client.
- amuled reads `Port` only at start (`CamuleApp::ReinitializeNetwork`), so a restart is needed anyway.
- The write waits for amuled to be down: amuled holds its config in a `wxFileConfig` that it deletes on
  exit, which flushes what it holds in memory to the file. A write while amuled runs could be overwritten.
- The `-u` always follows the `-d` because a `-d` leaves the service wanted down. A port-sync that stopped
  between the two would leave amuled down for good. The `-u` at start also repairs that.
- The `-wD` wait uses event subscription, which s6 gives by default only to the supervisor's primary group,
  root. So `amule` needs it granted, beside control.
- The High-ID re-check after a restart goes. It only fed the three events D7 removes, and the core's status
  loop already alerts on an eD2k channel not connectable for 5 min (stage 2 D15).
- The 60 s down timeout is our choice, unmeasured (section 7).

Sources: `src/Preferences.cpp:77` (`DEFAULT_TCP_PORT`), `src/amule.cpp:1474` (`ReinitializeNetwork` binds
`Port`), `src/amule.cpp:346` (`delete wxConfigBase::Set`), at commit `909d304`; s6-svperms manual (`-E`),
https://skarnet.org/software/s6/s6-svperms.html.

#### D5. Port-sync is configured by environment variables, with defaults

**In short: four variables with today's values as defaults; `PORT_SYNC` follows gluetun's own
`VPN_PORT_FORWARDING`.**

| Variable | Default | Read by |
|---|---|---|
| `PORT_SYNC` | `off` | the boot step |
| `GLUETUN_CONTROL_URL` | `http://localhost:8000` | port-sync |
| `PORT_SYNC_POLL_SECONDS` | `60` | port-sync |
| `PORT_SYNC_RESTART_MIN_SECONDS` | `300` | port-sync |

How the boot step (`python -m p2pwatch_amule.config`, as root, before s6) handles them:

1. It reads `PORT_SYNC` with one function, case-insensitive, taking gluetun's own boolean values:
   - `enabled`, `yes`, `on`, `true` turn it on;
   - `disabled`, `no`, `off`, `false` or an empty value turn it off;
   - any other value exits naming `PORT_SYNC`.
2. Off: it creates `/etc/services.d/port-sync/down`, so s6 never starts the service.
3. On: it removes that file and validates the three other variables. The URL needs an `http` or `https`
   scheme and a host; the numbers must be positive.
4. A bad value exits naming the variable, before s6 starts anything, as a missing `PUID` does today.

In compose:

- `deploy/ed2k/vpn.compose.yml` sets `PORT_SYNC: ${VPN_PORT_FORWARDING:-off}`. Turning on the tunnel's
  port forwarding in `.env` turns on port-sync, and nothing else does.
- `crawler.yml` refuses a `port_sync:` section (D7).

Why:

- Environment variables with correct defaults (operator, 2026-10-10).
- gluetun's control server can be moved with `HTTP_CONTROL_SERVER_ADDRESS`, so its URL is a setting.
- The two intervals keep the values they have had since port-sync exists.
- Tying `PORT_SYNC` to `VPN_PORT_FORWARDING` removes today's second switch (`port_sync.enabled` in
  `crawler.yml`), which an operator could forget.
- A vpn variant without forwarding would only poll a gluetun that reports no port.
- `PORT_SYNC` receives `VPN_PORT_FORWARDING` verbatim, so it accepts the same values. An operator who wrote
  `yes` gets a forwarded port; a port-sync that only knew `on` would stay down silently.
- Validating at boot makes a bad value stop the container visibly, instead of a service s6 restarts every
  second.
- The `down` file is s6's documented way to keep a service down, with no process started and no race.
- The boot step that writes it is testable Python, not shell.

Sources: gluetun reads `VPN_PORT_FORWARDING` with `gosettings`' `parseBool` (`internal/parse/parse.go`,
lowercased, the eight values above); s6-svstat manual (`normallyup`),
https://skarnet.org/software/s6/s6-svstat.html.

#### D6. The `p2pwatch-amule` image

**In short: today's aMule build moves to `packages/amule/Dockerfile`, and its image runs the boot step then
s6 with two services, amuled and port-sync.**

- **Build stage**: today's aMule build stage, moved from `packages/crawler/Dockerfile` with
  `ARG AMULE_VERSION`, `ARG AMULE_COMMIT` and `amule.cdx.json`.
- **uv and Python stages**: `uv sync --package p2pwatch-amule`.
- **Runtime**: Debian trixie-slim with `python3`, `s6` and aMule's libraries.
- **Entrypoint**: `python -m p2pwatch_amule.config`, then `exec s6-svscan /etc/services.d`.
- **s6 service `amuled`**: today's `run`, unchanged (`setpriv` to `amule`).
- **s6 service `port-sync`**:
  1. as root, waits for amuled's `supervise/control`;
  2. runs `s6-svperms -G amule -E amule` on it (control and event subscription, D4);
  3. then `setpriv` to `amule` and `python -m p2pwatch_amule.port_sync`;
  4. kept down by a `down` file when disabled (D5).

Why:

- Umbrella D1 and D4: aMule moves to its own image, built from a directory under `packages/`.

### Core

#### D7. The core loses port-sync

**In short: every port-sync module, event, metric and config key leaves `packages/crawler/`, and
`port_sync:` is refused.**

Removed from `packages/crawler/`, with every test of them:

- `application/port_sync_loop.py`, `adapters/gluetun_port.py`, `adapters/s6_restart.py`,
  `ports/mule_restarter.py`, `ports/port_forwarding.py`;
- `PortSyncConfig` and the composition's wiring;
- `AmuleApiClient.get_listen_port` and `set_listen_port` (port-sync was their only caller);
- the events `PortSyncTriggered`, `PortMismatchUnresolved` and `HighIdRecovered`, their policy arms, and
  their three metrics `emule_port_sync_triggered`, `emule_port_mismatch` and `emule_high_id_recovered`.

Kept: `EdgeState`, which the download loop's `disk_low` uses.

Added: `port_sync` joins the keys `parse_crawler_config` refuses by name (`_REMOVED_KEYS`).

Why:

- The events and their metrics are removed rather than served from the aMule container (operator,
  2026-10-10).
- That container has neither Prometheus nor apprise. Adding both for three signals would bring a dependency
  and a scrape target for nothing.
- The core's status loop already covers them: the `p2pwatch_channel_*` gauges and the 5 min alert on
  `connectable`.
- Refusing `port_sync:` keeps an operator from believing it still applies (stage 2 D17's rule).

#### D8. The `p2pwatch` core image

**In short: the core image holds Python and the venv only, and Docker, not s6, restarts it.**

- `packages/crawler/Dockerfile`: the uv and Python stages, and a Debian trixie-slim runtime with `python3`
  and the venv.
- `CMD ["python", "-m", "p2pwatch", "--config", ..., "--targets", ..., "--matcher", ...]`.
- No s6, no entrypoint, no aMule, no `services.d/`.
- `__main__.main()`'s exit codes stop being a contract with s6's `finish`. Under `restart: unless-stopped`,
  Docker restarts the core after any exit.
- The dashboard's restart button still works: the core exits 0, Docker starts it again.
- A config that fails validation now makes the container restart in a loop. `docker compose ps` shows it
  as `Restarting`, with the `ConfigError` in its logs.

Why:

- Umbrella D1: the core image runs the crawler and its webui, and nothing else.
- The restart loop is accepted (operator, 2026-10-10). Docker has no policy that restarts on 0 and not on
  1, and the loop shows the error as plainly as a stopped container.
- The core needs no entrypoint once nothing has to run as root before it. The ownership of `data/` is the
  compose file's `user:` (D10).

#### D9. The core reaches amuleapi at `ed2k:4711`

**In short: `AMULE_API_HOST` becomes the constant `"ed2k"`, the same name in both compose variants.**

- `AMULE_API_HOST` in `adapters/config/crawler_config.py` becomes `"ed2k"`, still a constant.
- In the `direct` variant, `ed2k` is aMule's service.
- In the `vpn` variant aMule has no network of its own, and `ed2k-gluetun` carries the network alias `ed2k`.
- The integration suites keep their own host variables.

Why:

- One address in both variants: switching variant edits one `include:` line and nothing in `crawler.yml`
  (operator, 2026-10-10).
- A container on gluetun's network reaches a gluetun-connected container at `gluetun:<port>` without
  publishing it. So an alias on the gluetun service reaches amuleapi.
- A config key would be an operator surface that stage 5 replaces with one endpoint per client anyway.

Sources: gluetun wiki, inter-containers networking,
https://github.com/qdm12/gluetun-wiki/blob/main/setup/inter-containers-networking.md.

### Compose layout

#### D10. A base file and one include per network

**In short: `deploy/compose.yml` holds the core and one `include:` line; each network lives in its own
directory with its files and its data.**

```
deploy/
  compose.yml                 project p2pwatch: the core service, include: [ed2k/direct.compose.yml]
  .env.example
  crawler.yml  targets.yml  matcher.yml
  data/                       the core's only writable mount
  ed2k/
    service.compose.yml       service ed2k: the image, PUID/PGID, both passwords, ./amule, ./downloads
    direct.compose.yml        extends service.compose.yml, publishes 4711/tcp, 4662/tcp, 4672/udp
    vpn.compose.yml           ed2k-gluetun (ports 4711, alias ed2k) + ed2k extends service.compose.yml,
                              network_mode: service:ed2k-gluetun, PORT_SYNC
    amule/                    aMule's config dir
    downloads/                incoming/ and temp/
```

The core service, `p2pwatch`:

- `user: "${PUID:?}:${PGID:?}"`, `restart: unless-stopped`, `no-new-privileges`;
- today's `pids_limit` and `mem_limit`;
- `AMULE_API_PASSWORD` in its environment, port 8080;
- the three config files read-only, `./data:/data`, and `./ed2k/downloads:/downloads:ro`;
- no `depends_on` on `ed2k`, no healthcheck;
- in the vpn variant, not behind the tunnel: only `ed2k` shares `ed2k-gluetun`'s network. The core's own
  traffic, apprise webhooks included, leaves from the host's address.

The `ed2k` service, in `ed2k/service.compose.yml`:

- today's `pids_limit: 512` and `mem_limit: 2g`, `restart: unless-stopped` and `no-new-privileges`. Each
  container keeps at least the bound both processes shared.
- the healthcheck on `s6-svstat`, moved from `base.compose.yml`.

Rules:

- **Names are prefixed by the network** (`ed2k`, `ed2k-gluetun`): two included files declaring one name
  conflict.
- **A network's data lives under its directory**; the core writes only `data/`.
- Relative paths in an included file resolve against its own directory, so `./amule` in
  `ed2k/service.compose.yml` is `deploy/ed2k/amule`.
- `.gitkeep` files keep `data/`, `ed2k/amule/`, `ed2k/downloads/incoming/` and `ed2k/downloads/temp/` in
  the ZIP the operator downloads.
- `base.compose.yml` and `gluetun.compose.yml` are removed.

Why:

- Umbrella D5, with the data directories settled (operator, 2026-10-10). Per-network directories avoid the
  `../` paths D5 warned about, and every stage 5 network gets its own place.
- `user:` replaces the `chown -R` today's crawler `run` does as root. `data/`'s files stay the operator's
  without an entrypoint (operator, 2026-10-10).
- The existing node's `data/` is already owned by `PUID:PGID`.
- The read-only `/downloads` mount keeps `download.output_dir` measurable with `statvfs` (the disk floor),
  which today reads the directory amuled writes to. Read-only states that the core never writes there.
- No `depends_on`: commenting out a network's include must not break the stack. The core already treats an
  unreachable client as degraded (umbrella D6).
- No healthcheck on the core: it reported amuled's state, which the core's status loop now reports.
- The core leaves the tunnel because only a client needs one (umbrella D5: one gluetun per client). The
  core talks to clients and to notification services, never to a P2P network.
- Each container keeps today's limits rather than a split of them. No measurement says how the 2 GiB
  divide between the crawler and amuled, and a tighter bound on either is a new failure with no reason to
  risk it.
- `4672/udp` replaces today's `4662/udp`. aMule's UDP port, Kad's, is `UDPPort`, 4672 by default. In the
  direct stack nothing sets it to 4662, so Kad's UDP was never published.

Sources: Docker's `include` reference, https://docs.docker.com/reference/compose-file/include/;
`src/Preferences.cpp:78` (`DEFAULT_UDP_PORT`) at commit `909d304`.

#### D11. The smoke stack and the integration suites

**In short: the smoke stack builds and runs both images from the tree; the integration suites run
against the aMule image alone.**

- `tests/smoke/compose.yaml` holds two services built from the tree:
  - `ed2k` (`packages/amule/Dockerfile`);
  - `p2pwatch` (`packages/crawler/Dockerfile`, `user: "${PUID}:${PGID}"`, `/downloads` read-only);
  - both on the throwaway `SMOKE_STATE` bind mounts.
- It stays a standalone file, not an include of `deploy/`: it builds the images and points at a throwaway
  state directory.
- `test_compose_smoke.py` asserts:
  - both containers `running`, `ed2k` `healthy`;
  - `port-sync` kept down in `ed2k` (`PORT_SYNC` unset);
  - amuleapi's `/health`, the webui, and the core's first status reading of `amuled` on the dashboard;
  - `data/catalog.db` belongs to `PUID`. The test refuses to run as root: a `PUID` of 0 would make that
    check pass on a root-owned file.
  - block 35's s6 check, run as `amule` (section 4).
- Its `docker compose config` test:
  - copies `deploy/` to a temporary directory and writes a `.env` there;
  - renders `compose.yml` with each variant, with no `PUID` in the process environment, so the render can
    only take it from the parent's `.env` (section 4);
  - never writes `deploy/.env`, which is gitignored and holds a developer's secrets.
- The `api_integration`, `download_integration` and `orchestration_integration` suites run against a
  `p2pwatch-amule` container alone, started with `PUID`, `PGID` and both passwords, no config mount.

Why:

- The smoke test proves what one host can: both images, their wiring by name, and the ownership `user:`
  promises.
- The vpn variant needs a VPN account, so `docker compose config` is what CI can check of it (section 7).
- The integration suites drive amuled and amuleapi only. The aMule image now holds them without a crawler
  whose `finish` stopped the container when its config was missing.

### CI, docs and release

#### D12. Continuous integration builds, tests and publishes two images

**In short: every CI job that handled one image handles two, each with its own gates, signature,
attestations and VEX.**

- **`validate.yml`**: the `arch` matrix stays. Each job:
  - builds both images (two `docker-image` steps, cache scopes `crawler-<arch>` and `amule-<arch>`);
  - checks the aMule version and the aMule SBOM entry on `p2pwatch-amule`;
  - runs the smoke stack on both, and the integration suites on `p2pwatch-amule`;
  - uploads one digest per image (`digest-<package>-<arch>`).
- **`release.yml`**: `publish-manifest` becomes a matrix over the two images.
  - Each leg has its image name, its digests and its VEX file. The aMule SBOM gate runs on the aMule leg
    only.
  - Each image gets the recursive cosign signature and the three attestations (CycloneDX, Syft-JSON,
    OpenVEX).
  - `github-release` waits for both legs.
- **VEX split**:
  - `security/crawler.vex.openvex.json` keeps the two Python claims (CVE-2025-15366, CVE-2025-15367) for
    `pkg:oci/p2pwatch`;
  - `security/amule.vex.openvex.json` holds the two aMule claims (CVE-2006-2691, CVE-2006-2692) and the
    two Python claims, for `pkg:oci/p2pwatch-amule`, whose image holds Python too;
  - `vex_guards.repo.vex_files()` maps `crawler` and `amule`;
  - `source_dirs()` already scans every non-tooling `packages/*/src`, `packages/amule/src` included.
- **`amule-bump`**: the default `--dockerfile`, the workflow's `add-paths` and `validate.yml`'s pin read
  point at `packages/amule/Dockerfile`.

Why:

- Supply-chain parity for the new image (operator, 2026-10-10).
- The arch matrix stays because the smoke stack needs both images on one runner.
- One matrix leg per image in `publish-manifest` keeps each image's gates, signature and attestations
  independent. An image whose VEX fails publishes nothing.
- `check_claim_coverage` reads every VEX file, so a claim present in both files maps to its one guard.

#### D13. The daily Grype scan follows the rename and fails until the first release

**In short: `grype-scan.yml` scans the p2pwatch images, and fails every morning until one is released.**

- It scans `ghcr.io/mission-titar/p2pwatch:latest` from block 10 on, and both images from block 100, like
  `release.yml`.
- Its certificate identity stays built from `github.repository`, which spells the new name once the
  repository is renamed.
- Until the first p2pwatch release no `p2pwatch:latest` exists, so the scheduled scan fails every morning.

Why:

- The operator accepts a failing scan until the release (2026-10-10).
- Pinning it to `mulewatch:latest` would be a temporary mechanism to remove at the release, guarding a
  frozen 4.x image.

#### D14. The node's migration is documented now

**In short: `docs/migration-4x.md` describes moving a 4.x node to the new layout, and ships with the first
new network's release.**

The page is named after the version migrated from, like `migration-1x.md` (1.x to 2.0) and
`migration-2x.md` (2.x to 3.0). Its steps:

1. Stop.
2. Move `amule/` and `downloads/` under `ed2k/` (one `mv`, the same filesystem).
3. Drop `port_sync:` from `crawler.yml`, with the five stage 2 keys.
4. Replace the compose files.
5. Set `VPN_PORT_FORWARDING` for port-sync.
6. Start.

A `direct` node that forwards ports on its router also forwards `4672/udp` there instead of `4662/udp`
(D10).

The release that publishes it is the first new network's (stage 1 D16).

Why:

- The layout changes in this lot, so its migration is written by the people who change it.
- The node's own data proves it before the release (section 7).

#### D15. Docs

**In short: the living docs follow each block; `install.md` warns that `main`'s `deploy/` cannot start a
node before the release.**

- The living docs follow each block: `docs/install.md`, `operate.md`, `high-id.md`, `vpn.md`,
  `settings.md`, `glossary.md`, `limits.md`, `troubleshooting.md`, `troubleshooting-start.md`,
  `verify-image.md`, `contributing/architecture.md`, `contributing/testing.md`,
  `contributing/amule-bump.md`, `SECURITY.md`, `AGENTS.md`, `README.md`.
- `install.md` says that `data/` ships in the ZIP and must exist, owned by the operator, before the first
  `up` under Linux. A missing directory is created by Docker as root, and the core, which is not root,
  cannot write it.
- From block 10, `deploy/` on `main` names `p2pwatch:latest` (and from block 90 `p2pwatch-amule:latest`).
  These tags exist only once the first p2pwatch release is pushed.
- So a new operator who downloads `main`'s ZIP cannot start a node until then. `install.md` says so in a
  warning at its top.
- It sends them to the `v4.1.0` release's ZIP (https://github.com/mission-titar/p2pwatch/releases/tag/v4.1.0),
  whose `docs/` describe it. To confirm with the operator (section 7).

Why:

- `docs/` describes `main`, as since stage 1.
- Pointing `deploy/` at `:main` instead would run unreleased code on new nodes.
- `latest` is what the release will need anyway.

## 4. The claim that decides

**Port-sync never leaves amuled down, and never loses its write.**

It is the one new behaviour that can take the node off the network, and the vpn variant cannot run in CI.

Proved by unit tests on fakes of s6 and of the file, each watched failing with its rule removed:

- after any `-d`, a `-u` is sent:
  - with the down wait timing out (no write, `-u` sent);
  - with the write raising (`-u` sent, the error logged);
  - on success;
- the file is written only after the down wait returned 0;
- port-sync's start sends `-u` before its first round;
- the write keeps every other key and section of `amule.conf`, and writes `Port` and `UDPPort`;
- an equal port, or a restart within the window, calls no s6 command.

Then the s6 commands themselves, on a real container (block 35):

1. Run as `amule` like port-sync (`docker compose exec -u amule`), after the `port-sync` run script's
   `s6-svperms`.
2. Read `s6-svstat -o pid` on amuled.
3. `s6-svc -wD -T 60000 -d` exits 0.
4. `s6-svstat -o up` prints `false`.
5. `s6-svc -wu -T 60000 -u` exits 0.
6. `up` prints `true`, and `pid` differs from the first read.

It fails on a non-zero exit of either `s6-svc` (the rights of D6 missing), on `up` still `true` after the
`-d`, or on an unchanged pid. Run as root, the check could not see a missing right, which is why it runs as
`amule`.

A second claim the layout rests on: **an included file reads the parent's `.env`**.

- Docker's reference says an included file is interpolated from its own directory's `.env`, "overridden by
  the local project's environment".
- Proved by the smoke test's `docker compose config` (block 90, D11): a `.env` beside a temporary copy of
  `compose.yml`, no `ed2k/.env`, no `PUID` in the process environment. `ed2k`'s `PUID` must render from that
  `.env`.
- The reviewer rendered it with Compose 5.5.1 on 2026-10-10, on a scratch copy of the layout: `PUID`
  rendered, `./amule` resolved under `ed2k/`, the alias rendered.

## 5. Blocks

Paths under `packages/crawler/` unless noted. Sizes are estimates from `wc -l` of the moved and deleted
files and `git grep -c` of the touched sites. Each block measures itself after committing.

| # | Branch | Goal | Est. lines / files |
|---|---|---|---|
| 10 | `refactor/rename-p2pwatch` | Rename to p2pwatch, merged alone | **820 / 265** |
| 20 | `refactor/amule-package` | The `packages/amule/` member | 120 / 15 |
| 30 | `feat/amule-port-sync-parts` | Port-sync's parts | 330 / 7 |
| 35 | `feat/amule-port-sync-loop` | Port-sync's loop and service | 440 / 9 |
| 40 | `refactor/core-port-sync-off` | The core stops running port-sync | 220 / 8 |
| 50 | `chore/remove-port-sync-loop` | Remove the core's port-sync loop | **770** / 3 |
| 60 | `chore/remove-port-sync-adapters` | Remove its adapters and ports | 300 / 7 |
| 65 | `chore/remove-port-sync-events` | Remove its events and listen-port calls | 300 / 10 |
| 70 | `feat/amule-image` | The `p2pwatch-amule` image | 300 / 8 |
| 80 | `refactor/core-image-split` | The core image without aMule | 460 / 16 |
| 90 | `refactor/deploy-include-layout` | The include layout | 320 / 14 |
| 100 | `ci/publish-two-images` | Publish and scan two images | 320 / 7 |
| 110 | `docs/stage3-operator-docs` | Operator docs | 450 / 11 |
| 120 | `docs/stage3-contributor-docs` | Contributor and troubleshooting docs | 420 / 7 |
| 130 | `docs/stage3-closing` | Closing | 80 / 4 |

### Contents per block

- **10**:
  - this spec and the markers of D2;
  - everything D1 lists, `grype-scan.yml` included (D13);
  - merged alone, then the repository rename;
  - the estimate: 123 files renamed under `src/`, 142 edited (`git ls-files` and `git grep -il` on
    2026-10-10).
- **20**:
  - `p2pwatch_amule.config` moved from `p2pwatch.amule_config` with its tests (`git mv`);
  - `packages/amule/pyproject.toml`;
  - root `pyproject.toml`: sources, dev group, ruff `src`, mypy `files`, the `test` task;
  - the crawler depends on `p2pwatch-amule` until block 80, so the single image's venv holds it;
  - `docker/entrypoint.sh` runs `python -m p2pwatch_amule.config`.
- **30**:
  - `config/conf.py` reads and writes `Port` and `UDPPort`;
  - the gluetun read over `urllib.request`, with the defensive parse;
  - the four variables, their defaults and the boot step's validation (D5);
  - tests.
- **35**:
  - the loop and its s6 calls (D4), `python -m p2pwatch_amule.port_sync`;
  - `docker/services.d/port-sync/run` in today's single image, with `s6-svperms -G amule -E amule`;
  - the crawler's `run` keeps its own `-G amule` until block 80, for the core's port-sync;
  - the boot step's `down` file (D5);
  - the claim tests of section 4, and its s6 check in `test_compose_smoke.py`.
- **40**:
  - composition unwired, `PortSyncConfig` removed and `port_sync` refused (D7);
  - `deploy/crawler.yml`;
  - `deploy/gluetun.compose.yml` sets `PORT_SYNC: ${VPN_PORT_FORWARDING:-off}`;
  - `test_app.py`, `test_crawler_config.py`.
- **50**: `application/port_sync_loop.py` with `test_port_sync_loop.py` and `test_run_port_sync_cycle.py`.
- **60**: `adapters/gluetun_port.py`, `adapters/s6_restart.py`, `ports/mule_restarter.py`,
  `ports/port_forwarding.py` and their tests.
- **65**:
  - the three events, their policy arms and metrics (D7);
  - `get_listen_port` / `set_listen_port` (inline in `adapters/mule_api/client.py`) and their `FakeAmuleApi`
    routes;
  - `tests/integration/test_amuled_preferences.py`, their only other caller;
  - tests.
- **70**:
  - `packages/amule/Dockerfile`: the aMule stage copied, Python stages, runtime with s6;
  - `amule.cdx.json`, `entrypoint.sh`, `services.d/amuled` and `services.d/port-sync` move from
    `packages/crawler/docker/` to `packages/amule/docker/` (`git mv`);
  - the crawler's Dockerfile copies them from there; its `services.d/mulewatch` stays until block 80;
  - `validate.yml` builds the image per arch, checks the aMule version and SBOM entry on it, and runs the
    integration suites against it.
- **80**:
  - the crawler's Dockerfile loses the aMule stage, s6, the entrypoint and `services.d/` (D8);
  - the crawler's dependency on `p2pwatch-amule` and the crawler's `services.d/mulewatch` go;
  - `AMULE_API_HOST = "ed2k"` (D9); `__main__` docstring;
  - the smoke stack's two services and `test_compose_smoke.py` (D11);
  - `validate.yml`'s crawler-image aMule checks go;
  - `amule-bump`'s three paths (D12).
- **90**:
  - `deploy/compose.yml`, `deploy/ed2k/{service.compose.yml,direct.compose.yml,vpn.compose.yml}`;
  - the `.gitkeep` moves, `.gitignore`, `.env.example`;
  - `base.compose.yml` and `gluetun.compose.yml` removed;
  - the smoke test's `config` render of both variants and of the `.env` claim (D10, section 4).
- **100**:
  - `release.yml`'s and `grype-scan.yml`'s per-image matrix (D13);
  - `security/amule.vex.openvex.json` and the crawler VEX's claims (D12);
  - `vex_guards.repo` and its tests.
- **110**: `docs/migration-4x.md` (D14), `install.md`, `operate.md`, `high-id.md`, `vpn.md`, `settings.md`,
  `glossary.md`, `limits.md`, `verify-image.md`, the nav in `zensical.toml`.
- **120**: `troubleshooting.md`, `troubleshooting-start.md`, `contributing/architecture.md`,
  `contributing/testing.md`, `contributing/amule-bump.md`, `SECURITY.md`, `AGENTS.md`.
- **130**: holistic findings, `BACKLOG.md` reconciled, handoff.

### Over-bound blocks

- **Block 10 passes both bounds.**
  - A Python package cannot be renamed in parts: every import, the dist name and the coverage source change
    with the directory, or the gate fails.
  - The 142 other files cannot follow in a later block either. The repository is renamed as soon as block
    10 merges, and from then the image name, the site and repository URLs, the VEX `@id` and the scanned
    image must already match it.
  - The one grep that reviews the rename only proves something over the whole of it.
  - It is reviewed by two commands rather than hunk by hunk: `git diff -M --stat origin/main...HEAD`
    (renames shown as such), and `git grep -il mulewatch -- ':/' ':/!agents'`, which must print only D1's
    files marked "block 10".
- **Block 50 passes the line bound.**
  - A module cannot leave without its tests (stage 2 block 120's reason).
  - `test_run_port_sync_cycle.py` alone is 498 lines.
  - What could leave on its own did, in blocks 60 and 65.

### Order

- 10 first and alone (D1).
- 20 before 30 before 35: the member exists before its code, and the parts before the loop.
- 35 before 40: the aMule side runs port-sync before the core stops, so no block between leaves the vpn
  stack without one.
- 40 before 50, 60 and 65: the modules lose their caller before they leave.
- 70 before 80: the aMule image builds and passes the integration suites before the core image drops aMule.
- 80 before 90: the smoke stack proves the two images before `deploy/` depends on them.
- 100 after 80: there are two images to publish.

### Surfaces whose consumer arrives in a later block

- `p2pwatch_amule.port_sync`'s parts (block 30), consumed by the loop in block 35.
- `PORT_SYNC` in the single image (block 35), set by `gluetun.compose.yml` in block 40.

### Transient states inside the stack

- Blocks 20 to 80: the crawler depends on `p2pwatch-amule` without importing it, so the single image holds
  the boot step and port-sync.
- Blocks 35 to 40: both port-syncs exist, and only the core's can be on (`PORT_SYNC` is set by no compose
  file). It keeps its right to restart amuled because the crawler's `run` still grants it.
- Blocks 70 to 80: the aMule build stage is in both Dockerfiles, and `amule-bump` still edits the
  crawler's.
- The stack merges as a whole after block 10, so `main` carries none of them.

### Adjacent backlog items

- **"Multi network", stage 3**: deleted at Wrap.
- **Stage 4**: stays open; nothing here touches sources or events.
- **Stage 5**: stays open. One thing this lot leaves to it, to add to its line with the operator's
  agreement: a free-space check per client output directory (D10).
- **"Paginate the file detail timeline"**: stays open; the file pages are not touched.

## 6. Acceptance

Each criterion names the output that would prove it wrong.

- **The rename is complete.** After block 10, `git grep -il mulewatch -- ':/' ':/!agents'` prints exactly
  D1's files marked "block 10". At the top of the stack, those plus `docs/migration-4x.md`. Any other file
  fails it.
- **The namespace is p2pwatch's.** `test_file_key.py` pins it to `d30da1ca-776a-5106-b046-daf77302c1af`.
  The old string, or any other, fails it.
- **Port-sync never leaves amuled down**: section 4's tests and real-container check, each watched failing.
- **Port-sync's defaults and validation** (tests, each watched failing):
  - with no variable set, port-sync polls `http://localhost:8000` every 60 s and restarts at most once per
    300 s;
  - `PORT_SYNC=on` with `PORT_SYNC_POLL_SECONDS=0` makes the boot step exit with a message naming
    `PORT_SYNC_POLL_SECONDS`;
  - `PORT_SYNC=yes` and `PORT_SYNC=On` remove the `down` file; `PORT_SYNC=off` and an unset one create it;
  - `PORT_SYNC=maybe` exits naming `PORT_SYNC`.
- **A disabled port-sync stays down.** In the smoke stack (no `PORT_SYNC`), 30 s after `ed2k` is healthy:
  - `s6-svstat -o up,wantedup,normallyup,updownfor /etc/services.d/port-sync` in `ed2k` prints
    `false false false` and an `updownfor` of 25 or more;
  - amuled's `up` prints `true`;
  - a run script respawned every second reads `wantedup` `true` and an `updownfor` under 2, and fails it.
- **The core knows nothing of port-sync.**
  - `parse_crawler_config` on a config with `port_sync:` raises a `ConfigError` naming `port_sync`.
  - `grep -rniE 'port_sync|gluetun|s6-svc|listen_port' packages/crawler/src` prints only the
    `"port_sync",` line of `_REMOVED_KEYS` in `adapters/config/crawler_config.py`. Any other line fails it.
  - `grep -c '"emule_' packages/crawler/src/p2pwatch/domain/observability/policy.py` prints `0`.
- **The core image holds no aMule.** `docker run --rm --entrypoint sh <core image> -c 'command -v amuled
  amuleapi s6-svscan'` prints nothing. Any path fails it.
- **The two containers work together.** In the smoke stack, the dashboard (`GET /`) shows a current reading
  for the client `amuled` within 120 s of `ed2k` turning healthy. "No reading yet." at 120 s fails it, and
  so does a core still pointing at `127.0.0.1`.
- **The core writes as the operator.** In the smoke stack, `stat -c %u` of `data/catalog.db` prints the
  test's `PUID`, and the test refuses to run with a `PUID` of 0. `0` fails it.
- **The layout renders.** `docker compose -f deploy/compose.yml config` renders:
  - the services `p2pwatch` and `ed2k`;
  - `ed2k`'s bind sources under `deploy/ed2k/`;
  - the core's `/downloads` from `deploy/ed2k/downloads`, read-only;
  - `ed2k`'s `PUID` from `deploy/.env` (section 4's second claim);
  - with the include switched to `ed2k/vpn.compose.yml`: `ed2k-gluetun` with the alias `ed2k`, and `ed2k`
    with `network_mode: service:ed2k-gluetun` and `PORT_SYNC`;
  - a missing alias, a source outside `deploy/ed2k/` or an uninterpolated `PUID` fails it.
- **Supply chain.**
  - After the stack merges, `cosign verify` and `cosign verify-attestation --type openvex` pass for both
    `ghcr.io/mission-titar/p2pwatch:main` and `ghcr.io/mission-titar/p2pwatch-amule:main`, with the new
    repository's identity.
  - `docker buildx imagetools inspect` lists `linux/amd64` and `linux/arm64` for each.
  - A digest missing, an arch missing or a failed verification fails it.
  - Before merging, `uv run poe vex-claim-coverage` passes with the two VEX files. It fails with
    `CVE-2006-2691: guard has no claim` when `amule` is dropped from `vex_files()` (watched failing, block
    100).
- **The compose smoke test passes in CI**, on both architectures.
- **No release.** `git tag -l 'v*'` prints the same list before block 10 and after the stack merges.
- **Every block** is green at its tip (`uv run poe check`) and measured by the workflow's command, with its
  numbers in its report. Blocks 10 and 50 carry their justification in their pull requests.

## 7. Risks and accepted limits

- **Accepted limit: a config that fails validation loops** (D8), visible as `Restarting`.
- **Accepted limit: the core measures one output directory**, `ed2k/downloads` (D10). Stage 5 decides
  per-client free space.
- **Accepted limit: until the first p2pwatch release, the docs describe an unreleased p2pwatch**, while
  operators run `mulewatch:latest`, as since stage 1.
- **Accepted limit: the daily Grype scan fails until the first p2pwatch release** (D13).
- **To confirm with the operator: `main`'s `deploy/` cannot start a node before the release** (D15).
  - Its `latest` tags do not exist yet; `install.md` sends new operators to the `v4.1.0` ZIP.
  - The alternative, `:main` in `deploy/`, would run unreleased code on new nodes.
- **amuled's stop time is unmeasured.**
  - The 60 s down timeout (D4) assumes amuled saves its known files and exits within it.
  - A slower exit skips the write, brings amuled back up and retries after the restart window. It costs
    time, not the daemon.
  - To measure on the node, after the switch: `time s6-svc -wD -d /etc/services.d/amuled` inside the
    container.
- **A port-sync restart is seen by the core as an unreachable client.** It alerts after 2 min (stage 2
  D15); a restart shorter than that alerts nothing. Unmeasured with the node's queue.
- **The vpn variant cannot run in CI.** Only its `docker compose config` render is tested. Before the
  release, on the node:
  - the core's dashboard reads `amuled` through `ed2k-gluetun`'s alias;
  - a change of forwarded port is followed by High-ID within the restart window.
- **The GitHub Pages site moves.** Whether `mission-titar.github.io/mulewatch/` redirects after the rename
  is to check once renamed (umbrella D15). `docs/migration-4x.md` gives the new address either way.
- **The new GHCR packages are created by the first `main` push after block 10.**
  - Their visibility follows the organization's default for new packages.
  - A private `p2pwatch` would make the node's pull fail.
  - The operator checks it before the release:
    `gh api /orgs/mission-titar/packages/container/p2pwatch -q .visibility`.
