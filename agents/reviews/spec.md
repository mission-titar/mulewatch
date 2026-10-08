# Review mandate: specification

**Artefact: a specification**, block table included. Run once, in an agent the lead dispatches by
name, before the operator reads it.

**This document states its mandate before its argument.** The bullets bind. The `**Detail.**`
paragraph that closes a section explains and binds nothing.

## What you deliver

- **Judge what the document claims to be true and whether its acceptance criteria can be failed,
  then whether every decision it settles states its reason.**
- **Write your report to `.reviews/<lot>-<mandate>.md`**, the lot the brief names and `spec` as the
  mandate.
- **Report findings there as `SEVERITY | file:line | issue | suggested fix`**, most severe first,
  SEVERITY one of `CRITICAL`, `MAJOR`, `MINOR`. Say plainly if a part finds nothing.
- **Return these in your message and nothing else**: the report's path, the counts by severity,
  the three findings you would fix first, and the marker line.
- **End every message you return with the line `END OF MESSAGE`**, whether or not it carries a
  report.
- **Do not edit anything.** Stay inside the repository.

**Detail.** The findings are closed in the document before the operator reads it. The name is there
so a report that does not arrive can be asked for again: that is the only second message you will
ever get. The report goes to a file because the message channel truncates, and a report cut cleanly
between two findings reads as a complete report with fewer findings; `.reviews/` is in `.gitignore`,
so writing there stays inside the repository and no closing block's `git add` can carry a review
onto `main`. The marker line is on every message and not only on one carrying a report, because the
failure the file leaves is the short return message being cut, and that message is not a report.

## Evidence

- **Severity follows what rests on the claim**: false and a block depends on it, MAJOR; false and a
  decision was taken on it, CRITICAL.
- **Enumerate the factual claims**: every statement about how something behaves, a library, amuled
  or amuleapi, the build, either database, a class in this repository. Goals and intentions are not
  your subject.
- **Find the evidence or produce it.** A claim carries the command that established it or the source
  it cites. Where it carries neither, measure it yourself: read the source, run the query, grep the
  call sites.
- **A claim you could not settle is a finding**: report it as unverified with the command that would
  settle it.
- **Counted evidence is recounted** from the code, never read from the document. Report the command
  and the number it gave.
- **A dated measurement is not a current one.** Re-run it; when the numbers differ, the finding is
  against the document under review, and the stale source is named so it can be corrected too.
- **A spike proves nothing until it isolates its variable.** Ask what else could have produced the
  observed result.
- **Read the document against itself**: two sentences that cannot both be true, a count stated twice
  with two values, a claim contradicting the spec, handoff or backlog item it derives from.
- **Name the claim that decides**: the one claim the central decision rests on, and what happens to
  the work if it is false, whatever your verdict on it.

**Detail.** Counts are where documents lie most often. A claim quoting an earlier document is
evidence it was true once, which is why a dated measurement is re-run; `agents/reference/` holds
dated findings about amuled, true of the version they were measured on. A spike that leaves the old
path in place has not tested the new one. A design derived from a false premise is wrong in the most
expensive way, planned around before anyone measures.

## Falsifiability

- **Name the output that would prove each criterion wrong**: the command, the status code, the row,
  the file. Where you cannot, the criterion is the finding.
- **Ask whether it is already satisfied.** Run it against the tree as it is.
- **Ask whether the observable discriminates**: what other state produces the same observation.
- **Ask whether it names the observable or the instrument.**
- **Ask whether the property checked matches the property claimed.**
- **Ask how absence is observed**, and over what window: no leak, no log, nothing on disk.
- **Ask for out of scope's observable too.** It is a set of claims about what will not change; name
  how a reader would notice if one did.

**Detail.** A criterion nobody can fail is declared done against a green gate with nothing
established. The questions after the first are the shapes that failure takes. A criterion the tree
already meets tests nothing: "the generator reports no change" holds on an untouched tree too. An
assertion meant to require that a migration creates an index is satisfied by one that drops it, both
mentioning the name. "The repository test passes" names an instrument, which moves whenever the test
is edited. A bound on entries is not a bound on memory; a unique index is not uniqueness if the
column is nullable; a grep is not a structural test.

## Reasons and blocks

- **Every decision the specification settles states its reason.** A decision with none, or whose
  reason only restates it, is a finding.
- **It names every adjacent `BACKLOG.md` item and why each stays open**, or says there is none.
  Read `BACKLOG.md` and name any it missed.
- **Its block table numbers blocks by tens and names each branch.** A block that will plainly pass
  500 changed lines or 20 changed files (`agents/workflow.md`, "What a block is") is a finding unless
  the specification states in one line why it cannot split.

**Detail.** mulewatch records its decisions in specs and handoffs, not in a separate decision
record, so a reason missing from the specification is missing everywhere: the next lot meets the
decision with nothing to weigh against it.
