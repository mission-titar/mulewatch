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
celui du VPN, et tout le trafic d'aMule reste dans le tunnel. Le **port-sync**, un service qui
tourne à côté d'amuled dans le conteneur `ed2k`, interroge chaque minute le serveur de contrôle de
gluetun. Quand le port a changé, il arrête amuled, écrit le nouveau port (TCP et UDP) dans
`amule.conf`, puis le redémarre, au plus une fois toutes les 5 minutes.

Ce redémarrage reste dans le conteneur `ed2k`, par son superviseur s6 : ni socket Docker, ni
proxy, ni mot de passe, et le crawler n'y prend aucune part.

**Un seul réglage :** un fournisseur VPN **qui gère le port forwarding**, et
`VPN_PORT_FORWARDING=on` dans votre `.env`. Cherchez les fournisseurs marqués
`PORT_FORWARDING: yes` dans la
[liste gluetun](https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers). Cette variable
allume aussi le port-sync, et elle seule : `crawler.yml` n'a plus de section `port_sync:`, et la
refuse.

Le port-sync n'existe que sous la [variante VPN](vpn.md). Dans la variante directe, il reste
arrêté. Ses autres réglages ont des valeurs par défaut :

| Variable | Défaut | Rôle |
|---|---|---|
| `GLUETUN_CONTROL_URL` | `http://localhost:8000` | Le serveur de contrôle de gluetun. |
| `PORT_SYNC_POLL_SECONDS` | `60` | L'intervalle entre deux lectures du port. |
| `PORT_SYNC_RESTART_MIN_SECONDS` | `300` | L'intervalle minimal entre deux redémarrages d'amuled. |

Pour en changer un, ajoutez-le sous `environment:` du service `ed2k` dans `ed2k/vpn.compose.yml`.
Une valeur invalide arrête le conteneur `ed2k` au démarrage, en nommant la variable.

Une fois actif, le port-sync écrit chaque changement et chaque échec dans
`docker compose logs ed2k`. Le High-ID se lit sur le tableau de bord (le canal `ed2k` joignable
par les pairs) et dans la jauge `p2pwatch_channel_connectable`. Un canal non joignable depuis
5 minutes est notifié sur le canal *operations*. Pendant un redémarrage, le crawler voit aMule
injoignable, mais ne le notifie qu'au-delà de 2 minutes.

## Route B : ouvrir un port vous-même

Si votre fournisseur ne fait pas de port forwarding, redirigez `4662/tcp` (eD2k) et `4672/udp`
(Kad) depuis votre box vers cette machine, dans la variante directe, pour que les pairs joignent
aMule directement. Si vous changez de numéro de port, changez-le aussi dans la section `ports:` de
`ed2k/direct.compose.yml`.

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

**Symptôme.** Dans les journaux d'`ed2k-gluetun`, un `port forwarded is <N>` **différent à chaque
renouvellement**, toutes les 45 à 60 secondes, chaque fois précédé de
`ERROR [port forwarding] refreshing port mapping … external port requested as X but received Y`. Le
port-sync ne peut jamais converger : la cible bouge plus vite qu'il ne peut aligner aMule. Résultat,
un Low-ID permanent alors même que le port-sync fonctionne.

**Cause.** Le renouvellement NAT-PMP, obligatoire chez Proton, passe en UDP dans le tunnel
WireGuard. Sur une clé mal configurée, la passerelle ne préserve pas le mapping au renouvellement et
réassigne un port neuf. C'est un problème entre gluetun et Proton, pas un problème de p2pwatch (voir
[gluetun#3196](https://github.com/qdm12/gluetun/issues/3196)). `PORT_FORWARD_ONLY` seul ne suffit
pas : vérifié sur le terrain, le phénomène persiste sur les serveurs P2P.

**Solution.** Régénérez la clé WireGuard depuis le tableau de bord Proton, en couvrant les trois
causes connues d'un coup :

1. **Port Forwarding activé** sur la configuration au moment où vous la générez.
2. **Moderate NAT désactivé.** Proton le documente comme incompatible avec NAT-PMP. C'est la cause
   la plus fréquente.
3. **Une clé propre à ce nœud.** Une même clé réutilisée par un autre gluetun ou un autre appareil
   fait s'écraser mutuellement les renouvellements NAT-PMP.

Remplacez ensuite `WIREGUARD_PRIVATE_KEY` dans `.env` et recréez les deux services d'aMule. Le
conteneur `ed2k` vit dans le namespace réseau d'`ed2k-gluetun`, il doit donc être recréé avec lui :

```bash
docker compose up -d --force-recreate ed2k-gluetun ed2k
```

Gardez `PORT_FORWARD_ONLY: "on"`, qui est correct, juste insuffisant seul. Pour valider, observez
`ed2k-gluetun` : le port doit apparaître **une fois**, puis rester silencieux plusieurs cycles (plus de
5 minutes), sans `requested X but received Y`.

Si le port-sync ne fait rien du tout, c'est un autre problème :
[« Le port-sync reste inopérant »](troubleshooting.md#le-port-sync-reste-inopérant-toujours-low-id-alors-quil-est-activé).
