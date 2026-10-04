# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`mulewatch` continuously surveils the eMule network (eD2k + Kad, via an aMule client driven over amuleapi, its REST surface) to recover lost-media episodes of the French dub of *Keroro mission Titar* (aired 2008 on Teletoon), cataloguing all available metadata along the way.

It is a **virtual uv workspace** with four packages: `packages/crawler/` (package `mulewatch`, dist `mulewatch`), `packages/matching/` (package `catalog_matching`, dist `catalog-matching`, shared domain), and two dev/CI tooling packages never shipped in a prod image: `packages/vex_guards/` (package `vex_guards`, dist `vex-guards`, reads `security/*.vex.openvex.json` to keep our OpenVEX claims honest) and `packages/amule_bump/` (package `amule_bump`, dist `amule-bump`, stdlib only, run by `.github/workflows/amule-bump.yml` to bump the aMule pin and write the PR body). The crawler package `mulewatch` also contains the in-process webui subpackage `mulewatch.webui` (read-only catalog viewer + light runtime controls + read-only SQL console), served on its own thread by `python -m mulewatch` (one image, one compose service `mulewatch`, one of the two services s6 supervises in it, alongside amuled, which starts amuleapi itself — see the confinement invariant below).

## Orientation — read before substantial work

The live state, history, and recommended next step are deliberately **not** in this file (they would rot here). They live in:

- `agents/handoffs/` — one continuation guide per milestone (`<ISO date> - handoff - <context>.md`). **The newest is the entry point**: current state, what was just built, learned pitfalls, next step, and what is *not yet validated against real hardware*.
- `agents/specs/2026-06-10-crawler-mvp-design.md` — the authoritative MVP design (17 sections). Other dated specs in that dir record each subsystem's design + decisions; plans are in `agents/plans/`.
- `docs/contributing/testing.md` — every test suite (unit + the integration markers), prerequisites, CI pistes.
- `docs/install.md`: bring a node up (the two compose stacks, VPN, secrets, first boot, High-ID/Low-ID); `docs/operate.md`: operate & tune one (lifecycle, optional High-ID + its risks, metrics, container hardening, catalog tools, known limits); `docs/troubleshooting.md`: symptom → cause → fix entries (any level).
- `agents/reference/` — dated empirical findings about amuled. The EC notes there are a historical record since the 2026-09-22 migration to amuleapi; the live API reference is aMule's own `docs/api/REFERENCE.md`, in the source tree the image builds from.
- `BACKLOG.md` (repo root) — what the project intends to do next, one entry of at most two lines each, linking the spec that holds the detail. **Read it before proposing work**, and write to it only after the operator has agreed. It carries no history: an entry is deleted when it ships or is dropped, never annotated.
- `git tag`: releases are annotated `vX.Y.Z`, **pushed**, with the milestone name in the tag MESSAGE (`v1.0.1 - performance patch`), not in the tag itself. Pushing the tag is what publishes the versioned image: `release.yml` triggers on `v*`, publishing `X.Y.Z` **and** `latest`, then the GitHub release (title and notes from the tag message). Every push to `main` publishes `main`/`sha-<short>` and nothing else: `latest` means the newest release, never the tip of `main`, so an unreleased build is tested through the `main` tag.

### Where the code lives

The crawler is Clean/Hexagonal: `domain/` pure, `application/` async use-cases, `adapters/` I/O, `composition/` wiring (`CrawlerApp` + `python -m mulewatch`). Paths below are under `packages/crawler/src/mulewatch/` (**c:**) unless noted.

