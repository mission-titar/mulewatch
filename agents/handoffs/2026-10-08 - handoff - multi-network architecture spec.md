# Handoff: multi-network architecture spec

## State

Branch `docs/multi-network-architecture`, documentation only, no code changed. Spec:
`agents/specs/2026-10-08-multi-network-architecture.md` (approved 2026-10-08). It is an umbrella
spec: every stage it lists gets its own detailed spec before any code.

## What was decided

The session started as a review of the `mission-titar/p2pwatch_poc` prototype (a stdlib-only
process that compiled every P2P client into `vendor/` and ran them as subprocesses). Its only
behaviour beyond mulewatch was the extra networks, a network-agnostic identity and two event
kinds, so mulewatch evolves instead, and is renamed p2pwatch (D15). In short:

- A generic core with no embedded client; one container per client, each optionally behind its
  own gluetun. Nothing behind a VPN by default (D1, D5).
- One base `deploy/compose.yml` with one `include:` per network; each network directory holds
  `service.yml` (reached by `extends:`), `direct.compose.yml` and `vpn.compose.yml` (D5).
- Clients: aMule, slskd, AirDC++ Web Client, gtk-gnutella (topless, one local patch),
  Bitmagnet + PostgreSQL with a filtered crawl, qBittorrent for BitTorrent downloads (D3).
- Identity `(network, native_id)`; three search modes; downloads on every network; a generic
  download lifecycle and client status (D8, D9, D13, D14).
- Sources: pseudonymous identifiers stored (user hash, username, CID, servent GUID), addresses
  never; eD2k sources only from our own downloads, whichever side connects (D10).
- Stages: identity, ports, aMule split + rename (`v5.0.0`), sources and events, then networks.

## Pitfalls learned

- The first research pass was wrong twice (it missed slskd, and claimed aMule bootstraps its
  server list on its own). Every client fact in the spec was re-checked in source; keep that bar
  for the stage specs.
- aMule cannot rebind its listen port, and a failed bind leaves it in Low-ID without exiting, so
  restart policies never fix a wrong port: port-sync stays a loop, local to the amule container.
- slskd persists `Succeeded` before moving the file, and fails every in-flight transfer on
  restart. AirDC++ reports `completed` even when the final rename failed. gtk-gnutella purges
  completed downloads on a clean restart. Each is a completion false positive or a loss to test.
- `include:` cannot be overridden by the including file; the layout in D5 never does.
- The catalog already holds dormant `sources` / `source_observations` tables with `ip`, `port`
  and `country` columns: replace them, do not reuse them.

## Not validated

- No arm64 portable release of AirDC++ confirmed.
- Bitmagnet health endpoint and qBittorrent `connection_status` (D14 rows marked "to verify").
- Whether a DHT crawl needs inbound reachability; GitHub Pages and GHCR behaviour after the
  repository rename; availability of `ghcr.io/mission-titar/p2pwatch`.

## Next

Write the stage 1 spec: the catalog migration from `files.ed2k_hash` to `(network, native_id)`
across every table that references it, and the network-agnostic `FileObservation`.
