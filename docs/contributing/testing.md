---
description: "Lancer les suites de tests, leurs prérequis et ce qu'il faut attendre en sortie."
---

# Guide des tests : mulewatch

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
   uv run poe test      # les 3 suites unitaires seules, chacune dans son processus
   ```

   > La tâche `test` reste **par paquet** : elle lance `pytest` avec `cwd = packages/<pkg>` pour
   > chacun des 3 paquets, dans des processus séparés, afin de garder la coverage isolée. Un simple
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
| `api_integration` | crawler | L'adapter amuleapi (login, statut réseau, une recherche, get/set du port) face à un vrai démon | **Oui** (à lancer soi-même) | Un démon que vous fournissez, désigné par `MULEWATCH_TEST_API_HOST` (§3.0) | `( cd packages/crawler && uv run pytest -m api_integration --no-cov )` |
| `download_integration` | crawler | La mécanique du téléchargement (`start`, puis le fichier listé par `downloads`) face à un vrai démon | **Oui** (à lancer soi-même) | Le même démon que ci-dessus (§3.0) | `( cd packages/crawler && uv run pytest -m download_integration --no-cov )` |
| `orchestration_integration` | crawler | Une boucle de crawl complète (une recherche menée par les tâches, puis un arrêt borné) face à un vrai démon | **Oui** (à lancer soi-même) | Le même démon que ci-dessus (§3.0) | `( cd packages/crawler && uv run pytest -m orchestration_integration --no-cov )` |
| `compose_integration` | crawler | Smoke e2e de la pile docker compose assemblée (sans VPN) : câblage uniquement | **Oui** (compose v2) | docker compose v2 ; un build d'image | `( cd packages/crawler && uv run pytest -m compose_integration --no-cov )` |

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

Depuis le passage à amuleapi, le démon à lancer est **notre propre image** : amuleapi n'existe que
dans les versions récentes d'aMule, qu'aucune image tierce ne porte (la nôtre compile celle
qu'épingle `ARG AMULE_VERSION` dans `packages/crawler/Dockerfile`). Elle démarre ses trois
processus, donc les fichiers de config du smoke sont montés pour que le crawler reste debout.

| Variable | Requise | Défaut | Signification |
|---|---|---|---|
| `MULEWATCH_TEST_API_HOST` | **Oui** | aucun | Hôte d'amuleapi. **Absente, les trois suites sont ignorées (SKIP)**, avec un message qui reprend la commande ci-dessous. |
| `MULEWATCH_TEST_API_PORT` | Non | `4711` | Port HTTP d'amuleapi. |
| `MULEWATCH_TEST_API_PASSWORD` | Non | `indexer-api-test` | Mot de passe admin d'amuleapi (`AMULE_API_PASSWORD` du démon). |

Lancez un démon jetable, attendez qu'il réponde, lancez les suites, jetez-le :

```bash
docker run -d --rm --name mulewatch-test-amuled -p 4711:4711 \
    -e PUID="$(id -u)" -e PGID="$(id -g)" \
    -e AMULE_EC_PASSWORD=indexer-ec-test -e AMULE_API_PASSWORD=indexer-api-test \
    -v "$PWD/tests/smoke/crawler.yml:/app/config/crawler.yml:ro" \
    -v "$PWD/tests/smoke/targets.yml:/app/config/targets.yml:ro" \
    -v "$PWD/deploy/matcher.yml:/app/config/matcher.yml:ro" \
    ghcr.io/mission-titar/mulewatch:latest
until curl -fsS http://127.0.0.1:4711/api/v1/health >/dev/null; do sleep 2; done

export MULEWATCH_TEST_API_HOST=127.0.0.1
export MULEWATCH_TEST_API_PORT=4711
export MULEWATCH_TEST_API_PASSWORD=indexer-api-test
( cd packages/crawler && uv run pytest -m "api_integration or download_integration or orchestration_integration" --no-cov )

