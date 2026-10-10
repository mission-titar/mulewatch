---
description: "Faire passer le nœud derrière un VPN avec gluetun, pour masquer votre IP aux autres pairs."
---

# Passer derrière un VPN

Pour masquer votre IP aux autres pairs eD2k/Kad, faites passer le client eMule par un VPN avec le
conteneur `gluetun`.

**Ce qui change par rapport au parcours principal :**

1. **Un fournisseur VPN qui gère WireGuard.** C'est obligatoire : gluetun établit le tunnel en
   WireGuard.
2. **Trois variables de plus** dans votre `.env` :

   | Variable | Quoi |
   |---|---|
   | `WIREGUARD_PRIVATE_KEY` | La clé privée WireGuard, fournie dans l'espace client de votre VPN. |
   | `VPN_SERVICE_PROVIDER` | Le nom du fournisseur, par exemple `protonvpn`, `pia`, `privatevpn`. |
   | `SERVER_COUNTRIES` | Le ou les pays de sortie, en anglais, par exemple `Switzerland`. |

3. **Une ligne à changer dans `compose.yml`.** Sous `include:`, remplacez
   `- ed2k/direct.compose.yml` par `- ed2k/vpn.compose.yml`. Les commandes ne changent pas :

   ```bash
   docker compose up -d
   ```

Cette variante ajoute un service, `ed2k-gluetun`, donc `docker compose ps` en montre **trois**.
Seul le client eMule, `ed2k`, passe par le tunnel : il n'a pas de réseau propre et partage celui
d'`ed2k-gluetun` (`network_mode: service:ed2k-gluetun`), qui publie donc à sa place l'interface
d'aMule sur le port 4711, et porte le nom `ed2k` par lequel le crawler le joint.

Le crawler, `p2pwatch`, reste **hors du tunnel**, et publie son 8080 lui-même comme dans la
variante directe. Il ne parle à aucun pair : seulement à aMule, et à vos services de notification,
qui voient l'adresse de votre machine.

Le port eD2k n'est délibérément **pas** publié dans cette variante : les connexions entrantes
arrivent par le port forwardé du VPN, pas par votre hôte (voir [Devenir High-ID](high-id.md),
route A).

> **Non validé sur matériel réel.** Les sources divergent sur la nécessité de donner aussi
> `FIREWALL_INPUT_PORTS=4711` au pare-feu de gluetun pour les connexions venues de votre LAN. Si
> l'interface d'aMule répond sur l'hôte lui-même mais pas depuis une autre machine de votre réseau,
> cette variable est la première chose à essayer. Le crawler qui joint aMule par le nom `ed2k`
> d'`ed2k-gluetun` n'a pas encore tourné sur un vrai nœud non plus : si le tableau de bord montre
> aMule injoignable sous cette variante seulement, c'est là que regarder.

---
