---
description: "Ce que mulewatch ne fait pas, et les conséquences pratiques des choix assumés."
---

# Limites connues

Ce que mulewatch ne fait pas, et ce qui peut vous mordre. Rien ici n'est un bug : ce sont des choix
assumés, dont voici les conséquences pratiques.

## Le disque se remplit, et rien ne le vide

Les fichiers terminés restent dans `downloads/incoming` indéfiniment. Aucun ménage automatique
n'existe, et il n'y en aura pas : mulewatch n'ouvre jamais un fichier téléchargé, donc il ne peut
pas juger lequel garder.

Le crawler mesure l'espace libre et cesse d'accepter de nouveaux téléchargements quand il passerait
sous `download.min_free_bytes` (10 Gio par défaut). C'est un plancher qui vous protège d'un disque
plein, pas un plafond qui fait le ménage. Le tri reste à votre charge.

Effet secondaire sur un nœud ancien : la liste des fichiers partagés d'aMule est relue à chaque
cycle de téléchargement, et elle grossit avec chaque fichier terminé. Après des centaines de
téléchargements, attendez-vous à une détection de complétion plus lente.

## Une montée d'image demande de la place sur le disque, sur un gros catalogue

Au premier démarrage qui suit une mise à jour, le crawler applique les migrations de base en
attente. Une migration qui construit un index ou réécrit une table **trie dans des fichiers
temporaires**, dans le dossier temporaire du conteneur : sur le disque où Docker range ses
conteneurs, pas dans `data/`. Ce premier démarrage demande donc de la place libre sur ce disque, en
plus du catalogue.

S'il en manque, la migration échoue sans rien abîmer : elle est annulée, le catalogue reste dans son
état d'avant, et le crawler refuse de démarrer. Le diagnostic et le remède sont dans
[« Un conteneur redémarre en boucle »](troubleshooting-start.md#un-conteneur-redémarre-en-boucle).

### Le premier démarrage qui réécrit le catalogue

Les migrations `catalog/0006` à `0009` réécrivent tout le catalogue : chaque fichier change
d'identifiant, et les observations passent à un stockage compact. Mesuré le 2026-10-09 sur une
copie d'un catalogue réel de 11,65 millions d'observations (5,3 Go), avec l'image, la base sur un
dossier monté de Docker Desktop et `--memory 2g` :

| Étape | Durée | Mémoire au plus haut | Disque en plus, au plus haut |
|---|---|---|---|
| `0006` et `0007` | 170 à 235 s | 288 Mio | 0,44 Go |
| `0008` | 10 s | 324 Mio | 0,24 Go |

Comptez donc quelques minutes pendant lesquelles le crawler ne cherche rien et l'interface web ne
répond pas. Le journal annonce chaque migration par une ligne comme `migration 7: applying`. Ne
redémarrez pas le conteneur avant la fin : la migration en cours serait annulée, puis reprise à
zéro. Le disque en plus se répartit entre le WAL, à côté du catalogue, et les fichiers temporaires
dans le conteneur.

Les trois premières laissent le fichier à sa taille d'avant : l'espace libéré reste à l'intérieur.
`0009` le rend au disque (`VACUUM`) puis vide le WAL, et le fichier retombe à la taille de ses données, 0,21 Go pour
un catalogue synthétique de 11,5 millions d'observations (mesuré le 2026-10-08).

## Le durcissement du conteneur s'arrête assez bas

Le premier processus du conteneur tourne en root : il crée l'utilisateur `amule` à partir de vos
`PUID`/`PGID`, prend possession des dossiers montés et écrit la configuration d'aMule. Cela exclut
`user:`, `read_only:` et `cap_drop: ALL` ; chaque service abandonne ensuite ses privilèges de
lui-même. Ce qui reste, et que les fichiers compose doivent conserver :
`no-new-privileges:true`, `pids_limit: 512` et `mem_limit: 2g`.

**Risque accepté** : la compromission de l'un des trois processus atteint tout ce qui est monté,
c'est-à-dire `downloads/`, `data/` (votre catalogue) et `amule/`. Une isolation plus poussée
(namespaces noyau, bac à sable) demanderait des privilèges ou des réglages système non portables :
c'est hors périmètre, délibérément. Ne « corrigez » pas ce point sans rouvrir la décision, qui est
documentée dans le dépôt.

## Trois choses jamais éprouvées en conditions réelles

- **Le port-sync High-ID.** La boucle est construite et testée, mais personne ne l'a encore vue
  obtenir un High-ID stable derrière un VPN à port forwarding, sur du vrai matériel.
- **`no-new-privileges` combiné à l'abandon de privilèges** des trois services. Cohérent sur le
  papier, jamais confirmé sur une machine réelle.
- **Les plafonds `mem_limit: 2g` et `pids_limit: 512`.** Ils ont été relevés pour loger trois
  processus au lieu d'un, sans mesure sur un nœud en production. Si votre nœud se fait tuer sans
  raison apparente, ce sont les deux premiers chiffres à regarder.

## Ce qui n'est pas prévu

Pas de serveur central, pas de découverte entre nœuds, pas de rétention ni de purge automatique du
catalogue. Le partage entre chercheurs se fait à la main, comme décrit sur
[la page d'accueil](index.md#partage).
