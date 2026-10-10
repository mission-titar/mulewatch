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

    Depuis votre dossier de travail, celui qui contient `compose.yml`. Les mêmes commandes servent
    les deux variantes : c'est la ligne `include:` de `compose.yml` qui choisit. Le crawler est le
    service `p2pwatch`, le client eMule le service `ed2k`, et son tunnel `ed2k-gluetun` sous la
    variante VPN.

---

## Démarrage & réseau

### amuled ne se connecte à aucun serveur ni réseau (tunnel)

- **Cause la plus fréquente.** Au tout premier run, amuled doit amorcer sa liste de serveurs eD2k
  (`server.met`) et de nœuds Kad (`nodes.dat`) en faisant du DNS et du HTTPS sortant (443) à travers
  le VPN. Si gluetun n'est pas encore monté, ou si la sortie Internet est bloquée à ce moment, rien
  ne s'amorce et amuled reste sans serveurs ni nœuds.
- **Solution.** Vérifiez d'abord l'état du tunnel, avant amuled :
  ```bash
  docker compose logs ed2k-gluetun   # le tunnel doit être « up », IP publique VPN
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
  Le conteneur `ed2k` **partage le réseau d'`ed2k-gluetun`** (`network_mode:
  service:ed2k-gluetun`) : tant que le tunnel est down, amuled n'a aucune sortie. Le crawler, lui,
  reste hors du tunnel. Si le tunnel ne monte pas, corrigez le VPN (clé WireGuard, fournisseur,
  `SERVER_COUNTRIES`) puis relancez. Une fois `ed2k-gluetun` « up », redémarrez le processus amuled
  sans coucher le reste :
  ```bash
  docker compose exec ed2k s6-svc -r /etc/services.d/amuled
  ```
- **Version d'aMule.** Elle n'est pas un paramètre de déploiement : aMule est compilé sur Debian
  dans notre propre image, `p2pwatch-amule`, depuis un commit git épinglé dans son `Dockerfile`. Sa
  version suit donc celle de l'image. Pour savoir laquelle tourne :
  `docker compose exec ed2k amuled --version`. La commande sort en code 255 même quand tout va bien.

### s6 a redémarré un processus et le conteneur est resté debout

- **Symptôme.** amuled réapparaît dans `docker compose logs ed2k` (il se ré-annonce, recharge
  `server.met`) alors que `docker compose ps` n'a jamais quitté `Up` pour `ed2k`.
- **Cause. C'est normal.** Dans le conteneur `ed2k`, s6 supervise deux services, `amuled` et le
  port-sync, et relance sur place celui qui meurt. amuleapi n'en fait pas partie : `amuled` le
  démarre et l'arrête avec lui, donc un redémarrage d'`amuled` en entraîne un d'amuleapi. Le
  crawler n'est pas sous s6 : c'est Docker qui relance le conteneur `p2pwatch` après chacune de ses
  sorties, y compris celle que demande le bouton de redémarrage du tableau de bord. `ed2k` garde
  alors ses sessions eD2k et Kad, ce qui est tout l'intérêt.
- **Comment le confirmer.** `s6-svstat` affiche l'uptime du service en secondes : un petit nombre
  signifie qu'il vient d'être redémarré. Pour le crawler, la colonne `STATUS` de `docker compose ps`
  donne la même information.
  ```bash
  docker compose exec ed2k s6-svstat /etc/services.d/amuled
  docker compose ps
  ```
- **Conséquence à garder en tête.** `healthy` ne concerne que `ed2k`, et ne dit qu'une chose :
  amuled tourne. Le crawler n'a pas de healthcheck. Un crawler qui tombe en boucle se voit en
  `Restarting` dans `docker compose ps` ; un crawler vivant écrit des lignes `verdict(s) changed`
  dans `docker compose logs p2pwatch`, une par recherche terminée.

### Le crawler refuse de démarrer : « environment variable '…' referenced but not set »

- **Symptôme.** `docker compose ps` montre `p2pwatch` en `Restarting`, et
  `docker compose logs p2pwatch` affiche
  `Invalid config, refusing to start: … : environment variable 'DISCORD_WEBHOOK_ID' referenced but not set`,
  alors que la variable est bien renseignée dans `.env`.
- **Cause.** Compose ne lit `.env` que pour substituer les `${...}` **dans les fichiers compose**.
  Le crawler, lui, interpole les `${VAR}` de `crawler.yml` depuis son propre environnement de
  conteneur. Une variable référencée dans `crawler.yml` doit donc être injectée explicitement dans
  le service `p2pwatch` (bloc `environment:` de `compose.yml`), sinon le process ne la voit pas.
  Seul `AMULE_API_PASSWORD` y est câblé par défaut.
- **Solution.** Si vous ajoutez un nouveau `${VAR}` dans `crawler.yml`, typiquement en activant une
  URL de notification `notifications[].url: "discord://${DISCORD_WEBHOOK_ID}/…"`, ajoutez la même
  variable au bloc `environment:` du service `p2pwatch` :
  ```yaml title="compose.yml"
  p2pwatch:
    environment:
      AMULE_API_PASSWORD: ${AMULE_API_PASSWORD:?}
      DISCORD_WEBHOOK_ID: ${DISCORD_WEBHOOK_ID:?}     # ← nouvelle ligne par secret ajouté
      DISCORD_WEBHOOK_TOKEN: ${DISCORD_WEBHOOK_TOKEN:?}
  ```
  Le mapping est **explicite** (et non `env_file: .env`) pour le moindre privilège : le crawler n'a
  à voir ni la clé WireGuard, ni le mot de passe EC, ni les autres secrets du déploiement.

### Le statut « Low-ID » apparaît dans les logs

- **Ce n'est pas une panne.** Low-ID est l'état normal par défaut : recherche, catalogage et
  téléchargement fonctionnent, seule la joignabilité est sous-optimale (moins de sources directes).
  Le tableau de bord le montre aussi : `Connectable` y vaut `no` sur le canal `ed2k`.
- **Pour passer en High-ID** (optionnel), voir [Devenir High-ID](high-id.md).

---

## Téléchargements

### Un fichier terminé n'apparaît jamais dans `ed2k/downloads/incoming`

- **Ce qui se passe normalement.** amuled écrit un fichier terminé directement dans son
  `IncomingDir`, que le conteneur `ed2k` atteint par le bind mount `./downloads:/downloads` de son
  dossier `ed2k/`. Le crawler détecte la complétion depuis la liste des fichiers partagés d'amuled
  (le hash est partagé **et** a quitté la file de téléchargement), passe le téléchargement en
  `completed` et notifie. Il ne déplace, n'ouvre ni n'inspecte jamais le fichier : il monte
  `ed2k/downloads` en lecture seule, et n'y fait qu'un `statvfs`, pour le plancher d'espace libre.
- **Regardez d'abord l'état que voit le crawler.** Si la webui montre encore le téléchargement en
  `downloading`, c'est qu'il n'est tout simplement pas fini : rien n'est cassé.
- **Si le crawler dit `completed` mais que le dossier est vide**, amuled a posé le fichier ailleurs.
  Deux causes, dans l'ordre :
  1. **Une catégorie amuled redirige la destination.** Dans `amule.conf` (ou via l'interface
     d'aMule sur le port 4711), aucune catégorie ne doit porter un `Path=` non vide qui envoie le
     fichier terminé hors d'`IncomingDir`.
  2. **`IncomingDir` ne pointe pas sur le chemin monté en bind.** Le one-shot de démarrage d'`ed2k`
     écrit `IncomingDir=/downloads/incoming` et `TempDir=/downloads/temp` dans `amule.conf`, mais
     seulement quand ce fichier est absent. Un nœud migré depuis une organisation plus ancienne
     porte son propre `amule.conf`, donc une vieille valeur survit à tous les redémarrages.
     `amule.conf` est un simple fichier de votre dossier de travail, lisez-le depuis l'hôte :
     ```bash
     grep -E '^(Incoming|Temp)Dir' ed2k/amule/amule.conf
     ```
     Corrigez les deux lignes, puis redémarrez amuled seul :
     ```bash
     docker compose exec ed2k s6-svc -r /etc/services.d/amuled
     ```
- **Gardez amuled dédié au crawler.** Ses fichiers partagés sont lus à chaque cycle de
  téléchargement : ne pointez donc pas cet amuled sur une grande bibliothèque partagée
  préexistante, la détection de complétion en deviendrait plus lente et plus bruyante. Contexte et
  sources :
  [`reference/2026-06-17-amuled-completion-behavior.md`](https://github.com/mission-titar/p2pwatch/blob/main/agents/reference/2026-06-17-amuled-completion-behavior.md)
  (ses contraintes 1 et 2, sur un volume de quarantaine partagé, ne s'appliquent plus : l'étape de
  quarantaine a été retirée le 2026-09-13).

### Un téléchargement est bloqué, puis passe en `failed`

- **Ce que fait le crawler.** Chaque cycle de téléchargement horodate `last_seen_at` pour chaque
  fichier suivi qu'amuled rapporte, que ce soit dans sa file de téléchargement ou dans ses fichiers
  partagés. Un téléchargement `queued` ou `downloading` qu'amuled ne rapporte pas lui est renvoyé à
  chaque cycle, jusqu'à ce qu'il le rapporte de nouveau. S'il ne l'a plus rapporté depuis
  `download.lost_after_seconds` (24 h par défaut), il est marqué `failed`, avec une ligne de
  journal : `file=... unseen by the client for 86400.0s: marked failed`.
- **Pourquoi c'est sans danger.** Une entrée reste dans la file d'amuled même avec **zéro source** :
  elle devient dormante, mais elle ne disparaît pas. Un téléchargement de lost media qui stagne à
  0 % pendant des mois n'est donc jamais menacé. Une absence côté amuled signifie que l'entrée a
  réellement été retirée, et le crawler la remet alors en file.
- **Ne sortez pas un fichier d'`IncomingDir` avant que le téléchargement soit `completed`.**
  Absent à la fois de la file et des fichiers partagés, il serait renvoyé à amuled et téléchargé de
  nouveau en entier.
- **`failed` n'est pas définitif.** amuled reste l'autorité : si le hash réapparaît dans sa file
  sans erreur, le crawler remet le téléchargement en `downloading` ; s'il apparaît dans les fichiers
  partagés, le téléchargement se termine et la notification part. Un fichier qu'amuled signale en
  erreur reste `failed`, avec la raison `error` sur `/node`. Regardez `ed2k/downloads/incoming`
  avant de conclure que le fichier est perdu.
- **Pour en réessayer un à la main**, supprimez sa ligne : `is_downloaded()` ignore l'état, donc une
  ligne `failed` continue de bloquer la remise en file automatique, à dessein. Il n'existe pas de
  contrôle webui pour cela et la console SQL est en lecture seule : c'est donc une écriture manuelle
  sur `local.db`. Remplacez `<hash>` par le hash eD2k de la ligne à supprimer :

  ```bash
  docker compose stop p2pwatch # (1)!
  docker compose run --rm p2pwatch python -c \
    "import sqlite3; db = sqlite3.connect('/data/local.db', autocommit=True); \
     db.execute('DELETE FROM downloads WHERE native_id = ?', ('<hash>',))" # (2)!
  docker compose start p2pwatch # (3)!
  ```

  1.  On arrête le crawler **seul** : il est l'écrivain unique de `local.db` par doctrine. `ed2k`
      continue de tourner, donc les sessions eD2k et Kad survivent.
  2.  Un conteneur jetable de la même image, avec les mêmes montages et sous le même `PUID:PGID`
      que le crawler : les fichiers WAL créés par SQLite restent donc les vôtres. Il ne publie aucun
      port et ne démarre rien d'autre.
  3.  Une fois le crawler relancé, le cycle suivant remet le fichier en file depuis la décision du
      catalogue, à condition qu'il corresponde toujours à une cible qui n'est pas `complete`.
- **Pour abandonner un téléchargement**, l'annuler dans l'interface d'aMule ne suffit pas : amuled
  accepte de nouveau le lien d'un fichier annulé, et le cycle suivant le lui renvoie. Marquez aussi
  sa ligne `failed`, qu'aucun cycle ne relance : arrêtez le crawler comme ci-dessus, annulez le
  téléchargement dans aMule, puis lancez la même commande avec cette écriture à la place du
  `DELETE`, qui ferait l'inverse, et relancez le crawler :
  `db.execute('UPDATE downloads SET state = ? WHERE native_id = ?', ('failed', '<hash>'))`.
- **Si rien du tout ne se télécharge**, vérifiez le plancher disque avant de soupçonner le TTL. Une
  ligne de journal `candidate file=... → skip_disk_cap (skipped/deferred)` signifie que l'espace
  libre, moins ce qu'amuled doit encore récupérer, passerait sous `download.min_free_bytes`. Un
  `output directory unmeasurable` signifie au contraire que le montage
  `./ed2k/downloads:/downloads:ro` manque au service `p2pwatch` de `compose.yml`.

---

## High-ID / port-sync

> ⚠️ **Prérequis pour ce diagnostic** : lecture de logs gluetun et notions de port forwarding VPN.
> Si vous n'êtes pas à l'aise avec ces concepts, le port-sync n'est probablement pas la bonne voie
> pour vous : envisagez la Route B (port-forward manuel sur votre box) ou restez en Low-ID, qui
> marche très bien. Voir [Devenir High-ID](high-id.md).

### Le port-sync reste inopérant (toujours Low-ID alors qu'il est activé)

Le port-sync tourne dans le conteneur `ed2k` et écrit chacune de ses actions et chacun de ses
échecs dans `docker compose logs ed2k`. Plusieurs causes, à vérifier dans cet ordre :

- **Il n'est pas démarré.** Il ne tourne que sous la variante VPN (`ed2k/vpn.compose.yml`), et
  seulement quand `VPN_PORT_FORWARDING` l'allume : c'est son unique interrupteur, `crawler.yml`
  n'y joue aucun rôle. Vérifiez-le :
  ```bash
  docker compose exec ed2k s6-svstat -o up,normallyup /etc/services.d/port-sync
  ```
  `true true` : il tourne. `false false` : il est arrêté à dessein, soit parce que la ligne
  `include:` désigne encore `ed2k/direct.compose.yml`, soit parce que `VPN_PORT_FORWARDING` vaut
  `off` ou manque dans `.env`. Corrigez, puis `docker compose up -d`.
- **Fournisseur sans port forwarding.** Le High-ID exige un fournisseur à port forwarding
  (Proton/PIA/PrivateVPN/PerfectPrivacy). Le journal le montre à chaque tour :
  ```
  gluetun reports no forwarded port (...)
  ```
  Une ligne `gluetun's control server is unavailable (...)` signifie au contraire que le port-sync
  ne joint pas gluetun du tout : `GLUETUN_CONTROL_URL` est faux, ou `ed2k-gluetun` n'est pas monté.
