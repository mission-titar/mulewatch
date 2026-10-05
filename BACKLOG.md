# Backlog

What the project intends to do next. One entry per improvement, at most two lines, stating
what changes and why it is wanted. Every entry stands alone: it carries its own link, so
deleting any other entry leaves it intact.

Not a changelog and not a record. An entry is **deleted** when it ships or when it is
dropped, never annotated with what became of it. The history is in git and in
`agents/handoffs/`.

An agent adds an entry only after the operator has agreed to it.

- **Notifications for researchers**: one message per file with title, clean name and ed2k link,
  sent on a tier change only. Findings in `agents/specs/2026-10-04-matcher-file-vetoes.md` §7.

- **Lossless compact observation storage**: 10.9M rows / 4.9 GB in three months on the node, mostly
  repeated sightings; store them compactly by default without losing information. No spec yet.

- **Paginate the file detail timeline** (low priority): it renders every sighting, 12.9 MB for the
  node's heaviest file (38,593 rows). Measured in `agents/handoffs/2026-10-05 - handoff - shared observation reads.md`.
