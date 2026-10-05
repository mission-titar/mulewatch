# Handoff: notifications for researchers

## State

Branch `feat/researcher-notifications`, gate green. Not released. Spec:
`agents/specs/2026-10-05-researcher-notifications.md` (approved). Not yet checked on a real Discord
webhook (spec §8).

## What was built

- `DecisionsRecorded`, one per evaluation of a file, replaces the per-target `DecisionRecorded`:
  clean name, size, and every change `(target_id, title, before, after)`. `describe()` derives
  the metrics, one log line and, on a tier rise only, one `community` message (`Report.notification`,
  the multi-line body; `message` stays the log line).
- Clean name: `sightings.best_name` (most sources, then latest), via `CatalogRepository.best_observation`.
- `MatchingEngine.target(target_id)` for the titles.
- `CrawlerStarted` and `HighIdRecovered` go to `operations` only.
- Per-destination `node_prefix` (default true): the apprise adapter keeps a prefixed and a bare group.

## Pitfalls

- Every `@` is neutralised (since `agents/specs/2026-10-05-discord-embed-notifications.md`): with
  `body_format=MARKDOWN` apprise parses `@word`, `<@id>` and `<@&id>` of the body into pings, so
  the adapter puts a zero-width space after every `@` of the title and the body. A test checks it
  against apprise's own `USER_ROLE_DETECTION_RE`.
- A name sits in an inline code span, and a backtick in it becomes `'` so it cannot close the
  span.

## Next

- Spec §8: restart a node on this build with a test Discord webhook (`node_prefix: false`) and
  check the rendering and the push preview before wiring the community channel.
- Then delete the `BACKLOG.md` entry.
