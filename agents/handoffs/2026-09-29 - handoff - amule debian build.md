# Handoff: aMule built on Debian, with an automated bump (2026-09-29)

## State

Branch `docs/amule-debian-build-spec` (kept under that name at the operator's request), worktree
`.claude/worktrees/amule-debian-build`. Spec `agents/specs/2026-09-29-amule-debian-build.md` is
approved and fully implemented. `uv run poe check` and `uv run poe docs-build` are green locally.
Not merged, not released: the next release is proposed as **v3.2.0** (spec section 9).

## What changed

- `packages/crawler/Dockerfile`: the nix stage is gone (`amule.nix` deleted). A trixie builder
  compiles amuled + amuleapi from `ADD --keep-git-dir=true --checksum=${AMULE_COMMIT}
  …amule.git#${AMULE_VERSION}`; the two `ARG`s are the only place the aMule version lives. UPnP,
  GeoIP, NLS, BFD, the version check and `ed2k` are off. Image 364 MB to 270 MB.
- `packages/crawler/docker/amule.cdx.json` is a CycloneDX template the build fills with `sed`
  into `/usr/local/share/amule/amule.cdx.json` (`pkg:generic/amule@<pin>`), read by Syft only with
  `SYFT_SELECT_CATALOGERS=+sbom-cataloger`.
- `validate.yml`, per arch, per PR: `--version` of both binaries equals the pin; the SBOM of the
  built image holds `pkg:generic/amule@<pin>`. `release.yml` gates on `pkg:generic/amule@`.
  sbom-action moved to v0.24.2.
- New dev/CI package `packages/amule_bump` (`python -m amule_bump`): reads the pin, finds the latest
  release and its peeled commit, rewrites the two `ARG`s and writes the PR body (changelog,
  `options.cmake` and `REFERENCE.md` diffs, checklist). `.github/workflows/amule-bump.yml` runs it
  weekly and opens the PR with `peter-evans/create-pull-request`.
- PR review (operator): no inline Python in the Dockerfile, no substantial bash script, no
  version-mention gate task. All three were removed; keep it that way.
- `SECURITY.md`, `AGENTS.md`, `docs/`: nix wording gone; new page `docs/contributing/amule-bump.md`.

## Pitfalls

- Pin the **peeled** commit (`refs/tags/X^{}`), never the annotated tag object, or `--checksum`
  never matches.
- A builder without `git`, or a checkout without the tag, still builds but the binaries say
  `aMule GIT`, also on the network. Only the `--version` check catches it; the SBOM check does not.
- `--version` exits 255 on success.
- The release source tarball has no `.git` and an unexpanded `.git_archival.txt` (binaries say
  `GIT`); the `/archive/` tarball has unstable bytes. Hence the git source.
- Declaring 2.3.3 as a red SBOM test proves nothing: it shares with 3.1.0 two amuleweb CVEs without
  a version bound. The red version is 2.2.4 (CVE-2009-1440).
- Grype matches rise from 280 to 315 (Debian's older curl, pulled by wx); all Debian-specific
  Critical/High are `wont-fix` upstream. CVE-2006-2691/2692 match on every aMule version and are not
  in the VEX file yet.
- sbom-action prefixes `registry:` when `registry-username` is set, so validate uses `docker:` for
  the local image; `upload-release-assets` is off there because validate also runs on release.
- Host port 4711 is taken by the live node: test a local daemon on another port.

## Not validated yet

- arm64 and the real sbom-action run: the PR's CI is the proof.
- The bump workflow has never run in Actions: it needs the operator's GitHub App (steps in
  `docs/contributing/amule-bump.md`, secrets `AMULE_BUMP_APP_CLIENT_ID` and
  `AMULE_BUMP_APP_PRIVATE_KEY`). Until then its weekly run fails at the token step.
- Real eD2k/Kad traffic on the Debian build.

## Next step

PR, CI green on both arches, merge, tag v3.2.0, then create the GitHub App and dispatch the bump
workflow once. Candidate BACKLOG line (not written, needs the operator's agreement): VEX
`not_affected` for CVE-2006-2691/2692.
