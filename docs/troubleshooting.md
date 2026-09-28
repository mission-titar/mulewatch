---
description: "Diagnostics au-delà du premier déploiement : téléchargement, High-ID, stockage, récupération après panne."
---

# Diagnostics avancés

Ces sections vont plus loin que le premier déploiement : téléchargement, High-ID, stockage,
récupération. La plupart restent accessibles (lecture de logs, redémarrage de processus), mais
**High-ID/port-sync et Stockage & droits exigent une familiarité Linux/Docker** et le signalent à
leur ouverture. Si une étape dépasse votre confort, l'option de repli sûre est presque toujours de
repartir d'un dossier `data/` vide (voir « Récupération après panne ») : vous perdez le catalogue
accumulé, mais vous redémarrez d'un état connu.

!!! info "Où lancer ces commandes"

    Depuis votre dossier de travail, celui qui contient `compose.yml`. Sous la pile VPN,
    ajoutez `-f gluetun.compose.yml` à chaque `docker compose ...`.

---

## Démarrage & réseau

### amuled ne se connecte à aucun serveur ni réseau (tunnel)

- **Cause la plus fréquente.** Au tout premier run, amuled doit amorcer sa liste de serveurs eD2k
  (`server.met`) et de nœuds Kad (`nodes.dat`) en faisant du DNS et du HTTPS sortant (443) à travers
  le VPN. Si gluetun n'est pas encore monté, ou si la sortie Internet est bloquée à ce moment, rien
  ne s'amorce et amuled reste sans serveurs ni nœuds.
- **Solution.** Vérifiez d'abord l'état du tunnel gluetun, avant amuled :
  ```bash
  docker compose -f gluetun.compose.yml logs gluetun   # le tunnel doit être « up », IP publique VPN
  ```

  **Ce que vous devez voir (tunnel sain) :**
  ```
  [gluetun] [main] Listening on 0.0.0.0:8000
  [gluetun] [main] You are running on the public IP address W.X.Y.Z   ← IP VPN (pas la vôtre !)
  [gluetun] [vpn] connected
  ```
  **Symptômes d'un tunnel cassé :**
  ```
  [gluetun] [vpn] cannot connect to ...   ← VPN provider/clé refusée
  [gluetun] [main] retrying in N seconds
  ```
  Le conteneur mulewatch **partage le réseau de gluetun** (`network_mode: service:gluetun`) : tant
  que le tunnel est down, amuled n'a aucune sortie. Si le tunnel ne monte pas, corrigez le VPN (clé
  WireGuard, fournisseur, `SERVER_COUNTRIES`) puis relancez. Une fois gluetun « up », redémarrez le
  processus amuled sans coucher le reste :
  ```bash
  docker compose -f gluetun.compose.yml exec mulewatch s6-svc -r /etc/services.d/amuled
  ```
- **Version d'aMule.** Elle n'est plus un paramètre de déploiement : aMule **3.1.0** est compilé
  dans notre propre image depuis un nixpkgs épinglé. Il n'y a plus d'image tierce à vérifier ni à
  épingler ; la version d'aMule suit celle de l'image mulewatch.

### s6 a redémarré un processus et le conteneur est resté debout

- **Symptôme.** amuled réapparaît dans le journal (il se ré-annonce, recharge
  `server.met`) alors que `docker compose ps` n'a jamais quitté `Up`. Ou bien : vous avez appuyé sur
  le bouton de redémarrage de `/controls` et rien ne semble être arrivé au conteneur.
- **Cause. C'est normal.** s6 supervise chacun de ses deux services indépendamment et en relance un
  sur place quand il meurt. amuleapi n'en fait pas partie : `amuled` le démarre et l'arrête avec
  lui, donc un redémarrage d'`amuled` en entraîne un d'amuleapi. Le conteneur ne tombe que lorsque
  le crawler sort en code non nul : son script `finish` demande alors à s6 de coucher tout l'arbre
  de supervision, de sorte que `restart: unless-stopped` donne une boucle de backoff visible au lieu
  d'un crash-loop silencieux.
  Une sortie propre du crawler, exactement ce que demande le bouton de redémarrage de `/controls`,
  ramène le crawler seul ; amuled garde ses sessions eD2k et Kad, ce qui est tout l'intérêt.
