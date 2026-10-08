# Handoff: stacked blocks

Draft written by block 20, the lot's last code block, from the block reports of #99 and #100; the
closing block (30) corrects it and fills in what only Wrap knows.

## State

Spec `agents/specs/2026-10-08-stacked-blocks.md`, tier Spec, documentation and process only: no
code, config or image change, so no release (D10). Stack #101 on GitHub:

| # | Branch | PR | Contents |
|---|--------|----|----------|
| 10 | `docs/stacked-blocks-workflow` | #99 | spec, `agents/workflow.md`, `AGENTS.md`, `.gitattributes`, `.gitignore` |
| 20 | `docs/stacked-blocks-reviews` | #100 | `agents/reviews/spec.md`, `agents/reviews/holistic.md`, this draft |
| 30 | `docs/stacked-blocks-closing` | | holistic findings, this handoff corrected |

## What was built

- `pinry-reborn`'s process, ported: a lot ships as a stack of bounded pull requests (under 500
  changed lines and 20 files, counted per hunk), one named teammate per block, the next block
  starting once the previous one is green locally and its pull request open. Two tiers, chosen by
  the operator: Direct (one block, inline) and Spec (spec, stack, holistic review).
- `agents/workflow.md` carries the process; `AGENTS.md` lost its `## Workflow` section for a
  pointer, and its hard rule on the holistic review now runs once per tier Spec lot, at the head of
  Wrap, over `origin/main...origin/<top branch>`. Wrap no longer tags; a release is proposed after
  the stack merges.
- `uv.lock` marked with a bare `linguist-generated`, outside the block budget; `.reviews/`
  gitignored for the review agents' reports.
- Two review mandates under `agents/reviews/`. Spec: claims verified, criteria falsifiable, every
  decision states its reason (in place of `pinry-reborn`'s ADR check), adjacent backlog items and
  the block table checked. Holistic: range `origin/main...origin/<top branch>` after
  `git fetch origin` (no `lot/` tag), behaviour with no failing test watched first is a finding
  (strict TDD, where `pinry-reborn` retired that rule), detached worktrees only.

Budgets (D3 command, 2026-10-08): block 10, 253 lines and 4 files against `main`; block 20, 166
lines and 2 files against block 10, before this draft (the handoff is outside the count). Gate
`uv run poe check` green at both tips.

## Departures from the spec

- Block 10, D6 "copied verbatim": the body rule says "departures from the spec", "class 1 fixes",
  "class 2 questions" where the source says "plan", "tier-1", "tier-2", since the Vocabulary
  forbids "tier" for a defect and D13 writes no plan.
- Block 10 kept from the old `## Workflow`, unmentioned by the spec: `main` integration-only, branch
  naming, no admin merge, the docs-only local merge (tier Direct), a tier Direct pull request merged
  by the operator with `gh pr merge --rebase`. Not ported: `pinry-reborn`'s Scope and Design
  sections (outside D1).
- Block 20: the spec mandate's replacement section also checks adjacent backlog items (D11) and the
  block table (D3, D13); the holistic mandate adds `git fetch origin`, `git worktree add --detach`
  and mulewatch's real coverage perimeter.

## Defects and findings

- Class 1 (block 10): `AGENTS.md`'s Language rule named `.gitignore` as the only French
  housekeeping file, `.gitattributes` already was; en and em dashes removed from every `AGENTS.md`
  line touched.
- Class 2: none.
- Holistic findings and their exits: to be filled in by the closing block.

## Lot counts (D9)

To be completed by the closing block. Known at this draft: one fix-back, block 20 adding this draft
after its pull request opened (the brief first said block 30 would write it), re-triggering one run
on #100; cascaded rebases: none so far; the operator's reading of the bodies: not yet given.

## Pitfalls learned

- The forbidden-string grep `red run` matches inside "re-triggered runs"; phrase it "runs
  re-triggered".
- A worktree holding a branch of the stack stops `gh stack rebase` from moving it; a reviewer
  needing a tree uses `git worktree add --detach`.
- zsh expands an unquoted `=====` as a command lookup and fails; quote separators in chained reads.

## Not validated

- The atomic `gh stack merge --rebase` under `required_conversation_resolution: true`, the spec's
  deciding claim: observable only at this lot's merge. If it fails, D4 falls back to plain stacked
  branches merged bottom up, and the lot stops for the operator.
- Neither review mandate has run on a lot under its mulewatch wording; this lot's holistic review is
  their first use.

## Next

The multi-network lot (`agents/specs/2026-10-08-multi-network-architecture.md`), stage 1 spec,
under this workflow.
