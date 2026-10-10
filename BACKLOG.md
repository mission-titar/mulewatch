# Backlog

What the project intends to do next. One entry per improvement, at most two lines, stating
what changes and why it is wanted. Every entry stands alone: it carries its own link, so
deleting any other entry leaves it intact.

Not a changelog and not a record. An entry is **deleted** when it ships or when it is
dropped, never annotated with what became of it. The history is in git and in
`agents/handoffs/`.

An agent adds an entry only after the operator has agreed to it.

- **Multi network**: a generic core driving one container per P2P client, renamed p2pwatch.
  Umbrella spec: `agents/specs/2026-10-08-multi-network-architecture.md`.
  - **Stage 3, aMule leaves the core**: `p2pwatch-amule` image, local port-sync (its metrics and
    `NetworkStatus` leave the core with it), include-based compose, rename to p2pwatch. Stage 3 of `agents/specs/2026-10-08-multi-network-architecture.md`.
  - **Stage 4, sources and events**: pseudonymous source history, eD2k download sources,
    `new-location` / `reappeared`, `docs/legal.md`. Stage 4 of `agents/specs/2026-10-08-multi-network-architecture.md`.
  - **Stage 5, new networks**: Soulseek (slskd), Direct Connect (AirDC++), Gnutella (gtk-gnutella),
    BitTorrent (Bitmagnet + qBittorrent); persistent and passive search ports with their first client, a `retry_after` on `SearchFailed` if slskd announces one. Stage 5 of `agents/specs/2026-10-08-multi-network-architecture.md`.

- **Paginate the file detail timeline** (low priority): it renders every sighting, 12.9 MB for the
  node's heaviest file (38,593 rows). Measured in `agents/handoffs/2026-10-05 - handoff - shared observation reads.md`.
