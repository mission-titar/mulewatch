# aMule built on Debian, with an automated bump

- Date: 2026-09-29
- Status: DRAFT (awaiting operator review)
- Scope: replace the nixpkgs build of aMule with a Debian trixie build; make every aMule bump
  a single reviewed PR, opened by automation, whose checks prove the image still tells the truth
- Supersedes: section 2 of `2026-09-16-single-container-embedded-amule.md` (the rest stands)
- Related: `packages/crawler/Dockerfile`, `packages/crawler/docker/amule.nix`,
  `.github/workflows/{validate,release}.yml`, `SECURITY.md`, `packages/vex_guards/`

## 1. Why

The 2026-09-16 spec chose nixpkgs because it was the only source that gave every component a
name and a version. Two things changed:

- **Trust.** Everything else in the image (python3, s6, the base) already trusts Debian: signed
  archive, per-package maintainers, a security team, a DSA feed. aMule pulled in a second trust
  root, nixpkgs plus cache.nixos.org, for one package. The operator no longer wants that.
- **The "we would own the whole dependency graph" objection was overstated.** aMule 3.1.0 needs
  wxWidgets >= 3.2, Boost >= 1.70 (headers only), Crypto++ >= 8.1 and zlib, all packaged by
  trixie. There is no ffmpeg dependency. We own the compile of aMule itself, nothing else.

nixpkgs does not ship 3.1.0 anyway (master is on 3.0.1 as of today): we already override its
source, so "aMule from nixpkgs" was already "aMule built by us, on nixpkgs".

### Routes rejected

| Route | Why not |
|---|---|
| Stay on nixpkgs | Second trust root for one package (above). |
| Upstream's static tarball (`aMule-X.Y.Z-Linux-x64-static.tar.gz`) | Alpine 3.20 (EOL) musl build that statically links OpenSSL, libcurl, nghttp2, c-ares, zlib, expat and more, taken from `apk add` without versions. They would vanish from the SBOM, we could not even declare them honestly, and a CVE in OpenSSL would wait on an aMule release. No checksum or signature is published. |
| A hand-made `.deb` for aMule | More packaging to maintain than the one-component SBOM of section 4 buys. |

## 2. Verified by prototype (2026-09-29, amd64)

All of this was executed, not assumed (Syft 1.52.0, Grype 0.119.0, db v6.1.9). The full image
(the current Dockerfile with the nix stage swapped for the Debian one) starts under s6 with the
real entrypoint: `/api/v1/health` returns 200, the web UI is served, the crawler logs in and arms
its download loop.

| | nix (today) | Debian (prototype) |
|---|---|---|
| Full image | 363.7 MB | **270.5 MB** |
| aMule build stage, `--no-cache` | 224 s | **95 s** |
| aMule components in the SBOM | nix closure, CPE-matched | deb packages + one declared component |
| Grype matches, full image | 280 (13 Critical) | 315 (21 Critical), see section 8 |

## 3. The build

A builder stage on the same `debian:trixie-slim` digest as the rest of the image:

- **Build packages:** `cmake g++ make pkgconf python3 libwxgtk3.2-dev libcrypto++-dev
  libboost-dev zlib1g-dev libcurl4-openssl-dev libglib2.0-dev`.
  - `libwxgtk3.2-dev` even for a daemon: trixie has no `libwxbase3.2-dev`, and `wx-config` plus
    the `libwx_baseu` dev links live in the gtk3 package. The builder is 1.56 GB; it never ships.
  - `libglib2.0-dev` because that `wx-config` defines `__WXGTK__`, and aMule's CMake then
    requires glib for `BUILD_DAEMON` (`amule.cpp` calls `g_set_prgname()`).
  - `libcurl4-openssl-dev` enables aMule's own curl tuning (timeouts, stalled-transfer detection
    for server.met / nodes.dat). The runtime lib is pulled by wx anyway.
