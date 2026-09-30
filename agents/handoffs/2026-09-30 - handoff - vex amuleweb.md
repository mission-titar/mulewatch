# Handoff: VEX claims for the amuleweb CVEs

## State

Branch `feat/vex-amuleweb`, gate green. The VEX (version 5) now claims `not_affected` for
CVE-2006-2691 and CVE-2006-2692: aMuleWeb flaws fixed in aMule 2.1.2, flagged on every aMule
because NVD's CPE `cpe:2.3:a:amule:amule:*` has no version bound.

## What was built

- `vex_guards`: new image-family guard `DeclaredMinVersion(package, minimum)`, evaluated on the
  hand-declared components of the Syft JSON SBOM (artifacts with a `pkg:generic/` purl), kept
  apart from dpkg names. A component absent from the SBOM is no violation.
- Registry: both CVEs map to `DeclaredMinVersion("amule", "2.1.2")`.
- `SECURITY.md`: `check_image_claims` now has image claims to assert; the local verification
  command selects the `sbom-cataloger`, without which aMule is absent from the SBOM.

## Verified against the real tools

- Syft 1.52 on `ghcr.io/mission-titar/mulewatch:3.2.0` (with `+sbom-cataloger`) emits aMule as
  `UnknownPackage`, purl `pkg:generic/amule@3.1.0`, the only `pkg:generic/` artifact.
- Grype v0.115.0 reports both CVEs without `--vex` and suppresses both with it: the versionless
  subcomponent `pkg:generic/amule` matches.
- `check_image_claims`: exit 0 on the real SBOM, exit 1 with both violations once aMule is edited
  to 2.1.1.

## Pitfalls and open points

- `check_stale_claims` against a local Grype v0.115.0 scan flags four older Python claims
  (CVE-2026-11940, CVE-2026-11972, CVE-2026-4360, CVE-2026-0864) as no longer reported. They
  predate this change; confirm on the daily scan before pruning.
- Code scanning alerts #681 and #686 still point at the old nix image; the first daily scan of an
  release carrying VEX version 5 should close them: the scan reads the VEX attested on `:latest`,
  so merging alone changes nothing there.
