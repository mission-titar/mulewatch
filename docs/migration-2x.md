---
description: "Migrer un nœud 2.x vers la 3.0 : renommer une variable, renommer une clé, redémarrer."
---

# Migrer un nœud 2.x vers la 3.0

La 3.0 remplace `amuleweb` par **amuleapi**, la nouvelle interface web d'aMule, dont le crawler
se sert aussi pour piloter le client eMule. Aucune donnée ne bouge, aucun port ne change. Deux
éditions, depuis le dossier de votre `compose.yml` :

```bash
docker compose down     # ou -f gluetun.compose.yml si vous êtes sur la pile VPN
sed -i 's/^WEBUI_PWD=/AMULE_API_PASSWORD=/' .env
sed -i 's/^amule_ec_password: .*/amule_api_password: ${AMULE_API_PASSWORD}/' crawler.yml
docker compose up -d
```

Le mot de passe lui-même ne change pas : il protège toujours l'interface d'aMule sur le port 4711,
et il sert désormais aussi au crawler. Gardez `AMULE_EC_PASSWORD` dans `.env` : amuleapi et amuled
continuent de se parler avec.

Sans ces éditions, le nœud ne redémarre pas : le conteneur s'arrête en nommant la variable
manquante, ce qui vaut mieux qu'un nœud qui démarre et ne se connecte à rien.

## Ce qui change

- **Le port 4711 sert l'interface d'amuleapi**, plus complète que celle d'amuleweb, avec le même
  mot de passe.
- **Il n'y a plus que deux services supervisés.** `s6-svstat /etc/services.d/amuleweb` n'existe
  plus ; amuleapi n'est pas un service s6 non plus, c'est amuled qui le démarre.
- **La durée, le débit et le codec commencent à se remplir** dans le catalogue, quand le serveur
  qui répond les annonce. C'est une déclaration du réseau, jamais une mesure du fichier.

## Retour arrière

Remettez `WEBUI_PWD` et `amule_ec_password`, épinglez votre ancienne image
(`ghcr.io/geoffreycoulaud/mulewatch:2.0.0`, restée sous l'ancien propriétaire) dans `compose.yml`,
puis `docker compose up -d`. Rien n'a été supprimé côté données ; la section `[AmuleApi]` et le
fichier `amuleapi-passwords` écrits dans `amule/` sont ignorés par une 2.x.
