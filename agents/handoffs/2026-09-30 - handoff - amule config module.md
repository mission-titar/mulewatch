# Handoff: amule-config.py moved into the mulewatch package

## State

Branch `refactor/amule-config-module`, gate green. The container boot step is now
`mulewatch.amule_config`, run by `entrypoint.sh` as `python -m mulewatch.amule_config` (the image's
`PATH` puts `/app/.venv/bin` first). `docker/amule-config.py` and its bash test are gone.

## Why

It was the only Python shipped in the image outside a package: no mypy, no coverage, a bash test run
by hand, and invisible to the `vex_guards` source guard, which scans `packages/*/src`. That blind
spot hid a false claim: CVE-2026-0864 said `configparser` was never imported, and this script
imported it (the claim was pruned as stale in #82).

## What was built

- `amule_config/conf.py`: pure `reconcile_conf(existing, ec_digest)`; `__main__.py`: the I/O
  (env, users, non-recursive chown, 0600 write, `setpriv ... amuleapi --set-admin-pass`).
- 20 pytest cases replace every check of the old bash test, plus the `main()` paths.
- Behaviour unchanged. Verified by a real boot of the image: digest written, `[AmuleApi]`
  reconciled, operator key kept on a second boot, amuled/amuleapi/crawler up, missing variable
  exits 1 naming it.

## Pitfalls

- On Docker Desktop, bind mounts are `fakeowner`: files show 0:0 inside the container while the
  host sees the real PUID:PGID. Display quirk, not a chown bug.
- The operator refused a test that inspects the Dockerfile's `COPY` lines: a shipped `.py` outside
  `packages/*/src` would again escape the guard, so keep shipped Python inside a package.
