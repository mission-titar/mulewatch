---
description: "Piloter un nœud au quotidien : mises à jour, sauvegardes, journaux, métriques et outils de catalogue."
---

# Faire tourner un nœud

Cette page s'adresse à vous une fois le nœud monté : le piloter, le sauvegarder, le régler, savoir
quoi regarder quand il ne catalogue plus. Si vous n'avez pas encore de nœud, commencez par
[Installer un nœud](install.md). Si quelque chose est cassé, allez à
[Diagnostics avancés](troubleshooting.md).

---

## Cycle de vie & données

Un nœud est **un seul conteneur**, le service compose `p2pwatch`. Dedans, le superviseur s6 fait
tourner deux services : `amuled` et `p2pwatch`, le crawler, qui sert aussi le catalogue web sur un
thread dédié. `amuled` démarre à son tour `amuleapi`, l'interface web d'aMule : cela fait trois
processus, mais deux services supervisés. Au démarrage, le conteneur crée l'utilisateur `amule` à
partir de `PUID` et `PGID`, prend possession des dossiers montés, puis écrit un `amule.conf`
**seulement s'il n'y en a pas**.

Quatre variables sont obligatoires : `PUID`, `PGID`, `AMULE_EC_PASSWORD` et `AMULE_API_PASSWORD`.
Si l'une manque, `docker compose up` échoue avec un message clair, plutôt que de démarrer un
conteneur qui meurt aussitôt.

**Persistance.** Tout vit dans des dossiers de votre dossier de travail, jamais dans des volumes
Docker : le catalogue et l'état local dans `data/` (`catalog.db`, `local.db`), la configuration
d'aMule dans `amule/`, les téléchargements dans `downloads/incoming` et `downloads/temp`.
`docker compose down` n'y touche pas, et aucun `-v` ne peut effacer le catalogue par accident : pour
l'effacer, vous supprimez `data/` vous-même. Corollaire voulu, `sqlite3 data/catalog.db` marche
directement depuis l'hôte, sans `sudo` : c'est le rôle de `PUID` et `PGID`.

**Arrêter et mettre à jour.** `docker compose down` arrête le nœud. Pour mettre à jour :

```bash
docker compose pull
docker compose up -d
```

Sous la pile VPN, ajoutez `-f gluetun.compose.yml` à chaque commande.

**Redémarrage de l'hôte.** Le conteneur revient seul au boot, aucune commande à relancer, à
condition que Docker démarre en service système. Vérifiez avec `docker compose ps`.

**Nœud 1.x.** Ne lancez pas la 2.0 par-dessus un nœud 1.x sans avoir suivi
[Migrer un nœud 1.x](migration-1x.md) : les volumes nommés ne sont pas lus par la nouvelle pile, et
le nœud semblera avoir perdu son catalogue. Les données, elles, sont toujours dans les volumes.

### Redémarrer un processus plutôt que le conteneur

`docker compose up -d`, `restart` et `down` agissent sur **tout le conteneur**, les trois processus
d'un coup. Pour n'en toucher qu'un, adressez-vous à s6 :

```bash
docker compose exec p2pwatch s6-svstat /etc/services.d/amuled   # état
docker compose exec p2pwatch s6-svc -r /etc/services.d/amuled   # redémarrer
docker compose exec p2pwatch s6-svc -d /etc/services.d/amuled   # arrêter
docker compose exec p2pwatch s6-svc -u /etc/services.d/amuled   # démarrer
```

Remplacez `amuled` par `p2pwatch`. Il n'existe pas de service compose `amuled`, donc
`docker compose restart amuled` ne veut rien dire. `amuleapi` n'est pas un service s6 non plus :
c'est `amuled` qui le démarre, donc redémarrer `amuled` le redémarre avec lui.

Un détail qui compte : si le crawler s'arrête proprement, comme le fait le bouton de redémarrage du
tableau de bord, s6 le relance seul et aMule garde ses sessions eD2k et Kad. S'il plante, tout le
conteneur redescend, pour que la panne soit visible plutôt que silencieuse. aMule, lui, est
simplement relancé sur place.

### Quand le nœud ne catalogue plus

Les deux services supervisés partagent un seul flux de journaux, `docker compose logs p2pwatch`,
et chaque ligne est préfixée par le service qui l'a émise. C'est toujours le premier endroit à
regarder. amuleapi fait exception : démarré par `amuled` plutôt que par s6, il écrit dans
`amule/amuleapi.log` de votre dossier de travail. Pour aller du symptôme à la cause, voyez
[Diagnostics avancés](troubleshooting.md).

