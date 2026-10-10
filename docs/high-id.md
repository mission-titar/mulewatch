---
description: "Passer un nœud de Low-ID à High-ID : ce que cela apporte, et les risques que cela ajoute."
---

# Devenir High-ID

Par défaut, un nœud est en **Low-ID**, et il fonctionne très bien ainsi : il cherche, catalogue et
télécharge. Il est seulement sous-optimal côté sources, parce que les autres pairs ne peuvent pas le
joindre directement.

Le **High-ID** rend votre machine joignable depuis l'extérieur, ce qui donne plus de sources
directes et une recherche plus efficace. C'est **facultatif** : si vous voulez juste contribuer au
catalogage, le Low-ID suffit et vous pouvez passer cette page.

Pour être joignable, il faut qu'un **port entrant** atteigne aMule. Deux routes, selon que vous
gardez ou non un VPN devant le trafic P2P.

## Route A, recommandée : le port forwarding de votre VPN

gluetun sait demander un **port forwarding** à votre fournisseur VPN. Le port joignable est alors
celui du VPN, et tout le trafic reste dans le tunnel. Le crawler interroge le serveur de contrôle de
gluetun, et quand le port a changé, il redémarre aMule pour qu'il écoute sur le nouveau.

Ce redémarrage est local au conteneur : le crawler et aMule y sont deux processus voisins, donc rien
ne passe par Docker. Il n'y a ni socket Docker, ni proxy, ni service supplémentaire dans la boucle.

**Deux réglages solidaires :**

1. Un fournisseur VPN **qui gère le port forwarding**, et `VPN_PORT_FORWARDING=on` dans votre
   `.env`. Cherchez les fournisseurs marqués `PORT_FORWARDING: yes` dans la
   [liste gluetun](https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers).
2. Dans `crawler.yml`, `port_sync.enabled: true`. Le bloc est déjà présent, avec
   `gluetun_control_url` pointant sur `http://localhost:8000`.

Cette route n'a de sens que sous `gluetun.compose.yml`. Dans la pile par défaut il n'y a pas de
serveur de contrôle gluetun à joindre, donc le port-sync tournera dans le vide : il le signale et
n'arrête pas le nœud pour autant.

Une fois actif, surveillez les événements `port-sync` dans les journaux et les métriques
`emule_port_sync_triggered`, `emule_high_id_recovered` et `emule_port_mismatch`.

## Route B : ouvrir un port vous-même

Si votre fournisseur ne fait pas de port forwarding, redirigez le port `4662` (en TCP **et** en UDP)
depuis votre box vers cette machine, pour que les pairs joignent aMule directement. Si vous changez
de numéro de port, changez-le aussi dans la section `ports:` de `compose.yml`.

C'est une option parfaitement viable. Le choix relève surtout de votre tolérance au risque, sur deux
points :

- **Légalité.** Partager une œuvre sous droit d'auteur est illégal dans la plupart des juridictions,
  et c'est vrai dès qu'on fait tourner un nœud, route B ou non. Le risque pratique pour ce projet
  est faible mais pas nul, et dépend surtout de votre pays. La discussion complète est dans
  [Légalité et vie privée](legal.md).
- **Surface d'attaque.** Un port entrant ouvert est un point d'entrée de plus sur votre réseau
  domestique. Redirigez précisément ce port, jamais une plage, et gardez la machine à jour.

La route A garde tout derrière le VPN sans rien ouvrir chez vous.

## Le port forwardé change toutes les minutes (ProtonVPN et WireGuard)

**Symptôme.** Dans les journaux de `gluetun`, un `port forwarded is <N>` **différent à chaque
renouvellement**, toutes les 45 à 60 secondes, chaque fois précédé de
`ERROR [port forwarding] refreshing port mapping … external port requested as X but received Y`. Le
port-sync ne peut jamais converger : la cible bouge plus vite qu'il ne peut aligner aMule. Résultat,
un Low-ID permanent alors même que le port-sync fonctionne.

**Cause.** Le renouvellement NAT-PMP, obligatoire chez Proton, passe en UDP dans le tunnel
WireGuard. Sur une clé mal configurée, la passerelle ne préserve pas le mapping au renouvellement et
réassigne un port neuf. C'est un problème entre gluetun et Proton, pas un problème du crawler (voir
[gluetun#3196](https://github.com/qdm12/gluetun/issues/3196)). `PORT_FORWARD_ONLY` seul ne suffit
pas : vérifié sur le terrain, le phénomène persiste sur les serveurs P2P.

**Solution.** Régénérez la clé WireGuard depuis le tableau de bord Proton, en couvrant les trois
causes connues d'un coup :

1. **Port Forwarding activé** sur la configuration au moment où vous la générez.
2. **Moderate NAT désactivé.** Proton le documente comme incompatible avec NAT-PMP. C'est la cause
   la plus fréquente.
3. **Une clé propre à ce nœud.** Une même clé réutilisée par un autre gluetun ou un autre appareil
   fait s'écraser mutuellement les renouvellements NAT-PMP.

Remplacez ensuite `WIREGUARD_PRIVATE_KEY` dans `.env` et recréez les deux services. Le conteneur
mulewatch vit dans le namespace réseau de gluetun, il doit donc être recréé avec lui :

```bash
docker compose -f gluetun.compose.yml up -d --force-recreate
```

Gardez `PORT_FORWARD_ONLY: "on"`, qui est correct, juste insuffisant seul. Pour valider, observez
`gluetun` : le port doit apparaître **une fois**, puis rester silencieux plusieurs cycles (plus de
5 minutes), sans `requested X but received Y`.

Si le port-sync ne fait rien du tout, c'est un autre problème :
[« Le port-sync reste inopérant »](troubleshooting.md#le-port-sync-reste-inopérant-toujours-low-id-alors-quil-est-activé).