- **Runtime packages:** `libwxbase3.2-1t64 libcrypto++8t64 libglib2.0-0t64 libcurl4t64 zlib1g`,
  exactly the owners of the binaries' `NEEDED` entries. `ldd` + `dpkg -S`: no missing lib, and all
  45 resolved libs belong to a dpkg package.
- **Shipped files:** `amuled`, `amuleapi`, `amuleapi-static/` (the web UI, found at the compiled-in
  `/usr/local/share/amule/amuleapi-static/`; removing it turns `GET /` into a 404),
  `LICENSE.md` (GPL; the prototype forgot it), and the SBOM file of section 4.

### CMake options

`-DCMAKE_BUILD_TYPE=Release -DBUILD_MONOLITHIC=OFF -DBUILD_DAEMON=ON -DBUILD_AMULEAPI=ON`, and:

| Option | Value | Why |
|---|---|---|
| `BUILD_ED2K` | **OFF** | The browser link handler. Nothing in the image calls it; nix shipped it by default. |
| `ENABLE_VERSION_CHECK` | **OFF** | Otherwise the daemon polls the GitHub API at start and daily. Upstream documents OFF for OS-package builds; the image owns updates. |
| `ENABLE_UPNP` | **OFF** | Router port mapping means nothing behind gluetun, and the direct stack documents manual forwarding on the box (`docs/high-id.md`); UPnP discovery does not cross Docker's bridge anyway. Drops libupnp + libixml. |
| `ENABLE_IP2COUNTRY` | **OFF** | Locating peers is about persons, not files (first invariant). Nothing reads it: the catalog's `country` column is only carried by merge/compact. Drops libmaxminddb. |
| `ENABLE_NLS` | **OFF** | 5.3 MB of translations for a headless daemon. |
| `ENABLE_BFD` | **OFF** | Already off under nix; `backtrace_symbols()` is the fallback. |

UPnP, GeoIP and NLS were on under nix: these are behaviour changes, hence section 9.

### The source

```dockerfile
ARG AMULE_VERSION=3.1.0
ARG AMULE_SHA256=a05f9b655f4407f18179640f65ae50c93bd36a90c268c8fff7adc575fe8eb041
ADD --checksum=sha256:${AMULE_SHA256} \
    https://github.com/amule-org/amule/releases/download/${AMULE_VERSION}/aMule-${AMULE_VERSION}-src.tar.gz \
    /src/amule.tar.gz
```

These two `ARG`s are **the single source of truth for the aMule version**. Everything else
derives from them or is checked against them.

- **Why a checksum:** upstream tags are unsigned (created by `github-actions[bot]`) and a tag can
  move. A wrong checksum fails the build (`digest mismatch`, verified red).
- **Why the release asset, not `/archive/refs/tags/X.tar.gz`:** the auto-generated archive
  expands `ref-names` in `.git_archival.txt`, so its bytes (and hash) change whenever another ref
  lands on the same commit. The asset is a plain `tar` of `git ls-files`: stable bytes.