| Subsystem | Location | Role |
|---|---|---|
| Matching engine | `packages/matching/src/catalog_matching/` | declarative YAML-policy file→episode matcher (see Architecture) — shared by the crawler and the in-process webui |
| WebUI (in-process) | c: `webui/` | read-only catalog viewer + runtime controls (`/controls`) + read-only SQL console (`/console`); Starlette/Jinja2 served on its own thread by `python -m mulewatch`; the bind is FIXED at `0.0.0.0:8080` in code, `crawler.yml`'s `webui.enabled` only gates the whole surface |
| amuleapi adapter | c: `adapters/mule_api/` | REST client / JSON mapping / error contract for amuled (no probe tool: `curl` and amuleapi's own web UI replace them) |
| Persistence | c: `adapters/persistence_sqlite/` | append-only catalog.db + local.db; `.sql` migrations; sync repos |
| Search / crawl loop | c: `domain/search/`, `application/` | keywords/cycle/backoff/coverage; worker pool, persisted backoff |
| Download | c: `domain/download/` + ports/adapters | candidate → eD2k link → amuled queue → completion. Nothing moves or opens the file |
| Observability | c: `domain/observability/`, `adapters/observability/` | events → policy → dispatcher; Prometheus + apprise |
| Port-sync (High-ID) | c: `application/` | gluetun port → PATCH /preferences → restart amuled |
| Standalone catalog tools | c: `merge/`, `compact/` | `python -m mulewatch.{merge,compact}` — N→1 fusion / daily rollup |
| Container boot | c: `amule_config/` | `python -m mulewatch.amule_config`, run once as root by the entrypoint before s6: `amule` user, mount point ownership, amule.conf reconciled, amuleapi admin pass. Never imported by the crawler |
| Packaging | `deploy/base.compose.yml` + `deploy/compose.yml` (direct) + `deploy/gluetun.compose.yml` (VPN) + `tests/smoke/compose.yaml`, `packages/crawler/Dockerfile` | one image, one `mulewatch` service (crawler + amuled under s6, amuled starting amuleapi), no compose profile; smoke stack; container hardening. aMule is built on Debian from the git commit pinned by `ARG AMULE_VERSION` + `ARG AMULE_COMMIT` (bumped weekly by `.github/workflows/amule-bump.yml`); never write its version anywhere else |
| Supply-chain artefacts | `security/` + `.github/workflows/grype-scan.yml` + `release.yml` (`publish-manifest`) | keyless cosign signature + 3 signed attestations (CycloneDX/Syft-JSON SBOM, OpenVEX) on the image's multi-arch index; daily Grype scan → Code scanning. See `SECURITY.md`. |

## Design invariants (do not violate)

- **The catalog's subject is the file, never the person** — no tracking, no deanonymization.
- **The crawler never reads downloaded bytes, and never touches the filesystem of the output directory.** One deliberate exception, outside the crawler's runtime: the boot step `mulewatch.amule_config` creates the mount points `/downloads/incoming` and `/downloads/temp` and chowns them, not recursively, to `PUID:PGID`, so that amuled, running as `amule`, can write there. It runs once as root before s6 starts anything, the crawler never imports it, and it never lists, opens or changes what the mounts contain. It lives in this package only so that the gate (mypy, coverage, the VEX source guard) covers it.
- **`docs/` is the Zensical root and is published in full**: every `.md` under it becomes a page on https://mission-titar.github.io/mulewatch/, because Zensical builds every file in `docs_dir` whether or not the nav references it. Anything that should not be published goes in `agents/` (specs, plans, handoffs, reference notes), never in `docs/`. The site is built by `uv run poe docs-build`, which passes `--strict` so a broken link or a dead anchor fails the build instead of shipping silently.
- **Package boundary:** `catalog_matching` is a pure shared library imported by the crawler (and by its in-process webui), never the reverse. `vex_guards` and `amule_bump` are dev/CI tooling: never imported by shipped code, nor installed in the prod image.
- **Two run modes, one topology:** `download.enabled: true` (the shipped default) wires the download loop live; `download.enabled: false` or an absent `download:` section is crawl-only. This is a **config flag**, not two ways of assembling the stack: both compose stacks start every service unconditionally, and there is no compose profile anywhere.
- **Standalone tools** (`merge`, `compact`) never touch prod code or mutate a DB in place — they read a source and write a NEW file.
- **`deploy/` is the operator-owned single source of truth for config** — `deploy/crawler.yml` / `deploy/matcher.yml` / `deploy/targets.yml` stay editable-by-operator deployment config, next to the compose files that mount them; it is **forbidden to canonicalize them as code artifacts** (package data, inline policy dicts, or duplicate test fixtures that shadow them). Every consumer *derives from* `deploy/`, never the reverse: the matcher policy has exactly ONE copy (`deploy/matcher.yml`), read by the matching golden corpus + engine unit tests via `parents[N]` — a test-time path coupling to `deploy/`, deliberately accepted (test-only, not an import; the code DAG is unchanged). Do not reintroduce a `canonical_config.yaml` fixture or an inline `_CANONICAL_RAW` policy dict.
- **Boundary discipline** absorb failures from external I/O (an apprise notifier, a call to the daemon → degrade), but let in-process 100%-tested code crash loudly (a `PrometheusSink` failure is a bug, not a transient).

## Commands

The gate lives in `pyproject.toml` under `[tool.poe.tasks]` (poethepoet) as the single source of truth — the pre-push hook and CI both call it. `uv run poe` lists every task with its `help`.

```bash
uv sync --dev        # install (scripts/setup-dev.sh also installs the pre-push hook)
uv run poe check     # THE FULL GATE (lint-all + test) — the pre-push hook and CI run exactly this
uv run poe fix       # auto-fix everything mechanical: ruff --fix + ruff format + sqlfluff fix
```

Gate sub-tasks, runnable in isolation: `lint` · `format-check` · `type-check` · `sql-lint` · `template-check` (grouped as **`lint-all`**), and **`test`** (runs each package's suite in its own process, so per-package coverage stays isolated). Fixers: `lint-fix` · `format-fix` · `sql-fix` (grouped as **`fix`**).

**Before hand-fixing lint / formatting / SQL, run `uv run poe fix`** — don't spend turns rewriting by hand what a fixer applies mechanically; review its diff instead.

**The gate is PER PACKAGE** (`cd packages/<pkg> && uv run pytest`). The intent: each package owns its own pytest config and 100 % branch coverage in isolation — a root run would mix coverage data across packages and break the per-package threshold. A bare `uv run pytest` from the repo root is also blocked mechanically (the root has no `[tool.pytest.ini_options]` and a root `conftest.py` sets `collect_ignore_glob = ["packages/*"]` → exit 5 with zero collected). Tooling split: `[tool.ruff]` / `[tool.mypy]` at root span all four packages; `[tool.pytest]` / `[tool.coverage]` / `[tool.sqlfluff]` are per-package; one root `uv.lock`. Deployment artifacts live under `deploy/` (compose + `config/` + `deploy/.env.example`); the smoke stack under `tests/smoke/`.

**Single test** (the package-wide `--cov-fail-under=100` makes a lone test "fail" — disable coverage):

```bash
( cd packages/matching && uv run pytest tests/test_engine.py::test_evaluate_real_62a_is_download_via_first_rule_on_62a --no-cov -q )
```

Integration suites (Docker / ffmpeg, deselected by default, excluded from coverage) are documented in `docs/contributing/testing.md`.

## Hard rules (enforced, non-negotiable — do not relax)

- **100% branch coverage on unit tests, per package**, gated in CI and the pre-push hook (`--cov-fail-under=100`, `branch=true`). Integration suites (`api_integration`, `download_integration`, `orchestration_integration`, `compose_integration`) are deselected by the per-package `addopts` and excluded from coverage measurement: they run **on demand**, not in the gate (see `docs/contributing/testing.md`). Never lower the unit-test threshold; add the missing test (exercise *both* sides of every conditional).
- **Strict TDD**: tests are the spec; write the failing test first, watch it fail, then the minimal implementation. Code review judges the tests first. Every test function is annotated `-> None` with typed params.
- **`mypy --strict`** over **both `src` and `tests`**. **`ruff`** selects `E,F,I,UP,B,SIM`, line-length **100**.
- **Clean / Hexagonal**: `domain/` is **pure** — no I/O, no `yaml`/DB/network/clock/logging imports. All I/O lives in `adapters/`. The dependency graph is a DAG. `${NAME}` env-var interpolation in `crawler.yml` is resolved by the config adapter before anything reaches the domain — the domain itself never touches env vars.
- **Python only** (>=3.13). Conventional commits (`feat(domain):`, `fix(domain):`, `test:`, `chore:`, `docs:`).
- **Language: all code is English** (decided 2026-07-02) — identifiers AND prose: comments, docstrings, runtime-emitted messages/logs, CI step names, and commit messages. `.gitignore` is the one deliberate exception, decided 2026-09-17: it is operator-facing housekeeping, and it is consistently French. The only other French in the codebase is genuine *domain data* (real VF episode titles like `La Grenouille Cosmique`, eMule filenames, non-ASCII test fixtures) — data, not prose. **New docs under `agents/specs/`, `agents/plans/` and `agents/handoffs/` are written in English** (decided 2026-07-03); past docs keep their original language (no retro-translation). **Everything under `docs/` is French** (decided 2026-09-17), contributor section included: `docs/` is the published documentation site and its readers are operators. Conversational replies to the operator stay in their chosen language.
- **Subagent-driven execution** (Act phase) + **holistic review** (Verify phase): the cross-cutting review regularly catches bugs — don't skip it.
- For library/framework/CLI questions, use the current docs, not recalled knowledge.

## Workflow

- **Committing is cheap**, you're allowed to commit autonomously.

### 1. Discuss

**Free-form text** discussion with the user. No code, no plan — just understanding.

### 2. Spec

Two forms, depending on complexity:

- **Simple / obvious** : inline spec in the conversation, a few paragraphs.
- **Structured** : spec markdown (`agents/specs/<date>-<slug>.md`)

**The spec is reviewed and approved by the user**.

### 3. Act

`main` is **integration-only** ; never edit directly on it. 
As soon as code or docs will be modified, **branch first**.

**Branching :** ask the user :
1. Stay on current branch
2. New branch **in-place** (`git switch -c <branch>`) — suggested default for edits the user follows in their editor
3. New **worktree** (`EnterWorktree`) — suggested default when dispatching coding agents
4. Other (user describes)

Naming: `<type>/<kebab-slug>` (conventional-commit types: `feat`, `fix`, `docs`, `chore`, `test`, `refactor`).

**Execution: subagent-driven by default.** Delegate work to teammates or subagents to keep the main context clean. Exception: very simple, short, localized action (e.g. one file, one change) → do inline.

### 4. Verify

Run the **full gate** (unit tests 100% branch per package, ruff, mypy, sqlfluff, check_templates). Review the produced code **holistically** — this review regularly catches cross-cutting bugs.

Any non-documentation change reaches `main` **through a PR** (see Wrap) so CI's required `validate / gate` runs before merge — this holistic review is the last local check before that PR.

### 5. Wrap

Once the gate is green and code reviewed:

1. **Write a handoff** in `agents/handoffs/<ISO date> - handoff - <context>.md`: current state, what was just built, learned pitfalls. The handoff is committed before continuing the wrap phase.
2. **Integrate.** **Push the branch and open a PR** for any change touching code, config, tests, `deploy/`, or CI: `main`'s branch protection requires the `validate / gate` check, but `enforce_admins: false` means a local admin merge silently bypasses CI — don't. Wait for the gate green, then merge (linear history is required → **squash or rebase**, not a merge commit). **Exception — documentation-only** (diff touches only `docs/**` + root `*.md`): a local merge/commit to `main` is fine, no PR needed. "Leave as-is" stays available when the user wants to handle it later.
3. **Tag** annotated `vX.Y.Z`, first line `vX.Y.Z - <milestone name>`, then what shipped. **Push it** — that is what builds and signs the versioned image, then creates its GitHub release.
4. **Clean up** branch and/or worktree if applicable.

## Architecture — the matching engine

The core is one layered, declarative matching engine under `packages/matching/src/catalog_matching/`. **The matcher/rule policy is 100% in YAML config; the code is a minimal fixed engine.**

```
load_yaml(path)                         # adapters/config/yaml_loader.py — the ONLY I/O
  → parse_matcher_config / parse_targets   # validation.py — schema + fail-fast graph validation:
  →                                        #   DAG/named-cycle, depth bound (32), regex compile-check,
  →                                        #   unique target_id, closed tier/attr enums → ConfigError
  → MatchingEngine(config, targets)        # engine.py — pre-resolves a matcher tree PER TARGET once
  → engine.evaluate(FileCandidate(...))    #   brute-force over all targets (no funnel)
       → MatchDecision(target_id, rule_name, tier, explanation)  |  None  (file discarded)
```

Module roles (each file is single-purpose):
- `normalization.py` — `fold()` (NFKD + strip diacritics + casefold + `{œ→oe, æ→ae}`, keeps punctuation/digits), `normalize()`/`tokenize()` (alphanumerics only).
- `models.py` — `FileCandidate`, `TargetSegment` (`season`/`seasonal_number`/`absolute_number`/`segment`/`title`/`status`/`sole_segment`; `.target_id` = `062A` from `absolute_number`, zero-padded; double numérotation absolu+saisonnal).
- `matchers.py` — the 4 leaf matchers (`KeywordMatcher`, `RegexMatcher` over stdlib **`re`** (`re.ASCII`), `CoverageMatcher` via rapidfuzz, `AttrBetweenMatcher`).
- `interpolation.py`: per-target regex placeholder interpolation (`{season} {seasonal_number} {absolute_number} {segment} {title}`). Named `fragments:` are expanded earlier, at parse time, in `validation.py`.
- `combinators.py` — the `Matcher` Protocol (`matches(candidate) -> bool`) + `All/Any/NotMatcher`; leaf matchers satisfy it structurally.
- `config.py` — frozen tagged-union config model (`*Def`, `Rule`, `MatcherConfig`, `TIERS`).
- `validation.py` — `parse_*` (structural) + `validate_config` (semantic/graph pass; checks needing the full token table live here, not in parsing).
- `resolver.py` — builds the per-target `Matcher` tree; regex interpolated+compiled per target, coverage bound to the title.
- `engine.py`: `MatchingEngine`; a target is skipped when any `vetoes:` token matches ANY name of the file, then the best rule over all names wins per target; the fan-out is driven by each rule's required `scope` (`segment`/`episode`/`unattributed`), never by rule names; `Explanation` is **returned, not logged**.

Invariants: the decision is order-independent (target_ids are unique); `MatchDecision`'s three string fields are exactly the future `match_decisions` columns (persistence columns like `decided_at`/`node_id` are an adapter's job). Regex tokens compile under stdlib `re` with `re.ASCII` (was RE2 until 2026-07-03 — see Gotchas).

## Gotchas

**Matching engine (stdlib `re`, `re.ASCII`):**
- Regex tokens compile with `re.compile(pattern, re.ASCII)` (`matchers.py`, `validation.py`). `re.ASCII` keeps `\b \d \s \w` ASCII — matching the pre-2026-07-03 RE2 default the existing policy relies on (`fold()` does not reduce non-Latin scripts to ASCII). Case-insensitivity is a leading `(?i)` prefix (not a flag arg). An invalid pattern raises **`re.error`** (caught in `validation.py` → `ConfigError` "not compilable").
- Coverage idioms: a `Protocol` stub `def m(...) -> bool: ...` must be **one line** (a body with `...` on a second line counts as an uncovered branch under `branch=true`). A `case _: assert_never(x)` arm — i.e. the "unreachable default" of a `match` over a closed tagged-union — needs `# pragma: no cover` because it is unreachable by design but the branch counter doesn't know that.
- Don't validate config order-dependently (parse pass = structural; graph pass = full table). Recursive validators need an explicit depth guard → a clean `DepthExceededError`, not `RecursionError` (which is a Python runtime artifact, not a domain error).

**amuleapi / amuled (empirical — facts established by hardware probes / source reading, see `agents/reference/`):**
- *amuleapi = the daemon aMule (the version pinned by `ARG AMULE_VERSION` in `packages/crawler/Dockerfile`) ships to expose amuled over `/api/v1/*` REST plus SSE, and to serve aMule's web UI. amuled starts it itself (`[AmuleApi] Enabled=1`) and hands it a one-off EC token; EC is now an internal link between the two, not something we speak.*
- **Every list endpoint silently returns only the first 100 rows.** `limit` defaults to 100 on `/downloads`, `/shared`, `/search/{id}/results` and every other list route, so omitting it returns the first hundred items, not the collection. For `shared_files()` that is a silent completion failure on any node with more than a hundred shared files. The adapter keyset-pages (`sort=hash` + `after=<last hash>`), never `offset`, because `offset` is a position and a row deleted below the cursor slides one row past the window for good.
- **A `401` is terminal for the session.** amuleapi counts every rejected token per IP (30 in 60 s → a 300 s lockout), so a client that retries a `401` locks itself out. The adapter re-logs in exactly ONCE per request and never loops. Restarting amuleapi, or any password change, invalidates every issued token.
- **`media` on a search result is what the responding server advertised**, never a local probe: it can contradict the file (a `.pdf` reporting a runtime and a video codec is a real observed result), and it is `null` on most global and Kad hits. The key is always present, so test for `null`, not for the key.
- **amuled folds the same file's alternative filenames** into `alternate_names[]` under the most-sourced parent; only parents appear at the top level. The mapper unfolds them into one observation per name, because dropping them loses the descriptive name on a third of the catalog's multi-name hashes.
- amuled moves a finished file to its IncomingDir and flips status to complete **after** the move (no race), so by the time the completion is observable the file is already at its final on-disk path. Completion is detected via the **shared-files list** (a positive signal: amuled auto-shares completed files, so the file appearing in `GET /shared` = it exists at its final path). Detail: `2026-06-17-amuled-completion-behavior.md`.
