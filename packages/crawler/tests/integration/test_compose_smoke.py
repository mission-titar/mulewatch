"""e2e smoke of the ASSEMBLED docker compose stack, without VPN (single-container design §9).

Dedicated run: ( cd packages/crawler && uv run pytest -m compose_integration --no-cov )
Docker + docker compose v2 required. Brings up the ONE service of tests/smoke/compose.yaml —
the crawler and amuled under s6 in a single container — and asserts the WIRING, NO real
download (amuled has neither an eD2k server nor a VPN; only its EC server is exercised):
  1. `docker compose build` succeeds (the image builds).
  2. the container stays Up, turns `healthy`, supervises its two s6 services, answers on
     amuleapi's /health (started by amuled, not by s6), and its in-process webui answers
     /health.
  3. both deployment entry points render with `docker compose config`, as one service each.
Tear-down: `docker compose down -v` plus the throwaway state directory, in a finally.

The suite needs an engine whose bind mounts are REAL KERNEL MOUNTS. Under Docker Desktop on
Linux the state lives in the Desktop VM's mediated mount, which does not keep SQLite's `-shm`
coherent between processes: the crawler stamps a completion its own connection sees and no other
process ever does, so `test_a_file_amuled_shares_is_recorded_completed` fails there and only
there. `DOCKER_CONTEXT` and `DOCKER_HOST` are forwarded to the CLI (`_docker_env`) precisely so
the run can be pointed at another engine.

Mechanics established EMPIRICALLY (compose v5, Docker 29):
  * The compose file's relative paths are resolved against the project-directory. We PIN it
    explicitly to `_REPO_ROOT` via `--project-directory` (cf. `_run`): `./tests/smoke/...` and
    `context: .` resolve deterministically, without depending on the default (cwd vs the `-f`
    file's directory). The `subprocess.run` calls also run `cwd=_REPO_ROOT`.
  * State lives in BIND MOUNTS under `SMOKE_STATE`, like the real stacks — named volumes are
    gone. The test creates the three subdirectories as the invoking user and passes its own
    uid/gid as PUID/PGID, so the smoke exercises the real ownership path: the container's root
    PID 1 chowns those mount points, then every service drops to the `amule` user and writes
    there. A regression on that path shows up as `unable to open database file`.
  * The image hard-requires PUID, PGID, AMULE_EC_PASSWORD and AMULE_API_PASSWORD: without them the
    startup one-shot exits 1 and the container dies. They are supplied on every compose call,
    since the file is re-parsed each time (and each has a `:?` guard).
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from functools import partial
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.compose_integration

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SMOKE = _REPO_ROOT / "tests/smoke/compose.yaml"

_SERVICE = "p2pwatch"
_S6_SERVICES = ("amuled", "p2pwatch")

# In CI, the build step pre-builds the image and passes IMAGE_TAG; the smoke then consumes it
# WITHOUT a rebuild. Locally (IMAGE_TAG absent) we rebuild via compose, as before.
_IMAGE_TAG = os.environ.get("IMAGE_TAG")
_USES_PREBUILT = _IMAGE_TAG is not None
_BUILD_FLAGS: tuple[str, ...] = () if _USES_PREBUILT else ("--build",)

# Explicit (label, path) pairs: the two stack files share no naming pattern.
_ENTRY_POINTS: tuple[tuple[str, str], ...] = (
    ("compose", "deploy/compose.yml"),
    ("gluetun", "deploy/gluetun.compose.yml"),
)
# No compose profile anywhere: every service of a stack starts unconditionally.
_ALWAYS_ON_SERVICES = frozenset({_SERVICE})
# VPN-stack-only. The socket proxy that used to sit here is gone with the Docker API: port-sync
# restarts amuled with `s6-svc` inside the container now (design §9).
_GLUETUN_ONLY_SERVICES = frozenset({"gluetun"})
_DELETED_SERVICES = frozenset({"docker-proxy", "crawler", "amuled"})

# Isolated project (unique prefix per run) so we NEVER touch a real stack on the host.
_PROJECT = f"emule_smoke_{uuid.uuid4().hex[:8]}"

# Throwaway bind-mount root, named after the project so two runs never share it. Computed, not
# created, at import time: this module is collected by every gate run, and only the fixture
# below (which runs when the suite is actually selected) touches the filesystem.
_STATE_DIR = Path(tempfile.gettempdir()) / _PROJECT
_STATE_SUBDIRS = ("amule", "data", "downloads")

_EC_PASSWORD = "smoke-ec-password"
_API_PASSWORD = "smoke-api-password"

# Everything the smoke stack interpolates. PUID/PGID are OURS on purpose: the bind mounts must
# stay readable from the host, which is the whole reason named volumes were dropped.
_SMOKE_ENV = {
    "PUID": str(os.getuid()),
    "PGID": str(os.getgid()),
    "AMULE_EC_PASSWORD": _EC_PASSWORD,
    "AMULE_API_PASSWORD": _API_PASSWORD,
    "SMOKE_STATE": str(_STATE_DIR),
}

# `docker compose config` on the deployment entry points interpolates at PARSE time, gluetun's
# variables included: stub them so a missing variable is not what fails.
_CONFIG_ENV = {
    **_SMOKE_ENV,
    "WIREGUARD_PRIVATE_KEY": "x",
    "SERVER_COUNTRIES": "",
}

# The ONLY two variables of the caller's environment the docker CLI is allowed to see. They pick
# WHICH daemon answers, they change nothing about what the suite does, and without them the suite
# is unpointable at anything but the machine's default context - which on a host running Docker
# Desktop is the Desktop VM, whose mediated bind mounts break this suite (see the module docstring).
# CI sets neither, so CI behaviour is unchanged. Everything else stays stripped: the sanitised
# environment is what keeps the smoke reproducible.
_DAEMON_SELECTORS = ("DOCKER_CONTEXT", "DOCKER_HOST")


def _docker_env(*extra: Mapping[str, str]) -> dict[str, str]:
    """The environment handed to a `docker` subprocess: PATH, the stubs, the daemon selectors."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    for mapping in extra:
        env.update(mapping)
    for name in _DAEMON_SELECTORS:
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    return env