docker rm -f mulewatch-test-amuled
```

Le démon est à état (il persiste ses préférences et sa file de téléchargement dans son conteneur), si
bien que les suites ne sont fiablement répétables que face à un démon NEUF : recréez-le plutôt que de
réutiliser un conteneur de longue durée.

Ces trois suites n'ont **aucune exigence particulière sur le moteur Docker** : elles parlent à un
démon par un port publié, rien de plus. Elles ont été lancées avec succès sur la machine de
développement, sous Docker Desktop, le 2026-09-22 (9 tests). Toute note plus ancienne les déclarant
impossibles à lancer localement décrivait l'ancien montage `testcontainers`, qui démarrait son propre
conteneur et son propre Ryuk ; ce montage n'existe plus.

---

### 3.1 `api_integration` (crawler, **Docker requis**)

**Ce que ça prouve.** L'adapter parle à un **vrai démon** : le mot de passe admin écrit par
`amuleapi --set-admin-pass` ouvre bien une session, un mauvais mot de passe est refusé, le statut
réseau se décode, et une recherche ed2k (`search()`) va à son terme et rend ses résultats. Le
second fichier (`test_amuled_preferences.py`) valide le **get/set du port d'écoute** (port-sync
High-ID) : `get_listen_port()` lit un port plausible, et l'aller-retour `set -> get` renvoie la
valeur qui a été posée.

**Prérequis exacts.** Un démon lancé selon le **§3.0** et `MULEWATCH_TEST_API_HOST` exportée. Sans
elle, la suite est ignorée (elle n'échoue jamais sur une absence, et ne passe jamais silencieusement).

> Le conteneur éphémère **n'a aucun accès au réseau eD2k** : une recherche peut être refusée ou
> renvoyer des résultats vides. Les tests **le tolèrent explicitement** : ce qui est validé, c'est le
> **cycle requête/réponse**, pas la richesse des résultats.

**Commande.**
```bash
( cd packages/crawler && uv run pytest -m api_integration --no-cov )
```

**Attendu.** 6 tests passés (4 dans `test_amuled_api.py` + 2 dans `test_amuled_preferences.py`),
aucun skip. Une recherche refusée est tolérée en interne (le test passe quand même).

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

**Ce que ça prouve.** La pile `docker compose` **assemblée** (**un seul** service depuis le
2026-09-16, portant le crawler et amuled sous s6, amuled démarrant amuleapi) démarre et se câble
correctement. **Aucun octet de contenu n'est jamais téléchargé** (amuled n'a ni serveur eD2k ni
VPN ; seule son API est sollicitée). Quatre choses :
1. `docker compose build` réussit (l'image se construit) ;
2. le conteneur reste `Up`, devient **`healthy`**, `s6-svstat` signale les deux services up, la
   webui in-process répond à `/health` et amuleapi répond à `/api/v1/health` (interrogées via
   `docker compose exec`, donc aucun port hôte n'est nécessaire) ;
3. un fichier qu'amuled partage et qui a quitté sa file est enregistré `completed` par le crawler :
   le vrai chemin HTTP par loopback, face au vrai amuled de l'image livrée ;
4. les deux points d'entrée de déploiement se rendent avec `docker compose config`, et la topologie
   rendue est vérifiée : un service `mulewatch`, la pile VPN n'ajoutant que `gluetun`, **rien** qui
   subsiste de `crawler` / `amuled` / `docker-proxy`, et **aucun volume nommé** nulle part.

Le smoke sollicite **délibérément** le vrai chemin de propriété : l'état vit dans des **bind mounts**
sous un répertoire jetable `SMOKE_STATE` créé sous l'utilisateur appelant, dont les uid/gid propres
sont passés en `PUID`/`PGID`. Le PID 1 root du conteneur chowne ces points de montage et chaque
service redescend ensuite vers l'utilisateur `amule`. Une régression là-dessus se manifeste par
`unable to open database file`.

**Prérequis exacts.**
- **Docker** + **docker compose v2** (le test pilote `docker compose …` via `subprocess`).
- Les builds tournent **depuis la racine du dépôt** (le test fixe `cwd = racine du dépôt` et
  `--project-directory`).
- Chaque variable interpolée est **bouchonnée par le test lui-même** : les quatre que l'image exige
  absolument (`PUID`, `PGID`, `AMULE_EC_PASSWORD`, `AMULE_API_PASSWORD`, sans lesquelles le one-shot de
  démarrage sort en 1 et le conteneur meurt), plus celles de gluetun (`WIREGUARD_PRIVATE_KEY`,
  `SERVER_COUNTRIES`), que compose interpole au parse même quand gluetun ne fait pas partie de la
  pile. Les ports et le tag d'image sont écrits en dur dans les fichiers compose de `deploy/`, ils
  n'interpolent donc rien. **Rien à régler pour l'opérateur.**
- Fichiers compose utilisés : `tests/smoke/compose.yaml` (autonome) plus `deploy/compose.yml` et
  `deploy/gluetun.compose.yml` pour `test_entrypoint_config_renders` ; les configs du smoke vivent
  sous `tests/smoke/`.
- Le test n'importe **aucun** module `mulewatch` (cela préserve les 100 % de couverture de branches
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
    (vérifié en reconstruisant le commit précédent), donc ne le rediagnostiquez pas en bug de code.

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

**Attendu.** **7 tests passés** en local : les deux tests du harnais
(`test_the_harness_hides_everything_but_the_daemon_selectors`,
`test_a_chosen_daemon_that_answers_nothing_fails_the_call`), `test_build_succeeds`,
`test_one_container_supervises_the_two_services`,
`test_a_file_amuled_shares_is_recorded_completed`, et les 2 cas paramétrés de
`test_entrypoint_config_renders` (`compose` et `gluetun`). En CI l'image est préconstruite et
`IMAGE_TAG` est posée, si bien que `test_build_succeeds` est **ignoré** (6 passés, 1 skip) et que le
`up` réutilise l'image préconstruite. Le teardown est un `docker compose down -v` plus le répertoire
d'état jetable, dans un `finally`. Prévoyez plusieurs minutes (le build et le up tiennent sous des
timeouts de 900 s).

> **Jamais exécutée.** Au 2026-09-16 il n'y a aucun runtime de conteneurs sur la machine de
> développement : cette suite (et l'image qu'elle construit) n'a donc pas été lancée une seule fois.
> Voir le
> [handoff mono-conteneur](https://github.com/mission-titar/mulewatch/blob/main/agents/handoffs/2026-09-16%20-%20handoff%20-%20single%20container%20with%20embedded%20aMule.md),
> section 5.

---

## 4. Prérequis machine (récapitulatif installable)

Pour pouvoir lancer **toutes** les suites :

- **Docker** + **docker compose v2**. Les trois suites qui parlent au démon en veulent un que
  **vous** lancez (§3.0, notre propre image) ; la suite compose pilote `docker compose` directement.
- Un **`.env`** (copié depuis `deploy/.env.example`) pour les commandes compose **manuelles** :
  `WIREGUARD_PRIVATE_KEY`, `SERVER_COUNTRIES`, `AMULE_EC_PASSWORD`. À noter que le test
  `compose_integration` **les bouchonne lui-même**, donc le `.env` n'est pas requis pour le lancer.

---

## 5. Intégration CI

Déjà en CI :

- `.github/workflows/validate.yml` est le **gate** réutilisable, appelé par `pr.yml` (sur les pull
  requests) et par `release.yml` (sur un push de tag). Ses jobs :
  - `lint` : `uv run poe lint-all` (ruff, format, mypy, sqlfluff, vérification des templates) ;
  - `test` : `uv run poe test` (les 3 suites unitaires par paquet, 100 % de branches chacune) ;
  - `build-and-verify` : un job **par architecture sur son runner natif** (`amd64` sur
    `ubuntu-latest`, `arm64` sur `ubuntu-24.04-arm`). Chacun construit l'image du crawler puis lance
    **`compose_integration`** contre cette image construite localement (`IMAGE_TAG=ci-<sha>`), puis
    démarre un conteneur jetable depuis cette même image et lance contre lui **`api_integration`,
    `download_integration` et `orchestration_integration`** en un seul appel pytest. Le Docker du
    runner sait créer un veth, donc la défaillance réseau qui bloque ces suites sur certaines
    machines de développement ne s'applique pas ;
  - `gate` : l'unique check d'agrégation exigé par la protection de branche.
- `.github/workflows/pr.yml` lance aussi le job `vex-checks` (`poe vex-source-claims` +
  `poe vex-claim-coverage`).
- `.github/workflows/grype-scan.yml` scanne quotidiennement l'image publiée et remonte dans Code
  scanning.

Tous les markers tournent désormais en CI. Les seules suites encore absentes sont celles appartenant
aux autres paquets (voir leurs sections ci-dessus).

Ces trois suites tournent désormais sur **les deux arches**, puisqu'elles vivent dans
`build-and-verify` : amuleapi n'existant que dans notre image, le démon qu'elles interrogent est
celui que le job vient de construire, et il n'y a plus de raison de les cantonner à `ubuntu-latest`.

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
