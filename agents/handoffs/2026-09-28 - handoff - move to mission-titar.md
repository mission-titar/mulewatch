# Handoff: move to the mission-titar organization (2026-09-28)

## State

The project now lives at `mission-titar/mulewatch`, a fresh repository (not a GitHub transfer).
`GeoffreyCoulaud/mulewatch` is archived and keeps every release up to v3.1.0 and their images under
`ghcr.io/geoffreycoulaud/`.

## What changed

- Every live reference (workflows, compose files, smoke stack, integration conftest, docs, zensical,
  VEX `@id`/`author`) now points to `mission-titar`. `agents/` is history and was left untouched.
- Decision: no retro-release. Old GitHub releases and images are neither copied nor rebuilt (a copy
  would carry another CI's signature, a rebuild would produce different images under old versions).
  README, `docs/verify-image.md` and `docs/migration-1x.md` point to the archived repo instead.

## Pitfalls

- `ghcr.io/mission-titar/mulewatch` has no `latest` until the first tag is pushed from the new repo:
  `deploy/base.compose.yml` cannot pull before that release.
- The GHCR package created by the first CI push must be made public in the org's package settings.
- The deployed node (`/home/geoffrey/Projets/mulewatch`) still pulls the old image.

## Next step

Merge, tag the first release from the org, make the package public, then repoint the live node.
