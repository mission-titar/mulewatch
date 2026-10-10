---
description: "Faire passer le nœud derrière un VPN avec gluetun, pour masquer votre IP aux autres pairs."
---

# Passer derrière un VPN

Pour masquer votre IP aux autres pairs eD2k/Kad, faites passer le nœud par un VPN avec le conteneur
`gluetun`.

**Ce qui change par rapport au parcours principal :**

1. **Un fournisseur VPN qui gère WireGuard.** C'est obligatoire : gluetun établit le tunnel en
   WireGuard.
2. **Trois variables de plus** dans votre `.env` :

   | Variable | Quoi |
   |---|---|
   | `WIREGUARD_PRIVATE_KEY` | La clé privée WireGuard, fournie dans l'espace client de votre VPN. |
   | `VPN_SERVICE_PROVIDER` | Le nom du fournisseur, par exemple `protonvpn`, `pia`, `privatevpn`. |
   | `SERVER_COUNTRIES` | Le ou les pays de sortie, en anglais, par exemple `Switzerland`. |

3. **Un fichier de pile différent.** À la place de `docker compose up -d`, vous utilisez la pile
   `gluetun.compose.yml`, et vous ajoutez `-f gluetun.compose.yml` à **toutes** les commandes
   compose ensuite (`ps`, `logs`, `pull`, `down`, etc.) :

   ```bash
   docker compose -f gluetun.compose.yml up -d
   ```

Cette pile ajoute exactement un service, `gluetun`, donc `docker compose -f gluetun.compose.yml ps`
montre **deux** services au lieu d'un. p2pwatch n'y a pas de réseau propre : il partage celui de
gluetun (`network_mode: service:gluetun`), donc tout son trafic (celui du client eMule compris)
passe par le tunnel, et ses deux pages web sont publiées **sur le service gluetun** à la place.

Le port eD2k n'est délibérément **pas** publié dans cette pile : les connexions entrantes arrivent
par le port forwardé du VPN, pas par votre hôte (voir [Devenir High-ID](high-id.md), route A).

> **Non validé sur matériel réel.** Les sources divergent sur la nécessité de donner aussi
> `FIREWALL_INPUT_PORTS=8080,4711` au pare-feu de gluetun pour les connexions venues de votre LAN.
> Si les deux pages répondent sur l'hôte lui-même mais pas depuis une autre machine de votre réseau,
> cette variable est la première chose à essayer.

---
