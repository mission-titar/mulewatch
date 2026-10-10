---
description: "Surveillance continue du réseau eMule pour retrouver des médias perdus, à commencer par la VF de Keroro mission Titar."
---

# p2pwatch

p2pwatch surveille le réseau eMule en continu pour retrouver des médias perdus. Sa première
mission : la version française de *Keroro mission Titar*, diffusée sur Teletoon en 2008 et
aujourd'hui quasiment introuvable.

Ces épisodes n'ont pas disparu. Ils réapparaissent par intermittence, le temps qu'un détenteur reste
connecté, puis s'évanouissent. Une recherche manuelle tombe presque toujours au mauvais moment. Un
nœud qui tourne jour et nuit, non : il cherche sans relâche, note tout ce qu'il croise, et vous
prévient dès qu'un épisode manquant apparaît.

**Le sujet du catalogue est le fichier, jamais la personne.** p2pwatch ne piste personne et ne
cherche à désanonymiser personne. Il enregistre qu'un fichier existe, où et quand il a été vu, et
rien d'autre.

## Par où commencer

<div class="grid cards" markdown>

-   __[Installer un nœud](install.md)__

    ---

    Monter un nœud, de zéro à un catalogue qui se remplit.

-   __[Faire tourner un nœud](operate.md)__

    ---

    Le piloter au quotidien, le sauvegarder, le régler.

-   __[Le déploiement bloque](troubleshooting-start.md)__

    ---

    Réparer quelque chose qui ne marche pas.

-   __[Légalité et vie privée](legal.md)__

    ---

    Savoir ce que vous risquez et ce que le nœud stocke.

-   __[Glossaire](glossary.md)__

    ---

    Comprendre un mot croisé en chemin.

</div>

Comptez une quinzaine de minutes pour l'installation, une fois Docker en place. Installer Docker est
de loin l'étape la plus longue ; le reste tient en une commande et un mot de passe à choisir.

Un nœud, c'est **deux conteneurs**. `p2pwatch` cherche, catalogue et sert le catalogue web.
`ed2k` fait tourner `amuled`, le client eMule, et `amuleapi`, son interface web, qu'`amuled` démarre
lui-même. Vous n'avez normalement pas à le savoir, mais cela compte dès que vous lisez les journaux
ou redémarrez une pièce, et les pages le rappellent là où ça se voit.

## Partager un catalogue entre chercheurs {#partage}

Chaque chercheur fait tourner **son propre nœud**. Il n'y a pas de serveur central, et c'est
volontaire. Les nœuds ne se connaissent pas et ne se synchronisent jamais : le partage se fait à la
main, en s'échangeant des fichiers de catalogue.

Votre catalogue est un simple fichier, `data/catalog.db` dans votre dossier de travail. Pour le
partager, copiez-le (nœud arrêté) et envoyez-le par le canal que vous voulez. Pour intégrer celui
d'un autre chercheur, fusionnez-le au vôtre avec l'outil `merge`, décrit dans
[Faire tourner un nœud](operate.md#outils-de-catalogue).

La fusion est sûre : chaque fichier est identifié par son empreinte de contenu, donc deux chercheurs
qui ont vu le même fichier écrivent la même ligne. Refusionner deux fois le même catalogue ne change
rien, et rien n'est écrasé sans que vous le demandiez.

Le cycle habituel : vous cataloguez quelques semaines, vous échangez votre `catalog.db`, vous
fusionnez ce que vous recevez, et vous remplacez votre catalogue par le catalogue fusionné.

## Ce qui n'existe pas

Pas de mécanisme pour découvrir les autres chercheurs, pas d'alerte quand un autre nœud trouve un
fichier que vous cherchez, pas de synchronisation automatique, pas de serveur central. Cela viendra
peut-être si le projet réunit du monde ; pour l'instant, l'échange à la main suffit largement.

## Contribuer au code

Le code est en Python, en architecture hexagonale, avec des tests stricts. Voyez
[Architecture du code](contributing/architecture.md) pour comprendre comment le crawler fonctionne,
et [Lancer les tests](contributing/testing.md) pour les suites de tests et leurs prérequis. Les
conventions du projet, les specs et l'historique des décisions vivent dans le dépôt, sous
[`AGENTS.md`](https://github.com/mission-titar/p2pwatch/blob/main/AGENTS.md) et
[`agents/`](https://github.com/mission-titar/p2pwatch/tree/main/agents).