### Planification disque

Des ordres de grandeur, à ajuster selon votre trafic eMule réel et le nombre de cibles :

- **`data/catalog.db`** grossit lentement, de l'ordre de 0,25 Go par trimestre au rythme d'un nœud
  réel (estimation du 2026-10-09).
- **`downloads/`** s'accumule sans borne, rien ne le purge. Le crawler mesure l'espace libre et
  refuse un nouveau candidat si cela passerait sous `download.min_free_bytes` (10 Gio par défaut).
  C'est un **plancher**, pas un ménage : il bloque les nouveaux téléchargements quand le disque se
  tend, il n'efface rien. Le tri reste à votre charge. La jauge `p2pwatch_download_disk_free_bytes`
  publie l'espace libre mesuré à chaque cycle, et le canal *operations* est notifié une fois quand
  il passe sous le plancher, puis de nouveau seulement après être remonté au-dessus.
- **`amule/`** tient en quelques mégaoctets : `amule.conf`, `server.met`, `nodes.dat`, préférences.

Le crawler ne fait qu'un `statvfs` sur `/downloads`, il n'ouvre jamais un fichier téléchargé. C'est
aussi pourquoi `./downloads` est monté en entier plutôt que par ses deux sous-dossiers : sans cela,
la mesure porterait sur le mauvais système de fichiers.

Si votre machine approche de la saturation, lancez `du -sh downloads/ data/ amule/` pour identifier
le coupable, puis faites le ménage dans `downloads/incoming`.

---

## Outils de catalogue

Ces deux outils sont ponctuels et jamais déclenchés par le crawler. La fusion **n'écrit que dans sa
destination** : un fichier neuf (`--output`), ou la source que vous désignez par `--into`.

**Valider la configuration**, sans rien démarrer. À lancer avant un déploiement. Sort en erreur si
l'un des trois fichiers de config est invalide.

```bash
docker compose exec p2pwatch python -m p2pwatch validate-config
```

