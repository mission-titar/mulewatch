# mulewatch

mulewatch surveille en continu le réseau eMule (eD2k + Kad) pour retrouver des médias perdus. Sa
première mission : la version française de *Keroro mission Titar* (Teletoon, 2008), aujourd'hui
quasiment introuvable. Ces épisodes n'ont pas tout à fait disparu : ils refont surface par
intermittence, le temps qu'un détenteur reste connecté, puis s'évanouissent. Une recherche manuelle
tombe presque toujours au mauvais moment ; une veille permanente, non. Un nœud cherche sans relâche,
catalogue ce qu'il croise, et alerte dès qu'un épisode manquant apparaît. Le sujet du catalogue est
**le fichier, jamais la personne** : mulewatch ne piste personne et ne cherche à désanonymiser
personne.

**[Documentation complète](https://mission-titar.github.io/mulewatch/)** : installer un nœud, le
faire tourner, le dépanner, et ce qu'il faut savoir côté légalité et vie privée.

Le projet vit dans l'organisation `mission-titar` depuis septembre 2026. Les versions antérieures
(releases, images jusqu'à la 3.1.0) restent sur le
[dépôt d'origine, archivé](https://github.com/GeoffreyCoulaud/mulewatch).

## Développement

```bash
./scripts/setup-dev.sh   # environnement (uv sync --dev) et hook pre-push
uv run poe check         # le gate complet : lint, types, SQL, tests
```

Les conventions et invariants du projet sont dans [`AGENTS.md`](AGENTS.md) ; les specs, plans et
handoffs dans [`agents/`](agents/).
