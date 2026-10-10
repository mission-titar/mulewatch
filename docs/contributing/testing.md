---
description: "Lancer les suites de tests, leurs prérequis et ce qu'il faut attendre en sortie."
---

# Guide des tests : p2pwatch

Ce guide décrit **comment lancer les suites d'intégration** (les lourdes, désélectionnées par
défaut), leurs **prérequis exacts** et **ce qu'il faut attendre** en sortie. Il complète
[Installer un nœud](../install.md) : cette page-là explique comment faire tourner un nœud, celle-ci
explique comment le **valider**.

Public : **dev local et CI**. Pas pour les opérateurs (qui n'ont aucune raison de lancer les suites
de tests). Tout ce qui suit est **extrait du code réel** (fichiers de tests, `pyproject.toml`,
fichiers compose). Lorsqu'un prérequis ne peut pas être vérifié dans le code, il est marqué
« à confirmer ».

> **La suite e2e « transfert réel » a été abandonnée** (et son échafaudage supprimé du dépôt). La
> raison : faire en sorte qu'un vrai `amuled` signale un téléchargement terminé impliquerait
> d'orchestrer et de rétro-concevoir des outils tiers (`amuled`, `ed2kd`), ce qui valide surtout le
> comportement de tiers de confiance plutôt que notre code (le même argument que pour la couche de
> port-forwarding gluetun). La détection de complétion reste couverte par des **tests unitaires**,
> plus les contraintes de déploiement documentées dans
> `agents/reference/2026-06-17-amuled-completion-behavior.md`.

---

## 1. Vue d'ensemble : la pyramide de tests

Le projet a **deux niveaux** :

1. **Le gate unitaire** (lancé par défaut, **100 % de couverture de branches** imposée). C'est ce que
   vérifient le hook pre-push et la CI, via une source de vérité unique dans `pyproject.toml`
   (`[tool.poe.tasks]`) :

   ```bash
   uv run poe check     # le gate complet : lint-all + test (ce que lancent pre-push et la CI)
   uv run poe test      # les 5 suites unitaires seules, chacune dans son processus
   ```

   > La tâche `test` reste **par paquet** : elle lance `pytest` avec `cwd = packages/<pkg>` pour
   > chacun des 5 paquets, dans des processus séparés, afin de garder la coverage isolée. Un simple
   > `uv run pytest` depuis la racine du dépôt n'est **pas** le gate (la racine n'a pas de config
   > pytest, et un `conftest.py` racine neutralise la collecte, donnant `exit 5`).

   L'`addopts` de chaque paquet **désélectionne** tous les markers d'intégration
   (`-m "not api_integration and not …"`), si bien que le gate ne les lance jamais, et ils sont exclus
   de la mesure de coverage.