- **Pitfall, the version string.** Because the asset is not a `git archive`, its
  `.git_archival.txt` keeps its `$Format:` placeholders, CMake ignores it, and the binaries call
  themselves `aMule GIT` (also on the network, as the client's mod version). The build rewrites it
  (`describe-name: ${AMULE_VERSION}`) before configuring. A `-D` cannot do it: `VERSION` is a plain
  `set()`.
- **A non-tautological version check.** The rewrite makes the binaries report whatever
  `AMULE_VERSION` says. So the build also asserts that the tarball is that version, from content
  the rewrite does not touch: `grep -q "^## Version ${AMULE_VERSION} " docs/CHANGELOG.md`.
- **To verify at implementation:** that `ADD --checksum` expands a build `ARG` (else the bump
  workflow edits a literal).

## 4. aMule in the SBOM

Debian's libraries are dpkg packages, which Syft catalogues on its own. aMule, compiled by us,
has no package metadata: it is declared.

- The Dockerfile **generates** `/usr/local/share/amule/amule.cdx.json` (CycloneDX 1.6) from
  `AMULE_VERSION`, so the declaration cannot drift from the pin: one component, `type:
  application`, `name: amule`, `purl: pkg:generic/amule@X`, `cpe:
  cpe:2.3:a:amule:amule:X:*:*:*:*:*:*:*`, licence `GPL-2.0-or-later`. Boost (headers compiled in),
  picojson and libutp (vendored, `docs/THIRDPARTY.md`) are declared too, with the versions the
  build used: they are in the binaries, and nix never showed them either.
- Syft reads it only with **`--select-catalogers "+sbom-cataloger"`**: that cataloger is tagged
  `package, sbom`, not `image`, so it is off by default. Its globs include `**/*.cdx.*`.
- The CPE is what Grype matches on. Verified: `+sbom-cataloger` makes aMule appear (`amule 3.1.0
  UnknownPackage pkg:generic/amule@3.1.0`), without it aMule is absent.

### Proving the declaration is read (red, then green)

The obvious red, declaring 2.3.3, **proves nothing**: 2.3.3 and 3.1.0 both match exactly
CVE-2006-2691 and CVE-2006-2692, which carry no version bound in the NVD. The working red is
**2.2.4**, which adds CVE-2009-1440 (`= 2.2.4`); back to 3.1.0 and it disappears.

## 5. Checks that run on every PR

Today the "aMule is in the SBOM" gate runs only in `release.yml`, at tag time: a bump PR would be
merged before anything proved it. Every check below runs in `validate.yml`, on both architectures,
so a bump PR (automated or not) proves itself before merge.

| Check | What it catches |
|---|---|
| Build: `ADD --checksum` | a tarball other than the pinned one |
| Build: CHANGELOG assertion | a pin whose version and tarball disagree |
| Build: CMake minimums | a trixie library below aMule's floor |
| Syft on the built image with `+sbom-cataloger`: `pkg:generic/amule@${AMULE_VERSION}` present | the SBOM file missing, misnamed, or out of the cataloger's globs; the cataloger not selected |
| `amuled --version` and `amuleapi --version` match `^aMuleD (\S+) compiled` / `^amuleapi (\S+) compiled` and equal `AMULE_VERSION` | the `GIT` version pitfall coming back; a stale binary. The exit code is 255 even on success: do not test it. |
| Compose smoke + API/download/orchestration integration (existing) | amuleapi contract or behaviour changes |
| Gate check: no aMule version written outside the pin | prose and comments going stale (below) |

`release.yml` keeps its gate, switched from `pkg:nix/amule@` to `pkg:generic/amule@`, and both
`sbom-action` steps get the cataloger selection (how `sbom-action` takes it is to verify).

**No version outside the pin.** About eight places write "aMule 3.1.0" today (`AGENTS.md`,
`docs/troubleshooting.md`, `docs/contributing/testing.md`, `validate.yml`, the integration
`conftest.py`, `amule-config.py`, `amule.nix`). They are reworded to name the pin instead, and a
gate task fails if a version re-appears next to "aMule"/"amuleapi" outside the Dockerfile,
`agents/` (dated history) and test fixtures. It must not trip on mulewatch's own versions, which
are also 3.x. Proven red by re-adding one mention.

Each check is proven red once before it is trusted (checksum: done by the prototype).

## 6. The bump

### Automated: `.github/workflows/amule-bump.yml`

Weekly schedule plus `workflow_dispatch`. Dependabot cannot follow a GitHub release of an
arbitrary repo; Renovate could, but a second dependency bot for one package is not worth it.

1. Read `AMULE_VERSION` from the Dockerfile; ask `repos/amule-org/amule/releases/latest` (stable
   releases only). Same version: stop.
2. Download the `-src.tar.gz` asset, hash it, and **compare with the `digest` GitHub reports for
   the asset**. Mismatch: fail, open nothing.
3. Rewrite the two `ARG`s on branch `chore/amule-X.Y.Z`. A branch or PR already open for that
   version: stop (idempotent).
4. Open the PR with a body carrying: the upstream changelog section, the diff between the two tags
   of `cmake/options.cmake` (new or changed build switches) and of `docs/api/REFERENCE.md` (the
   amuleapi contract), and the manual checklist below.

**Token.** A PR opened with `GITHUB_TOKEN` does not trigger workflows, so its required
`validate / gate` would never run. The workflow uses a **GitHub App** installation token
(`actions/create-github-app-token`, pinned by SHA): scoped to this repo, short-lived, not tied to
a person. Creating the App and storing its id and key as secrets is a one-time operator step.

### Manual: the checklist in the PR body

- [ ] Read the changelog: anything that changes the daemon's defaults or the network behaviour?
- [ ] `options.cmake` diff: a new switch to set explicitly in section 3's table?
- [ ] `REFERENCE.md` diff: does the `mule_api` adapter need a change (with its tests first)?
- [ ] `amule-config.py`: does any setting we override (or rely on the default of) change default?
- [ ] CI green on amd64 and arm64.
- [ ] Merge, then tag a release (`vX.Y.Z - aMule A.B.C`): the image only changes on a tag.

## 7. Documentation and tooling

- `SECURITY.md`: the "renamed package" section now covers a declared component; the standing rule
  becomes "any component we declare by hand is proven red then green".
- `vex_guards/sbom.py` docstring and its test fixture: aMule is `UnknownPackage`, not nix.
- `docs/troubleshooting.md`, `docs/contributing/testing.md`, `AGENTS.md`: Debian build, version
  pointer instead of number.
- A short maintainer page under `docs/contributing/` (French): what the bump workflow does, and
  the checklist.
- `amule.nix` is deleted.

## 8. Accepted cost: more Grype matches

Full image: 315 matches instead of 280, 21 Critical instead of 13. Most of the difference is
Debian's `libcurl4t64` 8.14.1 (25 matches) against nix's curl 8.22.0, plus krb5, ldap, crypto++,
gnutls. Every Critical and High specific to the Debian build is `wont-fix` on Debian's side, that
is, judged minor by Debian's security team. Going the other way, 24 matches disappear (nix
libssh2, pcre2, zlib, krb5).

