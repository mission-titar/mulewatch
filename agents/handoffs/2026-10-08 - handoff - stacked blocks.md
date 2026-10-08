# Handoff: stacked blocks

Drafted by block 20 from the block reports of #99 and #100, corrected by the closing block (30),
which also fixed the holistic review's findings.

## State

Spec `agents/specs/2026-10-08-stacked-blocks.md`, tier Spec, documentation and process only: no
code, config or image change, so no release (D10). Stack #101 on GitHub:

| # | Branch | PR | Contents |
|---|--------|----|----------|
| 10 | `docs/stacked-blocks-workflow` | #99 | spec, `agents/workflow.md`, `AGENTS.md`, `.gitattributes`, `.gitignore` |
| 20 | `docs/stacked-blocks-reviews` | #100 | `agents/reviews/spec.md`, `agents/reviews/holistic.md`, the handoff draft |
| 30 | `docs/stacked-blocks-closing` | on top of #100 | holistic findings fixed, this handoff |

## What was built

- `pinry-reborn`'s process, ported: a lot ships as a stack of bounded pull requests (under 500
  changed lines and 20 files, counted per hunk), one named teammate per block of a tier Spec lot,
  the next block starting once the previous one is green locally and its pull request open. Two
  tiers, chosen by the operator: Direct (one block, inline) and Spec (spec, stack, holistic review).
- `agents/workflow.md` carries the process; `AGENTS.md` lost its `## Workflow` section for a
  pointer, and its hard rule on the holistic review now runs once per tier Spec lot, at the head of
  Wrap, over `origin/main...origin/<top branch>`. Wrap no longer tags; a release is proposed after
  the stack merges.
- `uv.lock` marked with a bare `linguist-generated`, outside the block budget; `.reviews/`
  gitignored for the review agents' reports.
- Two review mandates under `agents/reviews/`. Spec: claims verified, criteria falsifiable, every
  decision states its reason (in place of `pinry-reborn`'s ADR check), adjacent backlog items and
  the block table checked. Holistic: range `origin/main...origin/<top branch>` after
  `git fetch origin` (no `lot/` tag), behaviour with no failing test watched first is a finding,
  read from the block reports (strict TDD, where `pinry-reborn` retired that rule), detached
  worktrees only.

Budgets (D3 command, 2026-10-08): block 10, 253 lines and 4 files against `main`; block 20, 166
lines and 2 files against block 10; block 30, 70 lines and 4 files against block 20 (the spec and
this handoff are outside the count). Gate `uv run poe check` green at every tip.

## Departures from the spec

- Block 10, D6 "copied verbatim": the body rule says "departures from the spec", "class 1 fixes",
  "class 2 questions" where the source says "plan", "tier-1", "tier-2", since the Vocabulary
  forbids "tier" for a defect and D13 writes no plan.
- Block 10 kept from the old `## Workflow`, unmentioned by the spec: `main` integration-only, branch
  naming, no admin merge, the docs-only local merge (tier Direct), a tier Direct pull request merged
  by the operator with `gh pr merge --rebase`. Not ported: `pinry-reborn`'s Scope and Design
  sections (outside D1).
- Block 10 also added rules the spec does not contain, listed here for the operator's approval:
  "Recommend Spec when both fit; if its trigger surfaces mid-task, stop and ask again"; "Stage paths
  explicitly"; the cascade-conflict procedure (the lead aborts, the conflicting branch's teammate
  rebases and runs the gate, the cascade resumes); "No hunk of a diff should be unexplainable by the
  request"; "correct the document when the numbers differ".
- Block 20: the spec mandate's replacement section also checks adjacent backlog items (D11) and the
  block table (D3, D13); the holistic mandate adds `git fetch origin`, `git worktree add --detach`
  and mulewatch's real coverage perimeter.
- Block 30: the spec gained four `(Corrected: ...)` markers (D2, D4, D6, D13), one per decision a
  holistic fix changed. Tier Direct moves (c), the backlog, before Integrate along with (d), the
  handoff: the report's suggested fix named the handoff, and the backlog is a file of the same branch.
  The spec is written untracked on `main` until approved, as this lot did. The `(Corrected: ...)`
  marker applies after the operator's approval, so the spec review's own fixes carry none.

## Defects and findings