- **`ed2k` redémarre en boucle.** Une valeur que le port-sync ne comprend pas fait sortir le
  conteneur dès son démarrage, avant amuled, sur une seule ligne qui nomme la variable, par exemple
  `PORT_SYNC must be one of enabled, yes, on, true, disabled, no, off, false, got 'maybe'`. Ici
  `PORT_SYNC` reçoit `VPN_PORT_FORWARDING` tel quel : corrigez cette dernière dans `.env`.
- **Le redémarrage d'amuled échoue.** Pour appliquer un nouveau port, le port-sync arrête amuled,
  écrit le port dans `amule.conf`, puis le relance toujours. Une ligne
  `s6-svc -wD -T 60000 -d exited ...` dit que l'arrêt n'a pas abouti : amuled a mis plus de
  60 secondes à s'arrêter, ou le port-sync n'a pas reçu le droit de le piloter. Rien n'a été écrit,
  amuled est relancé, et le port-sync réessaie après 5 minutes. Si la ligne revient à chaque
  tentative, redémarrez le conteneur (`docker compose restart ed2k`) : le script de démarrage du
  port-sync lui redonne ses droits sur amuled.
- **`amule.conf` illisible.** `amule.conf's port is unreadable, amuled left as is (...)` : la ligne
  `Port=` de la section `[eMule]` de `ed2k/amule/amule.conf` n'est pas un nombre, ou le fichier est
  abîmé. Le port-sync ne touche à rien tant que vous ne l'avez pas corrigé.

