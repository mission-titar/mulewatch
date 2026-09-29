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
2. résout le tag en son commit **pelé** (le commit, pas l'objet tag annoté) ;
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

La PR la porte, en anglais, à cocher avant de fusionner (source : `packages/amule_bump/src/amule_bump/body.py`) :

- [ ] Read the changelog: anything that changes the daemon's defaults or the network behaviour?
- [ ] `options.cmake` diff: a new switch to set explicitly in the Dockerfile's CMake options?
- [ ] `REFERENCE.md` diff: does the `mule_api` adapter need a change (with its tests first)?
- [ ] `amule-config.py`: does any setting we override (or rely on the default of) change default?
- [ ] CI green on amd64 and arm64.
- [ ] Merge, then tag a release (`vX.Y.Z - aMule A.B.C`): the image only changes on a tag.

La dernière ligne compte : fusionner ne change que la branche `main`, l'image `latest` ne suit
qu'au prochain tag.

## Monter à la main

Pour une autre version que la dernière release (ou sans l'App), sur une branche :

1. Trouvez le commit pelé du tag voulu :

    ```bash
    git ls-remote https://github.com/amule-org/amule.git 'refs/tags/<version>^{}'
    ```

    Sans ligne en retour, le tag est léger : prenez alors celle de `refs/tags/<version>`.

2. Réécrivez les deux `ARG` : `AMULE_VERSION` est le **nom exact du tag**, `AMULE_COMMIT` le commit
   obtenu. Ne prenez pas l'objet tag annoté que renvoie `refs/tags/<version>` sans `^{}` : le
   checksum ne correspondrait jamais.
3. Ouvrez la PR et déroulez la liste ci-dessus.

Pour la dernière release, le module fait les deux premières étapes. Cette commande affiche les deux
lignes réécrites et la description de PR sans rien écrire ; sans `--dry-run`, elle réécrit le
Dockerfile en place et écrit la description dans un fichier temporaire :

```bash
GH_TOKEN="$(gh auth token)" uv run python -m amule_bump --dry-run
```

`GH_TOKEN` est facultatif : sans lui, l'API GitHub limite à 60 requêtes par heure.
`--dockerfile <chemin>` fait travailler le module sur une copie du Dockerfile.

Une erreur se voit au build : un commit qui n'est pas celui du tag fait échouer l'`ADD
--checksum`, et une version montée sans son commit aussi, puisque le tag pointe alors ailleurs.
