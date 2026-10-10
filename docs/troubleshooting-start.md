---
description: "Une fiche par symptôme d'installation : symptôme, cause, solution, dans l'ordre du guide d'installation."
---

# Le déploiement bloque

Une fiche par symptôme d'installation, dans l'ordre des Points de contrôle du
[guide d'installation](install.md). Pour le téléchargement, le High-ID, le stockage et la
récupération après panne, voir [Diagnostics avancés](troubleshooting.md).

!!! info "Où lancer ces commandes"

    Depuis votre dossier de travail, celui qui contient `compose.yml`. Les chemins sont donc
    relatifs : `.env`, `crawler.yml`, `data/`. Sous la pile VPN, ajoutez `-f gluetun.compose.yml` à
    chaque `docker compose ...`.

### Docker introuvable, ou installé mais sans réponse

- **Symptôme.** `docker compose version` répond `command not found`, ou affiche une version `1.x` du
  vieil outil `docker-compose` (avec un tiret). Ou bien il répond correctement, mais une commande
  qui interroge le moteur (`docker ps`, `docker compose up -d`) échoue sur
  `Cannot connect to the Docker daemon`, sous Windows `error during connect ...`.
- **Cause.** Dans le premier cas Docker n'est pas installé, ou votre distribution ne fournit que
  l'ancien `docker-compose` v1. Dans le second, le moteur n'est pas démarré :
  `docker compose version` est une commande côté client, elle répond même moteur éteint.
- **Solution.** Installez Docker **depuis la documentation officielle**, jamais depuis un tutoriel
  tiers : <https://docs.docker.com/get-started/get-docker/>. Puis lancez Docker Desktop et attendez
  qu'il s'annonce prêt (Windows, macOS), ou démarrez le service (`sudo systemctl start docker` sous
  Linux). `docker ps` doit alors répondre par un tableau, même vide.

### Une variable obligatoire manque

- **Symptôme.** `docker compose up -d` refuse de démarrer quoi que ce soit :
  ```
  error while interpolating services.p2pwatch.environment.PUID: required variable "PUID" is not set
  ```
  Idem pour `PGID`, `AMULE_EC_PASSWORD` et `AMULE_API_PASSWORD`. Variante : la variable est
  déclarée mais vide, et le conteneur sort en moins d'une seconde sur une seule ligne de journal,
  `PUID is required`. **Le signe distinctif est l'absence de traceback Python** : rien de Python
  n'a démarré. Si vous en voyez une, lisez plutôt
  [« Un conteneur redémarre en boucle »](#un-conteneur-redémarre-en-boucle).
- **Cause.** Les quatre variables sont strictement obligatoires : le one-shot de démarrage les lit
  avant tout le reste, pour créer l'utilisateur du conteneur, prendre possession des dossiers montés
  et écrire les deux mots de passe aMule. Il sort en 1 si l'une manque ou est vide.
- **Solution.** Renseignez les quatre dans `.env` (copiez `.env.example` si ce n'est pas déjà fait),
  puis `docker compose up -d`. Le détail de chacune est au
  [tableau de l'étape 3](install.md#3-choisir-vos-mots-de-passe).

??? question "Cas voisin : un `change-me` oublié"

    Si `AMULE_EC_PASSWORD` ou `AMULE_API_PASSWORD` est resté à sa valeur d'exemple, votre nœud
    démarre avec un mot de passe public. Vérifiez-le ainsi : la commande ne doit **rien** afficher :

    ```bash
    grep -E '^(AMULE_EC_PASSWORD|AMULE_API_PASSWORD)=change-me' .env
    ```

    Le `change-me` restant sur `WIREGUARD_PRIVATE_KEY` est normal, il ne sert qu'au VPN.

    Changer l'un des deux sur un nœud **déjà lancé** demande un redémarrage du nœud, pas seulement
    une édition : voir
    [« J'ai perdu `AMULE_EC_PASSWORD` »](troubleshooting.md#jai-perdu--je-ne-me-souviens-plus-de-amule_ec_password).

### Un conteneur redémarre en boucle

- **Symptôme.** `docker compose ps` montre le service en **`Restarting`** ou `Exited`.
- **Diagnostic.** Il n'y a qu'un seul service, `p2pwatch` (plus `gluetun` sous la pile VPN) : la
  question n'est pas « quel conteneur ? » mais **lequel des processus a échoué**. Lisez
  `docker compose logs p2pwatch` : le journal est entrelacé, amuled et le crawler y écrivent tous
  les deux, repérez qui parle en dernier. Seul un arrêt non nul du crawler couche le conteneur ; si
  amuled tombe, s6 le relance sur place et le conteneur reste `Up`. amuleapi, démarré par amuled,
  n'écrit pas là mais dans `amule/amuleapi.log`.
- **Causes fréquentes.**
    - **Configuration invalide.** Le journal finit par `Invalid config, refusing to start: ...`.
      Corrigez `crawler.yml`, `targets.yml` ou `matcher.yml`, puis `docker compose up -d`. Vous
      pouvez valider les trois sans rien démarrer, voir
      [« Valider la configuration »](troubleshooting.md#valider-la-configuration-sans-rien-démarrer).
    - **Mot de passe EC refusé** (`EcAuthError`) : voir le cas `change-me` ci-dessus.
    - **Journal d'une ligne, sans Python** : voir
      [« Une variable obligatoire manque »](#une-variable-obligatoire-manque).

!!! bug "`database or disk is full`, juste après une montée d'image"

    Le journal du crawler contient `MigrationError: migration N failed: database or disk is full`.
    Un disque s'est rempli pendant la migration : celui de Docker, qui reçoit ses fichiers
    temporaires (voir [Limites connues](limits.md)), ou celui de `data/`, où le catalogue grandit le
    temps d'être réécrit. La migration a été annulée : le catalogue est intact. Vérifiez la place
    libre sur les deux, celui de Docker mesuré depuis un conteneur :

    ```bash
    df -h data
    docker run --rm --entrypoint df ghcr.io/mission-titar/p2pwatch:latest -h /tmp
    ```

    Sous Docker Desktop, ce second chiffre est celui de l'image disque de sa machine virtuelle, qui
    ne grandit que tant que le disque de l'hôte qui la porte a de la place : vérifiez aussi ce
    disque-là. Son emplacement est affiché dans *Settings*, *Resources*, *Advanced*.

    Remède : libérez de la place sur le disque plein (`docker image prune` retire les images
    inutilisées), puis `docker compose up -d`. La migration repart de zéro et, une fois passée, ne
    se rejoue plus.

!!! bug "`file_observation_ranges holds rows`, juste après une montée d'image"

    Le journal du crawler contient `MigrationError: migration 6 failed: file_observation_ranges
    holds rows: a compacted day cannot become observations`. Votre catalogue a été compacté par
    l'ancien outil `python -m mulewatch.compact`, qui n'existe plus. Une journée compactée ne garde
    ni le mot-clé, ni la durée, ni les métadonnées brutes : elle ne peut pas devenir des
    observations, et la migration refuse plutôt que d'inventer ce qui manque. Elle a été annulée :
    le catalogue est intact.

    Remède : épinglez votre ancienne image (`ghcr.io/mission-titar/mulewatch:4.1.0`) dans
    `base.compose.yml`, puis `docker compose up -d`. Ouvrez ensuite un ticket sur
    <https://github.com/mission-titar/p2pwatch/issues> avec cette ligne de journal et le résultat
    de `SELECT COUNT(*) FROM file_observation_ranges` dans la console SQL : la conversion s'écrira
    sur vos données.

### Le port est déjà pris

- **Symptôme.** `docker compose up -d` s'arrête sur `bind: address already in use`. Le numéro dans
  le message dit lequel : `8080` (le catalogue), `4711` (l'interface d'aMule) ou `4662` (le port
  eMule, publié par la pile directe seulement).
- **Cause.** Un autre programme occupe déjà ce port sur votre machine.
- **Solution.** Les ports ne sont pas des variables, ils sont écrits en clair dans le `ports:` de
  votre pile. Donnez au port concerné une valeur libre **du côté gauche**, par exemple
  `"8090:8080"`, puis `docker compose up -d`, et pensez à ouvrir la nouvelle adresse. La marche à
  suivre est détaillée dans [Régler le nœud, § Changer un port](settings.md#changer-un-port).

### amuled ne se connecte à rien

- **Symptôme.** Le crawler tourne (lignes `verdict(s) changed`) mais signale au bout de 5 minutes
  `amuled ed2k: off its network for 5 min` (ou `kad`), avec des avertissements d'injoignabilité.

!!! tip "D'abord, patientez : au premier démarrage, c'est attendu"

    amuled amorce seul sa liste de serveurs eD2k et de nœuds Kad par DNS et HTTPS sortant, ce qui
    prend 1 à 3 minutes, sous le délai de 5 minutes avant l'alerte. Une alerte qui tombe quand même
    se résorbe toute seule (`back on its network`) ; vous n'avez aucun serveur à ajouter.

    Un message **Low-ID** n'est pas non plus une panne : c'est l'état normal par défaut, recherche,
    catalogage et téléchargement fonctionnent, seule la joignabilité est sous-optimale. Voir
    [Devenir High-ID](high-id.md).

- **Si cela dure.** Vérifiez la sortie Internet de la machine (amuled a besoin du 443 sortant). **Si
  vous avez ajouté un VPN**, c'est presque toujours le tunnel `gluetun` qui n'est pas monté : le
  conteneur partage son réseau, donc tant que le tunnel est down, amuled n'a aucune sortie.
  ```bash
  docker compose -f gluetun.compose.yml logs gluetun
  ```
  Un tunnel sain affiche `[gluetun] [vpn] connected` et une IP publique qui n'est pas la vôtre.
  Sinon, corrigez la clé WireGuard et les autres variables VPN dans `.env`, puis redémarrez le seul
  processus amuled :
  ```bash
  docker compose -f gluetun.compose.yml exec p2pwatch s6-svc -r /etc/services.d/amuled
  ```
  Plus de détails dans la
  [fiche opérateur](troubleshooting.md#amuled-ne-se-connecte-à-aucun-serveur-ni-réseau-tunnel).

### La webui reste vide

- **La page se charge, mais le tableau est vide.** C'est normal les premières heures : le catalogue
  se remplit au fil des recherches, et les cibles rares peuvent mettre des jours à réapparaître.
  Vérifiez plutôt que le nœud vit : `docker compose logs p2pwatch` doit montrer une ligne
  `verdict(s) changed` par recherche terminée. S'il signale `off its network`, voir
  [« amuled ne se connecte à rien »](#amuled-ne-se-connecte-à-rien).
- **La page ne se charge pas du tout.** La webui est servie en intra-processus par le crawler, il
  n'y a pas de service `webui` séparé. Et un conteneur `Up (healthy)` ne prouve pas que le crawler
  est vivant, la sonde n'interroge qu'amuled. Vérifiez donc les deux :
  ```bash
  docker compose ps
  ```
  ```bash
  docker compose exec p2pwatch s6-svstat /etc/services.d/p2pwatch
  ```
  Conteneur pas `Up` → [« Un conteneur redémarre en boucle »](#un-conteneur-redémarre-en-boucle).
  `s6-svstat` à `down` → relisez le journal. Tout `up` mais page inaccessible → confirmez l'adresse
  (le port est peut-être remappé, voir [« Le port est déjà pris »](#le-port-est-déjà-pris)) et que
  `webui.enabled` vaut `true` dans `crawler.yml`.