curl is not optional: `libwxbase3.2-1t64` depends on it. This is the price of taking fixes on
Debian's schedule rather than nixpkgs': more triage in the daily scan, through VEX as usual.

CVE-2006-2691 and CVE-2006-2692 already match today (on the nix aMule) and are not in the VEX
file: they target amuleweb before 2.1.2, which we neither build nor run. Two `not_affected`
statements, out of this spec's scope but worth a BACKLOG line.

## 9. Release and live node

- Release **v3.2.0** (minor): no config or data change, but UPnP, GeoIP, NLS and the version check
  are gone from the daemon, and the client no longer reports a nix-built aMule.
- The live node needs nothing beyond the usual image pull: same aMule version, same config dir.

## 10. Work packages

1. **Image:** Debian builder, `ARG` pin, CHANGELOG assertion, archival rewrite, generated SBOM
   file, `LICENSE.md`; delete `amule.nix`.
2. **Checks:** the section 5 table in `validate.yml`; `release.yml` gate and cataloger selection;
   the version-mention gate task; each proven red once, including the 2.2.4 SBOM cycle.
3. **Bump workflow** and the operator's GitHub App.
4. **Docs and tooling:** section 7.

## 11. Not validated yet

- **arm64**: the prototype ran on amd64 only; WP2's CI matrix is the proof.
- Real eD2k/Kad traffic: the prototype ran with `--network none`.
- `ADD --checksum` with an `ARG`, and `sbom-action`'s way to select a cataloger.
