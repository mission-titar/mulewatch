# Stacked blocks: bounded pull requests in a stack

Bring the process `pinry-reborn` runs (its `agents/workflow.md` and `agents/reviews/`) to mulewatch
before the multi-network lot (`agents/specs/2026-10-08-multi-network-architecture.md`): work ships as
a stack of bounded pull requests, each written by its own teammate, the whole read by a holistic
review before the operator reviews it.

Scope chosen by the operator on 2026-10-08, option B: the block and its budget, the stack, the
pull request's body, the lead and teammate roles, the two review mandates, the two tiers. Out of
scope: the rest of `pinry-reborn`'s split of `AGENTS.md` (its engineering and writing documents;
only the workflow part moves here), its evidence-guard hook and its path-reading `pre-push`.

## Vocabulary

- **Lot**: what one work session delivers. Discuss and Spec run once per lot.
- **Block**: the smallest change that can merge to `main` on its own; one block, one pull request.
- **Stack**: a lot's blocks, each branch cut from the previous block's, each pull request targeting
  its parent branch, the first targeting `main`.
- **Lead**: the main loop the operator talks to. Keeps the lot's thread, creates the branches,
  rewrites the stack, writes no block of a tier Spec lot.
- **Teammate**: a named background agent that implements one block, stays idle once its pull
  request is open, and is stopped when the stack merges.
- **Tier**: Direct or Spec, a property of the lot (D2). **Defect class**: 1, 2 or 3, a property of an
  adjacent defect found on the way (D11). Two words, so neither is read for the other.

## Decisions

**D1. One new document carries the process.** `agents/workflow.md`, in English, holds tiers,
phases, the block, the stack, the pull request's body, Wrap, the defect classes, the evidence rules
and the finding exits. `AGENTS.md` loses its `## Workflow` section and gets a one-line pointer in
its place; its Orientation list (`agents/workflow.md` added, the `agents/plans/` mention marked
historical), its "Subagent-driven execution" hard rule (rewritten per D9) and its `BACKLOG.md` line
point there too. *Reason: the process is read whole by the lead and by part by each teammate; out of
`AGENTS.md`, it is loaded only by whoever needs it.*

**D2. Two tiers, the operator picks.** The lead recommends one and waits.

| Tier   | Trigger | What runs |
|--------|---------|-----------|
| Direct | One block: no design decision, no new dependency, no change to an operator-facing surface (config keys, either DB's schema, webui routes, metrics, notifications, the `merge`/`compact` CLIs, compose env) | Act, Verify, Integrate, Wrap, inline by the lead. No review unless the operator asks for one. One branch (`git switch -c`), one pull request, or the existing docs-only local merge. |
| Spec   | Anything else | Discuss, Spec, then per block Act, Verify, Integrate in a teammate, then Wrap with the holistic review (D9). |

**No block of a tier Spec lot starts before the operator has read and approved the spec file**,
its review findings already closed. Agreement on a summary of the spec in conversation is not that
approval. The current "ask which branch or worktree" question goes: tier Spec always stacks in the
one working tree (D4), tier Direct always branches in place. *Reason: a lot with no design decision
gives a spec and two reviews nothing to judge; the approval rule is the existing Spec phase, stated
so a summary cannot stand in for the file (it did once, on this very lot).*

**D3. A block is bounded.** Green alone (`uv run poe check` passes at its tip), coherent alone
(nothing it adds is unreachable; a surface whose consumer arrives later is named in the spec and the
pull request), readable alone: **under 500 changed lines and under 20 changed files**, both strict.
Past either, the block splits or the spec says in one line why it cannot. A line is counted per hunk
of `git diff -U0` against the block's parent branch (each hunk costs the larger of its deleted and
added counts), after committing:

```bash
X=(-- ':/' ':/!agents/specs' ':/!agents/plans' ':/!agents/handoffs' ':/!agents/reference' ':(top,exclude,attr:linguist-generated)')
git diff -U0 <parent>...HEAD "${X[@]}" | awk '/^@@/ { split($2, o, ","); split($3, n, ","); b = (2 in o) ? o[2] : 1; d = (2 in n) ? n[2] : 1; s += (b > d ? b : d) } END { print s + 0 }'
git diff --name-only <parent>...HEAD "${X[@]}" | wc -l
```

Outside both counts: the four dated `agents/` directories and the files `.gitattributes` marks with
a **bare** `linguist-generated` attribute, which this lot sets on `uv.lock`. Never `=true`: git then
reports the value `true`, and the `attr:linguist-generated` pathspec no longer excludes the file.
*Reason: the budget measures what a human rereads. The file bound is where Microsoft measured useful
review feedback starting to fall, the line bound is the operator's; counting per hunk keeps an
addition at a file's top and an unrelated deletion at its foot from paying for each other
(`pinry-reborn` ADR 0041). Expected cost: 12 of mulewatch's last 30 merged pull requests pass a
bound under this command, so about four lots in ten will split.*

