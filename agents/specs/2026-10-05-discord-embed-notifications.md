# Notifications as Discord embeds, mentions neutralised

- Date: 2026-10-05
- Status: APPROVED (2026-10-05)
- Scope: send every notification as markdown, so Discord shows an embed; finding and completion
  messages in a fixed shape; no mention possible
- Corrects: `agents/specs/2026-10-05-researcher-notifications.md` §4 (plain content, PR #94)
- Related: `domain/observability/{events,policy}.py`, `ports/telemetry.py`,
  `adapters/observability/{apprise_notifier,dispatcher}.py`, `application/run_download_cycle.py`,
  `deploy/crawler.yml`

## 1. Why

PR #94 sends plain content: Discord renders it, but it is hard to read, and the inline ed2k link
right after the inline name blurs where one ends and the other starts. Embed experiments on a
private channel (2026-10-05) read much better, in the channel and in the push preview. PR #94
avoided markdown because apprise then parses every `@word` of the body into mentions; this spec
keeps the embed and closes that hole instead. The completion message (`✅ download completed:
074A`) is aligned on the finding's shape at the same time.

## 2. The messages

Validated on Discord. A finding, sections in this order:

```
author: Mulewatch - <node-id>
title:  📥 Download

**Targets**
074A - Keroro special

**File**
93.5 MiB - `[TV] KERORO MISSION TITAR N°074A « Kéroro spécial » [Mercredi 15 octobre 2008 à 16H50 sur TELETOON].avi`

**ed2k**
`ed2k://|file|...|98078720|f3fffa86ceee8b8dca030c67c4d393a3|/`
```

A completed download, no size and no link (the file is already here):

```
author: Mulewatch - <node-id>
title:  ✅ Downloaded

**Targets**
074A - Keroro special

**File**
`[TV] KERORO MISSION TITAR N°074A « Kéroro spécial » [Mercredi 15 octobre 2008 à 16H50 sur TELETOON].avi`
```

- The heading (`📥 Download`, `🔎 Notify`, `✅ Downloaded`) is the embed title: `Report` gains a
  `title` field, empty for every other event, and the `Notifier` port's `notify` takes it.
- A name keeps its backtick replacement (`'`), so it cannot close its code span.
- `DownloadCompleted` carries the clean name and the targets with their titles: every target of
  the hash at tier `download` (`download_decisions()`, titles from `DownloadDeps.targets`), so a
  whole-episode file lists 062A and 062B. The clean name comes from `best_observation`, added to
  the loop's `CatalogReader`. The log line keeps its current one-line form.

## 3. Colours

apprise has four notification types, one colour each: info blue, success green, warning yellow,
failure red. A new `Severity.SUCCESS` (logged as INFO) maps to success.

| Message | Type | Colour |
|---|---|---|
| `🔎 Notify`, `📥 Download` | info | blue |
| `✅ Downloaded` | success | green |
| `operations` alerts | warning, as today | yellow |

## 4. The apprise adapter

- `body_format=NotifyFormat.MARKDOWN` on every message: a Discord destination gets an embed,
  email gets HTML, a text-only service gets the markdown as is.
- **No mention, ever.** apprise turns `@word`, `<@id>` and `<@&id>` of a markdown body into
  pings. The adapter puts a zero-width space after every `@` of the title and the body, whatever
  the event: mulewatch never mentions anyone. Mentions displayed inside an embed never ping; only
  the ones apprise extracts do. The ed2k link is unaffected (its `@` is already `%40`).
- Identity: an `AppriseAsset` with `app_id="Mulewatch - <node-id>"` and the repository URL, the
  embed's author line instead of "Apprise". The node id is fixed for the process lifetime. The
  asset's `image_url_mask` and `image_url_logo` are empty: apprise then sends no avatar,
  thumbnail or logo to any service, so a Discord webhook keeps its own avatar with no URL flag.
- `node_prefix` stays (default `true`), for services that show no author (syslog, ntfy): the
  `[node-id]` prefix goes into the title. A Discord destination sets it to `false`, since the
  author already names the node.

## 5. Config and docs

- `docs/operate.md`: the notification paragraph mentions the embed and the author line.
- The researcher notifications spec §4 and its handoff point to this spec; the "never pass
  MARKDOWN" pitfall becomes "every `@` is neutralised".

## 6. Proof

- TDD, red first: both messages of §2 exactly (title and body); every other event has an empty
  title; the colours of §3.
- Download loop: a completion emits the clean name and every `download` target of the hash.
- Adapter: MARKDOWN passed; the asset identity with the node id and no image; a body and a title with
  `@everyone`, `@here`, `<@123>` and `<@&456>` reach apprise with no match of apprise's own
  `USER_ROLE_DETECTION_RE` (imported from the installed apprise, not copied); the prefix in the
  title only for a prefixed destination.
- On Discord: the private test channel shows both embeds, the push preview, and no ping for a
  hostile name.
