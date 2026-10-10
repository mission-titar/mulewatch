---
description: "Monter un nœud en cinq étapes et une quinzaine de minutes : Docker, secrets, premier démarrage, premier catalogue."
---

# Installer un nœud

Cinq étapes, une quinzaine de minutes une fois Docker en place. À la fin, un catalogue web sur
`http://localhost:8080` et un nœud qui cherche, catalogue, vous notifie et télécharge ce qu'il
identifie avec certitude, dans un dossier `downloads/` posé à côté de votre fichier compose.

!!! warning "Vous mettez à niveau un nœud 1.x ?"

    Lisez d'abord [Migrer un nœud 1.x](migration-1x.md), et ne lancez rien avant : la migration se
    fait à la main, une fois.

!!! info "Votre adresse IP sera visible des autres pairs"

    C'est le fonctionnement normal du réseau eMule. Pour la masquer, voyez
    [Passer derrière un VPN](vpn.md) une fois ces cinq étapes faites, et
    [Légalité et vie privée](legal.md) pour ce que le nœud enregistre et ce que vous risquez.

## 1. Installer Docker

Il vous faut une machine qui reste allumée avec une connexion permanente (un nœud n'est utile que
s'il surveille en continu), environ 2 Go de RAM libre et 5 Go de disque pour commencer.

Installez Docker depuis la page officielle : <https://docs.docker.com/get-started/get-docker/>.
Docker Desktop sous Windows et macOS, Docker Engine sous Linux.

!!! success "Point de contrôle"

    ```bash
    docker compose version
    ```

    Doit afficher `Docker Compose version v2.x.x`, ou un numéro plus récent.

## 2. Récupérer le dossier `deploy`

Sur <https://github.com/mission-titar/p2pwatch>, bouton vert **`Code`** puis **`Download ZIP`**.
Décompressez, et gardez **uniquement le dossier `deploy`** : copiez-le où vous voulez, renommez-le à
votre goût. C'est votre **[dossier de travail](glossary.md#vocabulaire-du-projet)** ; toutes les commandes qui suivent s'y lancent. Le
reste du ZIP peut être supprimé.

Vous y trouvez les fichiers compose, les réglages du nœud (`crawler.yml`, `targets.yml`,
`matcher.yml`) et trois dossiers vides qui se rempliront seuls : `data/` (votre catalogue), `amule/`
(l'état du client eMule) et `downloads/` (`incoming/` pour les fichiers terminés, `temp/` pour les
partiels). **Ce sont de simples dossiers sur votre disque**, pas des volumes Docker : les
sauvegarder, c'est les copier.

## 3. Choisir vos mots de passe

Copiez `.env.example` en `.env` (`cp .env.example .env`), ouvrez la copie dans un éditeur, et
renseignez les quatre valeurs obligatoires. Le conteneur refuse de démarrer si l'une manque.

| Variable | Ce qu'il faut y mettre |
|---|---|
| `AMULE_EC_PASSWORD` | Un mot de passe d'au moins **12 caractères**, de votre choix. Il relie entre eux les deux processus aMule ; notez-le quelque part. |
| `AMULE_API_PASSWORD` | Un autre mot de passe de votre choix. Il protège l'interface web d'aMule sur le port 4711, et c'est aussi par lui que le crawler pilote le client eMule. |
| `PUID` | Votre identifiant d'utilisateur : `id -u` sous macOS et Linux, `1000` sous Windows. |
| `PGID` | Votre identifiant de groupe : `id -g` sous macOS et Linux, `1000` sous Windows. |

Laissez le reste tel quel, les autres valeurs ne servent qu'au VPN. **Ne laissez aucun `change-me`
en place** : ce sont des mots de passe en clair, donc des portes ouvertes.

!!! danger "Le port 8080 n'a aucune authentification"

    `AMULE_API_PASSWORD` protège le port **4711 seulement**. Le catalogue p2pwatch sur le port
    **8080** est servi **sans mot de passe, sans connexion, sans jeton CSRF**, et il expose le
    catalogue, les
    contrôles de crawl (pause, redémarrage) **et une console SQL en lecture seule** à
    quiconque atteint ce port.

    C'est voulu : l'authentification est déléguée à ce que vous mettez devant. Sur une machine
    joignable depuis Internet, mettez-le derrière un reverse proxy authentifié, ou un VPN, ou ne
    publiez pas le 8080 du tout. Voir
    [Faire tourner un nœud, § Exposition derrière un reverse proxy](operate.md#exposition-derrière-un-reverse-proxy).

## 4. Lancer

```bash
docker compose up -d
```

Au premier lancement, Docker télécharge l'image, ce qui peut prendre quelques minutes.

!!! success "Point de contrôle"

    ```bash
    docker compose ps
    ```

    Doit montrer **un seul service**, `p2pwatch`, dont l'état commence par `Up`. Au bout d'une
    demi-minute environ, il passe à `Up (healthy)` : le client eMule tourne vraiment à l'intérieur.

## 5. Ouvrir le catalogue

Votre nœud sert deux pages web :

| Adresse | Ce que c'est | Mot de passe |
|---|---|---|
| <http://localhost:8080> | **Le catalogue p2pwatch** : catalogue en lecture seule, contrôles de crawl, console SQL. | **Aucun.** Voir l'avertissement de l'étape 3. |
| <http://localhost:4711> | **L'interface web propre à aMule** : recherche, transferts, serveurs, état Kad. | `AMULE_API_PASSWORD` de votre `.env`. |

Sur un serveur distant, remplacez `localhost` par son adresse.

!!! success "Point de contrôle"

    <http://localhost:8080> affiche le tableau de bord, avec l'état d'aMule, l'identifiant de votre
    nœud et la liste des épisodes cibles. **Si cette page se charge, votre nœud tourne.**

!!! note "Un catalogue vide au début est normal"

    Il se remplit au fil des heures, et certaines cibles rares mettent des jours à réapparaître :
    c'est la nature du lost media.

---

**Un Point de contrôle échoue ?** Chaque symptôme a sa fiche dans
[Le déploiement bloque](troubleshooting-start.md).

**Et ensuite ?** Mettre à jour, arrêter, sauvegarder, régler les intervalles, prévoir la place
disque : [Faire tourner un nœud](operate.md).
