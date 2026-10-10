---
description: "Vérifier la signature cosign et les attestations d'une image p2pwatch avant de la lancer."
---

# Vérifier l'authenticité d'une image


Chaque image publiée est signée et attestée par la CI (cosign, keyless OIDC). Avant de
lancer une image tirée de GHCR, on peut vérifier qu'elle vient bien de notre pipeline.

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

> **Deux noms qui ne bougent pas, exprès.** Le fichier de claims reste
> `security/crawler.vex.openvex.json` et les catégories SARIF gardent leur suffixe `-crawler`
> (`grype-crawler`, `vex-image-claims-crawler`, `vex-stale-claims-crawler`) : renommer une
> catégorie Code scanning rend ses findings existants orphelins. Seul le *produit* VEX a suivi le
> renommage de l'image.

---

