---
description: "Ce que votre nœud fait sur le réseau, ce qu'il stocke, et ce que vous risquez en l'hébergeant."
---

# Légalité et vie privée

Cette page s'adresse à **vous qui hébergez un nœud** `mulewatch` : chez vous, sur un VPS ou dans une
infra que vous administrez. Elle répond à trois questions : ce que votre nœud enregistre, ce que
vous risquez légalement, ce qu'un VPN protège vraiment.

Ce n'est **pas un avis juridique**. Dans un cadre institutionnel (université, association de
préservation, employeur), faites valider par un juriste qui connaît votre juridiction.

## Ce que votre nœud enregistre

Dans le catalogue (`data/catalog.db` et `data/local.db`) :

- Les **empreintes eD2k** des fichiers vus sur le réseau, leur **nom** tel que publié par les pairs,
  leur **taille** et le nombre de sources rapporté.
- Les **décisions de matching** : la cible qu'un fichier satisfait, selon vos règles YAML.
- Des **métadonnées techniques du nœud** : `node_id` interne, état du planificateur, dernière passe
  de catalogage.

Ce qui n'y entre jamais :

- **Aucune IP de pair.** Le crawler passe par le protocole EC d'aMule, qui dit combien de pairs ont
  une copie, jamais qui.
- **Aucune trace utilisateur.** Ni cookie, ni session, ni journal d'accès : la WebUI est en lecture
  seule et n'authentifie personne — exposée, elle exige un reverse proxy pour l'authentification.
- **Aucune télémétrie sortante.** Rien ne part vers un service tiers ; `/metrics` est un endpoint
  local, à scraper si vous le voulez, sans Prometheus ni Grafana dans la pile.
- **Aucun contenu de fichier.** Même en téléchargement actif, mulewatch ne lit jamais les octets
  d'un fichier : ni détection de type, ni sonde média, ni antivirus. Le fichier vit à part dans
  `downloads/incoming`.

Sur votre disque : les bases SQLite, de quelques Mo à quelques Go selon l'usage (voir
[Planification disque](operate.md#planification-disque)), et les fichiers téléchargés, que rien ne
purge : ils s'accumulent jusqu'à votre ménage.

Sur votre réseau : eD2k est un protocole ancien, **non chiffré**. Un pair voit quels fichiers vous
demandez et quels hashes vous proposez. [Derrière un VPN](vpn.md), tout ce trafic passe par le
tunnel et votre FAI ne voit que du chiffré ; dans la pile par défaut, il sort en clair depuis votre
IP domestique et votre FAI voit les flux vers les pairs, sans leur contenu.

## Le risque légal

**Partager une œuvre soumise au droit d'auteur sans autorisation est illégal dans la plupart des
juridictions**, et c'est vrai dès qu'un nœud eMule tourne. Renseignez-vous sur la vôtre : les
dispositifs de riposte varient d'un pays à l'autre, et ils changent.

Trois niveaux d'exposition :

- **Catalogage seul** (`download.enabled: false`) : aMule s'annonce comme source dès qu'un fichier
  est dans son IncomingDir ; sans téléchargement il reste vide, donc exposition faible.
- **Téléchargement actif** (le défaut) : vous téléchargez **et** re-partagez, eMule étant
  symétrique. Les fichiers terminés restent dans l'IncomingDir, donc offerts aux pairs tant que
  vous ne les déplacez pas.
- **[High-ID par la route B](high-id.md)** : un port ouvert sur votre box vous rend joignable
  directement, avec une visibilité accrue comme source et votre IP visible.

En pratique, le risque reste **faible mais non nul** : la surveillance du P2P se concentre sur les
nouveautés à forte valeur commerciale, et eD2k est un réseau vieillissant où la cible de ce projet
— un dessin animé de 2008 jamais réédité — n'intéresse personne. C'est une affaire de probabilité,
pas de légalité. Là où une procédure est engagée, « j'utilisais un VPN » n'est pas une défense,
seulement une piste plus dure à remonter.

## Ce qu'un VPN protège, et ce qu'il ne protège pas

Un VPN bien configuré ([gluetun](vpn.md)) masque **votre IP domestique face aux pairs** — ils voient
celle du serveur VPN — et **vos flux face à votre FAI**, qui ne voit qu'un tunnel chiffré.

Il ne protège pas contre :

- **Une procédure visant votre fournisseur VPN.** Il peut être contraint de livrer ses journaux, ou
  de prouver qu'il n'en garde pas. Vérifiez sa juridiction et son historique.
- **Une fuite DNS ou IPv6.** Des résolutions hors tunnel ou un IPv6 en clair font fuir votre IP ;
  gluetun les bloque par défaut, c'est une de ses raisons d'être.
- **Une corrélation de timing.** Seul en France à télécharger une œuvre obscure à 3 h du matin, vous
  restez identifiable par analyse de flux côté FAI malgré le VPN. De la science-fiction ici : cible
  trop banale, volume trop faible.
- **Un compromis de votre machine.** Le conteneur héberge trois processus et n'est durci que
  partiellement ([risque accepté](limits.md)) : qui en compromet un atteint `downloads/`, `data/`
  (votre catalogue) et `amule/`. Votre IP reste masquée par le VPN, leur contenu non.

## En pratique

- **Préférez la pile VPN** (gluetun, Low-ID) à la pile par défaut.
- **N'exposez sur Internet ni la WebUI ni `/metrics`.** Restez en réseau local, ou passez par un VPN
  d'accès (WireGuard, Tailscale) et un reverse proxy avec authentification.
- **Ne publiez pas votre IP publique** sur des forums liés au projet : « mon nœud est ici, venez
  voir » vous expose même via VPN si vous êtes seul à l'utiliser à cet instant.
- **Gardez votre système à jour**, surtout avec un port entrant ouvert (route B).
- **Ne mélangez pas les usages.** Ce nœud ne sert qu'à mulewatch : pas de bibliothèque P2P partagée
  pré-existante, pas d'autres tests.

Pour échanger un catalogue avec d'autres chercheurs, voir [la page d'accueil](index.md#partage).
