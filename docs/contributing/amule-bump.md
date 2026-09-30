---
description: "Comment la version d'aMule est épinglée, montée chaque semaine par une PR automatique, ou à la main."
---

# Mettre à jour aMule

L'image compile aMule sur Debian depuis un commit git épinglé. La version tient en **deux lignes**
de `packages/crawler/Dockerfile`, et nulle part ailleurs :

```dockerfile
ARG AMULE_VERSION=<le tag amont, tel quel>
ARG AMULE_COMMIT=<le commit sur lequel pointe ce tag>
```

Le reste du dépôt nomme cette épingle au lieu d'écrire un numéro.

## La PR automatique

Le workflow `.github/workflows/amule-bump.yml` tourne chaque lundi (et à la demande, depuis
l'onglet **Actions**), en deux étapes.

`uv run python -m amule_bump` (le paquet d'outillage `packages/amule_bump/`) fait la logique :

1. lit `AMULE_VERSION` et demande à GitHub la dernière release stable d'`amule-org/amule` ; même
   version, ou plus ancienne : il s'arrête ;
2. trouve le commit sur lequel pointe le tag de cette release ;
3. réécrit les deux `ARG` du Dockerfile ;
4. écrit la description de la PR : l'extrait du changelog amont, le diff entre les deux tags de
   `cmake/options.cmake` (les options de build) et de `docs/api/REFERENCE.md` (le contrat
   d'amuleapi), et la liste de vérifications ci-dessous.

L'action `peter-evans/create-pull-request` pousse ensuite le Dockerfile modifié sur la branche
`chore/amule-<version>`, dans un commit signé par l'App, et ouvre la PR.

La CI de la PR compile alors exactement ce commit, sur amd64 et arm64 : checksum du commit,
version rapportée par `amuled --version` et `amuleapi --version`, présence d'aMule dans le SBOM,
puis les suites d'intégration.

Relancer le workflow ne duplique rien : une branche déjà là est reconstruite depuis `main` et
n'est repoussée que si elle diffère, et la PR ouverte sur cette branche est mise à jour.

!!! note "Une PR fermée sans fusion revient"
    Une PR fermée n'empêche pas d'en ouvrir une autre : tant que `main` épingle l'ancienne version,
    chaque exécution rouvre une PR vers la dernière release. Pour ne pas monter, désactivez le
    workflow (*Actions*, *aMule bump*, *Disable workflow*) jusqu'à la release suivante.

## Mise en place de l'App GitHub (une seule fois)

Une PR ouverte avec le `GITHUB_TOKEN` du workflow ne déclenche aucun workflow : le contrôle
obligatoire `validate / gate` ne tournerait jamais. Le workflow s'authentifie donc avec le jeton
d'installation d'une **App GitHub**, limité à ce dépôt et de courte durée.

1. Dans l'organisation **mission-titar** : *Settings*, *Developer settings*, *GitHub Apps*, *New
   GitHub App*. Un nom libre, l'URL du dépôt comme page d'accueil.
2. *Webhook* : décochez **Active**.
3. *Repository permissions* : **Contents** en *Read and write*, **Pull requests** en *Read and
   write*, rien d'autre (GitHub impose *Metadata* en lecture seule). Aucune permission
   d'organisation ni de compte.
4. *Where can this GitHub App be installed?* : **Only on this account**. Créez l'App.
5. Sur la page de l'App, notez le **Client ID**, puis *Generate a private key* : un fichier `.pem`
   est téléchargé.
6. *Install App*, sur **mission-titar**, avec **Only select repositories** : `mulewatch` seul.
7. Rangez les deux valeurs dans les secrets Actions du dépôt, puis supprimez le `.pem` local :

    ```bash
    gh secret set AMULE_BUMP_APP_CLIENT_ID --repo mission-titar/mulewatch --body '<Client ID>'
    gh secret set AMULE_BUMP_APP_PRIVATE_KEY --repo mission-titar/mulewatch < chemin/vers/cle.pem
    ```

8. Vérifiez : *Actions*, *aMule bump*, *Run workflow*. Sans nouvelle release, le journal se termine
   par « is the latest release: nothing to do ».

## La liste de vérifications

La PR la porte, en anglais, à cocher avant de fusionner (source :
`packages/amule_bump/src/amule_bump/body.py`) :

- [ ] Read the changelog: anything that changes the daemon's defaults or the network behaviour?
- [ ] `options.cmake` diff: a new switch to set explicitly in the Dockerfile's CMake options?
- [ ] `REFERENCE.md` diff: does the `mule_api` adapter need a change (with its tests first)?
- [ ] `amule_config`: does any setting we override (or rely on the default of) change default?
- [ ] CI green on amd64 and arm64.
- [ ] Merge, then tag a release (`vX.Y.Z - aMule A.B.C`): the image only changes on a tag.

La dernière ligne compte : fusionner ne change que la branche `main`, l'image `latest` ne suit
qu'au prochain tag.

## Monter à la main

Sans l'App, ou pour une autre version que la dernière release, travaillez sur une branche.

**Vers la dernière release**, le module réécrit lui-même les deux `ARG` du Dockerfile :

```bash
uv run python -m amule_bump
```

Ajoutez `--dry-run` pour seulement afficher les deux lignes et la description de PR qu'il
produirait. Avec `GH_TOKEN="$(gh auth token)"` devant, les appels à l'API GitHub sont
authentifiés ; sans, ils sont limités à 60 par heure.

**Vers une autre version**, écrivez les deux `ARG` vous-même :

- `AMULE_VERSION` : le nom du tag amont, tel quel (par exemple `3.1.0`) ;
- `AMULE_COMMIT` : le commit sur lequel pointe ce tag, que donne cette commande :

    ```bash
    gh api repos/amule-org/amule/commits/<version> --jq .sha
    ```

Ouvrez ensuite la PR et cochez la liste ci-dessus.

Une erreur ne passe pas inaperçue : si les deux `ARG` ne correspondent pas, le build échoue sur
l'`ADD --checksum` du Dockerfile, que la version ou le commit soit faux.
