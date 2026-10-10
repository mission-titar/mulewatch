---
description: "Migrer un nœud 4.x vers la 5.0 : aMule passe dans son propre conteneur, et ses dossiers sous ed2k/."
---

# Migrer un nœud 4.x vers la 5.0

La 5.0 renomme mulewatch en **p2pwatch** et sépare le nœud en deux conteneurs : `p2pwatch`, le
crawler et son catalogue web, et `ed2k`, le client eMule avec son port-sync. Les dossiers d'aMule
passent sous `ed2k/`, et une ligne de `compose.yml` choisit entre la variante directe et la
variante VPN. Aucune donnée n'est perdue. Six étapes, depuis votre dossier de travail.

!!! warning "Sauvegardez d'abord votre configuration et votre catalogue"

    Le premier démarrage de la 5.0 réécrit le catalogue : cette copie est votre seul retour vers la
    4.x. `amule/` et `downloads/` ne sont que déplacés, inutile de les copier.

    ```bash
    mkdir ../mulewatch.4x.bak
    cp -a .env *.yml data ../mulewatch.4x.bak/
    ```

**1. Arrêtez le nœud 4.x**, avec ses anciens fichiers compose.

```bash
docker compose down     # ou -f gluetun.compose.yml si vous étiez sur la pile VPN
```

**2. Rangez les dossiers d'aMule sous `ed2k/`.** Un déplacement sur le même disque, immédiat quelle
que soit la taille de `downloads/`. `data/` ne bouge pas.

```bash
mkdir ed2k
mv amule downloads ed2k/
```

**3. Éditez `crawler.yml`.** Retirez la section `port_sync:` entière, et ces cinq clés de la
racine : `cycle_interval_seconds`, `search_poll_budget_seconds`, `search_poll_interval_seconds`,
`keyword_pause_min_seconds` et `keyword_pause_max_seconds`. Tant que l'une reste, le crawler refuse
de démarrer en la nommant : `crawler: key 'port_sync' was removed, delete it from crawler.yml`.

**4. Remplacez les fichiers compose** par ceux du dossier `deploy` de la 5.0 : `compose.yml` à la
racine, et les trois fichiers de son dossier `ed2k/` dans le vôtre.

```bash
rm compose.yml base.compose.yml gluetun.compose.yml
cp /chemin/vers/deploy-5.0/compose.yml .
cp /chemin/vers/deploy-5.0/ed2k/*.compose.yml ed2k/
```

Sur la pile VPN, remplacez dans `compose.yml` la ligne `- ed2k/direct.compose.yml` par
`- ed2k/vpn.compose.yml`. Plus de `-f` ensuite : les mêmes commandes servent les deux variantes. Si
vous aviez changé un port, reportez-le : le 8080 est dans `compose.yml`, les ports d'aMule dans
`ed2k/direct.compose.yml` (le 4711 seul dans `ed2k/vpn.compose.yml`).

**5. Réglez `VPN_PORT_FORWARDING` dans `.env`** (variante VPN seulement). C'est désormais le seul
interrupteur du port-sync : si `port_sync.enabled` valait `true`, mettez `on`, sinon laissez `off`.
Si vous aviez changé `gluetun_control_url` ou l'un des deux intervalles, leurs variables sont dans
[Devenir High-ID](high-id.md#route-a-recommandée--le-port-forwarding-de-votre-vpn).

**6. Démarrez.**

```bash
docker compose up -d
docker compose ps       # p2pwatch Up, ed2k Up (healthy) en ~30 s, plus ed2k-gluetun sous VPN
```

Un nœud direct dont la box redirige ses ports redirige désormais `4672/udp` (Kad), et non plus
`4662/udp`. `4662/tcp` ne change pas.

## Ce qui change

- **Le premier démarrage réécrit le catalogue** : trois à cinq minutes sur un gros catalogue,
  pendant lesquelles le catalogue web ne répond pas. Ne redémarrez rien avant la fin, voir
  [Limites connues](limits.md#le-premier-démarrage-qui-réécrit-le-catalogue).
- **Deux journaux.** `docker compose logs p2pwatch` ne montre plus que le crawler ; amuled et le
  port-sync écrivent dans `docker compose logs ed2k`, amuleapi dans `ed2k/amule/amuleapi.log`.
- **Sous la variante VPN, le crawler sort du tunnel.** Seul `ed2k` passe par `ed2k-gluetun`. Le
  crawler ne parle à aucun pair, et vos notifications partent de l'adresse de votre machine.
- **Une configuration invalide fait redémarrer `p2pwatch` en boucle** (`Restarting`) au lieu
  d'arrêter le conteneur. L'erreur est dans `docker compose logs p2pwatch`.
- **Les métriques `emule_*` deviennent `p2pwatch_*`.** Les trois du port-sync
  (`emule_port_sync_triggered`, `emule_port_mismatch`, `emule_high_id_recovered`) disparaissent
  avec leurs notifications : un Low-ID se lit sur `p2pwatch_channel_connectable`, et un canal non
  joignable depuis 5 minutes est notifié.
- **La documentation** est désormais sur <https://mission-titar.github.io/p2pwatch/>.

## Retour arrière

```bash
docker compose down
mv ed2k/amule ed2k/downloads . && rm -r ed2k data
cp -a ../mulewatch.4x.bak/. .
docker compose up -d    # ou -f gluetun.compose.yml
```

Votre ancien `compose.yml` tire toujours `ghcr.io/mission-titar/mulewatch:latest`, qui reste la
4.1.0. Le catalogue revient à son état d'avant la migration.
