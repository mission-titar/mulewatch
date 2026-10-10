# Handoff: stage 3, aMule leaves the core, renamed p2pwatch

Drafted by block 120, for the closing block (130) to correct.

## State

Tier Spec lot, spec `agents/specs/2026-10-10-stage3-amule-leaves-the-core.md` (approved 2026-10-11).
Fifteen blocks. Block 10 (#157, `refactor/rename-p2pwatch`) was merged alone, cut from `main` at
`30fc074`; the operator then renamed the repository to `mission-titar/p2pwatch`, the lead ran
`git remote set-url` and reset the stale `.git/gh-stack` state the rename left. The rest is a stack
cut from the new `main`: #159, #162, #164 to #173, and block 120's PR on top, then the closing block
130 (`docs/stage3-closing`).

The holistic review runs over `git diff 30fc074...origin/<top branch>`, not over the merge base with
`main`, so it reads the rename too (D1).

Nothing of the stack is merged and no release follows (Non-goals): the node pulls `latest`, which
stays on `mulewatch` 4.1.0. `git tag -l 'v*'` still ends at `v4.1.0`. Neither database changes
schema in this lot.

A node is now two images, two containers: the core `p2pwatch` (crawler and webui, no s6, run as
`PUID:PGID`, restarted by Docker) and `p2pwatch-amule`, compose service `ed2k` (s6: amuled, which
starts amuleapi, and port-sync). The core reaches amuleapi at `ed2k:4711` in both variants.

## What was built

- **The rename** (block 10): package, dist, image, compose project, `P2PWATCH_TEST_API_*`, docs site,
  VEX `@id`, apprise identity, webui titles, living docs. The `file_id` namespace URL became
  `https://mission-titar.github.io/p2pwatch/file` (UUID `d30da1ca-776a-5106-b046-daf77302c1af`): no
  production catalog carried one yet, and it can never change after the next release. Kept on
  purpose: `docs/migration-1x.md`, `migration-2x.md`, `troubleshooting-start.md`, `verify-image.md`,
  and from block 110 `migration-4x.md`.
- **`packages/amule/`** (block 20): workspace member `p2pwatch-amule`, package `p2pwatch_amule`,
  stdlib only, under the gate (own pytest at 100 %, root ruff and `mypy --strict`). The boot step
  moved in unchanged as `p2pwatch_amule.config`.
- **Port-sync's parts** (block 30): `amule.conf`'s `Port` / `UDPPort` read and written through the
  boot step's case-sensitive, non-interpolating parser; gluetun's `/v1/portforward` over
  `urllib.request` (10 s, defensive parse); the four variables with defaults (`PORT_SYNC` off,
  `GLUETUN_CONTROL_URL` `http://localhost:8000`, 60 s, 300 s). `PORT_SYNC` takes gluetun's eight
  boolean spellings, any case. The boot step validates the three others only when it is on.
- **Port-sync's loop and s6 service** (block 35): `-wD -T 60000 -d`, the write only on exit 0,
  `-u` in a `finally`, a `-u` at start; the write is atomic (temporary file + `os.replace`, `0600`)
  and reads the file only after the stop; an unreadable `amule.conf` is logged and touches nothing.
  The run script grants `s6-svperms -G amule -E amule` on amuled, then drops to `amule`. The boot
  step creates or removes `services.d/port-sync/down`.
- **The core loses port-sync** (blocks 40 to 65): composition unwired, `port_sync` refused by name
  (`crawler: key 'port_sync' was removed, delete it from crawler.yml`), `PORT_SYNC:
  ${VPN_PORT_FORWARDING:-off}` in the VPN stack; then the loop, its adapters and ports, its three
  events, their policy arms and metrics (`emule_port_sync_triggered`, `emule_port_mismatch`,
  `emule_high_id_recovered`), and `get_listen_port` / `set_listen_port` deleted with their tests and
  `test_amuled_preferences.py`. `EdgeState` stays (the download loop's `disk_low`).
- **The `p2pwatch-amule` image** (block 70): aMule's build stage, a Python stage with only
  `p2pwatch-amule`, Debian runtime with s6; entrypoint runs the boot step then `s6-svscan`. The
  three integration suites run against it alone (`PUID`, `PGID`, both passwords, no config mount).
- **The core image without aMule** (block 80): Python and the venv, `CMD python -m p2pwatch ...`, no
  entrypoint, no s6. `AMULE_API_HOST = "ed2k"`. The smoke stack builds and runs both images; the
  core runs as `user: PUID:PGID` with `/downloads` read-only. `amule-bump` and CI's pin read point
  at `packages/amule/Dockerfile`.
- **The include layout** (block 90): `deploy/compose.yml` (core + one `include:`),
  `deploy/ed2k/{service,direct,vpn}.compose.yml`; `ed2k-gluetun` carries the alias `ed2k`; the
  direct variant publishes `4672/udp` (Kad) instead of `4662/udp`; no `depends_on` between core and
  `ed2k`. The smoke suite renders both variants from a temporary copy with its own `.env`.
- **Two images published and scanned** (block 100): `publish-manifest` and `grype-scan.yml` are a
  matrix over `crawler` and `amule`; one VEX per image (`security/amule.vex.openvex.json` holds the
  two aMule claims and the two Python ones); `vex_files()` maps both; `validate.yml` uploads
  `digest-<package>-<arch>`.
- **Docs** (blocks 110, 120): `docs/migration-4x.md` and the living operator pages (110);
  troubleshooting, contributor pages, `SECURITY.md` and `AGENTS.md` (120).

## Decisions taken during Act

- **Class 2, block 10**: three files cited historical `mulewatch` names that D1's table did not
  list. Operator: option 2. The `mulewatch-crawler` paragraphs were deleted (`SECURITY.md`,
  `verify-image.md`), and `troubleshooting-start.md` and `verify-image.md` joined D1's table with a
  `(Corrected: ...)` marker.
- **Fix-back, block 30 (operator's review)**: the operator's rule, "data read from the environment
  is validated early, a rejection names the variable". `GLUETUN_CONTROL_URL` is fully parsed (a
  malformed host or a port outside 1-65535 exits naming it); both passwords refuse any Unicode `Cc`
  character. The 12-character EC password minimum `install.md` claimed does not exist in aMule at
  `909d304`: the doc was corrected, no rule added.
- **Fix-back, block 35 (asked by the lead)**: a non-numeric `[eMule] Port` or a `configparser.Error`
  is absorbed (logged, no s6 command, retried next round), per the boundary discipline.
- **Block 30**: the two intervals are positive **integers** (D5 says "positive"); an empty variable
  takes its default, like an unset one.
- **Block 35**: the atomic write and `CONFIG_DIR` moved to `config/conf.py`; the smoke test's
  "disabled port-sync stays down" landed here rather than in block 80.
- **Block 50**: `ports/port_forwarding.py` left here, not in block 60: once the loop went, nothing
  imported it and the coverage gate failed on it.
- **Block 80**: the ownership check reads `stat` inside the core container (Docker Desktop shows every
  bind-mounted file as the host user's); no CI step asserts that the core image holds no aMule, it
  was checked once locally with a loop over the three names.
- **Block 90**: the render test copies only `deploy/**/*compose.yml`, not all of `deploy/` (D11), so a
  developer's `deploy/.env` and `data/` are never copied.
- **Block 100**: `validate.yml` edited too (aMule build pushes when `inputs.push`, two digest
  artifacts); the new VEX is version 1, the crawler VEX goes 6 to 7.
- **Block 110**: `index.md` and `legal.md` fixed as class 1 (they described one container);
  `docker compose exec --user amule p2pwatch ... merge` dropped its `--user` (the core image has no
  `amule` user).
- **Block 120**: class 1 fixes. The documented `validate-config` command never worked in the image:
  its defaults are `deploy/*.yml` relative to `/app` (`Invalid config: unreadable YAML file:
  deploy/crawler.yml`); `troubleshooting.md` and `operate.md` now use
  `docker compose run --rm p2pwatch python -m p2pwatch validate-config --config /app/config/...`,
  which also works while the core loops. The manual `local.db` write uses
  `docker compose stop` / `run --rm` / `start` (no s6 nor `amule` user in the core). The
  `file_observation_ranges` remedy points at `migration-4x.md`'s rollback instead of pinning an
  image in the removed `base.compose.yml`. The integration suites' skip message named the core
  image; it now builds `p2pwatch-amule` from the tree. `testing.md`'s "never run" note of 2026-09-16
  and `troubleshooting-start.md`'s `EcAuthError` cause went (both obsolete). `AGENTS.md`'s em dashes
  went with its rewrite.

## Spec corrections for the closing block

Each with a `(Corrected: ...)` marker:

- Section 6, "The core image holds no aMule": `sh -c 'command -v amuled amuleapi s6-svscan'` checks
  only the first name under Debian's dash; loop over the names (block 80).
- "Transient states inside the stack": from block 80 to block 90, `deploy/` points at a core image
  without aMule and cannot start a node (unlisted).
- Block 100's row: it also edits `validate.yml` (D12 lists it).
- Blocks 50 and 60's rows: block 50 removed `ports/port_forwarding.py`.
- Blocks 70 and 80's rows: the crawler's s6 service was `services.d/p2pwatch` after block 10, not
  `services.d/mulewatch`.
- Candidates from the block reports, for the closing block to judge: D5's intervals are positive
  integers and empty means default (block 30); D4's write is atomic (block 35); D11's render copies
  only the compose files (block 90).

## Wrap counts

- Fix-backs: 2 (blocks 30 and 35). Cascaded rebases: 1, with one conflict: block 35 onto block 30's
  fix, in `config/__main__.py` and `test_config_main.py`, both sides kept.
- Runs re-triggered by them: 3 (`feat/amule-port-sync-parts` once, `feat/amule-port-sync-loop` twice,
  one of them cancelled; `gh run list --branch`). Fewer than the 15 blocks: stacking cost less than
  pull requests in series.
- Bodies called unreadable: none so far; the operator has not reviewed the stack yet.
- Friction: the repository rename left `.git/gh-stack` stale; the lead reset it.

## Pitfalls

- **dash's `command -v a b c` prints only the first name**: loop over the names.
- **Docker Desktop shows every bind-mounted file as the host user's**: an ownership check must read
  `stat` inside the container.
- **s6 gives event subscription to root's group only**: without `-E`, `s6-svc -wD` exits 111 and
  sends nothing, amuled stays up. `-G` after `-G -E` keeps `events: group amule`. `s6-svperms` with no
  option prints three lines per service; the smoke test waits on that exact output.
- **`extends:` in an included file resolves `file:` and relative binds against the included file's
  directory**, and never carries `depends_on` over.
- **The crawler's `validate-config` defaults are relative `deploy/*.yml`**: in the image, pass the
  three `/app/config/` paths.
- **`docker compose run --rm p2pwatch ...`** is the one-off tool for the core: same image, user and
  mounts, no ports, no dependencies.
- **A removed-key test matching only the key name passes against any error that quotes the key**:
  match "was removed".
- **Removing a module's last importer leaves its port uncovered**, and the per-package gate fails.
- **Test basenames must stay unique across the non-crawler `tests/` directories** (flat modules for
  mypy): `test_config_main.py`, not `test_main.py`.
- **A mutation restored within the same second, at the same size, leaves a stale `.pyc`**: delete
  `__pycache__` before believing the result (blocks 30 and 90).
- **The live node publishes 4711 and 8080 on the dev machine**: a test daemon goes on another host
  port (`P2PWATCH_TEST_API_PORT`).
- **Zensical slugs ` : ` to `--`** in heading anchors.
- **The build step id is `amule-image`** in `validate.yml`, since `amule` reads the pin. A matrix
  job's `needs` waits for every leg.
- **The integration suites run locally, Docker Desktop included** (6 passed against
  `p2pwatch-amule` on 2026-10-11, blocks 70 and 120); only the compose smoke's completion test fails
  under Desktop (bind mount, SQLite `-shm`), and CI is authoritative for it.

## Holistic review

To fill by the closing block: `.reviews/stage3-holistic.md`, over
`git diff 30fc074...origin/docs/stage3-contributor-docs`, and each finding's exit.

Open for it: section 4's second claim (an included file reads the parent's `.env`) is proved by the
positive assertion `ed2k.environment.PUID == "4242"` with no `PUID` in the process environment, but
no run isolates it failing. Removing `PUID` from the test `.env` fails on the core's `user:` first.

## Not validated

- **The vpn variant end to end on the node**: the core reading `amuled` through `ed2k-gluetun`'s
  alias, `FIREWALL_INPUT_PORTS=4711`, and High-ID within the restart window after a port change. Only
  its `docker compose config` render is tested.
- **The first `main` push after the merge**: both images pushed, the per-image matrix legs,
  `cosign verify` and `cosign verify-attestation --type openvex` on `p2pwatch:main` and
  `p2pwatch-amule:main`, and `docker buildx imagetools inspect` listing `linux/amd64` and
  `linux/arm64` for each.
- **GHCR package visibility** of `p2pwatch` and `p2pwatch-amule`, created by that push with the
  organization's default: `gh api /orgs/mission-titar/packages/container/<name> -q .visibility`.
- **amuled's stop time under s6**, against the 60 s down timeout: `time s6-svc -wD -d
  /etc/services.d/amuled` in `ed2k` on the node.
- A port-sync restart as the core sees it (unreachable, alert after 2 min), unmeasured.
- `docs/migration-4x.md`'s commands and rollback on a real 4.x node.
- Whether `mission-titar.github.io/mulewatch/` redirects after the rename.
- The per-container `mem_limit: 2g` and `pids_limit: 512`, and `no-new-privileges` with `setpriv`, on
  a real node.
- arm64: CI only.
- `docs/high-id.md` keeps "Gardez `PORT_FORWARD_ONLY: "on"`", which no compose file has ever set
  (block 110 left it, it predates the lot).

## Next

The holistic review, then the closing block 130: its findings, `BACKLOG.md` (delete the stage 3
line; with the operator's agreement, add a per-client free-space check to stage 5's line, D10), the
spec corrections above and this handoff. Then the operator reviews the stack, the lead rebases it
onto `main`, and the operator merges. No release.

**At the first p2pwatch release**: remove the "Pas encore de version p2pwatch publiée" warning at
the top of `docs/install.md`; the daily Grype scan stops failing. The node migrates by
`docs/migration-4x.md` (which also drops the five stage 2 keys its `crawler.yml` still carries,
previous handoff), and whoever cuts the release tells the operator first.