**D4. The stack runs on `gh stack`, in one working tree.** The lead cuts each block's branch with
`gh stack init` / `gh stack add` before the block's first file is written, never with `-A` while an
untracked file it does not mean to commit lies in the tree. `gh stack init` checks the top branch
out, so the lead switches back before committing to a lower one. A teammate opens its pull request
with `gh stack submit --auto --open`, ready for review. A fix lands in the block it concerns
(`gh stack checkout <branch>`), then the lead cascades with `gh stack rebase --upstack` and
`gh stack push`. **Before the operator merges, the lead rebases the whole stack onto the current
`main` with `gh stack rebase` then `gh stack push`**, since `main` requires up-to-date branches and
moves during a lot (Dependabot, the weekly `amule-bump`, docs-only merges), and waits for every
run to be green again. The operator merges, after reading the whole stack, with
`gh stack merge --rebase`, the only method the repository allows; never on a green gate alone, and
never the lead's command (non-interactive, it merges the whole stack without prompting). No worktree
per block: `gh stack` 0.1.1 refuses to rebase a branch checked out in another worktree (measured in
`pinry-reborn` on 2026-09-25, two throwaway branches, "est déjà utilisé par l'arbre-de-travail").
*Reason: the stack is what lets the next block start before the previous one is reviewed (D5).*

**D5. The next block starts once the previous one is green locally and its pull request open**,
not once it has merged. One teammate works at a time. A teammate speaks only when it stops: a
defect class 2 question, a blocker, or continuous integration has started. The lead arms
`gh pr checks <number> --watch` in the background as soon as a run starts, and forwards the
operator's answers verbatim. Teammates and reviewers are named and run on Opus. *Reason: in
`pinry-reborn`'s lot 0.38.0, run this way, no block waited on a review or a merge; a name lets a
report that does not arrive be asked for again instead of paying for a second run.*

**D6. The pull request's body is written for a tech lead.** `pinry-reborn`'s seven rules, copied
verbatim: context, why, how at the level of the architecture, never a file-by-file account; length
fitted to the change, 50 lines a ceiling; the block's report last, collapsed in
`<details><summary>Block report, for the handoff</summary>`. *Reason: these rules won the operator's
blind ranking in `pinry-reborn` over bodies that were the block's report, which the operator called
unreadable.*

**D7. Two review mandates under `agents/reviews/`.** `spec.md` (claims verified, criteria
falsifiable) and `holistic.md` (what the blocks do to each other and to the project), adapted from
`pinry-reborn`. Removed or rewritten: references to its ADRs, ecosystems, `agents/engineering.md`
and Dagger; the spec mandate's "decision record" section, replaced by "every decision the spec
settles states its reason", since mulewatch records decisions in specs and handoffs; the holistic
mandate's item 10, whose "the red run is retired" contradicts mulewatch's strict TDD and becomes
"behaviour that arrived with no failing test watched first is a finding"; its range rule, which
reads `lot/*` tags, replaced by D8's. Reports go to `.reviews/<lot>-<mandate>.md`, gitignored. The
agent returns only the path, counts by severity, its top three findings and `END OF MESSAGE`.
*Reason: the report goes to a file because the message channel truncates, and a report cut between
two findings reads as a complete one.*

**D8. The holistic review reads `git diff origin/main...origin/<top branch>`.** The stack is not
merged when it runs, so the merge base with `main` is the lot's start, even after `main` moves or the
stack is rebased onto it. Hence **no `lot/` tag**, which `pinry-reborn` needs only to give that
review a base. Accepted limit: what reaches `main` outside a lot (tier Direct, Dependabot,
`amule-bump`) is never read holistically, being by definition free of design decisions or a
dependency bump.