- **Comment le confirmer.** `s6-svstat` affiche l'uptime du service en secondes : un petit nombre
  signifie qu'il vient d'être redémarré.
  ```bash
  docker compose exec mulewatch s6-svstat /etc/services.d/mulewatch
  docker compose exec mulewatch s6-svstat /etc/services.d/amuled
  ```
- **Conséquence à garder en tête.** Un conteneur en `Up (healthy)` ne prouve **pas** que le crawler
  tourne : le healthcheck n'interroge qu'amuled, et le conteneur ne passe `unhealthy` que quand
  amuled est arrêté. Dans le doute, interrogez `s6-svstat` sur `/etc/services.d/mulewatch`, ou
  cherchez des lignes `cycle ...` dans le journal.

### Le crawler refuse de démarrer : « environment variable '…' referenced but not set »

- **Symptôme.** `docker compose logs mulewatch` affiche
  `Invalid config, refusing to start: … : environment variable 'AMULE_EC_PASSWORD' referenced but not set`,
  alors que la variable est bien renseignée dans `.env`.
- **Cause.** Compose ne lit `.env` que pour substituer les `${...}` **dans les fichiers compose**.
  Le crawler, lui, interpole les `${VAR}` de `crawler.yml` depuis son propre environnement de
  conteneur. Une variable référencée dans `crawler.yml` doit donc être injectée explicitement dans
  le service `mulewatch` (bloc `environment:` de `base.compose.yml`), sinon le process ne la voit
  pas. `AMULE_EC_PASSWORD` y est câblé par défaut.
- **Solution.** Si vous ajoutez un nouveau `${VAR}` dans `crawler.yml`, typiquement en activant une
  URL de notification `notifications[].url: "discord://${DISCORD_WEBHOOK_ID}/…"`, ajoutez la même
  variable au bloc `environment:` du service `mulewatch` :
  ```yaml title="base.compose.yml"
  mulewatch:
    environment:
      PUID: ${PUID:?}
      PGID: ${PGID:?}
      AMULE_EC_PASSWORD: ${AMULE_EC_PASSWORD:?}
      AMULE_API_PASSWORD: ${AMULE_API_PASSWORD:?}
      DISCORD_WEBHOOK_ID: ${DISCORD_WEBHOOK_ID:?}     # ← nouvelle ligne par secret ajouté
      DISCORD_WEBHOOK_TOKEN: ${DISCORD_WEBHOOK_TOKEN:?}
  ```
  Le mapping est **explicite** (et non `env_file: .env`) pour le moindre privilège : le conteneur
  n'a pas à voir la clé WireGuard ni les autres secrets du déploiement.

### Le statut « Low-ID » apparaît dans les logs

- **Ce n'est pas une panne.** Low-ID est l'état normal par défaut : recherche, catalogage et
  téléchargement fonctionnent, seule la joignabilité est sous-optimale (moins de sources directes).
- **Pour passer en High-ID** (optionnel), voir [Devenir High-ID](high-id.md).

---

## Téléchargements

### Un fichier terminé n'apparaît jamais dans `downloads/incoming`

- **Ce qui se passe normalement.** amuled écrit un fichier terminé directement dans son
  `IncomingDir`, que les piles compose atteignent par l'unique bind mount `./downloads:/downloads`
  de votre dossier de travail. Le crawler détecte la complétion depuis la liste des fichiers
  partagés d'amuled (le hash est partagé **et** a quitté la file de téléchargement), passe le
  téléchargement en `completed` et notifie. Il ne déplace, n'ouvre ni n'inspecte jamais le fichier ;
  sur `/downloads`, il ne fait jamais qu'un `statvfs`, pour le plancher d'espace libre.
