# File-level vetoes, regex fragments and rule scope in the matcher

- Date: 2026-10-04
- Status: APPROVED (2026-10-05)
- Scope: make the matcher judge a file on ALL its names for what it is NOT; kill the false
  targets observed on the real catalog; move the fan-out rule names out of the engine; fix the
  ignored `status: found` of `targets.yml`
- Related: `packages/matching/src/catalog_matching/{config,validation,interpolation,resolver,engine}.py`,
  `deploy/matcher.yml`, `deploy/targets.yml`, `packages/crawler/src/mulewatch/domain/download/policy.py`,
  `packages/crawler/src/mulewatch/application/run_download_cycle.py`, the webui file detail

## 1. Why

Measured on the operator's node catalog on 2026-10-04 (169 live notify/download pairs, 93 hashes).

**Vetoes are per name, so one clean alias defeats them.** `evaluate_all` keeps, per target, the
best rule over every name of the hash. A `{ not: foreign_lang }` on one name therefore excludes
nothing: another name of the same hash without the marker matches. About 60 of the 69 live
`notify` hashes are this case:

| Name caught by the veto | Other name of the same hash, which matches |
|---|---|
| `[異域字幕組][4月新番][keroro][21][繁體].mp4` | `[ç°åå­å¹çµ][4ææ°çª][keroro][21][ç¹é«].mp4` (mojibake) |
| `Sgt. Frog - 065 [Keroro] (640x480 DVD XviD) [D6A10367].avi` | `[Keroro].065.[Xvid.Mp3].[D6A10367].avi` |
| `Keroro 025 - La gran evasion de Momoka [DVD+TV dual by XeTe].avi` | `Keroro 025 - La Gran Evasion De Momoka.avi` |
| `Dino-Riders - 10 - Titar pierde los estribos(...).avi` | `Dino_Riders_10_Titar_Pierde_Los_Estribos...avi` |

**False targets on real VF files.** All 24 `download` hashes carry an explicit `N°NNNx`, and 14
of them also carry targets of another episode:

- bare numbers of the title: `« Emmène-moi sur la lune 2 »` gives 002A/B, `« il y a 20 ans 1 »`
  gives 020A/B and 001A/B (`numero_nu_confirmed`);
- title coverage on another episode: 001B and 052A on `N°065A`, 005B, 045A, 048B, 049B on `N°066A`,
  052B on `N°077B` (`title_confirmed`).

`N°075B « L'éveil de la troisième Garance 2 »` → 075A is NOT a false target: episode 75 is
mono-segment in `targets.yml`, and Teletoon aired it in two parts. Same for 071B, 073B, 076B.

**Fan-out semantics hide in code.** `engine.py` hardcodes `_SEGMENT_LEVEL = {"id_segment_exact",
"title_confirmed", "title_review"}` and `_EPISODE_LEVEL = {"numero_nu_confirmed", "numero_nu"}`.
Renaming a rule in `matcher.yml` silently breaks the fan-out, and nothing in the YAML says so.

**`status: found` is ignored.** `targets.yml` marks recovered segments `status: found`, the
download policy tests `"complete"`, and `parse_targets` accepts any string. Found targets are
downloaded: `local.db` holds 001A, 001B and 002A.

**Duplicated regex text.** The month/date guard is copied verbatim between `segment_id_loose` and
`episode_number`; the new episode-marker list (§3) would be a second copy.

## 2. The model: vetoes say what the file is NOT, rules say what it IS

- `vetoes:` is evaluated on **every** known name of the hash. One name matching a veto excludes
  the target for the whole file.
- `rules:` is unchanged: per target, the best rule over all names wins.

