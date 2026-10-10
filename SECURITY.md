# Security Policy

p2pwatch publishes two images to GHCR: `p2pwatch`, the core (the crawler and its webui), and
`p2pwatch-amule`, the aMule client (amuled, amuleapi and port-sync). This policy applies to both,
each with its own signature, attestations and VEX.

## Signing & attestations

Every image pushed to GHCR is **signed** (keyless, OIDC-based, via
[cosign](https://github.com/sigstore/cosign)) and carries three **signed attestations**:

- a **CycloneDX** SBOM: the standard, portable [Syft](https://github.com/anchore/syft)
  bill of materials for external consumers;
- a **Syft-JSON** SBOM: Syft's native format, consumed by the daily scan because it
  preserves the image source identity that image-scoped VEX matching needs (see below);
- an **[OpenVEX](https://openvex.dev/)** document, one per image
  (`security/crawler.vex.openvex.json` for `p2pwatch`, `security/amule.vex.openvex.json` for
  `p2pwatch-amule`): the triage marking non-exploitable CVEs as `not_affected`.

Signing and attestation happen in `.github/workflows/release.yml` (job `publish-manifest`, one
matrix leg per image, so an image whose gates fail publishes nothing), bound to each image's
multi-arch **index digest**; the signature is `--recursive`, so each per-arch
child manifest is signed too. See `docs/verify-image.md` (section "Vérifier
l'authenticité d'une image") for how to verify a pulled image.

## Vulnerability scanning

A [Grype](https://github.com/anchore/grype) scan runs daily against each image's attested
Syft-JSON SBOM (`.github/workflows/grype-scan.yml`), applying that image's VEX. Results
appear in the repository's **Security > Code scanning** tab as SARIF findings, under the
`grype-crawler` and `grype-amule` categories. The scan never fails the workflow: findings are triaged through VEX.

### A misnamed or undeclared component stops matching, silently

Grype matches a package by the **CPE Syft derives from its name**. Rename the package, or leave it
out of the SBOM, and the lookup asks the NVD for a name it has never heard of: no error, no
warning, just zero findings. A scan that got quieter is indistinguishable from one that got safer.

Measured on the same deliberately vulnerable version (Syft 1.51.1, Grype 0.118.0), back when aMule
came from nixpkgs and an override renamed its derivation:

```
amule            2.3.1  ->  cpe:2.3:a:amule:amule:2.3.1            ->  2 findings
amule-web-daemon 2.3.1  ->  cpe:2.3:a:amule-web-daemon:...:2.3.1   ->  0 findings
```

aMule is now compiled from a pinned git commit, so it carries no package metadata at all: the
aMule image's Dockerfile (`packages/amule/Dockerfile`) **declares** it by hand in `/usr/local/share/amule/amule.cdx.json`, a CycloneDX file
generated from `ARG AMULE_VERSION` (`pkg:generic/amule@...` with the CPE Grype matches on). Syft reads that file only when its `sbom-cataloger` is selected
(`SYFT_SELECT_CATALOGERS=+sbom-cataloger`); without it, aMule is silently absent from the SBOM.

**Standing rule: any component we declare by hand is proven red then green**: declare it at a
version with a known CVE, scan it, confirm the finding appears, then confirm it disappears at the
real version. Pick the red version with care: it must match a CVE the real one does not. For aMule
that is 2.2.4 (CVE-2009-1440); later releases only share with the current one two amuleweb CVEs
that carry no version bound, so they prove nothing. The same care applies to a renamed Debian
package or any binary catalogued under a name of our choosing. The aMule case is enforced
mechanically: every PR (`validate.yml`) checks that the SBOM of the built aMule image holds
`pkg:generic/amule@` at the pinned version, and the aMule leg of `release.yml` fails before signing
if either of its SBOMs lacks it. Every other declared or renamed component is on whoever makes it.

## Triage process (VEX)

[OpenVEX](https://openvex.dev/) statements tell Grype which CVEs are **not exploitable** in
this deployment context, so they are filtered out of scan results automatically. They live in
`security/<package>.vex.openvex.json`, one file per image (`crawler`, `amule`), versioned here (the
source of truth) and attached to their released image as a signed OpenVEX attestation: the daily
scan pulls each **from its image**. For a local run, point Grype at the file explicitly, for
example `--vex security/crawler.vex.openvex.json`.

### Statements are image-scoped, and the scan reads the Syft-JSON SBOM

Grype resolves a VEX statement by the **image** identity (`pkg:oci/...`) then by the
vulnerable **package** PURL. We use the **image-scoped** form (product `pkg:oci/p2pwatch` or
`pkg:oci/p2pwatch-amule`, with the vulnerable package as a `subcomponent`) because it is
the only form safe to attach and redistribute: it is scoped to *this* image, so a downstream
consumer's unrelated packages are never suppressed by our statements. Use the subcomponent
PURL **without a version** so a statement survives package bumps. A CVE present in both images
(both ship Python) takes a statement in each file, since each image carries only its own.

For that to work the scan must expose the image identity to Grype. A **CycloneDX** SBOM drops
it (the image-scoped product then matches nothing); Syft's native **Syft-JSON** preserves it.
That is why the daily scan reads the attested Syft-JSON SBOM, and why local verification must
use Syft-JSON too, not CycloneDX.

### Adding a `not_affected` statement

When a CVE is triaged and found not exploitable, open a PR adding an OpenVEX statement with
[`vexctl`](https://github.com/openvex/vexctl):

```sh
go install github.com/openvex/vexctl@latest

# --subcomponents is the vulnerable package's PURL (from the Grype finding / SBOM),
# WITHOUT a version. Comma-separate several. For the aMule image, use
# security/amule.vex.openvex.json and pkg:oci/p2pwatch-amule.
vexctl add \
  --in-place \
  --file security/crawler.vex.openvex.json \
  --product "pkg:oci/p2pwatch" \
  --subcomponents "pkg:deb/debian/<package>" \
  --vulnerability CVE-YYYY-NNNNN \
  --status not_affected \
  --justification vulnerable_code_not_in_execute_path \
  --impact-statement "Brief explanation of why this CVE does not affect this image"
```

Verify the suppression applies before opening the PR, using a **Syft-JSON** SBOM:

```sh
# sbom-cataloger reads the hand-declared aMule; without it, aMule is absent from the SBOM.
SYFT_SELECT_CATALOGERS=+sbom-cataloger syft <image> -o syft-json=/tmp/sbom.syft.json
grype sbom:/tmp/sbom.syft.json --vex security/crawler.vex.openvex.json --show-suppressed | grep <CVE>
```

Valid `--justification` values (OpenVEX vocabulary): `component_not_present`,
`vulnerable_code_not_present`, `vulnerable_code_cannot_be_controlled_by_adversary`,
`vulnerable_code_not_in_execute_path`, `inline_mitigations_already_exist`.

The PR description must explain the triage rationale, and a reviewer must approve. Do **not**
add CVEs to ignore lists without a VEX statement: this keeps all suppressions auditable and
signed.

### Keeping the VEX claims honest: the four consistency checks

A `not_affected` claim is a promise about how the image is built and run. Over time source
and base images drift, so four checks (the `vex_guards` package) assert those promises still
hold. They run as a non-blocking PR job (only `validate / gate` is required to merge) and, for
the three that catch a genuinely false claim, as hard-fail steps in the release before anything
is signed or attested:

- **`check_source_claims`** (PR job, release hard-fail): fails if our own source starts
  reaching code a `vulnerable_code_not_in_execute_path` claim says we never execute, for
  example importing `imaplib` or `poplib`.
- **`check_claim_coverage`** (PR job, release hard-fail): reads every VEX file, and fails if a
  VEX `not_affected` claim has no guard in the registry, a guard has no claim, or a justification does not match its
  guard family. It keeps the VEX and the guard registry in bijection.
- **`check_image_claims`** (daily Grype scan as SARIF, release hard-fail): fails if the built
  image's SBOM contradicts an image-scoped claim, for example a dpkg package that should be
  absent is present, or a dpkg package or hand-declared component is below the minimum version
  a claim relies on. The aMule claims rest on the version declared in the aMule image's SBOM
  (`pkg:generic/amule`).
- **`check_stale_claims`** (daily Grype scan as SARIF, non-blocking): flags VEX entries Grype no
  longer reports for the image, so obsolete suppressions get pruned. Staleness never blocks a
  release: a suppressed CVE that Grype stops reporting has been fixed upstream, which does not
  make the image less safe, so it is surfaced for cleanup rather than gated.

In the daily scan `check_image_claims` and `check_stale_claims` run in SARIF mode: drift surfaces
in Code scanning without failing the workflow. In the release the first three hard-fail, so a
false source, coverage, or image claim stops the image from being tagged, signed, or attested;
staleness is left to the daily scan.

## Reporting a security issue

To report a vulnerability privately, use GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
feature for this repository.