- **Regardez d'abord l'état que voit le crawler.** Si la webui montre encore le téléchargement en
  `downloading`, c'est qu'il n'est tout simplement pas fini : rien n'est cassé.
- **Si le crawler dit `completed` mais que le dossier est vide**, amuled a posé le fichier ailleurs.
  Deux causes, dans l'ordre :
  1. **Une catégorie amuled redirige la destination.** Dans `amule.conf` (ou via l'interface
     d'aMule sur le port 4711), aucune catégorie ne doit porter un `Path=` non vide qui envoie le
     fichier terminé hors d'`IncomingDir`.
  2. **`IncomingDir` ne pointe pas sur le chemin monté en bind.** Le one-shot de démarrage écrit
     `IncomingDir=/downloads/incoming` et `TempDir=/downloads/temp` dans `amule.conf`, mais
     seulement quand ce fichier est absent. Un nœud migré depuis une organisation plus ancienne
     porte son propre `amule.conf`, donc une vieille valeur survit à tous les redémarrages.
     `amule.conf` est un simple fichier de votre dossier de travail, lisez-le depuis l'hôte :
     ```bash
     grep -E '^(Incoming|Temp)Dir' amule/amule.conf
     ```
     Corrigez les deux lignes, puis redémarrez amuled seul :
     ```bash
     docker compose exec mulewatch s6-svc -r /etc/services.d/amuled
     ```
- **Gardez amuled dédié au crawler.** `shared_files()` est interrogé à chaque cycle de
  téléchargement : ne pointez donc pas cet amuled sur une grande bibliothèque partagée
  préexistante, la détection de complétion en deviendrait plus lente et plus bruyante. Contexte et
  sources :
  [`reference/2026-06-17-amuled-completion-behavior.md`](https://github.com/mission-titar/mulewatch/blob/main/agents/reference/2026-06-17-amuled-completion-behavior.md)
  (ses contraintes 1 et 2, sur un volume de quarantaine partagé, ne s'appliquent plus : l'étape de
  quarantaine a été retirée le 2026-09-13).

### Un téléchargement est bloqué, puis passe en `failed`

- **Ce que fait le crawler.** Chaque cycle de téléchargement horodate `last_seen_at` pour chaque
  hash suivi qu'amuled rapporte, que ce soit dans sa file de téléchargement ou dans ses fichiers
  partagés. Un téléchargement encore `queued` ou `downloading` qu'amuled n'a plus rapporté depuis
  `download.lost_after_seconds` (24 h par défaut) est marqué `failed`, avec une ligne de journal :
  `hash=... unseen by amuled for 86400.0s: marked failed`.
- **Pourquoi c'est sans danger.** Une entrée reste dans la file d'amuled même avec **zéro source** :
  elle devient dormante, mais elle ne disparaît pas. Un téléchargement de lost media qui stagne à
  0 % pendant des mois n'est donc jamais menacé. Une absence côté amuled signifie que l'entrée a
  réellement été retirée, ou que le fichier a fini et a été déplacé hors d'`IncomingDir` avant
  l'interrogation suivante.
- **`failed` n'est pas définitif.** amuled reste l'autorité : si le hash réapparaît dans sa file, le
  crawler remet le téléchargement en `downloading` ; s'il apparaît dans les fichiers partagés, le
  téléchargement se termine et la notification part. Regardez `downloads/incoming` avant de conclure
  que le fichier est perdu.
- **Pour en réessayer un à la main**, supprimez sa ligne : `is_downloaded()` ignore l'état, donc une
  ligne `failed` continue de bloquer la remise en file automatique, à dessein. Il n'existe pas de
  contrôle webui pour cela et la console SQL est en lecture seule : c'est donc une écriture manuelle
  sur `local.db`. Remplacez `<hash>` par le hash eD2k de la ligne à supprimer :

  ```bash
  docker compose exec mulewatch s6-svc -d /etc/services.d/mulewatch # (1)!
  docker compose exec --user amule mulewatch python -c \
    "import sqlite3; db = sqlite3.connect('/data/local.db', autocommit=True); \
     db.execute('DELETE FROM downloads WHERE ed2k_hash = ?', ('<hash>',))" # (2)!
  docker compose exec mulewatch s6-svc -u /etc/services.d/mulewatch # (3)!
  ```

  1.  On arrête le crawler **seul** : il est l'écrivain unique de `local.db` par doctrine. amuled
      continue de tourner, donc les sessions eD2k et Kad survivent.
  2.  L'écriture se fait en tant qu'utilisateur `amule` du conteneur, pour que les fichiers WAL
      créés par SQLite restent la propriété de `PUID:PGID`.
  3.  Une fois le crawler relancé, le cycle suivant remet le fichier en file depuis la décision du
      catalogue — à condition qu'il corresponde toujours à une cible qui n'est pas `complete`.
- **Si rien du tout ne se télécharge**, vérifiez le plancher disque avant de soupçonner le TTL. Une
  ligne de journal `candidate hash=... -> skip_disk_cap (skipped/deferred)` signifie que l'espace
  libre, moins ce qu'amuled doit encore récupérer, passerait sous `download.min_free_bytes`. Un
  `output directory unmeasurable` signifie au contraire que le montage `./downloads:/downloads`
  manque dans votre fichier compose.

---

## High-ID / port-sync

> ⚠️ **Prérequis pour ce diagnostic** : lecture de logs gluetun et notions de port forwarding VPN.
> Si vous n'êtes pas à l'aise avec ces concepts, le port-sync n'est probablement pas la bonne voie
> pour vous : envisagez la Route B (port-forward manuel sur votre box) ou restez en Low-ID, qui
> marche très bien. Voir [Devenir High-ID](high-id.md).

### Le port-sync reste inopérant (toujours Low-ID alors qu'il est activé)

Plusieurs causes, à vérifier dans cet ordre :

- **Pile directe au lieu de la pile VPN.** Le port-sync n'a de sens que sous
  `gluetun.compose.yml` : il lit le port forwardé sur le serveur de contrôle de gluetun, à
  `http://localhost:8000`, adresse qui n'existe que parce que le conteneur partage le réseau de
  gluetun. Sous la pile directe, `port_sync.enabled: true` ne peut rien joindre.
- **Fournisseur sans port forwarding.** Le High-ID exige un provider à port forwarding
  (Proton/PIA/PrivateVPN/PerfectPrivacy) et `VPN_PORT_FORWARDING: "on"`.
- **Le redémarrage d'amuled est refusé.** Le port-sync applique le nouveau port en redémarrant le
  processus amuled (`s6-svc -r /etc/services.d/amuled`), ce qui suppose que le crawler ait pu poser
  la permission de groupe sur la FIFO de contrôle d'amuled au démarrage. Si cette étape a échoué,
  le journal du conteneur porte, dès le démarrage, la ligne :
  ```
  mulewatch: /etc/services.d/amuled/supervise/control never appeared; amuled restarts will be refused
  ```
  et, à chaque tentative de port-sync, une erreur `s6-svc exited ...`. Le crawl, lui, continue
  normalement. Remède : redémarrer le conteneur (`docker compose restart mulewatch`) pour rejouer
  la séquence de démarrage.


## Stockage & droits

> ⚠️ **Prérequis pour cette section** : Linux + notions d'UID/GID et de droits de fichiers. Si vous
> bloquez sur un de ces diagnostics et n'êtes pas à l'aise, l'option de repli sûre est de repartir
> d'un `data/` vide, au prix du catalogue accumulé : `docker compose down`, puis supprimez le
> dossier `data/` et relancez `docker compose up -d`. Lourd mais simple.

### amuled ne peut pas écrire dans les bind mounts (PUID / PGID)

- **Ce que fait l'image.** Il n'y a **plus aucun volume nommé** : tout est un bind mount relatif
  dans votre dossier de travail (`data/`, `amule/`, `downloads/`, plus les trois `.yml` montés en
  lecture seule). Au démarrage, le one-shot `amule-config.py` tourne en root, crée l'utilisateur
  `amule` avec `PUID:PGID`, puis donne les points de montage (`/home/amule/.aMule`,
  `/downloads/incoming`, `/downloads/temp`) à cet utilisateur. Le crawler fait de même sur `/data`.
- **Ce qu'il ne fait pas : ce `chown` n'est PAS récursif** sur `downloads/` ni sur `amule/`, et
  c'est délibéré. Ces dossiers peuvent contenir des centaines de gigaoctets de part files, et leur
  contenu appartient à l'opérateur.
- **Symptôme.** Après un changement de `PUID`/`PGID`, ou après avoir migré un nœud depuis un ancien
  déploiement, amuled journalise une erreur d'écriture ou de permission sur son dossier temp ou
  incoming, ou ne reprend pas ses téléchargements en cours ; ou bien amuled ne relit pas son
  `amule.conf`. Côté hôte, symptôme jumeau : `sqlite3 data/catalog.db` ou un simple `ls downloads/`
  demande `sudo`.
- **Cause.** Les contenus existants appartiennent encore à l'ancien uid/gid.
- **Solution.** Alignez `PUID`/`PGID` sur VOTRE utilisateur (c'est leur raison d'être : garder ces
  dossiers lisibles sans `sudo`), puis reprenez la propriété des contenus, une seule fois :
  ```bash
  id -u ; id -g                        # les valeurs à mettre dans PUID / PGID
  sudo chown -R "$(id -u):$(id -g)" data amule downloads
  docker compose up -d
  ```
- **Posture de confinement, pour mémoire.** PID 1 tourne en **root** : il crée l'utilisateur et
  prend possession des points de montage. Ce service ne porte donc ni `user:`, ni `read_only:`, ni
  `cap_drop: ALL`, qui ne peuvent pas survivre à ce besoin. Décision documentée et signée
  (spec 2026-09-16 §9), qui inverse la moitié « crawler » de la décision du 2026-06-17. Ce qui reste
  est conservé et relevé pour trois processus : `no-new-privileges:true`, `pids_limit: 512`,
  `mem_limit: 2g`. Risque résiduel accepté : un amuled compromis atteint les bind mounts de sortie.
  Voir [Limites connues](limits.md).

---

## Récupération après panne

Quelques scénarios « j'ai cassé quelque chose, comment je remonte ? » :

### J'ai perdu / je ne me souviens plus de `AMULE_EC_PASSWORD`

- **Symptôme.** amuleapi n'arrive plus à joindre amuled : la page du port 4711 se charge mais ne
  montre aucun transfert, et le crawler journalise des `503 ec_unavailable`.
- **Le piège.** Ce mot de passe ne sert plus qu'au lien interne entre amuleapi et amuled. Le
  one-shot de démarrage l'aligne dans `amule/amule.conf` à chaque boot, donc le changer dans `.env`
  suffit désormais : un redémarrage du nœud propage la nouvelle valeur des deux côtés.
- **Solution.** Choisissez un nouveau mot de passe, mettez-le dans `.env`, puis :
  ```bash
  docker compose up -d --force-recreate
  ```
  Pas de perte de catalogue : le mot de passe ne protège que le canal interne, pas les données. Si
  vous avez oublié `AMULE_API_PASSWORD` à la place, la marche à suivre est la même : le one-shot le
  réécrit dans `amule/amuleapi-passwords` à chaque boot. Pensez seulement à reporter la nouvelle
  valeur partout où vous vous connectiez avec l'ancienne.
- **Variante brutale.** Supprimer `amule/amule.conf` le fait régénérer au prochain démarrage, avec
  le mot de passe de `.env`. Vous perdez en revanche tous les autres réglages aMule accumulés dans
  ce fichier ; les serveurs eD2k et les nœuds Kad, eux, vivent dans `server.met` / `nodes.dat` et
  survivent.

### J'ai mal édité `.env` et le compose refuse de démarrer

- **Symptôme.** `docker compose up` retourne une erreur de parsing ou un service `Exited (1)`
  immédiatement.
- **Solution.** Recommencez à partir du modèle : `cp .env.example .env.new`, recopiez vos secrets
  un par un en vérifiant la syntaxe (pas d'espaces autour du `=`, pas de guillemets autour des
  valeurs sauf nécessaire), puis `mv .env.new .env`. Évite d'avoir à débugger un fichier corrompu.
  Si l'erreur nomme une variable (`required variable "..." is not set`), voir
  [« Une variable obligatoire manque »](troubleshooting-start.md#une-variable-obligatoire-manque).

### Où trouver un fichier téléchargé ?

- **Réponse.** Dans `downloads/incoming`, à l'intérieur de votre dossier de travail. C'est un simple
  dossier de votre disque, donc arrêter ou supprimer le conteneur n'y touche pas. Les fichiers
  encore en cours de téléchargement sont dans `downloads/temp`.
- **Rien n'a inspecté ce fichier.** mulewatch n'ouvre jamais un fichier téléchargé : pas de contrôle
  de type, pas de sonde média, pas d'analyse antivirus. Vérifiez-le vous-même avant de l'ouvrir.

### Je veux repartir de zéro (catalogue effacé)

- **Solution destructive (irréversible).** Il n'y a plus de volume Docker à supprimer : les données
  sont des dossiers de votre dossier de travail. Arrêtez la pile, puis effacez ce que vous voulez
  perdre.
  ```bash
  docker compose down
  rm -rf data          # le catalogue + l'état local du nœud
  ```
  Ajoutez `amule/` pour repartir d'un aMule vierge (mot de passe, serveurs, nœuds Kad), et
  `downloads/` pour jeter aussi les fichiers téléchargés ; ces deux-là sont indépendants du
  catalogue. `docker compose down -v` n'efface **plus rien** de tout cela. Sauvegardez d'abord ce
  que vous tenez à garder.

---

## Outils de diagnostic

### Piloter un processus dans le conteneur

Deux services sont supervisés par s6 dans l'unique conteneur `mulewatch` : ils se pilotent donc par
service, et non par service compose. Depuis votre dossier de travail (`<svc>` vaut `amuled` ou
`mulewatch` ; amuleapi suit `amuled`, qui le démarre) :

```bash
docker compose exec mulewatch s6-svstat /etc/services.d/<svc>   # actif/arrêté + durée en secondes
docker compose exec mulewatch s6-svc -r /etc/services.d/<svc>   # le redémarrer
docker compose exec mulewatch s6-svc -d /etc/services.d/<svc>   # l'arrêter
docker compose exec mulewatch s6-svc -u /etc/services.d/<svc>   # le relancer
```

Deux choses à savoir avant de les utiliser :

- Arrêter `mulewatch` (le crawler) laisse amuled en marche, ce qui est bien ce que vous voulez pour
  une écriture de maintenance sur les bases. Arrêter `amuled` rend le crawler aveugle, et emporte
  amuleapi avec lui : il journalisera des échecs et fera du backoff jusqu'au retour d'amuled.
- Une sortie **non nulle** du crawler couche tout le conteneur, à dessein. `s6-svc -d` est un arrêt
  propre, donc il ne le fait pas.

### Valider la configuration sans rien démarrer

```bash
docker compose exec mulewatch python -m mulewatch validate-config
```

Charge + valide les 3 configs et sort en erreur (code ≠ 0) si l'une est invalide, **sans rien
démarrer**. À lancer **avant** un déploiement (entre les étapes 3 et 4 d'[Installer un nœud](install.md))
ou après une modification de config.
