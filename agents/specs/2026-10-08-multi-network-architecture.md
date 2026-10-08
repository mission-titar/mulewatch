# Multi-network architecture: a generic core and one container per client

- Date: 2026-10-08
- Status: DRAFT (awaiting operator review)
- Scope: turn mulewatch into a generic watch core that drives several P2P networks, each client in
  its own container; this is an umbrella spec, every stage below gets its own detailed spec
- Release: staged; stage 3 (aMule leaves the core image) is breaking and ships as `v5.0.0`
- Related: `agents/specs/2026-09-16-single-container-embedded-amule.md` (partly reversed here),
  `agents/specs/2026-09-13-scope-reduction-catalog-notify-download.md`,
  `application/port_sync_loop.py`, `adapters/persistence_sqlite/migrations/catalog/0001_initial.sql`,
  `docs/legal.md`, the p2pwatch proof of concept (separate repository, reviewed 2026-10-08)

## 1. Context and goals

A proof of concept, p2pwatch, watched eD2k/Kad (by importing mulewatch's catalog), Soulseek,
Direct Connect, Gnutella/G2 and the BitTorrent DHT from one stdlib-only Python process that
compiled or downloaded every client into `vendor/` and ran them as subprocesses. Reviewed as a
whole, the only behaviour it adds over mulewatch is the extra networks, a network-agnostic file
identity, and two event kinds (`new-location`, `reappeared`). Catalog, matching, notifications,
webui, scheduling and packaging are all weaker copies of what mulewatch already does.

So we evolve mulewatch instead of maturing p2pwatch.

**Watch several networks.** Only eD2k has produced results so far. That is not a reason to drop
the others: the point of a watch is that having seen nothing does not mean nothing is there. The
BitTorrent DHT produced nothing useful over 100 GB of crawl, and stays, opt-in (decision 3).

**Generic core.** The core stops embedding aMule. Every client runs in its own container, can sit
behind its own VPN, with or without port forwarding, and the core talks to all of them over the
network.

**Stay close to upstream.** Upstream images unchanged where they exist, unpatched upstream source
built by us where they do not. We add only what we need, and we do not upstream needs that only
we have.

## 2. Non-goals

- No new feature beyond what the stages below list.
- No patch to any client except one local gtk-gnutella patch (decision 3).
- No rename: the project stays `mulewatch`. A rename may come later.
- No retention bound on the source history for now (decision 10).

## 3. Decisions

### D1. The core embeds no client

The core image runs the crawler and its in-process webui, and nothing else. aMule moves to its
own image. This reverses the 2026-09-16 decision for one reason: with one network the crawler and
amuled were born and died together; with five networks they are not, and Bitmagnet + PostgreSQL
or AirDC++ have lifecycles of their own.

What the reversal brings back: several images, one per client we build. What it does not bring
back: the Docker socket proxy (D6) and named volumes (bind mounts stay the rule).

What it buys: with no s6 and no root-owned boot step, the core can finally run as a non-root
`USER`, `read_only`, with `cap_drop: ALL`, the hardening the single container had to give up.

### D2. Upstream first

For each client, in this order of preference: the upstream image unchanged; our image built from
the unpatched upstream source or release, pinned by version and checksum (the aMule pattern of
`ARG VERSION` + `ADD --checksum`); a local patch only when nothing else works.

### D3. One client per network

| Network | Client | Image | Patch |
|---|---|---|---|
| eD2k / Kad | aMule (amuled + amuleapi) | ours, built from the pinned upstream commit, as today | none |
| Soulseek | slskd | upstream `slskd/slskd` (multi-arch) | none |
| Direct Connect (NMDC/ADC) | AirDC++ Web Client | ours, from the upstream portable release (see risks) | none |
| Gnutella + G2 | gtk-gnutella | ours, built from upstream source | **one local patch** |
| BitTorrent DHT (opt-in) | Bitmagnet + PostgreSQL | upstream `ghcr.io/bitmagnet-io/bitmagnet`, upstream `postgres` | none |

Rejected, verified 2026-10-08:

- **MLDonkey**: its Gnutella/G2 modules are experimental and disabled at configure time, and G2
  did not even build with a recent OCaml until late 2024. aMule covers eD2k better.
- **eiskaltdcpp-daemon**: last release 2021, and its RPC has no authentication.
- **Nicotine+ headless**: no remote API. slskd has one, plus native gluetun support.
- **Gnutonium**: young, G1 only. A fallback to watch, not a choice.
- **A dedicated G2 client**: G2 is nearly dead (4 hosts refreshed within 24 h on the main GWC).
  gtk-gnutella's G2 leaf mode covers it for free.
- **magnetico**: YAGNI while Bitmagnet's own crawler suffices.

**Why gtk-gnutella needs a patch.** In a topless build, the shell's only search verb, `search
add`, can never create a search: `gcu_search_gui_new_search()` returns `FALSE` when
`running_topless` (`src/if/bridge/c2ui.c:127-135`). Only the GTK UI creates searches and
listens for their results, and the core drops hits that match no search. The only patch-free
route (a GTK build under Xvfb plus the `log_query_hit_records` debug log) folds names to ASCII
and loses the servent GUID and the hit-to-query link. The patch stays local and minimal, in
`src/shell/search.c` only:

1. `search add` creates and starts a search when topless.
2. One generic results listener prints every non-spam hit to stdout, with no keyword filter (the
   prototype hard-coded its keywords in C, so changing them silently emptied Gnutella).
3. It calls `search_add_kept()` the way the GUI does, which dynamic querying relies on.

Node counts need no patch: `print node_g2_count` and its siblings already exist.

### D4. Repository layout

One rule: **a directory under `packages/` per image we build, and only for those.** Upstream
images we use unchanged get no directory; they appear in `deploy/` only. Python code shipped in
an image is a workspace member, under the same gate (100 % branch coverage, `mypy --strict`).

```
packages/
  crawler/      the core, image ghcr.io/mission-titar/mulewatch (name and dist unchanged)
  matching/     unchanged
  amule/        image mulewatch-amule: aMule build stage (moved from crawler), amule_config
                (moved), the local port-sync loop (D7)
  gnutella/     image mulewatch-gnutella: gtk-gnutella build, patches/, an HTTP shim
  airdcpp/      image mulewatch-airdcpp: upstream portable release, no code of ours
  amule_bump/   unchanged
  vex_guards/   unchanged
```

**The gnutella shim exists because the core cannot read another container's stdout.** It drives
gtk-gnutella's shell, reads the patched hit lines, and serves them over HTTP.

### D5. Deployment: one base compose, one `include:` per network

```
deploy/
  compose.yml                 the core service, plus one include line per network
  ed2k/
    service.yml               aMule's service definition, no ports, no networking
    direct.compose.yml        extends service.yml, publishes the P2P ports on the host
    vpn.compose.yml           its own gluetun, extends service.yml, network_mode: service:gluetun
  soulseek/                   same three files, slskd
  directconnect/              same three files, AirDC++
  gnutella/                   same three files, gtk-gnutella
  bitmagnet/                  same three files, Bitmagnet + PostgreSQL
```

`compose.yml` lists every network, the operator edits only its include lines:

```yaml
include:
  - ed2k/direct.compose.yml            # or ed2k/vpn.compose.yml
  - soulseek/direct.compose.yml        # or soulseek/vpn.compose.yml
  # - directconnect/direct.compose.yml # commented out: network disabled
```

**Nothing is behind a VPN by default.** Every shipped include points at the `direct` variant.
Opening ports or paying for a VPN with enough simultaneous tunnels and port forwarding is the
operator's choice: it is a technical step and a risk assessment we do not make for them.
`docs/install.md` and `docs/legal.md` lay out the trade-off; the shipped default enables eD2k
only, so an existing node keeps today's behaviour.

**This honours the 2026-09-16 lesson on `include:`.** An included service cannot be redefined by
the including file (`services.<name> conflicts with imported resource`). Here `compose.yml`
never redefines an included service, and each variant is self-contained. What the two variants
share lives in `service.yml` and is reached with `extends:`, as `base.compose.yml` is today, so
`ports:` stays out of the shared fragment (Compose merges `ports` additively and cannot remove
one).

Pitfall for stage 3: relative paths in an included file resolve against that file's directory,
so bind mounts must say where they point (`../data/ed2k`), or the include must set
`project_directory`.

**One gluetun per client that needs a forwarded port.** A VPN tunnel usually forwards one port,
and it changes, so two clients that both need inbound connections cannot share one gluetun.
Providers also cap simultaneous tunnels, which caps how many forwarded clients a node can run.

### D6. Client lifecycle: the core never restarts a client

The core holds no Docker socket and never restarts anything. It only observes that a client is
unreachable, alerts, and backs off. Each client container keeps itself alive, by a supervisor
inside it or by exiting on failure under `restart: unless-stopped`.

Compose does not restart an `unhealthy` container (only Swarm does). A healthcheck therefore
reports state; it does not repair it.

### D7. Port-sync is a per-client capability

aMule cannot rebind its listen port at runtime: a changed port only applies on the next start,
and a failed bind leaves amuled running in Low-ID instead of exiting (`amule.cpp:1480-1491`).
Port-sync only runs in a network's `vpn` variant (in the `direct` variant the operator forwards a
fixed port). It is set per client, not as a single core mechanism:

| Client | Port-sync | Where |
|---|---|---|
| aMule | `PATCH /preferences`, then restart amuled through s6, both local | inside the amule container: `port_sync_loop.py`, `gluetun_port.py` and `s6_restart.py` move to `packages/amule/` |
| slskd | native gluetun integration, hot (`SLSKD_VPN_PORT_FORWARDING`) | nothing to write |
| AirDC++ | `POST /settings/set` (`tcp_port`, `udp_port`, `tls_port`), applied hot | a small loop reading gluetun's `/v1/portforward`; stage 5 decides core vs gluetun's `VPN_PORT_FORWARDING_UP_COMMAND` |
| gtk-gnutella | `set listen_port N`, applied hot | through the gnutella shim |
| Bitmagnet | config, restart | not synced at first: we assume a DHT crawl works without inbound reachability (not verified) |

Rejected for aMule: an EC `shutdown` sent from the core. It would expose the EC listener (full
control, unencrypted transport, a second secret), bring an EC client back into the core after the
amuleapi migration removed it, and break whenever gluetun forwards port 4712 (amuled then picks
a random `ECPort`, `amule.cpp:1388-1400`).

The core keeps the alerting: it reads each client's reachability (`ed2k.high_id` for aMule) read
only and raises the existing edge-triggered alerts.

### D8. Network-agnostic file identity

Today `files.ed2k_hash` is the catalog's primary key, and every catalog table references it. The
identity becomes `(network, native_id)`:

| Network | `native_id` |
|---|---|
| eD2k | MD4 file hash |
| Direct Connect | TTH |
| Gnutella / G2 | SHA-1 (urn:sha1) |
| BitTorrent | infohash, plus the file index for a file inside a torrent |
| Soulseek | none in the protocol: a weak identity derived from (normalized filename, size) |

Identities never cross networks: the same TTH and ed2k hash never prove two rows are one file.
`FileObservation` loses its eD2k-only fields to a per-network metadata bag, keeping the `raw_meta`
rule (never lose a field). Stage 1 specifies the migration of every table that references
`files`.

### D9. Three search modes, one download capability

| Mode | Clients |
|---|---|
| **Bounded search**: start, collect until done or a timeout, stop | aMule, slskd, AirDC++ |
| **Persistent search**: a standing search that streams hits | gtk-gnutella |
| **Passive index query**: query an index someone else fills | Bitmagnet |

`MuleClient` is today's bounded-search port with eD2k specifics (`SearchChannel`, `widen_search`,
`KadStatus`). Stage 2 splits the generic bounded-search contract from those specifics, which stay
in the aMule adapter. The matching engine (`catalog_matching`) applies to every network unchanged.

**Downloading is a capability of every client that can download**, not an eD2k feature: a lost
episode is worth fetching from whichever network shares it. Today's download port
(`mule_download_client.py`) is aMule-shaped; stage 2 makes it generic, and each network's stage
wires its client to it. The existing download invariants hold for every network: the crawler
never reads downloaded bytes, and each client downloads into its own bind mount.

| Network | Download through | Status |
|---|---|---|
| eD2k | aMule | exists |
| Soulseek | slskd's transfers API | to detail in its stage |
| Direct Connect | AirDC++'s queue API | to detail in its stage |
| Gnutella / G2 | gtk-gnutella | to verify: topless downloads may hit the same UI stub as searches |
| BitTorrent | Bitmagnet only indexes; a torrent client container is needed | open question (section 6) |

### D10. Sources: pseudonymous identifiers, never addresses

The invariant "the catalog's subject is the file, never the person" is rewritten:

> The catalog records files and, where a protocol publishes one, the pseudonymous identifier of
> the source that shared them, so that a researcher can recognise a recurring sharer and contact
> a possible archivist. It never stores a network address (IP, port, country), and never links
> identifiers across networks.

Why addresses are excluded: an address is the one field that leads to a subscriber through their
ISP, and it is a poor identifier anyway (dynamic IPs, CGNAT, VPNs). Pseudonymous identifiers are
stable for an installation and identify a sharer without saying who they are.

| Network | Stored | Observed through |
|---|---|---|
| eD2k | user hash, nickname | the sources of our downloads (`GET /downloads/{hash}/clients`) |
| Soulseek | username | search responses, sources of our downloads |
| Direct Connect | CID (ADC, hub-verified `hash(PID) == CID`); (nick, hub) on NMDC | search results, sources of our downloads |
| Gnutella / G2 | servent GUID (persistent under `sticky_guid`) | query hits, sources of our downloads |
| BitTorrent DHT | nothing: no stable identity exists | none |

These identifiers are unauthenticated (an eD2k user hash is only bound to a key under Secure
Identification, and a reinstall changes it): a hint, never a proof, like file names already are.

eD2k search results carry no source identity at all, and a Kad source search only runs for an
active partfile (`PartFile.cpp:1806`, `DownloadQueue.cpp:1612`). Hence, on eD2k:

- **Recorded**: every source a download of ours gets data from, or could, whichever side opens
  the connection. A Low-ID source reaches us through our High-ID callback; that is a protocol
  detail, it is still a source sharing with us.
- **Never recorded**: a peer that merely contacts us without sharing anything with us
  (`GET /clients`, `/known_clients`).
- **Never done**: a download started only to obtain sources.

The history is kept in full for now, one row per (file, source, day). It must stay compactable:
the backlog's storage-growth entry applies to it too.

The dormant `sources` and `source_observations` tables (catalog `0001`, written by no application
code, copied by `merge` and `compact`) are replaced, not reused: they carry `ip`, `port` and
`country`.

`docs/legal.md` ("Aucune IP de pair") is updated in the same stage.

### D11. Researcher events

`new-location` (a known file seen at a new source) and `reappeared` (a known location seen again
after a configurable silence, 48 h in the prototype) join the existing decision-change
notifications. They describe observations: a reappearance does not prove the sharer was offline.

### D12. One version for every image

One `vX.Y.Z` tag versions the core and every image we build (`mulewatch`, `mulewatch-amule`,
`mulewatch-gnutella`, `mulewatch-airdcpp`), through the existing git-driven versioning.

## 4. Stages

Domain changes come before topology changes, so that every stage ships on its own.

| # | Stage | Contents |
|---|---|---|
| 1 | Generic identity | D8: catalog migration to `(network, native_id)`, network-agnostic `FileObservation`, in today's single container |
| 2 | Search and download ports | D9: generic bounded-search and download ports, aMule adapters on them; persistent and passive ports declared |
| 3 | aMule leaves the core | D1, D4, D5, D6, D7 for aMule: `mulewatch-amule` image, local port-sync, base compose with includes, core hardening. Breaking: `v5.0.0` |
| 4 | Sources and events | D10, D11: source history, eD2k download sources, `docs/legal.md` |
| 5 | New networks, one by one | search, then download where the client can: Soulseek (slskd), Direct Connect (AirDC++), Gnutella (gtk-gnutella + patch + shim), Bitmagnet (opt-in) |

## 5. Lessons from the prototype, as test cases

The prototype's review found edge cases that become fake-server tests for the new adapters:

- **One malformed reply must degrade one adapter, never the node.** In the prototype, an
  over-long line, an empty field, a `null` size or a missing column crashed the whole watch. This
  is the existing boundary-discipline rule, applied to every new adapter.
- **`Retry-After` must be bounded.** `inf` froze notifications forever; `nan` crashed the node.
- **The media gate must not reject torrent release titles.** The prototype took the last dotted
  segment of `Keroro.Mission.Titar.S01.FRENCH` for an extension and dropped it.
- **No keyword lives anywhere but in config**, not in a patch and not in a code default.
- **Soulseek bans excessive searching** (30 minutes, about 34 searches per 220 s by community
  usage, no official figure) and the server sends excluded phrases that silently empty results:
  the core needs a conservative limiter.
- **AirDC++ hub searches have no explicit end**: collect over a window, at a low priority.
- **gtk-gnutella searches do not survive a client restart** when topless (their persistence,
  `searches.xml`, is UI-side): the shim re-adds them on every start, exactly once.
- **No secret in argv** (the prototype passed the amuleapi password on the command line).

## 6. Risks and open questions

- **AirDC++ on arm64.** mulewatch publishes amd64 and arm64. The usual image
  (`gangefors/airdcpp-webclient`) is amd64 only, and whether upstream ships an arm64 portable
  release is not verified yet. If it does not, Direct Connect is amd64 only, or we build from
  source.
- **Bitmagnet is alpha.** Its GraphQL API and its schema may change before 1.0: pin the image and
  go through GraphQL, not the database (which also keeps PostgreSQL credentials out of the core).
- **VPN providers.** Not every provider forwards ports, and each caps simultaneous tunnels.
- **Storage growth.** A full source history adds to the 4.9 GB / three months already measured.
- **BitTorrent downloads** need a torrent client (a container of its own, e.g. qBittorrent or
  Transmission, upstream image) next to Bitmagnet. Which one, and whether it joins stage 5, is
  not decided.
- **gtk-gnutella downloads when topless** are unverified (`download add magnet:` may go through the
  same UI stub as `search add`). If they fail, the local patch grows or Gnutella stays search-only.
- **The p2pwatch repository** is archived once stage 5 has ported what it is worth.

## 7. Verified sources

Checked in source on 2026-10-08, beyond this repository:

- aMule (`mission-titar/amule`, upstream commit `909d304`): `src/ExternalConn.cpp:3347-3364`
  (EC shutdown), `src/amule.cpp:1388-1400, 1480-1491`, `src/PartFile.cpp:1806`,
  `src/DownloadQueue.cpp:1612`, `src/webapi/SearchJson.cpp`, `docs/api/REFERENCE.md`.
- gtk-gnutella v1.3.1 (`e0157dc8`): `src/if/bridge/c2ui.c`, `src/shell/search.c`,
  `src/core/search.c`, `src/core/settings.c` (`listen_port_changed`), `src/core/routing.c`
  (`sticky_guid`).
- slskd: `docs/vpn.md`, `src/slskd/Integrations/VPN/Clients/Gluetun.cs`,
  `src/slskd/Search/API/Controllers/SearchesController.cs`.
- AirDC++: https://github.com/airdcpp-web/airdcpp-webclient (`ConnectivityManager.cpp`,
  `ClientManager.cpp`), https://github.com/airdcpp-web/airdcpp-apidocs.
- ADC: https://adc.sourceforge.io/ADC.html (`PD` field).
- MLDonkey: https://github.com/ygrek/mldonkey (`config/configure.in`, issue 108).
- Bitmagnet: https://github.com/bitmagnet-io/bitmagnet (`graphql/schema/`, `docker-compose.yml`).
- gluetun: https://github.com/qdm12/gluetun-wiki (`setup/advanced/vpn-port-forwarding.md`).
