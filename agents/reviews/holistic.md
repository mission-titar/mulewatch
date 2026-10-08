# Review mandate: holistic

**Artefact: a whole lot on the top of its stack**, `git diff origin/main...origin/<top branch>`, every code block's
pull request open and no block being written. Run once per lot in tier Spec, at the head of Wrap, before the operator
reviews the stack, in an agent the lead dispatches by name.

**This document states its mandate before its argument.** The bullets bind. The `**Detail.**` paragraph that closes a
section explains and binds nothing.

## What you deliver

- **Your range is `git diff origin/main...origin/<top branch>`**, after `git fetch origin`, the top branch being the one
  your brief names. Read both sides as remote refs, never as local ones.
- **Read the specification, the handoff draft, then the full diff.**
- **Write your report to `.reviews/<lot>-<mandate>.md`**, the lot the brief names and `holistic` as the mandate.
- **Report findings there as `SEVERITY | file:line | issue | suggested fix`**, most severe first, SEVERITY one of
  `CRITICAL`, `MAJOR`, `MINOR`. Say plainly if you find nothing.
- **Return these in your message and nothing else**: the report's path, the counts by severity, the three
  findings you would fix first, and the marker line.
- **End every message you return with the line `END OF MESSAGE`**, whether or not it carries a report.
- **Do not edit anything.** Stay inside the repository.

**Detail.** The stack is not merged when you run, so the three-dot merge base with `main` is the lot's start, even
after `main` moves or the stack is rebased onto it; a local branch is whatever the shared working tree last left behind.
The name is there so a report that does not arrive can be asked for again, which is the only second message you will
ever get. Your findings become the lot's closing block, stacked on top with its own pull request, and that is their one
destination, so none of them is counted separately and the operator meets them there before reviewing the stack. Each
pull request will be read alone, by the human; your value is what that reading cannot see, which is what the blocks do
to each other and what the lot does to the project as a whole. The report goes to a file because the message channel
truncates, and `.reviews/` is in `.gitignore`, so writing there stays inside the repository and no closing block's
`git add` can carry a review onto `main`. The marker line is on every message and not only on one carrying a report,
because the failure the file leaves is the short return message being cut, and that message is not a report.

## What you look for

1. **Cross-cutting invariants.** Taken together, do the changes break an invariant no single block owns? The design
   invariants of `AGENTS.md`, layering and dependency direction, purity of the domain, package boundaries, atomicity
   across steps, ordering assumptions, resource lifetimes.
2. **Contract uniformity.** Are public surfaces consistent across the whole change, not only correct one by one?
   Configuration key naming, metric and label names, webui routes and status codes, log fields, timestamp formats.
3. **Spec conformance.** Implemented exactly: nothing invented, nothing silently dropped. List what the specification
   asked for that you cannot find in the diff, and every `(Corrected: ...)` it gained, with whether the code matches the
   correction.
4. **Emergent failure modes.** Off-by-one, partial-batch and rollback behaviour, races, retries that are not
   idempotent, cross-filesystem moves assumed atomic, unbounded growth, error paths that swallow the cause.
5. **Test suite as a whole.** Do the tests, collectively, still discriminate? Shared fixtures that weaken assertions,
   mocks that assert their own configuration, coverage achieved by tests that would pass against a broken
   implementation.
6. **Covered but unrequested.** For each new conditional, option, parameter, fallback and error path, name the line of
   the specification that demanded it; code whose only justification is the test covering it is an unrequested feature,
   and you say what to delete. A branch outside a coverage perimeter may have no test at all, which is its own finding.
7. **Living documentation.** Are the living documents (`docs/`, `AGENTS.md`, `BACKLOG.md`, `agents/workflow.md`)
   updated in the same commits as the behaviour they describe? Does any of them now describe behaviour the lot changed?
8. **Reference integrity.** Do cross-file references, relative links and heading anchors still resolve?
9. **Diff hygiene.** Files that should not have changed, refactors nobody requested, generated or build artefacts,
   leftover scaffolding, formatting churn unrelated to the work.
10. **Test evidence, across the lot.** Strict TDD (`AGENTS.md`): behaviour that arrived with no failing test watched
    first is a finding. So are behaviour no test exercises, a structural assertion nothing was shown to break, and a
    threshold met by a test that would pass against a broken implementation. **Never move the shared working tree**:
    read a file with `git show <commit>:<path>`, and where a run needs a tree, make a detached one with
    `git worktree add --detach` and remove it.
11. **Self-sufficient comments.** A comment states the why where it stands. One that defers to an identifier the reader
    must open elsewhere (a decision id, a section number, a ticket) explains nothing without that document. External
    references and clickable links are acceptable when they carry enough context.

**Detail.** One webui route left in the framework's default error format breaks what the others uphold, which is why
contract uniformity is read across the whole change. The gate enforces 100 % branch coverage on each package's unit
tests only: the integration suites, `packages/crawler/Dockerfile` and `docker/` (entrypoint, s6 services), `deploy/`,
`tests/smoke/` and the CI workflows are outside it, and so is any line under `# pragma: no cover`. Coverage anyway
proves nothing about whether anyone asked for the branch. Renumbering a section silently breaks anchors pointing into
it; `uv run poe docs-build`, run by `docs.yml` and not by the gate, fails on a dead link under `docs/` and nowhere else.
The working tree is on the top of the stack when you start and the closing block stacks on it right after you, so a
detached HEAD you leave behind is the next block's problem, and a worktree holding a branch of the stack stops
`gh stack rebase` from moving it.
