# Handoff: move to the mission-titar organization (2026-09-28)

## State

`GeoffreyCoulaud/mulewatch` was transferred to `mission-titar/mulewatch` (GitHub transfer: history,
releases v1.0.0 to v3.0.0, issues, PRs and branch protection moved; old URLs redirect). A
short-lived fresh repo at the same name was deleted first, and the pre-archive README notice commit
was dropped from `main` before the transfer.

## What changed

- Every live reference (workflows, compose files, smoke stack, integration conftest, docs, zensical,
  VEX `@id`/`author`) now points to `mission-titar`. `agents/` is history and was left untouched.
- GHCR packages are account-scoped and do not follow a transfer: images up to 3.1.0 stay under
  `ghcr.io/geoffreycoulaud/`, signed by the old repo's identity. They are neither copied nor rebuilt
  (a copy carries another CI's signature, a rebuild ships different images under old versions).
  `docs/verify-image.md` and both migration rollbacks name the old image location.

## Pitfalls

- `ghcr.io/mission-titar/mulewatch` has no `latest` until the first tag is pushed from the org:
  `deploy/base.compose.yml` cannot pull before that release.
- The GHCR package created by the first CI push must be made public in the org's package settings.
- v3.1.0 has a tag and an old-namespace image but no GitHub release object.
- The docs site moved to https://mission-titar.github.io/mulewatch/; the old github.io URL does not
  redirect.
- The deployed node (`/home/geoffrey/Projets/mulewatch`) still pulls the old image.

## Next step

Merge, tag the first release from the org, make the package public, then repoint the live node.