- Spec review (on `pinry-reborn`'s mandate): 18 findings, all fixed in the spec before the operator
  read it.
- Class 1 (block 10): `AGENTS.md`'s Language rule named `.gitignore` as the only French
  housekeeping file, `.gitattributes` already was; en and em dashes removed from every `AGENTS.md`
  line touched.
- Class 2: one, raised by the holistic review's docs-only finding. Answer (operator, 2026-10-08):
  option (a), the docs-only local merge extends to `agents/**`.
- Holistic review (`.reviews/stacked-blocks-holistic.md`, 4 MAJOR, 11 MINOR), every finding's exit
  is "fixed in the lot", in block 30. The lead applied the report's suggested fix to each
  (the default exit), except the docs-only exception, which is the operator's answer:

| Severity | Finding | Fix |
|---|---|---|
| MAJOR | The teammate's brief never points at `agents/workflow.md` | Brief lists its Act, Verify, Integrate and Defect classes; `AGENTS.md` says read it before a lot or a block |
| MAJOR | Holistic item 10 cannot tell test-first from test-after | Block report carries, per new behaviour, the test watched failing and its failure line; item 10 reads it from the bodies |
| MAJOR | A fix-back collides with the active teammate in the one tree | Forwarded only once the active teammate has stopped, work committed, tree clean; it is resumed after |
| MAJOR | Tier Direct's order cannot be followed | Handoff at the end of Verify, before Integrate; (f) covers tier Direct's pull request |
| MINOR | Docs-only exception excludes `agents/**` | Extended to `agents/**` (operator's decision, option (a)) |
| MINOR | Nowhere to write the spec before approval | Untracked on `main` until approved, committed in the first block |
| MINOR | `(Corrected: ...)` undefined | Defined in workflow.md's Spec phase and spec.md's Detail |
| MINOR | `AGENTS.md`: one teammate per block, for every tier | "per block of a tier Spec lot" |
| MINOR | `AGENTS.md` Language rule: "the one exception" plus a second | `.gitignore` and `.gitattributes` are the exceptions |
| MINOR | `AGENTS.md`: handoff per milestone | Per lot |
| MINOR | Rules absent from the spec, unlisted | Listed under Departures above |
| MINOR | Draft dropped the `textwrap` pitfall | Added under Pitfalls |
| MINOR | "Code block" undefined, clashing with fenced code | "Closing block" defined in Vocabulary; "the last block before the closing one" |
| MINOR | `docker/` does not exist at the root | `packages/crawler/docker/` |
| MINOR | Body rules unwrapped | Wrapped at 120 |

## Lot counts (D9)

- Fix-backs: 2. Block 20's handoff draft was added after its pull request opened: the lead's brief
  first said block 20 was not the last code block, and the correcting message crossed the
  teammate's report. Then this handoff, corrected after #102 opened when the lead amended its
  brief: the three MAJOR fixes it had called operator decisions are the lead's own.
- Cascaded rebases: 0.
- Runs re-triggered: 2 (#100, #102).
- The operator's reading of the bodies: to fill before the merge.

## Pitfalls learned

- The forbidden-string grep `red run` matches inside "re-triggered runs"; phrase it "runs
  re-triggered".
- A worktree holding a branch of the stack stops `gh stack rebase` from moving it; a reviewer
  needing a tree uses `git worktree add --detach`.
- zsh expands an unquoted `=====` as a command lookup and fails; quote separators in chained reads.
- `textwrap` reflow can split a code span across lines, and did again in block 30 (`gh pr view`).
  Reflow whole paragraphs, never single overlong lines, then list lines with an odd backtick count.
- The lead once took the operator's agreement on a summary of the spec for its approval; the
  operator corrected it, and the rule now says so (D2).

## Not validated

- The atomic `gh stack merge --rebase` under `required_conversation_resolution: true`, the spec's
  deciding claim: observable only at this lot's merge. If it fails, D4 falls back to plain stacked
  branches merged bottom up, and the lot stops for the operator.
- The spec mandate has not run under its mulewatch wording; the holistic one ran once, on this lot,
  which changed no behaviour, so item 10's evidence rule is untried.
- The tier Direct order (handoff before Integrate) and the fix-back wait have not been exercised.

## Backlog

No item is adjacent (`BACKLOG.md` holds no process item); unchanged.

## Next

The multi-network lot (`agents/specs/2026-10-08-multi-network-architecture.md`), stage 1 spec,
under this workflow.