Un changement réussi s'annonce par `gluetun forwards <N>, amuled listens on <M>: restarting
amuled`. Si la ligne revient toutes les minutes avec un port différent, voyez
[« Le port forwardé change toutes les minutes »](high-id.md#le-port-forwardé-change-toutes-les-minutes-protonvpn-et-wireguard).

## Stockage & droits

> ⚠️ **Prérequis pour cette section** : Linux + notions d'UID/GID et de droits de fichiers. Si vous
> bloquez sur un de ces diagnostics et n'êtes pas à l'aise, l'option de repli sûre est de repartir
> d'un `data/` vide, au prix du catalogue accumulé : voir
> [« Je veux repartir de zéro »](#je-veux-repartir-de-zéro-catalogue-effacé).

### Un conteneur ne peut pas écrire dans ses dossiers (PUID / PGID)

- **Ce que font les deux images.** Il n'y a **aucun volume nommé** : tout est un bind mount relatif
  dans votre dossier de travail (`data/`, `ed2k/amule/`, `ed2k/downloads/`, plus les trois `.yml`
  montés en lecture seule). Le crawler tourne directement sous votre `PUID:PGID` (`user:` dans
  `compose.yml`) et ne change la propriété de rien : `data/` doit déjà vous appartenir. Au démarrage
  d'`ed2k`, le one-shot `p2pwatch_amule.config` tourne en root, crée l'utilisateur `amule` avec
  `PUID:PGID`, puis lui donne les points de montage (`/home/amule/.aMule`, `/downloads/incoming`,
  `/downloads/temp`).
- **Ce qu'il ne fait pas : ce `chown` n'est PAS récursif** sur `ed2k/downloads/` ni sur
  `ed2k/amule/`, et c'est délibéré. Ces dossiers peuvent contenir des centaines de gigaoctets de
  part files, et leur contenu appartient à l'opérateur.
- **Symptôme.** Le crawler redémarre en boucle sur `unable to open database file` : `data/`
  manquait au premier `docker compose up`, et Docker l'a créé au nom de root. Ou bien, après un
  changement de `PUID`/`PGID` ou la migration d'un ancien nœud, amuled journalise une erreur
  d'écriture ou de permission sur son dossier temp ou incoming, ne reprend pas ses téléchargements
  en cours, ou ne relit pas son `amule.conf`. Côté hôte, symptôme jumeau :
  `sqlite3 data/catalog.db` ou un simple `ls ed2k/downloads/` demande `sudo`.
- **Cause.** Les contenus existants appartiennent encore à un autre uid/gid, ou à root.
- **Solution.** Alignez `PUID`/`PGID` sur VOTRE utilisateur (c'est leur raison d'être : garder ces
  dossiers lisibles sans `sudo`), puis reprenez la propriété des contenus, une seule fois :
  ```bash
  id -u ; id -g                        # les valeurs à mettre dans PUID / PGID
  sudo chown -R "$(id -u):$(id -g)" data ed2k/amule ed2k/downloads
  docker compose up -d
  ```
- **Posture de confinement, pour mémoire.** Le PID 1 d'`ed2k` tourne en **root** : il crée
  l'utilisateur et prend possession des points de montage, ce qui exclut `user:`, `read_only:` et
  `cap_drop: ALL` sur ce service. Le crawler tourne sous votre identifiant, sans `read_only:` ni
  `cap_drop: ALL` non plus, par choix. Les deux gardent `no-new-privileges:true`,
  `pids_limit: 512` et `mem_limit: 2g`. Le détail et le risque accepté sont dans
  [Limites connues](limits.md#le-durcissement-des-conteneurs-sarrête-assez-bas).

---

## Récupération après panne

Quelques scénarios « j'ai cassé quelque chose, comment je remonte ? » :

### J'ai perdu / je ne me souviens plus de `AMULE_EC_PASSWORD`

- **Symptôme.** amuleapi n'arrive plus à joindre amuled : la page du port 4711 se charge mais ne
  montre aucun transfert, et le crawler journalise des `503 ec_unavailable`.
- **Le piège.** Ce mot de passe ne sert qu'au lien interne entre amuleapi et amuled, dans le
  conteneur `ed2k`. Le one-shot de démarrage l'aligne dans `ed2k/amule/amule.conf` à chaque boot,
  donc le changer dans `.env` suffit : un redémarrage du nœud propage la nouvelle valeur des deux
  côtés.
- **Solution.** Choisissez un nouveau mot de passe, mettez-le dans `.env`, puis :
  ```bash
  docker compose up -d --force-recreate
  ```
  Pas de perte de catalogue : le mot de passe ne protège que le canal interne, pas les données. Si
  vous avez oublié `AMULE_API_PASSWORD` à la place, la marche à suivre est la même : le one-shot le
  réécrit dans `ed2k/amule/amuleapi-passwords` à chaque boot, et la recréation le donne aussi au
  crawler. Pensez seulement à reporter la nouvelle valeur partout où vous vous connectiez avec
  l'ancienne.
- **Variante brutale.** Supprimer `ed2k/amule/amule.conf` le fait régénérer au prochain démarrage,
  avec le mot de passe de `.env`. Vous perdez en revanche tous les autres réglages aMule accumulés
  dans ce fichier ; les serveurs eD2k et les nœuds Kad, eux, vivent dans `server.met` / `nodes.dat`
  et survivent.

### J'ai mal édité `.env` et le compose refuse de démarrer

- **Symptôme.** `docker compose up` retourne une erreur de parsing ou un service `Exited (1)`
  immédiatement.
- **Solution.** Recommencez à partir du modèle : `cp .env.example .env.new`, recopiez vos secrets
  un par un en vérifiant la syntaxe (pas d'espaces autour du `=`, pas de guillemets autour des
  valeurs sauf nécessaire), puis `mv .env.new .env`. Évite d'avoir à débugger un fichier corrompu.
  Si l'erreur nomme une variable (`required variable "..." is not set`), voir
  [« Une variable obligatoire manque »](troubleshooting-start.md#une-variable-obligatoire-manque).

### Où trouver un fichier téléchargé ?

- **Réponse.** Dans `ed2k/downloads/incoming`, à l'intérieur de votre dossier de travail. C'est un
  simple dossier de votre disque, donc arrêter ou supprimer les conteneurs n'y touche pas. Les
  fichiers encore en cours de téléchargement sont dans `ed2k/downloads/temp`.
- **Rien n'a inspecté ce fichier.** p2pwatch n'ouvre jamais un fichier téléchargé : pas de contrôle
  de type, pas de sonde média, pas d'analyse antivirus. Vérifiez-le vous-même avant de l'ouvrir.

### Je veux repartir de zéro (catalogue effacé)

- **Solution destructive (irréversible).** Il n'y a pas de volume Docker à supprimer : les données
  sont des dossiers de votre dossier de travail. Arrêtez la pile, effacez ce que vous voulez perdre,
  puis recréez `data/` vous-même : laissé à Docker, il serait créé au nom de root, et le crawler ne
  pourrait pas y écrire.
  ```bash
  docker compose down
  rm -rf data && mkdir data   # le catalogue + l'état local du nœud
  ```
  Videz aussi `ed2k/amule/` pour repartir d'un aMule vierge (mot de passe, serveurs, nœuds Kad), et
  `ed2k/downloads/` pour jeter les fichiers téléchargés ; ces deux-là sont indépendants du
  catalogue. `docker compose down -v` n'efface **rien** de tout cela. Sauvegardez d'abord ce que
  vous tenez à garder.

---

## Outils de diagnostic

### Piloter un service du conteneur `ed2k`

Dans `ed2k`, s6 supervise deux services, `amuled` et `port-sync` : ils se pilotent par service s6,
et non par service compose. Depuis votre dossier de travail (`<svc>` vaut `amuled` ou `port-sync` ;
amuleapi suit `amuled`, qui le démarre) :

```bash
docker compose exec ed2k s6-svstat /etc/services.d/<svc>   # actif/arrêté + durée en secondes
docker compose exec ed2k s6-svc -r /etc/services.d/<svc>   # le redémarrer
docker compose exec ed2k s6-svc -d /etc/services.d/<svc>   # l'arrêter
docker compose exec ed2k s6-svc -u /etc/services.d/<svc>   # le relancer
```

Le crawler, lui, se pilote en conteneur (`docker compose stop|start|restart p2pwatch`), sans toucher
amuled. Arrêter `amuled` rend au contraire le crawler aveugle, et emporte amuleapi : il notifie
au-delà de 2 minutes. Voir [Faire tourner un nœud](operate.md#redémarrer-une-pièce-plutôt-que-tout-le-nœud).

### Valider la configuration sans rien démarrer

```bash
docker compose run --rm p2pwatch python -m p2pwatch validate-config \
  --config /app/config/crawler.yml --targets /app/config/targets.yml \
  --matcher /app/config/matcher.yml
```

Charge + valide les 3 configs et sort en erreur (code ≠ 0) si l'une est invalide, **sans rien
démarrer** : un conteneur jetable lit les fichiers montés, puis disparaît. Il marche aussi quand le
crawler redémarre en boucle. À lancer **avant** un déploiement (entre les étapes 3 et 4
d'[Installer un nœud](install.md)) ou après une modification de config.
