# Notifications for researchers: one message per finding

- Date: 2026-10-05
- Status: APPROVED (2026-10-05)
- Scope: tell the community Discord what was found (file, title, ed2k link), once per tier rise,
  and keep operational noise out of it
- Builds on: `agents/specs/2026-10-04-matcher-file-vetoes.md` §7 (findings of the review)
- Related: `application/decisions.py`, `domain/observability/{events,policy}.py`,
  `adapters/observability/apprise_notifier.py`, `adapters/persistence_sqlite/sightings.py`

## 1. Why

A decision today sends `[node] decision download for 062A`: no file name, no title, no ed2k
link, so a researcher cannot act on it. It sends one message per `(hash, target)` row, so a
whole-episode file matching 062A and 062B sends two, and any row change re-sends, a `rule_name`
change included. `notify` decisions go to `operations`, out of the researchers' sight, while
`CrawlerStarted` and `HighIdRecovered` reach `community`, where they are noise. Alarm fatigue is
the failure to avoid: a message must mean "go look at this".

## 2. The event: one per evaluation of a file

`DecisionRecorded` (one per target of the file) becomes `DecisionsRecorded` (one for the file,
all its targets together): what changed in the file's judgement during one evaluation.
`record_decision_if_changed` emits it through `telemetry.emit` once, after recording, when it
wrote at least one row.

| Field | Source |
|---|---|
| `ed2k_hash` | the file |
| `filename`, `size_bytes` | the file's clean name (§4) and size |
| `changes` | tuple of `(target_id, title, before, after)`, one per target whose decision changed |

`before` is the persisted tier, or none for a target never judged; `after` is the fresh tier, or
`retracted`. `describe()` derives everything from this one fact, and stays the only place that
decides to notify:

- metrics: one `emule_decisions{tier=after}` increment per change, as today;
- log: one line listing the changes;
- notification: `community`, only when at least one change is a rise (§3).

The live pipeline and the startup re-evaluation share the helper, so both behave the same way:
a matcher change that reveals findings sends one message per file. Accepted (§6).

## 3. A rise

A change is a **rise** when `after` is `notify` or `download` and ranks above `before`
(`TIER_RANK`; none and `retracted` rank below every tier). So:

| before → after | Notified |
|---|---|
| none / retracted → `notify` or `download` | yes |
| `notify` → `download` | yes |
| `catalog` → `notify` | yes |
| same tier, other `rule_name` | no (logged only) |
| `download` → `notify`, any → `catalog`, retraction | no (logged only) |

No "already announced" store: tiers no longer flap since 2026-09-26, and a file that drops then
rises again is news.

## 4. The message

Built by `describe()` (domain, pure), written in markdown so Discord renders the code block (it
does not linkify `ed2k://`). Rendered:

```
[mulewatch-geoffrey-gluetun-7f3a2c91] 📥 Download

**File**
350.2 MiB - `[TV] KERORO MISSION TITAR N°062 « Les demoiselles cambrioleuses / Le grand combat sous-marin » [Teletoon 2008] [VF] [DVB-T].avi`
`ed2k://|file|[TV]%20KERORO%20...%5BDVB-T%5D.avi|367185920|8F3A1C0B9E7D44A2B6C1F0E9D8A7B6C5|/`

**Targets**
062A - Les demoiselles cambrioleuses
062B - Le grand combat sous-marin
```

- First line: the node id (the apprise adapter's existing prefix, see below) and the highest
  risen tier (`📥 Download`, `🔎 Notify`), the line Discord shows in push notifications and
  channel previews.
- **File** first, what was found: the size before the name, so a long name hides nothing (the
  name in inline code, so Discord does not read its `_` or `*` as markup, nor ping an
  `@everyone` in it; a backtick in the name becomes `'` so it cannot close the span), then the
  ed2k link.
- **Targets** then, why it matters: one line per risen target, `<target_id> - <title>`.
- The **clean name** is the name seen with the most sources (raw `source_count`, range
  `source_count_max`), latest seen on a tie: the mojibake twin of a release has fewer sources. A
  new read `best_name` in `sightings.py` (the only module allowed to read observations), exposed
  by the repository port with the size. Read only when the event is emitted.
- The ed2k link comes from `catalog_matching.ed2k_link.build_ed2k_link` with that name.
- Titles come from a new public `MatchingEngine.target(target_id) -> TargetSegment | None`
  (the engine already indexes targets by id).

The apprise adapter keeps apprise's passthrough on purpose: it passes no `body_format`, so a
Discord URL sends the body as plain message content, which Discord renders as markdown itself.
Declaring `MARKDOWN` (apprise 2.0) makes apprise send an embed instead, and parse every `@word` of
the body into mentions: `@everyone` in a network-supplied filename would ping the channel. The
other messages are plain text and stay readable as markdown.

### The node id prefix, per destination

A Discord webhook already carries an identity (its name, set in Discord or by apprise's
`discord://<botname>@<id>/<token>`), so the `[node-id]` prefix is redundant there. Each entry of
`observability.notifications` takes an optional `node_prefix` (default `true`):

```yaml
notifications:
  - { url: "discord://mulewatch-geoffrey@${DISCORD_WEBHOOK_ID}/${DISCORD_WEBHOOK_TOKEN}",
      tag: community, node_prefix: false }
```

Generic, not a Discord special case: any service with its own identity can drop it. apprise
sends one body to every URL of a tag, so the adapter keeps two apprise groups, prefixed and bare,
and `notify` sends to both. Without the prefix the message starts with `📥 Download`.

## 5. Audiences

| Event | Today | After |
|---|---|---|
| `DecisionsRecorded` | per target: `download` → community, `notify` → operations | community, on a rise only |
| `DownloadCompleted` | community | community: the file is fully downloaded on a node, the episode is saved |
| `CrawlerStarted` | community + operations | operations |
| `HighIdRecovered` | community | operations |

Audience names stay `community` / `operations`.

## 6. Known limits, out of scope

- A large `matcher.yml` change can notify several files at the next start, one message each.
- Several nodes on one Discord each announce the same finding (the `node_id` prefix tells them
  apart); deduplicating across nodes needs a central point that does not exist.
- `DownloadCompleted` keeps its current message (target only); enriching it is a later change.

## 7. Docs

- `docs/operate.md`, re-evaluation: `notify` now reaches *community*, one message per file on a
  tier rise only; `CrawlerStarted` and High-ID recovery go to *operations*.
- `deploy/crawler.yml`: the commented example shows a `community` Discord target with
  `node_prefix: false` and a `botname@`.

## 8. Proof

- TDD, red first: `record_decision_if_changed` emits one `DecisionsRecorded` with every
  row written (decisions and retractions), none when nothing was written.
- `describe(DecisionsRecorded)`: the rise table of §3, row by row; one message for a file with
  several risen targets; metrics per change; the exact message of §4; the audience table of §5.
- `best_name`: raw only, range only, mixed, tie on sources.
- The apprise adapter passes no `body_format` (guards the passthrough of §4); a
  `node_prefix: false` destination gets the bare body, the others the prefixed one; the config
  parses `node_prefix` (default, `false`, not a boolean).
- On the node: after a restart, a test Discord webhook with `node_prefix: false` shows the real
  rendering (markdown rendered from plain content, code block, first line in the push preview)
  before the community channel is configured.