def _run(*args: str, files: tuple[Path, ...], timeout: float) -> subprocess.CompletedProcess[str]:
    """Run `docker compose -p <project> -f ... <args>` from the repo root (cwd)."""
    file_flags: list[str] = []
    for path in files:
        file_flags += ["-f", str(path)]
    command = [
        "docker",
        "compose",
        "-p",
        _PROJECT,
        "--project-directory",
        str(_REPO_ROOT),
        *file_flags,
        *args,
    ]
    return subprocess.run(
        command,
        cwd=_REPO_ROOT,
        env=_docker_env(_SMOKE_ENV, {"IMAGE_TAG": _IMAGE_TAG} if _IMAGE_TAG is not None else {}),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _down(files: tuple[Path, ...]) -> None:
    """Idempotent tear-down: removes the project's containers + volumes + orphans."""
    _run("down", "-v", "--remove-orphans", files=files, timeout=180)


def _ps_field(field: str, files: tuple[Path, ...]) -> str:
    """One field of the service's `ps -a --format json` entry (one JSON object per line)."""
    result = _run("ps", "-a", "--format", "json", _SERVICE, files=files, timeout=60)
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("Service") == _SERVICE:
            return str(obj.get(field))
    return f"<absent from `ps`: {result.stdout!r}>"


def _exec(*command: str, files: tuple[Path, ...]) -> str:
    """Run a command in the container; on failure return the error AS the observed value.

    Tolerant on purpose: every caller is a readiness probe polling for an expected output, and a
    container that is not ready yet fails the exec rather than printing something wrong.
    """
    result = _run("exec", "-T", _SERVICE, *command, files=files, timeout=120)
    if result.returncode != 0:
        return f"<rc={result.returncode} {result.stderr.strip()}>"
    return result.stdout.strip()


def _wait_for(
    label: str,
    probe: Callable[[], str],
    target: str,
    files: tuple[Path, ...],
    *,
    attempts: int = 30,
    delay: float = 2.0,
) -> None:
    """Poll `probe` until it returns `target`; on exhaustion attach the container logs.

    Everything here is a readiness probe: the three s6 services start at once and the container
    reports `running` well before amuled listens on EC or uvicorn has bound its socket. With a
    single container, its logs are THE diagnostic, so a failure carries them.
    """
    last = "<never ran>"
    for _ in range(attempts):
        last = probe()
        if last == target:
            return
        time.sleep(delay)
    logs = _run("logs", "--no-color", "--tail", "80", _SERVICE, files=files, timeout=60)
    raise AssertionError(
        f"{label}: expected {target!r}, last was {last!r}\n"
        f"--- {_SERVICE} logs ---\n{logs.stdout}{logs.stderr}"
    )


_WEBUI_HEALTH = (
    "import urllib.request;print(urllib.request.urlopen('http://localhost:8080/health').status)"
)

# amuleapi is supervised by amuled, not by s6, so s6-svstat says nothing about it. Its /health
# needs no token and touches no EC, so it answers while amuled is still busy starting up.
_AMULEAPI_HEALTH = (
    "import urllib.request;"
    "print(urllib.request.urlopen('http://localhost:4711/api/v1/health').status)"
)


@pytest.fixture
def project_files() -> Iterator[tuple[Path, ...]]:
    """Standalone smoke compose file, a fresh state directory, and the surrounding tear-down.

    The three subdirectories are created HERE, by the invoking user, rather than left to the
    daemon: compose would create a missing bind source as root, which is not the shape an
    operator's `deploy/` has.
    """
    base = (_SMOKE,)
    _down(base)
    shutil.rmtree(_STATE_DIR, ignore_errors=True)
    for name in _STATE_SUBDIRS:
        (_STATE_DIR / name).mkdir(parents=True)
    try:
        yield base
    finally:
        _down(base)
        shutil.rmtree(_STATE_DIR, ignore_errors=True)


def test_the_harness_hides_everything_but_the_daemon_selectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCKER_CONTEXT", "chosen")
    monkeypatch.setenv("HOME", "/somewhere")
    env = _docker_env(_SMOKE_ENV)

    assert env["DOCKER_CONTEXT"] == "chosen"
    assert env["PUID"] == str(os.getuid())
    assert "HOME" not in env  # the sanitised environment is what keeps the smoke reproducible


def test_a_chosen_daemon_that_answers_nothing_fails_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative path of the two daemon selectors, which is the only side worth proving.

    Without the allowlist the variable never reaches the CLI: the call quietly lands on the
    machine's default daemon and SUCCEEDS, which is how a run meant for one engine ends up
    exercising another. Pointing it at a socket nothing listens on must therefore fail.
    """
    monkeypatch.setenv("DOCKER_HOST", "unix:///nonexistent/p2pwatch-smoke.sock")
    result = _run("ps", files=(_SMOKE,), timeout=120)

    assert result.returncode != 0, result.stdout
    assert "p2pwatch-smoke.sock" in result.stderr + result.stdout


@pytest.mark.skipif(_USES_PREBUILT, reason="image prebuilt in CI - nothing to build")
def test_build_succeeds(project_files: tuple[Path, ...]) -> None:
    result = _run("build", files=project_files, timeout=1800)
    assert result.returncode == 0, result.stderr


def test_one_container_supervises_the_two_services(project_files: tuple[Path, ...]) -> None:
    """The single container runs, turns healthy, and holds amuled + amuleapi + the crawler."""
    result = _run("up", "-d", *_BUILD_FLAGS, files=project_files, timeout=1800)
    assert result.returncode == 0, result.stderr

    _wait_for("state", lambda: _ps_field("State", project_files), "running", project_files)
    # The compose healthcheck tests s6-svstat's OUTPUT, not its exit code (it exits 0 for a
    # stopped service too). `healthy` therefore means amuled really is up under s6.
    _wait_for("health", lambda: _ps_field("Health", project_files), "healthy", project_files)
    for service in _S6_SERVICES:
        svstat = partial(
            _exec, "s6-svstat", "-u", f"/etc/services.d/{service}", files=project_files
        )
        _wait_for(f"s6-svstat {service}", svstat, "true", project_files)
    # The crawler ALSO serves the read-only webui in-process (spec P4), on a bind fixed at
    # 0.0.0.0:8080 in code. Polled from inside the container, so no host port is needed.
    _wait_for(
        "webui /health",
        lambda: _exec("python", "-c", _WEBUI_HEALTH, files=project_files),
        "200",
        project_files,
    )
    # amuleapi answering is the only proof that amuled's autorun worked and that the crawler has
    # a transport at all: nothing else in this stack reaches it.
    _wait_for(
        "amuleapi /health",
        lambda: _exec("python", "-c", _AMULEAPI_HEALTH, files=project_files),
        "200",
        project_files,
    )


@pytest.mark.parametrize(
    ("label", "path"), _ENTRY_POINTS, ids=[label for label, _ in _ENTRY_POINTS]
)
def test_entrypoint_config_renders(label: str, path: str) -> None:
    """`docker compose -f <stack file> config` renders without error.

    Locks in include + interpolation + the `:?` guards (no daemon required; the bind-mount
    sources need not exist for `config`). Also asserts the resulting topology: one `p2pwatch`
    service, the VPN stack adding only gluetun, nothing left of the four-service shape, and NO
    named volume — the operator must be able to `sqlite3 deploy/data/catalog.db` from the host.
    """
    command = ["docker", "compose", "-f", path, "config"]
    result = subprocess.run(
        command,
        cwd=_REPO_ROOT,
        env=_docker_env(_CONFIG_ENV),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr

    rendered = yaml.safe_load(result.stdout)
    assert isinstance(rendered, dict)
    services = set(rendered.get("services", {}))
    expected_gluetun = _GLUETUN_ONLY_SERVICES if label == "gluetun" else frozenset()
    assert services == _ALWAYS_ON_SERVICES | expected_gluetun, f"{path}: got {services}"
    assert not _DELETED_SERVICES & services, f"{path}: deleted service still declared, {services}"
    assert not rendered.get("volumes"), f"{path}: named volumes are gone, got {rendered['volumes']}"


# --- Completion scenario (scope-reduction spec §4) ---------------------------------------------
#
# amuled's IncomingDir, written by the startup one-shot into amule.conf. A file dropped there is
# hashed and shared at the next amuled start, which is how we obtain a REAL ed2k hash without
# ever computing one ourselves.
_INCOMING_DIR = "/downloads/incoming"
_SEEDED_TARGET = "062A"
_SEEDED_SIZE = 65536

# The one amuleapi is in THIS container, at the address fixed in code (design §6).
_API_HOST, _API_PORT = "127.0.0.1", 4711

_SHARED_HASHES = f"""
import asyncio, json
from p2pwatch.adapters.clock_asyncio import AsyncioClock
from p2pwatch.adapters.mule_api.client import AmuleApiClient

async def main() -> None:
    client = AmuleApiClient({_API_HOST!r}, {_API_PORT}, {_API_PASSWORD!r}, clock=AsyncioClock())
    await client.connect()
    completed = [d.file.native_id for d in await client.downloads() if d.completed]
    print(json.dumps(sorted(completed)))
    await client.close()

asyncio.run(main())
"""

_SEED_ROW = f"""
import datetime, sqlite3, sys
from p2pwatch.domain.file_key import FileKey, Network
now = datetime.datetime.now(datetime.UTC).isoformat()
file = FileKey(Network.ED2K, sys.argv[1])
conn = sqlite3.connect("/data/local.db", timeout=30)
conn.execute(
    "INSERT INTO downloads"
    " (file_id, network, native_id, target_id, state, queued_at, size_bytes, last_seen_at)"
    " VALUES (?, ?, ?, {_SEEDED_TARGET!r}, 'downloading', ?, {_SEEDED_SIZE}, ?)",
    (file.file_id, file.network, file.native_id, now, now),
)
conn.commit()
"""

_READ_ROW = """
import sqlite3, sys
conn = sqlite3.connect("/data/local.db", timeout=30)
row = conn.execute(
    "SELECT state, completed_at IS NOT NULL FROM downloads WHERE native_id = ?", (sys.argv[1],)
).fetchone()
print(row[0], bool(row[1]))
"""


def _exec_python(script: str, *args: str, files: tuple[Path, ...]) -> str:
    """Run `script` with the container's python and return its stdout (fails loudly)."""
    result = _run("exec", "-T", _SERVICE, "python", "-c", script, *args, files=files, timeout=120)
    assert result.returncode == 0, f"{result.stdout}{result.stderr}"
    return result.stdout.strip()


def _shared_hashes(files: tuple[Path, ...]) -> frozenset[str] | None:
    """Hashes amuled currently shares, read over the API from inside the container.

    `None` means the call itself did not complete. That is a READINESS state, not a result:
    `s6-svc -r` takes amuleapi down with amuled and brings both back, and in between the API
    answers `503 ec_unavailable` or nothing at all. Callers poll on it.
    """
    output = _exec("python", "-c", _SHARED_HASHES, files=files)
    try:
        return frozenset(json.loads(output))
    except json.JSONDecodeError:
        return None


def _wait_new_shared_hash(
    before: frozenset[str], files: tuple[Path, ...], *, attempts: int = 20, delay: float = 2.0
) -> str:
    """Poll until exactly one hash appeared in amuled's shared list, and return it.

    Called right after `s6-svc -r`, so it is the readiness probe for the restart TOO: amuled is
    still shutting down for the first attempts (EC refuses or resets), then comes back and hashes
    the IncomingDir asynchronously. A failed EC call is therefore a poll iteration, not a failure.
    The "exactly one" bound is what identifies OUR file: the smoke amuled shares nothing else at
    that point (its only queue entry has no sources and no bytes, so it is not shared yet).
    """
    appeared: frozenset[str] = frozenset()
    for _ in range(attempts):
        current = _shared_hashes(files)
        if current is not None:
            appeared = current - before
            if len(appeared) == 1:
                return next(iter(appeared))
        time.sleep(delay)
    raise AssertionError(f"expected exactly one new shared hash, got {sorted(appeared)}")


def test_a_file_amuled_shares_is_recorded_completed(project_files: tuple[Path, ...]) -> None:
    """End to end: amuled shares a file that left the queue, the crawler completes and notifies.

    Drives the running container rather than an in-process cycle: the shipped image carries the
    API adapter, amuled and the migrations, so the whole path (real HTTP call over loopback, real
    amuled behind amuleapi, real local.db on a bind mount) is exercised without building a
    parallel harness. The ed2k hash is never computed here; amuled computes it and we read it
    back over the API, which is what makes seeding a matching row possible at all.
    """
    result = _run("up", "-d", *_BUILD_FLAGS, files=project_files, timeout=1800)
    assert result.returncode == 0, result.stderr
    _wait_for("health", lambda: _ps_field("Health", project_files), "healthy", project_files)
    _wait_for(
        "webui /health",
        lambda: _exec("python", "-c", _WEBUI_HEALTH, files=project_files),
        "200",
        project_files,
    )

    before = _shared_hashes(project_files)
    assert before is not None, "amuled's EC server must answer before the restart"
    drop = _run(
        "exec",
        "-T",
        _SERVICE,
        "sh",
        "-c",
        f"head -c {_SEEDED_SIZE} /dev/urandom > {_INCOMING_DIR}/p2pwatch-smoke.bin",
        files=project_files,
        timeout=120,
    )
    assert drop.returncode == 0, f"{drop.stdout}{drop.stderr}"
    # amuled only scans its IncomingDir at startup. It is an s6 service now, so the rescan is a
    # process restart inside the container — the same `s6-svc -r` the port-sync restarter runs,
    # and the container itself never goes down.
    restart = _run(
        "exec",
        "-T",
        _SERVICE,
        "s6-svc",
        "-r",
        "/etc/services.d/amuled",
        files=project_files,
        timeout=60,
    )
    assert restart.returncode == 0, f"{restart.stdout}{restart.stderr}"
    ed2k_hash = _wait_new_shared_hash(before, project_files)

    # The file is shared and was never in the download queue: the completion signal is complete.
    _exec_python(_SEED_ROW, ed2k_hash, files=project_files)
    _wait_for(
        f"download {ed2k_hash}",
        lambda: _exec("python", "-c", _READ_ROW, ed2k_hash, files=project_files),
        "completed True",
        project_files,
    )

    # The completion also reached the observability pipeline (the notification, target-labelled).
    logs = _run("logs", "--no-color", _SERVICE, files=project_files, timeout=60)
    assert f"download completed: {_SEEDED_TARGET}" in logs.stdout, logs.stdout[-4000:]
