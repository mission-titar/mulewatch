---
description: "Migrer un nœud 1.x vers la 2.0 : une seule image, un seul service, et les données sorties des volumes nommés."
---

# Migrer un nœud 1.x vers la 2.0

La 2.0 remplace deux images et jusqu'à quatre services par une seule image et un seul service, et
sort les données des volumes nommés vers de simples dossiers. Aucune migration automatique : faites
les six étapes à la main, le vieux nœud arrêté, depuis le dossier de l'ancien `compose.yaml`.

!!! warning "Sauvegardez d'abord votre dossier de travail"

    Cette migration édite vos fichiers de config sur place. Copiez le dossier entier avant de
    commencer : `cp -a . ../mulewatch.1x.bak`.

**1. Arrêtez le vieux nœud.** Sans `-v` : les volumes nommés sont vos données et votre retour arrière.

```bash
docker compose down     # ou -f gluetun.compose.yml si vous étiez sur la pile VPN
```

**2. Copiez les volumes dans les nouveaux dossiers.** Copiez, ne déplacez pas.

```bash
mkdir -p data amule downloads/incoming downloads/temp
docker run --rm -v mulewatch_catalog-db:/src  -v "$PWD/data":/dst  alpine sh -c "cp -a /src/. /dst/"
docker run --rm -v mulewatch_local-db:/src    -v "$PWD/data":/dst  alpine sh -c "cp -a /src/. /dst/"
docker run --rm -v mulewatch_amule-state:/src -v "$PWD/amule":/dst alpine sh -c "cp -a /src/. /dst/"
ls data/     # catalog.db et local.db, côte à côte
```

Noms inconnus ? `docker volume ls` — avant le renommage du projet, le préfixe était `deploy_`.

**3. Remontez les configs d'un cran.** La 2.0 les monte d'à côté du fichier compose.

```bash
mv config/crawler/crawler.yml config/crawler/targets.yml config/crawler/matcher.yml .
rmdir config/crawler config
```

**4. Éditez `crawler.yml`** (`deploy/crawler.yml` de la 2.0 fait référence) :

| Action | Clé | Pourquoi |
|---|---|---|
| Retirez | `amules:` | un seul client eMule, adresse figée dans le code |
| Retirez | `download.endpoint` | idem |
| Retirez | `port_sync.restarter_url` | plus de proxy Docker |
| Ajoutez | `amule_ec_password: ${AMULE_EC_PASSWORD}` (racine) | |
| Changez | `catalog_db_path` → `/data/catalog.db` | |
| Changez | `local_db_path` → `/data/local.db` | |
| Changez | `download.output_dir` → `/downloads` | |
| Changez | `port_sync.gluetun_control_url` → `http://localhost:8000` | namespace réseau partagé avec gluetun |

**5. Complétez `.env`, puis prenez possession des dossiers.** Ajoutez `PUID`, `PGID` et `WEBUI_PWD`
(étape 4 du parcours principal).

```bash
sudo chown -R "$PUID:$PGID" data amule downloads
```

**6. Démarrez.** La pile directe est maintenant `compose.yml`, plus `compose.yaml`.

```bash
docker compose up -d
docker compose ps        # un seul service, `mulewatch`, Up (healthy) en ~30 s
```

## Ce qui change au premier boot

- **Votre `amule.conf` est conservé**, sauf `ECPassword` : le conteneur l'aligne à chaque boot sur
  `AMULE_EC_PASSWORD`. Changez ce mot de passe par `.env` et un redémarrage. Vérifiez au passage
  que `IncomingDir` et `TempDir` pointent sur `/downloads/incoming` et `/downloads/temp` :
  ```bash
  grep -E "^(Incoming|Temp)Dir" amule/amule.conf
  ```
- **Le catalogue est repris intact** : `catalog.db` est append-only, son schéma ne bouge pas.
- **Le backoff de recherche repart de zéro, une fois.** Le nom du client est désormais la constante
  `amuled` ; les lignes indexées sur l'ancien nom (typiquement `amule-1`) sont ignorées. Sans
  gravité, mais cela explique les journaux bavards du premier boot.
- **Le label `instance` a disparu** des métriques Prometheus. Retirez-le des tableaux de bord qui
  groupaient dessus.

## Retour arrière

L'image 1.x reste publiée sous `ghcr.io/geoffreycoulaud/mulewatch-crawler`, figée et jamais
supprimée. Restaurez votre ancien `compose.yaml`, votre `.env` et votre `config/crawler/`, remettez
votre tag 1.x dans `IMAGE_TAG` (la 2.0 n'a plus cette variable), puis `docker compose up -d`. Les
volumes ont été copiés, jamais supprimés : le vieux nœud retrouve ses données.

Une fois le nouveau nœud éprouvé, et seulement après avoir vérifié que `data/catalog.db` contient
votre historique, supprimez les anciens volumes — **point de non-retour** :

```bash
docker volume rm mulewatch_catalog-db mulewatch_local-db mulewatch_amule-state
```