**D9. Wrap closes the lot in a closing block** stacked on top: the holistic findings fixed, each
named in the handoff with its exit (D11); the backlog reconciled; the handoff, drafted by the last
code block's teammate from the blocks' reports and corrected here. The handoff also counts the
lot's fix-backs, cascaded rebases, runs they re-triggered, and the operator's reading of the
bodies, then the lead reports the friction met. **This amends the hard rule of `AGENTS.md`** ("holistic
review, don't skip it"): the review runs once per tier Spec lot at the head of Wrap, over the top of
the stack, instead of once per change in Verify; a lot of one block is offered its waiver and the
operator decides; tier Direct has none. *Reason: the review's value is what blocks do to each other,
which a single block, already read by the spec review, the gate and the operator, does not have.
The counts are the only way to tell whether stacking costs mulewatch more than pull requests in
series: more re-triggered runs than blocks, or bodies called unreadable again, means it does.*

**D10. Wrap no longer tags.** After the stack merges, the lead proposes a release if the lot changed
what the image ships, announcing the version number first; the operator decides, and the tag is
pushed as today. *Reason: today Wrap ends on a `vX.Y.Z` per milestone, which publishes an image; a
lot that changes only documents or process (this one) ships nothing, and a tag can only follow the
stack's merge, which is the operator's.*

**D11. Defect classes and finding exits**, ported from `pinry-reborn`. An adjacent defect is class 1
(trivial and contained: fixed in the change that finds it, flagged), class 2 (larger, reachable in
this lot: stop and ask the operator at the moment of discovery), or class 3 (refused, or another
lot's: the backlog, after the operator's agreement). A review finding exits as fixed in the lot, a
backlog item, an accepted limit written where the decision lives, or refused with its reason in the
handoff. A lot's spec names every adjacent backlog item and why it stays open. *Reason: the closing
block and the handoff name each finding's exit, so the exits need a definition.*

**D12. Three evidence rules**, ported because the spec mandate enforces them: nothing is asserted
without the command that established it; a measurement a document carries is dated, and re-run
before acting on it; a check that cannot fail is not a check, so name the output that would prove it
wrong.

**D13. The spec's block table numbers blocks by tens and names each branch**, so an inserted block
takes a free number. The spec ships in the first block's pull request. A tier Spec lot writes no
separate plan: the block's row, the spec and `AGENTS.md` are the teammate's brief. *Reason: no plan
has been written since 2026-09-17 while specs continued, so the brief already works without one.*

## The claim that decides

The atomic stack merge works under mulewatch's protection. That GitHub accepts a stack on an
organisation repository is settled: "Stacked pull requests require no setup or enablement", and
GitHub documents rolling them out to an organisation
(docs.github.com/en/pull-requests/tutorials/roll-out-stacked-prs). What `pinry-reborn` never
exercised is mulewatch's `required_conversation_resolution: true` (`false` there): `gh stack merge`
is all or nothing, so one unresolved thread on any pull request blocks the whole stack. Observed at
this lot's merge. If the stack cannot merge, D4 falls back to plain stacked branches
(`git rebase --onto`, `gh pr create --base <parent>`, merged one by one bottom up) and the lot stops
for the operator.

## Blocks

| #  | Branch | Block | Contents |
|----|--------|-------|----------|
| 10 | `docs/stacked-blocks-workflow` | The workflow | This spec, `agents/workflow.md`, `AGENTS.md`, `.gitattributes` (`uv.lock`), `.gitignore` (`.reviews/`, its comment in French like the rest of the file) |
| 20 | `docs/stacked-blocks-reviews` | The review mandates | `agents/reviews/spec.md`, `agents/reviews/holistic.md` |
| 30 | `docs/stacked-blocks-closing` | Closing | Holistic findings, handoff |

This lot bootstraps itself: its spec review ran on `pinry-reborn`'s `agents/reviews/spec.md`, the
mandate block 20 adapts. No backlog item is adjacent (`BACKLOG.md` holds no process item). The lead
adds the `.gitignore` line before staging anything, since this lot's spec review already lies
untracked in `.reviews/`.

## Acceptance

- `grep -nE '^### [0-9]\.|EnterWorktree' AGENTS.md` prints nothing, and
  `grep -n 'agents/workflow.md' AGENTS.md` prints the pointer.
- `agents/workflow.md` carries each decision; each of these greps prints at least one line:
  `Direct`, `operator has read and approved the spec file`, `500`, `gh stack merge --rebase`,
  `gh stack rebase`, `Block report, for the handoff`, `origin/main...origin/`, `defect class 2`,
  `accepted limit`, `fix-backs`.
- `grep -nE 'lot/|red run|engineering\.md|docs/adr|dagger' agents/reviews/*.md agents/workflow.md`
  prints nothing.
- Blocks 10, 20 and 30 are each under both bounds by the D3 command, the numbers in their reports.
- The three pull requests show as one stack on GitHub: `gh pr view <block 20> --json baseRefName`
  prints block 10's branch, and block 30's prints block 20's.
- `git check-ignore .reviews/x.md` succeeds; `git check-attr linguist-generated uv.lock` reports
  `set`.
- `git diff -U0 origin/main...origin/<top> | grep '^+' | grep -cP '[\x{2013}\x{2014}]'` prints 0.
- `git diff --name-only origin/main...origin/<top> -- .githooks .claude` prints nothing.