Every exclusion of a file becomes a veto, so rule bodies become positive. `not:` stays available
for a guard that belongs to one rule (`keroro_large`'s `{ not: episode_number }`).

### 2.1 `vetoes:` (engine)

- `MatcherConfig.vetoes: tuple[str, ...]`, a top-level YAML list of token names. Optional (empty).
- Validation: each name is a known token, no duplicates (`ConfigError`).
- Resolution: none new. Tokens are already resolved per target, so a veto without placeholder
  is target-agnostic (`foreign_lang`) and a veto with `{absolute_number}` is per target
  (`other_episode`). `ResolvedTarget.tokens[name]` is the veto matcher.
- `evaluate_all`: materialize the names (minus the over-long ones, as today), then skip every
  target for which any veto matches any name. The rest of the flow (best rule, fan-out, single
  winner) runs on the surviving targets. Vetoes apply to every tier, `catalog` included.

### 2.2 `fragments:` (validation only)

- A top-level map `name → raw regex text`, substituted as `{name}` into every `regex` token
  pattern **at parse time**. `RegexDef.pattern` stores the expanded text; `MatcherConfig`,
  the resolver and the engine never see fragments.
- Raw, not escaped (they are regex). A fragment may contain target placeholders (substituted
  later, per target as today); it may NOT contain another fragment (one level, no cycle).
- Validation (`ConfigError`): a fragment name colliding with a target placeholder (`season`,
  `seasonal_number`, `absolute_number`, `segment`, `title`); a `{name}` that is neither a
  fragment nor a target placeholder (already rejected by the interpolation probe); a fragment
  referencing a fragment.

### 2.3 `scope:` on rules (replaces the hardcoded sets)

- Required on every rule, closed enum: `segment` (pins one segment: a title or a lettered id),
  `episode` (pins the whole episode: a bare number, fans out to every segment), `unattributed`
  (pins nothing: at most one decision, only when no attributable rule fired).
- Required on purpose: an operator `matcher.yml` from before this change fails at boot with a
  clear `ConfigError` instead of silently losing its fan-out.
- `engine.py` drops `_SEGMENT_LEVEL`, `_EPISODE_LEVEL`, `_ATTRIBUTABLE` and reads the rule's scope.

### 2.4 Explainability

- `Explanation` gains `vetoes_fired: tuple[str, ...]` (sorted).
- `MatchingEngine.explain` takes every name of the file: `rules_fired`, `tokens_matched` and
  `vetoes_fired` are the ones true on at least one name. The webui file detail passes the
  distinct observed names and lists "Vetoes fired" next to the rules and tokens.
- Decisions emitted by `evaluate_all` keep their per-winning-name explanation (their
  `vetoes_fired` is empty by construction).

## 3. Target `deploy/matcher.yml`

```yaml
fragments:
  # A number written as an episode number: N°62, #062, ep 62, épisode 62.
  episode_marker: "(?:\\bn[°o]|#|\\bep(?:isode)?\\.?)\\s*-?\\s*"
  # Not a date: no month name and no d/m numeric form right after the number.
  not_a_date: "(?!\\s*(?:janv?(?:ier)?|fevr?(?:ier)?|mars|avr(?:il)?|mai|juin|juil(?:let)?|aout|sep(?:t(?:embre)?)?|oct(?:obre)?|nov(?:embre)?|dec(?:embre)?)\\b)(?!\\s*[/.\\-]\\s*\\d)"

tokens:
  keroro: { keyword: keroro }
  titar: { keyword: titar }
  keroro_titar: { any: [keroro, titar] }
  # Existing list, minus the explicit 繁體 (covered by the CJK range), plus CJK and Hangul.
  foreign_lang: { regex: "\\b(ITA|KOR|...)\\b|dino-riders|...|espana|[\\u1100-\\u11ff\\u3040-\\u30ff\\u3400-\\u9fff]" }
  not_episode: { regex: "movie|opening|...|onlyfans" }
  # The name carries an explicit episode number, whichever.
  explicit_id: { regex: "{episode_marker}\\d{1,3}(?!\\d)|\\bs\\d{1,2}\\s*e\\d" }
  # That number is the target's.
  own_id: { regex: "{episode_marker}0*{absolute_number}(?!\\d)|\\bs0*{season}\\s*e0*{seasonal_number}(?!\\d)" }
  # An explicit episode number that is not the target's.
  other_episode: { all: [explicit_id, { not: own_id }] }
  segment_id_loose: { regex: "(?:^|[^0-9A-Za-z])0*{absolute_number}{not_a_date}(?:[^0-9A-Za-z]|$)" }
  episode_number: { regex: "(?:^|[^0-9A-Za-z])\\d{2,3}{not_a_date}(?:[^0-9A-Za-z]|$)" }
  # teletoon, idf1, vf, source_marker, segment_id, title_hit, is_video, is_archive: unchanged.

# One name is enough: the target is excluded for the whole file.
vetoes: [foreign_lang, not_episode, other_episode]

# The best name wins.
rules:
  - { name: id_segment_exact,    tier: download, scope: segment,      all: [keroro_titar, is_video, segment_id] }
  - { name: title_confirmed,     tier: download, scope: segment,      all: [keroro_titar, is_video, title_hit, source_marker] }
  - { name: numero_nu_confirmed, tier: download, scope: episode,      all: [keroro_titar, is_video, segment_id_loose, source_marker] }
  - { name: title_review,        tier: notify,   scope: segment,      all: [keroro_titar, is_video, title_hit] }
  - { name: numero_nu,           tier: notify,   scope: episode,      all: [keroro_titar, is_video, segment_id_loose] }
  - { name: archive_candidate,   tier: notify,   scope: unattributed, all: [keroro_titar, is_archive, { any: [segment_id, title_hit, source_marker] }] }
  - { name: keroro_large,        tier: catalog,  scope: unattributed, all: [keroro_titar, { any: [is_video, is_archive] }, { not: episode_number }] }
```

`french_safe`, `is_keroro` and `is_episode` disappear.

### 3.1 Why the episode-number veto is shaped this way

- Only a **marked** number counts as explicit. A bare number cannot: `« …lune 2 »` would make
  the `N°076B` file "explicitly episode 2", and target 002 would protect itself.
- `explicit_id` ∧ ¬`own_id` instead of one regex with a negative lookahead: `0*` in front of
  `(?!62…)` lets the regex backtrack `0*` to empty and veto target 62 on its own `n°062`. In
  `own_id`, `0*{absolute_number}` is a positive match (the `segment_id` idiom) and the
  negation is the `not:` combinator, out of the regex backtracker's reach.
- The series name is not a marker (operator decision 2026-10-05): `Keroro Mission Titar 2008 -
  La Grenouille Cosmique.avi` or `keroro 1080p` would read as an episode number, and a veto false
  positive deletes a real VF file for every target. `titar 62` is therefore not explicit (it
  vetoes nothing, it still matches through `numero_nu`). The marked number is bounded to 1 to 3
  digits not followed by a digit, so `#2008` and `ep 1080` are not episode numbers either.
- Episode level only, never segment: `N°075B` must keep 075A (§1).
- Known limit, accepted: an unmarked number (`titar 62`, `62 - keroro.avi`) is not explicit.
  It vetoes nothing; it still matches positively through `numero_nu`. Absence of a veto never
  removes a match. `NxNN` is left out of `explicit_id` (`1280x720`).

### 3.2 Pitfalls

- `fold()` applies NFKD: Hangul syllables (`\uac00-\ud7af`) decompose into conjoining jamo
  (`\u1100-\u11ff`), halfwidth katakana fold to `\u30a0-\u30ff`, compatibility ideographs to the
  unified block. The CJK range must target the folded form; a test feeds the unfolded scripts.
- The mojibake alias `NÂ°076B` folds to `na°076b` and matches no marker. Harmless: the clean name
  of the same hash fires the veto. Covered by a golden case.

## 4. `status: found`

- `catalog_matching` owns the closed set: `TARGET_STATUSES = frozenset({"lost", "found"})`,
  default `lost`. `parse_targets` rejects any other value (`ConfigError`).
- `decide_download` skips on `"found"` (the `SKIP_COMPLETE` verdict keeps its name);
  `_target_status`'s fallback for a vanished target returns `"found"`. The constant is imported,
  not retyped.
- Not retroactive: what amuled already holds stays. Decisions are still recorded for found
  targets: the catalog's subject is the file.

## 5. Proof

- **TDD on the engine**, red first: per-name veto defeated by a clean alias (today's bug); a
  per-target veto; `n°062a` keeps 62 and `n°620` vetoes it; Hangul and halfwidth katakana
  caught after fold; fragment expansion, collision, nesting and unknown-name errors; `scope`
  required, closed, and driving the fan-out; `parse_targets` rejecting `status: complete`.
- **Golden corpus** gains the real cases of §1: the four alias pairs, `N°065A`, `N°066A`,
  `N°071B`, `N°073A`, `N°073B`, `N°075B`, `N°076B`, `N°077B` with their expected target sets,
  the marker forms `titar 62`, `titar 062` (not explicit, kept) and `titar #062` (explicit), and
  the non-episode numbers `titar 2008`, `keroro 1080p`, `titar #2008`, `ep 1080` (never a veto).
- `test_engine_properties.py` rewrites its inline policy to the vetoes model.
- **Replay on a copy of the node catalog** (acceptance, throwaway script in the scratchpad, not
  committed): for every hash, the latest persisted decisions versus the new engine over all known
  names, built the way `reevaluate_catalog` builds its candidates. The diff (removed, added and
  re-tiered pairs) is reviewed with the operator before merge. Expected: every §1 false target
  gone, every `N°`-confirmed target kept, the CJK and alias notify hashes gone.

## 6. Rollout

- The node's `matcher.yml` is an operator copy: it must be replaced with the new one when the
  image is upgraded, or the boot fails on the missing `scope` (intended).
- The first boot re-evaluates the catalog and appends retractions for every vetoed pair. No
  storm reaches anyone: the node's notifications go to `syslog://` only.

## 7. Follow-up (out of scope): notifications

Findings of the 2026-10-04 review, for the next spec:

- the message carries `target_id` and `tier` only: no file name, title, size or ed2k link;
- one notification per `(hash, target)` row: a file with N targets sends N messages, and a
  `rule_name` change alone re-notifies (decision: notify on a **tier** change only, one message
  per hash per evaluation, no "already announced" store, since flapping is gone since 2026-09-26);
- `notify` decisions should reach researchers (today `operations`); `CrawlerStarted` and
  `HighIdRecovered` go to `community` and are noise there; audience names stay as they are;
- the message should pick the clean name (most sources) over its mojibake twin, and put the
  ed2k link in a code block (Discord does not linkify `ed2k://`).