**Fusionner des catalogues**, pour consolider ceux de plusieurs chercheurs en un seul. La fusion est
idempotente et n'écrase rien sans `--force` ; `--into <source>` fusionne dans une source existante.
Le cycle de partage est décrit sur la [page d'accueil](index.md#partage).

```bash
docker compose exec --user amule p2pwatch python -m p2pwatch.merge \
  --output /data/catalog-merged.db /data/catalog.db /data/source-b.db
```

Une source doit être au même schéma que votre version : la fusion refuse une copie faite par une
version plus ancienne (`has catalog schema version 5, expected 9`), car elle ne migre jamais une
source. Migrez-la d'abord en la fusionnant dans elle-même, qui l'ouvre et la met à niveau sur place,
de préférence sur une copie, puisqu'une version plus ancienne ne pourra plus la lire :

```bash
docker compose exec --user amule p2pwatch python -m p2pwatch.merge \
  --into /data/source-b.db /data/source-b.db
```

Pour valider en profondeur (suites d'intégration, smoke, CI), voyez
[Lancer les tests](contributing/testing.md).

---

## Ré-évaluation du catalogue au démarrage

À chaque démarrage, le crawler ré-évalue tout le catalogue contre le matcher courant
(`matcher.yml` et `targets.yml`), **mais seulement s'ils ont changé**. Il stocke une empreinte
`sha256` des deux fichiers dans `local.db` : empreinte identique, passe entièrement sautée, donc un
simple redémarrage ne coûte rien. Éditer l'un des deux, même un commentaire, déclenche une passe au
prochain démarrage.

Deux effets possibles pour un fichier déjà catalogué :

- **Rétractation.** Un fichier que le nouveau matcher n'attrape plus reçoit une ligne
  `tier="retracted"` : le catalogue reste append-only, rien n'est supprimé. Le catalogue web le
  traite alors comme non identifié et il sort de la file de téléchargement. On ne « dé-télécharge »
  jamais : un fichier déjà récupéré reste.
- **Re-classement.** Un fichier qui change de palier déclenche l'action du nouveau palier : le
  palier `download` le met en file. Un fichier qui **monte** à `notify` ou `download` notifie le
  canal *community* (configurez une cible `tag: community` sous `observability.notifications` dans
  `crawler.yml`) : un seul message par fichier, avec son nom, sa taille, son lien ed2k et les
  segments concernés. Une baisse, un passage à `catalog`, une rétractation ou un changement de
  règle sans changement de palier restent dans les logs, sans notification.

Un gros changement de règles peut donc émettre une rafale de notifications, une par fichier dont le
palier a monté. C'est voulu. La passe est idempotente et tourne que le téléchargement soit activé ou
non.

Le canal *operations* reçoit l'état du nœud, et rien d'autre :

- le démarrage de l'instance ;
- le retour du High-ID, et un High-ID qui ne revient pas après une synchronisation du port (une seule
  fois) ;
- l'espace disque passé sous le plancher (voir [Planification disque](#planification-disque)) ;
- un canal d'un client (`ed2k` ou `kad` pour aMule) **hors de son réseau** ou **non joignable par
  les pairs** depuis 5 minutes, puis son retour ;
- l'API d'un client **injoignable** depuis 2 minutes, puis son retour. Tant qu'elle l'est, l'état de
  ses canaux est inconnu et ne déclenche aucune alerte de canal.

Une alerte part sur un état dégradé qui dure, quel que soit l'état d'avant, démarrage compris : un
nœud qui démarre en Low-ID est signalé au bout de 5 minutes, et la fenêtre où Kad se croit derrière
un pare-feu après chaque reconnexion ne l'est pas tant qu'elle dure moins de 5 minutes. Le retour
n'est notifié que si l'alerte est partie. Ces durées sont fixes, et un redémarrage du crawler remet
leurs horloges à zéro.

Les messages partent en markdown : Discord les affiche en embed, signé `p2pwatch - <node-id>` en
ligne d'auteur, bleu pour une découverte (`🔎 Notify`, `📥 Download`), vert pour un téléchargement
terminé (`✅ Downloaded`, avec les segments et le nom du fichier). Aucun message ne mentionne qui
que ce soit : un `@everyone` dans un nom de fichier ne notifie personne.

Chaque cible préfixe le titre de ses messages de l'identifiant du nœud (`[node-id]`), utile aux
services qui n'affichent pas d'auteur (syslog, ntfy). Une cible Discord le porte déjà dans sa ligne
d'auteur et peut s'en passer avec `node_prefix: false`.

---

## WebUI (consultation du catalogue)

Un nœud publie deux surfaces web, qui n'ont pas la même posture :

| Port | Ce que c'est | Authentification |
|---|---|---|
| **8080** | l'interface de catalogue p2pwatch | **AUCUNE, D'AUCUNE SORTE** |
| **4711** | amuleapi, l'interface propre à aMule | le mot de passe admin `AMULE_API_PASSWORD` |

**Le port 8080 n'a aucune authentification, d'aucune sorte.** Quiconque l'atteint obtient le
catalogue, les contrôles du tableau de bord qui modifient l'état, et une console SQL en lecture seule.
`AMULE_API_PASSWORD` ne protège que le 4711. Mettez le 8080 derrière un reverse proxy ou un VPN, ou
gardez-le sur un réseau de confiance, et ne le posez jamais sur l'Internet ouvert.

Le catalogue web est servi en lecture seule, dans le processus `p2pwatch` lui-même : il démarre et
s'arrête avec lui, il n'y a rien de spécial à lancer. Il ne modifie jamais les bases, parce qu'il
ouvre ses propres connexions SQLite en lecture seule (`mode=ro` et `PRAGMA query_only=ON`), jamais
une connexion en écriture. Toute tentative d'écriture est refusée par SQLite avant d'atteindre le
disque, ce qui protège votre catalogue même d'une régression du code.

Pour le couper sans couper le crawl, mettez `webui.enabled: false` dans `crawler.yml`. Pour couper
le crawl en gardant aMule vivant, c'est
`docker compose exec p2pwatch s6-svc -d /etc/services.d/p2pwatch`.

### Routes disponibles

| Route | Description |
|---|---|
| `/` | Tableau de bord : état de chaque client (version, API joignable, et par canal : sur le réseau, joignable par les pairs), d'après la dernière lecture de moins de deux minutes ; état du crawl, avec les boutons pause, reprise et redémarrage du crawler seul (le conteneur reste debout, aMule garde ses sessions) ; couverture par cible (épisodes trouvés et manquants) |
| `/files` | Liste paginée des fichiers ; filtres `?target=`, `?tier=`, `?q=` |
| `/files/{file_id}` | Détail d'un fichier, désigné par son `file_id` en 32 caractères hexadécimaux minuscules, tel que la console SQL l'affiche : réseau, identifiant natif, observations, décisions, explication du matching |
| `/targets/{target_id}` | Fichiers d'une cible (alias de `/files?target=`) |
| `/node` | État du crawler : `node_id`, entrées du `scheduler_state` et téléchargements (voir [Suivre un téléchargement](#suivre-un-téléchargement)). |
| `POST /controls/pause`, `/controls/resume`, `/controls/restart` | Les boutons du tableau de bord, qui y renvoient. La pause n'arrête pas les recherches en cours, ni celles qui attendent déjà leur créneau réseau : avec K mots-clés, jusqu'à K-1 recherches ed2k, espacées d'une minute, et la prochaine de chaque cible Kad partent encore, puis le crawler se tait. |
| `/console` | Console SQL en lecture seule : un unique `SELECT` sur `catalog.db` ou `local.db`, avec export CSV. Toujours active. |
| `/health` | Healthcheck JSON : répond `{"status": "ok"}` si le service est opérationnel |

Les `POST /controls/*` déclenchent des actions qui modifient l'état, sans jeton CSRF ni
authentification, par conception. `/console` est structurellement en lecture seule et bornée contre
le déni de service (délai maximum, plafond de lignes rendues, une seule instruction). **Ces deux surfaces ne sont
défendables que derrière votre propre périmètre** : réseau privé, VPN ou reverse proxy authentifié,
jamais sur Internet.

### Suivre un téléchargement

La table des téléchargements de `/node` montre pourquoi un téléchargement avance ou non :

- **Dernier progrès** : la dernière fois que le crawler a vu le nombre d'octets reçus augmenter.
  Vide tant qu'il ne l'a jamais vu augmenter, ce qui vaut aussi pour un téléchargement antérieur
  à la mise à niveau, jusqu'à son prochain progrès.
- **Attente** : `no_source` (aucune source connue), `remote_queue` (des sources, aucune
  n'envoie), `local` (aMule vérifie ou alloue le fichier), `paused` (en pause dans aMule),
  `disk_full` (disque plein côté aMule).
- **Échec** : `error` (aMule signale le fichier en erreur), `rejected` (aMule a refusé de le
  démarrer), `lost` (aMule ne le montre plus depuis `download.lost_after_seconds`).

Attendre n'est pas échouer : un téléchargement sans source pendant des mois reste `downloading`
avec sa raison d'attente. Seul un échec le passe en `failed`, et un fichier qu'aMule signale en
erreur y reste tant qu'aMule le signale ainsi.

### Adresse d'écoute et chemins de bases

Le catalogue web ne lit aucune variable d'environnement : il dérive tout de `crawler.yml`. Son
adresse d'écoute interne est figée à `0.0.0.0:8080` dans le code, donc c'est le port publié par
compose qui gouverne l'accès.

| Réglage | Où | Valeur par défaut | Rôle |
|---|---|---|---|
| `catalog_db_path` | `crawler.yml` | `/data/catalog.db` | Base catalogue, lue en lecture seule (= `data/catalog.db` côté hôte) |
| `local_db_path` | `crawler.yml` | `/data/local.db` | Base état local, lue en lecture seule (= `data/local.db` côté hôte) |
| `webui.amule_url` | `crawler.yml` | `http://localhost:4711` | Cible du lien « aMule » dans la navigation. À changer uniquement derrière un reverse proxy : c'est le navigateur qui résout cette URL, pas le conteneur. |
| port publié du catalogue | `compose.yml` (`ports:`) | `8080` | Dans le mapping `"8080:8080"`, changez le nombre de gauche pour publier ailleurs. Ne change pas le port d'écoute interne. |
| port publié d'amuleapi | `compose.yml` (`ports:`) | `4711` | Idem pour amuleapi. Sous la pile VPN, les deux mappings sont portés par le service `gluetun`. |

### Exposition derrière un reverse proxy

Le catalogue web n'a ni TLS ni authentification : mettez un reverse proxy devant dès qu'il est
accessible sur le réseau. Exemple minimal avec Caddy, pointant sur le port publié par le nœud :

```caddyfile title="Caddyfile"
webui.example.com {
    basicauth /* {
        alice $2a$14$...  # bcrypt généré par caddy hash-password
    }
    reverse_proxy node.example.lan:8080
}
```

Pensez alors à `webui.amule_url` dans `crawler.yml` : le lien « aMule » est résolu par le
navigateur, donc `http://localhost:4711` ne veut plus rien dire pour un visiteur distant. Pointez-le
sur l'hôte réel, ou sur un second `reverse_proxy`. amuleapi, lui, a bien un mot de passe.
