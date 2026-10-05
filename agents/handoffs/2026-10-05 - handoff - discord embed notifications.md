# Handoff: notifications as Discord embeds

## State

Branch `fix/discord-embed-notifications`, gate green. Not released. Spec:
`agents/specs/2026-10-05-discord-embed-notifications.md` (approved), correcting PR #94's plain
content. Checked on a private Discord channel with the real `describe()` and adapter: blue
`📥 Download` (Targets, File, ed2k), green `✅ Downloaded` (Targets, File), author
`Mulewatch - <node-id>`, webhook avatar kept, no ping from a hostile name.

## What was built

- `Report.title`, passed through the `Notifier` port; `Severity.SUCCESS` (green) for a completion.
- `DownloadCompleted` carries the clean name and every `download` target of the hash.
- The apprise adapter sends markdown (an embed on Discord), neutralises every `@` with a
  zero-width space, and names the node in an image-less `AppriseAsset`.

## Pitfalls

- apprise has four notification types, one colour each: a fifth colour needs another type.
- apprise only pings from a markdown body; the `@` neutralisation is what makes markdown safe.

## Next

- Wire the community webhook on the node (`node_prefix: false`) after the release.
