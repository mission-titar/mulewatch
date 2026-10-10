---
description: "Les réglages courants d'un nœud en route : cataloguer sans télécharger, changer un port, brancher la supervision."
---

# Régler le nœud

Les réglages que vous êtes le plus susceptible de vouloir changer une fois le nœud en route : ne
rien télécharger, déplacer un port, brancher votre supervision.

## Cataloguer sans télécharger

Par défaut, un nœud télécharge les candidats qu'il identifie avec certitude. Pour seulement
cataloguer et être notifié, sans qu'aucun fichier n'atterrisse sur votre disque :

1. Dans `crawler.yml`, passez `download.enabled` de `true` à `false`.
2. Relancez depuis votre dossier de travail :

   ```bash
   docker compose up -d
   ```

Rien d'autre ne change. Les mêmes conteneurs démarrent, le catalogue web est servi pareil et les
notifications partent toujours. Seule la boucle de téléchargement n'est pas câblée, donc
`ed2k/downloads/incoming` reste vide.

C'est un simple réglage, pas une autre façon de monter le nœud : il n'y a aucun profil compose à
ajouter ni à retirer.

## Changer un port

Les ports ne sont pas des variables : ils sont écrits en clair dans les sections `ports:` des
fichiers compose. Le `8080`, le catalogue, est dans `compose.yml`. Le `4711`, l'interface d'aMule,
est dans `ed2k/direct.compose.yml`, avec les ports eD2k et Kad, ou dans `ed2k/vpn.compose.yml` si
vous êtes derrière un VPN (il y est publié par le service `ed2k-gluetun`).

Ne changez que **le nombre de gauche**, celui côté hôte : `"8090:8080"` publie le catalogue sur
8090. Dans le conteneur, les ports sont figés.

Si un port est déjà pris au lancement, voyez
[« Le port est déjà pris »](troubleshooting-start.md#le-port-est-déjà-pris).

## Derrière un reverse proxy

Si vous mettez un proxy devant le port 8080, réglez `webui.amule_url` dans `crawler.yml` sur
l'adresse à laquelle **le navigateur** peut joindre l'interface d'aMule. Cette clé n'est que la
cible du lien « aMule » de la navigation, et c'est le navigateur, pas le conteneur, qui la résout :
sa valeur par défaut, `http://localhost:4711`, ne veut rien dire pour un visiteur distant.

L'exemple de configuration complet est dans
[Faire tourner un nœud](operate.md#exposition-derrière-un-reverse-proxy).

## Métriques Prometheus

Le crawler expose un point d'accès Prometheus sur le port réglé par `observability.metrics.port`
dans `crawler.yml` (`9090` par défaut, actif par défaut). **Ni Prometheus ni Grafana ne sont livrés
avec la pile** : si vous voulez des tableaux de bord, faites pointer votre propre Prometheus sur le
nœud.

Ce port n'est pas publié sur l'hôte. Pour le joindre, publiez-le vous-même en ajoutant
`"9090:9090"` à la liste `ports:` du service `p2pwatch` dans `compose.yml`, sous les deux
variantes : le crawler n'est jamais derrière le VPN.

Traitez ce port comme le 8080 : **aucune authentification**, donc gardez-le hors de l'Internet
ouvert.

Exemple de `scrape_config` pour votre propre `prometheus.yml` :

```yaml
scrape_configs:
  - job_name: 'p2pwatch'
    static_configs:
      - targets: ['node.example.lan:9090']   # l'hôte sur lequel vous avez publié 9090
```

Pour couper entièrement le point d'accès, mettez `observability.metrics.enabled: false` dans
`crawler.yml`. Le crawl et le catalogue web continuent normalement.
