# Handoff: file-level vetoes, regex fragments and rule scope in the matcher

## State

Branch `feat/matcher-file-vetoes`, gate green (matching 338, crawler 967, 100% branch coverage).
Spec: `agents/specs/2026-10-04-matcher-file-vetoes.md` (approved 2026-10-05). Not released yet.

## Why

A review of the notifications (for a researchers' Discord) showed the matcher was the problem, not
the routing: the `notify` tier was all noise, and real Teletoon files carried false targets. Two
causes: vetoes were judged per name (one clean alias of a hash defeated `{ not: foreign_lang }`),
and bare numbers or title coverage pinned other episodes (`« …lune 2 »` gave 002A/B).

## What was built

- `vetoes:` in `matcher.yml`: token names judged on EVERY name of a file; one hit excludes the target
  for the whole file. `foreign_lang` (now with a CJK/kana/jamo range), `not_episode`, and
  `other_episode` (`explicit_id` and not `own_id`).
- `fragments:`: raw regex text expanded at parse time (`{episode_marker}`, `{not_a_date}`); the
  engine never sees them.
- Required `scope: segment | episode | unattributed` on every rule; the engine no longer hardcodes
  rule names for the fan-out.
- `Explanation.vetoes_fired`; `MatchingEngine.explain` takes every name of the file; the webui file
  detail lists the vetoes fired.
- `parse_targets` rejects any status other than `lost`/`found`.

## Replay on the node catalog (acceptance)

1924 hashes, 95 changed, no VF episode lost: 11 Teletoon `N°0NNx` files lose all their false
targets and keep their own; 67 notify files (Chinese fansubs, `Sgt. Frog` aliases, XeTe,
Dino-Riders) and 16 catalog files (Spanish/English dubs reached through a clean alias) drop out.

## Pitfalls

- **Only a marked number is explicit** (`n°`, `#`, `ep`, `episode`, `SxxEyy`), 1 to 3 digits. The
  series name is NOT a marker (operator decision): `Titar 2008` or `keroro 1080p` would veto a real
  VF file for every target.
- **No `0*` in front of a negative lookahead**: the regex backtracks `0*` to empty and the target
  vetoes itself on its own `n°062`. Hence `explicit_id` ∧ ¬`own_id` with the `not:` combinator.
- **Found targets are still downloaded**, on purpose (2026-07-01 spec, Batch C, reconfirmed). The
  download policy's `"complete"` is only the sentinel for a target that vanished from `targets.yml`.
- `fold()` is NFKD: Hangul syllables become jamo, so a regex range must target the folded form.
- `iter_reevaluation_rows` crawls on a 4.8 GB snapshot (seconds per row); the throwaway replay
  script fetched each hash's latest observation by its own indexed query instead. Not measured on
  the node's own startup re-evaluation.

## Rollout (operator)

The node's `matcher.yml` is an operator copy: replace it with the new `deploy/matcher.yml` when the
image is upgraded, or the boot fails on the missing `scope` (intended). The first boot appends
retractions for the vetoed pairs; notifications only go to `syslog://`, so nothing reaches anyone.

## Not yet validated

- On the running node (only on a snapshot, outside the container).

## Leads, not done

- `N°074B « La couronne du paranormal »` (a real Teletoon file) has no target: `targets.yml` lists
  episode 74 as mono-segment `Keroro special`. Either Teletoon split it like 75/76, or a segment is
  missing from the Wikipedia-derived list. Worth a look by the operator.
- Next: the notifications spec (`BACKLOG.md`, findings in the spec's §7).
