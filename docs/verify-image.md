---
description: "Vérifier la signature cosign et les attestations d'une image p2pwatch avant de la lancer."
---

# Vérifier l'authenticité d'une image


Chaque image publiée est signée et attestée par la CI (cosign, keyless OIDC). Avant de
lancer une image tirée de GHCR, on peut vérifier qu'elle vient bien de notre pipeline.

Un nœud tire deux images, signées par la même identité : `p2pwatch`, le crawler, et
`p2pwatch-amule`, le client eMule. Vérifiez les deux, en relançant les commandes ci-dessous avec
`IMAGE=ghcr.io/mission-titar/p2pwatch-amule:latest`.

Prérequis : [cosign](https://github.com/sigstore/cosign) installé.

L'identité attendue est le workflow de release du dépôt :

```bash
IMAGE=ghcr.io/mission-titar/p2pwatch:latest
IDENTITY='^https://github.com/mission-titar/p2pwatch/.github/workflows/release.yml@refs/'
ISSUER=https://token.actions.githubusercontent.com
```

Les images publiées avant le transfert du dépôt dans l'organisation (jusqu'à la 3.1.0) sont
restées sous `ghcr.io/geoffreycoulaud/`, et leur identité est
`^https://github.com/GeoffreyCoulaud/mulewatch/.github/workflows/release.yml@refs/`.

Vérifier la **signature** de l'image :

```bash
cosign verify \
  --certificate-identity-regexp "$IDENTITY" \
  --certificate-oidc-issuer "$ISSUER" \
  "$IMAGE"
```

Vérifier une **attestation** (SBOM ou VEX ; `--type` parmi `cyclonedx`,
`https://syft.dev/bom`, `openvex`) :

```bash
cosign verify-attestation \
  --type openvex \
  --certificate-identity-regexp "$IDENTITY" \
  --certificate-oidc-issuer "$ISSUER" \
  "$IMAGE"
```

Une commande qui réussit prouve que ce digest a été signé/attesté par notre CI : un digest
substitué (image malveillante) n'aurait pas d'attestation signée par notre identité OIDC.
La signature étant `--recursive`, la vérification fonctionne aussi bien par tag (index) que
par digest d'architecture. Le détail de la chaîne et du triage VEX est dans `SECURITY.md`.

> **Des noms qui ne suivent pas celui de l'image, exprès.** Chaque image a son fichier de claims
> et ses catégories SARIF, nommés d'après son paquet : `security/crawler.vex.openvex.json` et le
> suffixe `-crawler` pour `p2pwatch`, `security/amule.vex.openvex.json` et le suffixe `-amule` pour
> `p2pwatch-amule` (`grype-crawler`, `vex-image-claims-crawler`, `vex-stale-claims-crawler`, et
> leurs équivalents `-amule`). Renommer une catégorie Code scanning rend ses findings existants
> orphelins. Seul le *produit* VEX porte le nom de l'image.

---

