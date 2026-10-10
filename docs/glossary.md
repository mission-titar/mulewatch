---
description: "Les termes que la documentation emploie sans les réexpliquer : jargon eMule, conteneur, vocabulaire du projet."
---

# Glossaire

Les termes que la documentation emploie sans les réexpliquer à chaque fois.

## Jargon eMule et conteneur

Ceux-là sont opaques la première fois qu'on les croise. Inutile de revenir ici : partout
ailleurs sur le site, ils sont soulignés en pointillés et affichent leur définition.

eD2k
:   eDonkey2000, le plus ancien des deux réseaux eMule surveillés. Il passe par des serveurs
    centraux, auxquels le nœud se connecte.

Kad
:   Kademlia, le second réseau surveillé. Décentralisé : aucun serveur, les clients se trouvent
    entre eux. Le nœud cherche sur les deux en parallèle.

High-ID
:   Votre nœud est joignable depuis l'extérieur, donc les autres clients peuvent l'appeler
    directement. C'est le bon cas : plus de sources, des téléchargements plus rapides.

Low-ID
:   Votre nœud n'est pas joignable de l'extérieur — pare-feu, box, ou VPN sans port ouvert. Tout
    fonctionne quand même, simplement avec moins de sources. C'est l'état par défaut, et il
    convient parfaitement pour cataloguer.

IncomingDir
:   Le dossier où le client eMule dépose un fichier une fois terminé. Ici, il correspond à
    `downloads/incoming` dans votre dossier de travail.

s6
:   Le petit superviseur qui, à l'intérieur du conteneur, fait tourner deux programmes (`amuled` et
    `p2pwatch`) et en relance un s'il meurt. Vous le croisez surtout dans les journaux.

amuleapi
:   L'interface web et REST livrée avec aMule, qu'`amuled` démarre et arrête avec lui. C'est elle
    que vous ouvrez sur le port 4711, et c'est par elle que `p2pwatch` pilote le client eMule :
    lancer une recherche, mettre un fichier en file, lire l'état. Une erreur d'authentification
    signifie presque toujours un `AMULE_API_PASSWORD` qui ne correspond pas.

EC
:   *External Connection*, le canal interne par lequel `amuleapi` parle à `amuled`. Les deux sont
    dans le même conteneur ; `AMULE_EC_PASSWORD` est ce qui les relie.

## Vocabulaire du projet

Des mots courants employés dans un sens précis. Le contexte suffit en général.

pile
:   Le ou les conteneurs que `docker compose` démarre ensemble, décrits par un fichier compose.
    La pile par défaut n'en a qu'un ; la pile VPN ajoute `gluetun`.

service
:   Une brique de la pile : un conteneur géré par `docker compose`. Un nœud est un service,
    `p2pwatch`, deux avec le VPN.

dossier de travail
:   Le dossier qui contient votre `compose.yml`, votre `.env` et vos données (`data/`, `amule/`,
    `downloads/`). Toutes les commandes de la documentation se lancent depuis là.

--8<-- [start:abbr]
*[eD2k]: eDonkey2000, le réseau eMule à serveurs centraux
*[Kad]: Kademlia, le réseau eMule décentralisé, sans serveur
*[High-ID]: Nœud joignable depuis l'extérieur : plus de sources, téléchargements plus rapides
*[Low-ID]: Nœud non joignable de l'extérieur : moins de sources, mais tout fonctionne
*[IncomingDir]: Le dossier où le client eMule dépose un fichier terminé, ici downloads/incoming
*[s6]: Le superviseur qui fait tourner les trois programmes du conteneur et les relance
*[EC]: External Connection, le canal par lequel p2pwatch pilote le client eMule
--8<-- [end:abbr]