2. **Les suites d'intégration** (désélectionnées par défaut, lancées **à la demande**). Chacune porte
   un **marker** pytest. Lancez-les une à la fois avec `--no-cov` (sinon le seuil de 100 % valable
   pour tout le paquet fait « échouer » un run ciblé même quand les tests passent) :

   ```bash
   ( cd packages/<pkg> && uv run pytest -m <marker> --no-cov )
   ```

   Ces suites ont besoin de ressources externes (Docker). **Elles ne tournent pas dans un bac à sable
   sans accès réseau complet ni Docker** : lancez-les sur une vraie machine. Sur une vraie machine,
   elles tournent bel et bien, Docker Desktop compris : les trois suites qui parlent au démon
   prennent celui que vous leur fournissez (§3.0) et `compose_integration` pilote `docker compose`.
   La seule réserve porte sur un test de `compose_integration`, et elle tient au moteur : voir
   [§3.4](#34-compose_integration--la-pile-smoke-crawler-docker--compose-v2-requis).

---

## 2. Récapitulatif des markers

| Marker | Paquet | Ce qu'il valide | Docker ? | Autres prérequis | Commande |
|---|---|---|---|---|---|
| `api_integration` | crawler | L'adapter amuleapi (login, statut réseau, une recherche) face à un vrai démon | **Oui** (à lancer soi-même) | Un démon que vous fournissez, désigné par `P2PWATCH_TEST_API_HOST` (§3.0) | `( cd packages/crawler && uv run pytest -m api_integration --no-cov )` |
| `download_integration` | crawler | La mécanique du téléchargement (`start`, puis le fichier listé par `downloads`) face à un vrai démon | **Oui** (à lancer soi-même) | Le même démon que ci-dessus (§3.0) | `( cd packages/crawler && uv run pytest -m download_integration --no-cov )` |
| `orchestration_integration` | crawler | Une boucle de crawl complète (une recherche menée par les tâches, puis un arrêt borné) face à un vrai démon | **Oui** (à lancer soi-même) | Le même démon que ci-dessus (§3.0) | `( cd packages/crawler && uv run pytest -m orchestration_integration --no-cov )` |
| `compose_integration` | crawler | Smoke e2e des deux conteneurs assemblés (sans VPN), le port-sync, et le rendu des deux variantes de `deploy/` | **Oui** (compose v2) | docker compose v2 ; le build des deux images ; un utilisateur non root | `( cd packages/crawler && uv run pytest -m compose_integration --no-cov )` |

---

## 3. Une section par marker (de la plus légère à la plus lourde)

### 3.0 Le démon dont les trois suites ont besoin (à lancer soi-même)

`api_integration`, `download_integration` et `orchestration_integration` parlent toutes au MÊME
démon, et **aucune ne le démarre** : l'appelant en fournit un et y pointe les suites via trois
variables d'environnement. Elles démarraient autrefois leur propre conteneur via `testcontainers`,
ce qui est inutilisable sur les hôtes où Docker ne peut pas créer de paire veth sur son réseau
`bridge` par défaut (le mode de défaillance rencontré pendant des mois : `failed to add the host
(veth...) <=> sandbox (veth...) pair interfaces: operation not supported`). Un simple `docker run`
avec un port publié fonctionne partout.

Le démon à lancer est **notre propre image d'aMule**, `p2pwatch-amule` : amuleapi n'existe que
dans les versions récentes d'aMule, qu'aucune image tierce ne porte (la nôtre compile celle
qu'épingle `ARG AMULE_VERSION` dans `packages/amule/Dockerfile`). Elle ne contient pas de crawler :
elle démarre amuled, qui démarre amuleapi, et laisse le port-sync arrêté. Aucun fichier de config
n'est à monter.

| Variable | Requise | Défaut | Signification |
|---|---|---|---|
| `P2PWATCH_TEST_API_HOST` | **Oui** | aucun | Hôte d'amuleapi. **Absente, les trois suites sont ignorées (SKIP)**, avec un message qui reprend la commande ci-dessous. |
| `P2PWATCH_TEST_API_PORT` | Non | `4711` | Port HTTP d'amuleapi. |
| `P2PWATCH_TEST_API_PASSWORD` | Non | `indexer-api-test` | Mot de passe admin d'amuleapi (`AMULE_API_PASSWORD` du démon). |

Construisez l'image depuis l'arbre, lancez un démon jetable, attendez qu'il réponde, lancez les
suites, jetez-le :

```bash
docker build -f packages/amule/Dockerfile -t p2pwatch-amule:dev .
docker run -d --rm --name p2pwatch-test-amuled -p 4711:4711 \
    -e PUID="$(id -u)" -e PGID="$(id -g)" \
    -e AMULE_EC_PASSWORD=indexer-ec-test -e AMULE_API_PASSWORD=indexer-api-test \
    p2pwatch-amule:dev
until curl -fsS http://127.0.0.1:4711/api/v1/health >/dev/null; do sleep 2; done

export P2PWATCH_TEST_API_HOST=127.0.0.1
export P2PWATCH_TEST_API_PORT=4711
export P2PWATCH_TEST_API_PASSWORD=indexer-api-test
( cd packages/crawler && uv run pytest -m "api_integration or download_integration or orchestration_integration" --no-cov )

docker rm -f p2pwatch-test-amuled
```

Le démon est à état (il persiste ses préférences et sa file de téléchargement dans son conteneur), si
bien que les suites ne sont fiablement répétables que face à un démon NEUF : recréez-le plutôt que de
réutiliser un conteneur de longue durée.

Ces trois suites n'ont **aucune exigence particulière sur le moteur Docker** : elles parlent à un
démon par un port publié, rien de plus. Elles ont été lancées avec succès sur la machine de
développement, sous Docker Desktop, contre l'image `p2pwatch-amule`, le 2026-10-11 (6 tests). Toute note plus ancienne les déclarant
impossibles à lancer localement décrivait l'ancien montage `testcontainers`, qui démarrait son propre
conteneur et son propre Ryuk ; ce montage n'existe plus.

---

### 3.1 `api_integration` (crawler, **Docker requis**)

**Ce que ça prouve.** L'adapter parle à un **vrai démon** : le mot de passe admin écrit par
`amuleapi --set-admin-pass` ouvre bien une session, un mauvais mot de passe est refusé, le statut
réseau se décode, et une recherche ed2k (`search()`) va à son terme et rend ses résultats.

**Prérequis exacts.** Un démon lancé selon le **§3.0** et `P2PWATCH_TEST_API_HOST` exportée. Sans
elle, la suite est ignorée (elle n'échoue jamais sur une absence, et ne passe jamais silencieusement).

> Le conteneur éphémère **n'a aucun accès au réseau eD2k** : une recherche peut être refusée ou
> renvoyer des résultats vides. Les tests **le tolèrent explicitement** : ce qui est validé, c'est le
> **cycle requête/réponse**, pas la richesse des résultats.

**Commande.**
```bash
( cd packages/crawler && uv run pytest -m api_integration --no-cov )
```

**Attendu.** 4 tests passés (`test_amuled_api.py`), aucun skip. Une recherche refusée est tolérée en interne (le test passe quand même).

---

### 3.2 `download_integration` (crawler, **Docker requis**)

**Ce que ça prouve.** La mécanique du téléchargement face à un vrai démon : `start` est accepté
et le fichier apparaît dans `downloads` avec des compteurs lisibles. Un hash et une taille
réalistes (~700 MiB) sont employés, **jamais** le MD4 du fichier vide (qu'amuled traite comme
instantanément complet et ne liste pas). C'est aussi ce qui prouve que `?status=all` est bien
demandé : sans lui, la file ne montre que ce qui transfère.

**Prérequis exacts.** Les mêmes que `api_integration` (§3.0).

**Commande.**
```bash
( cd packages/crawler && uv run pytest -m download_integration --no-cov )
```

**Attendu.** 1 test passé (`test_start_then_appears_in_downloads`). Une complétion réelle n'est pas
atteignable (pas de sources eD2k) : seul le cycle start, liste, statut est validé.

---

### 3.3 `orchestration_integration` (crawler, **Docker requis**)

**Ce que ça prouve.** Une vraie `CrawlerApp` (vrai `AmuleApiClient` + vraies bases SQLite sur
`tmp_path`) déroule **une recherche complète** face à l'`amuled` fourni puis **s'arrête
proprement** dans un `wait_for` de 180 s. L'assertion clé : une recherche est revenue, et l'arrêt
n'est demandé que quand sa tâche revient chercher, une fois cette recherche enregistrée. Un démon
fraîchement lancé refuse les recherches eD2k (il n'est connecté à aucun serveur) mais accepte
celles de Kad : c'est elle qui revient.

**Prérequis exacts.** Les mêmes que `api_integration` (§3.0). Le test charge la config du matcher
depuis la source de vérité unique, `deploy/matcher.yml`, et tourne avec la webui désactivée (son bind
est un `0.0.0.0:8080` fixe, qui entrerait en collision avec ce qui écoute déjà là).

**Commande.**
```bash
( cd packages/crawler && uv run pytest -m orchestration_integration --no-cov )
```

**Attendu.** 1 test passé (`test_real_loop_runs_one_search_and_stops`). Les résultats de recherche
peuvent tout à fait être vides : ce qui est validé, c'est la **boucle** (démarrage, recherche, mise
au catalogue, arrêt borné).

---

### 3.4 `compose_integration` : la pile smoke (crawler, **Docker + compose v2 requis**)

**Ce que ça prouve.** Les **deux conteneurs** du nœud, construits depuis l'arbre et assemblés par
`tests/smoke/compose.yaml` : `ed2k` (l'image `p2pwatch-amule`, amuled et le port-sync sous s6) et
`p2pwatch` (le cœur, sous `user: PUID:PGID`, `/downloads` en lecture seule). **Aucun octet de
contenu n'est jamais téléchargé** (amuled n'a ni serveur eD2k ni VPN ; seule son API est
sollicitée). Cinq choses :

1. `docker compose build` réussit (les deux images se construisent) ;
2. les deux conteneurs tournent, `ed2k` devient **`healthy`**, le cœur joint amuleapi à
   `ed2k:4711`, sa webui répond à `/health` et son tableau de bord montre une lecture d'`amuled`
   (interrogés via `docker compose exec`, donc aucun port hôte n'est nécessaire), et
   `data/catalog.db` appartient à `PUID`, lu dans le conteneur ;
3. le port-sync reste arrêté sans `PORT_SYNC` (`false false false` pour `up`, `wantedup` et
   `normallyup`, depuis 25 s au moins), puis, une fois lancé, ses droits laissent l'utilisateur
   `amule` arrêter et relancer amuled par `s6-svc -wD -d` et `-wu -u`, avec un nouveau pid ;
4. un fichier qu'amuled partage et qui a quitté sa file est enregistré `completed` par le cœur,
   par le vrai chemin HTTP vers `ed2k:4711` ;
5. `deploy/` se rend avec `docker compose config` dans ses deux variantes : services, alias,
   `network_mode`, `PORT_SYNC`, montages d'`ed2k` sous `ed2k/`, **aucun volume nommé**.

Le smoke sollicite **délibérément** le vrai chemin de propriété : l'état vit dans des **bind mounts**
sous un répertoire jetable `SMOKE_STATE` créé sous l'utilisateur appelant, dont les uid/gid propres
sont passés en `PUID`/`PGID`. Le PID 1 root d'`ed2k` chowne ses points de montage et chaque service
redescend ensuite vers l'utilisateur `amule` ; le cœur tourne sous `PUID:PGID` dès le départ. Une
régression là-dessus se manifeste par `unable to open database file`. La suite **refuse de tourner
en root** : un `PUID` à 0 ferait passer la vérification de propriété sur un fichier de root.

**Prérequis exacts.**
- **Docker** + **docker compose v2** (le test pilote `docker compose …` via `subprocess`).
- Un utilisateur **non root**.
- Les builds tournent **depuis la racine du dépôt** (le test fixe `cwd = racine du dépôt` et
  `--project-directory`).
- Chaque variable interpolée est **bouchonnée par le test lui-même** : `PUID`, `PGID`,
  `AMULE_EC_PASSWORD`, `AMULE_API_PASSWORD` et `SMOKE_STATE` pour la pile smoke. Le test de rendu
  copie les fichiers compose de `deploy/` dans un répertoire temporaire et y écrit son propre
  `.env`, aux valeurs qu'aucun défaut ne produit (`PUID=4242`) : sans `PUID` dans l'environnement
  du processus, `ed2k` ne peut le tenir que du `.env` du projet parent. Il n'écrit jamais
  `deploy/.env`, qui porte les secrets d'un développeur. **Rien à régler pour l'opérateur.**
- Le test n'importe **aucun** module `p2pwatch` (cela préserve les 100 % de couverture de branches
  du paquet).
- **Un moteur dont les montages liés sont de vrais montages du noyau.** C'est la seule exigence qui
  ne saute pas aux yeux, et elle porte sur un test : voir l'encadré ci-dessous.

**Commande.**
```bash
( cd packages/crawler && uv run pytest -m compose_integration --no-cov )
```

!!! warning "Sous Docker Desktop, un test échoue, et ce n'est pas un bug du code"

    `test_a_file_amuled_shares_is_recorded_completed` échoue sous Docker Desktop sur Linux, et
    seulement là. L'état du smoke vit dans un montage lié, que Desktop fait passer par sa machine
    virtuelle : ce montage médié ne garde pas le fichier `-shm` de SQLite cohérent d'un processus à
    l'autre. Le crawler estampille bien la complétion (sa propre connexion la voit, il ne la rejoue
    jamais) et **aucun autre processus ne la lit** : le test, qui relit la base depuis un second
    processus, voit une ligne restée `downloading`. Mesuré le 2026-09-22 : la même scène passe dès
    que `/data` n'est plus un montage lié, et elle passe aussi sur le moteur Docker natif avec un
    vrai montage lié du noyau. Le même test échouait déjà avant la migration vers amuleapi
    (vérifié en reconstruisant le commit précédent), et de nouveau le 2026-10-11 sur les deux
    conteneurs, tous les autres passant : ne le rediagnostiquez pas en bug de code. La CI fait foi.

    **L'échappatoire**, si votre machine fait tourner les deux : `DOCKER_CONTEXT` et `DOCKER_HOST`
    sont transmis à la CLI par le harnais (les deux seules variables de votre environnement qui le
    sont), donc un run se pointe sur un autre moteur sans rien modifier :

    ```bash
    ( cd packages/crawler && DOCKER_CONTEXT=default uv run pytest -m compose_integration --no-cov )
    ```

    Cela suppose un noyau hôte où `veth` est disponible : si le module manque (typiquement après
    une mise à jour du noyau tant que la machine n'a pas redémarré), le démon natif ne sait plus
    créer de réseau et `docker run` échoue sur
    `failed to add the host (veth…) <=> sandbox (veth…) pair interfaces: operation not supported`.

**Attendu.** **8 tests passés** sur un moteur aux vrais montages du noyau : les deux tests du
harnais (`test_the_harness_hides_everything_but_the_daemon_selectors`,
`test_a_chosen_daemon_that_answers_nothing_fails_the_call`), `test_build_succeeds`,
`test_the_two_containers_work_together`,
`test_port_sync_stays_down_then_its_rights_let_amule_restart_amuled`,
`test_a_file_amuled_shares_is_recorded_completed`, et les 2 cas paramétrés de
`test_the_deploy_layout_renders` (`direct` et `vpn`). Sous Docker Desktop : 7 passés, 1 échec
(l'encadré ci-dessus). En CI les images sont préconstruites et `IMAGE_TAG` est posée, si bien que
`test_build_succeeds` est **ignoré** (7 passés, 1 skip) et que le `up` réutilise les images
préconstruites. Le teardown est un `docker compose down -v` plus le répertoire d'état jetable, dans
un `finally`. Prévoyez plusieurs minutes (le build et le up tiennent sous des timeouts de 1 800 s).

---

## 4. Prérequis machine (récapitulatif installable)

Pour pouvoir lancer **toutes** les suites :

- **Docker** + **docker compose v2**. Les trois suites qui parlent au démon en veulent un que
  **vous** lancez (§3.0, notre image d'aMule) ; la suite compose pilote `docker compose`
  directement.
- Un **`.env`** (copié depuis `deploy/.env.example`) pour les commandes compose **manuelles** :
  `PUID`, `PGID`, `AMULE_EC_PASSWORD`, `AMULE_API_PASSWORD`, plus `WIREGUARD_PRIVATE_KEY` pour la
  variante VPN. Le test `compose_integration` **les bouchonne lui-même**, donc le `.env` n'est pas
  requis pour le lancer.

---

## 5. Intégration CI

Déjà en CI :

- `.github/workflows/validate.yml` est le **gate** réutilisable, appelé par `pr.yml` (sur les pull
  requests) et par `release.yml` (sur un push de tag). Ses jobs :
  - `lint` : `uv run poe lint-all` (ruff, format, mypy, sqlfluff, vérification des templates) ;
  - `test` : `uv run poe test` (les 5 suites unitaires par paquet, 100 % de branches chacune) ;
  - `build-and-verify` : un job **par architecture sur son runner natif** (`amd64` sur
    `ubuntu-latest`, `arm64` sur `ubuntu-24.04-arm`). Chacun construit les **deux images**, le cœur
    et `p2pwatch-amule` (caches `crawler-<arch>` et `amule-<arch>`). Sur l'image d'aMule, il vérifie
    la version que rapportent `amuled` et `amuleapi` et la présence de `pkg:generic/amule` à la
    version épinglée dans son SBOM. Il lance ensuite **`compose_integration`** contre les deux
    images construites localement (`IMAGE_TAG=ci-<sha>`), puis démarre un conteneur jetable de
    `p2pwatch-amule` et lance contre lui **`api_integration`, `download_integration` et
    `orchestration_integration`** en un seul appel pytest. Il dépose enfin un digest par image
    (`digest-crawler-<arch>`, `digest-amule-<arch>`), que `release.yml` assemble. Le Docker du
    runner sait créer un veth, donc la défaillance réseau qui bloque ces suites sur certaines
    machines de développement ne s'applique pas ;
  - `gate` : l'unique check d'agrégation exigé par la protection de branche.
- `.github/workflows/pr.yml` lance aussi le job `vex-checks` (`poe vex-source-claims` +
  `poe vex-claim-coverage`).
- `.github/workflows/grype-scan.yml` scanne quotidiennement les deux images publiées, chacune avec
  sa VEX, et remonte dans Code scanning.

Tous les markers tournent désormais en CI. Les seules suites encore absentes sont celles appartenant
aux autres paquets (voir leurs sections ci-dessus).

Ces trois suites tournent désormais sur **les deux arches**, puisqu'elles vivent dans
`build-and-verify` : amuleapi n'existant que dans notre image, le démon qu'elles interrogent est
l'image `p2pwatch-amule` que le job vient de construire, et il n'y a plus de raison de les cantonner à `ubuntu-latest`.

---

## 6. Outils de diagnostic (mesure, dev)

Outils ponctuels destinés au **développeur** (mesure et diagnostic, pas exploitation) :

- **Lire l'API à la main.** Les sondes maison (`ec_probe`, `download_probe`) sont parties avec
  l'adapter EC : `curl` sur `/api/v1/*` les remplace, et amuleapi sert sa propre interface web sur le
  port 4711. Un jeton s'obtient avec
  `curl -sX POST 'http://127.0.0.1:4711/api/v1/auth/login?include_token=true' -H 'Content-Type:
  application/json' -d '{"password":"…"}'`, puis se présente en `Authorization: Bearer`. Pensez à
  `limit=1000000000` sur les routes de liste : sans lui, elles ne rendent que les 100 premières
  lignes.

---

## 7. Voir aussi

- [Installer un nœud](../install.md) : pour déployer et faire tourner un nœud.
- [Architecture du code](architecture.md) : comment le crawler fonctionne à l'intérieur.
