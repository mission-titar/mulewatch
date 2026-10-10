"""config main: env check, port-sync's variables, user, mount points, amule.conf, admin pass."""

import grp
import hashlib
import os
import pwd
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from p2pwatch_amule.config import __main__ as entry
from p2pwatch_amule.config.conf import reconcile_conf

DIGEST = hashlib.md5(b"hunter2").hexdigest()
ENV = {
    "PUID": "1000",
    "PGID": "1001",
    "AMULE_EC_PASSWORD": "hunter2",
    "AMULE_API_PASSWORD": "s3cret",
}
PORT_SYNC_VARIABLES = [
    "PORT_SYNC",
    "GLUETUN_CONTROL_URL",
    "PORT_SYNC_POLL_SECONDS",
    "PORT_SYNC_RESTART_MIN_SECONDS",
]

Call = tuple[list[str], dict[str, Any]]


class Boot:
    """Redirects main's paths under tmp_path and records its commands and chowns."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.home = tmp_path / "home/amule"
        self.config = self.home / ".aMule"
        self.incoming = tmp_path / "downloads/incoming"
        self.temp = tmp_path / "downloads/temp"
        self.conf = self.config / "amule.conf"
        self.port_sync_down = tmp_path / "services.d/port-sync/down"
        self.port_sync_down.parent.mkdir(parents=True)
        self.amule_exists = False
        self.calls: list[Call] = []
        self.chowns: list[tuple[str, int, int]] = []
        for name, value in ENV.items():
            monkeypatch.setenv(name, value)
        for name in PORT_SYNC_VARIABLES:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(entry, "HOME_DIR", str(self.home))
        monkeypatch.setattr(entry, "CONFIG_DIR", str(self.config))
        monkeypatch.setattr(entry, "INCOMING_DIR", str(self.incoming))
        monkeypatch.setattr(entry, "TEMP_DIR", str(self.temp))
        monkeypatch.setattr(entry, "PORT_SYNC_DOWN", str(self.port_sync_down))
        monkeypatch.setattr(grp, "getgrnam", self._lookup)
        monkeypatch.setattr(pwd, "getpwnam", self._lookup)
        monkeypatch.setattr(subprocess, "run", self._run)
        monkeypatch.setattr(os, "chown", self._chown)

    def _lookup(self, name: str) -> object:
        assert name == "amule"
        if not self.amule_exists:
            raise KeyError(name)
        return object()

    def _run(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)

    def _chown(self, path: str, uid: int, gid: int) -> None:
        self.chowns.append((path, uid, gid))

    def set_admin_pass(self) -> Call:
        argv = ["amuleapi", f"--config-dir={self.config}", "--set-admin-pass=s3cret"]
        kwargs = {
            "user": 1000,
            "group": 1001,
            "extra_groups": [],
            "env": {**os.environ, "HOME": str(self.home)},
            "check": True,
        }
        return argv, kwargs


@pytest.fixture
def boot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Boot:
    return Boot(tmp_path, monkeypatch)


def test_first_boot_creates_the_user_and_writes_the_minimal_conf(boot: Boot) -> None:
    entry.main()
    useradd = ["useradd", "-o", "-u", "1000", "-g", "1001", "-M", "-d", str(boot.home)]
    assert boot.calls == [
        (["groupadd", "-o", "-g", "1001", "amule"], {"check": True}),
        ([*useradd, "-s", "/usr/sbin/nologin", "amule"], {"check": True}),
        boot.set_admin_pass(),
    ]
    assert boot.conf.read_text() == reconcile_conf(None, DIGEST)


def test_existing_user_and_group_are_not_recreated(boot: Boot) -> None:
    boot.amule_exists = True
    entry.main()
    assert boot.calls == [boot.set_admin_pass()]


def test_mount_points_are_created_and_owned_but_not_their_contents(boot: Boot) -> None:
    entry.main()
    owned = [boot.home, boot.config, boot.incoming, boot.temp]
    assert all(directory.is_dir() for directory in owned)
    assert boot.chowns == [(str(path), 1000, 1001) for path in [*owned, boot.conf]]


def test_the_conf_is_private_to_its_owner(boot: Boot) -> None:
    entry.main()
    assert stat.S_IMODE(boot.conf.stat().st_mode) == 0o600


def test_an_existing_conf_is_reconciled_not_replaced(boot: Boot) -> None:
    boot.config.mkdir(parents=True)
    boot.conf.write_text("[eMule]\nMaxUpload=42\n\n[ExternalConnect]\nECPassword=dead\n")
    entry.main()
    assert boot.conf.read_text() == reconcile_conf(
        "[eMule]\nMaxUpload=42\n\n[ExternalConnect]\nECPassword=dead\n", DIGEST
    )


@pytest.mark.parametrize("name", list(ENV))
def test_a_missing_variable_aborts_the_boot_naming_it(
    boot: Boot, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.delenv(name)
    with pytest.raises(SystemExit, match=f"^{name} is required$"):
        entry.main()
    assert boot.calls == []


@pytest.mark.parametrize("name", ["PUID", "PGID"])
@pytest.mark.parametrize("value", ["amule", "-1", "1 000", "1_000", "+1000", "١٠٠٠"])
def test_a_non_numeric_id_aborts_the_boot_before_any_change(
    boot: Boot, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit, match=re.escape(f"{name} must be a numeric id, got {value!r}")):
        entry.main()
    assert boot.calls == []
    assert boot.chowns == []
    assert not boot.home.exists()


def test_an_empty_variable_counts_as_missing(boot: Boot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMULE_EC_PASSWORD", "")
    with pytest.raises(SystemExit, match="^AMULE_EC_PASSWORD is required$"):
        entry.main()


@pytest.mark.parametrize(
    ("variables", "message"),
    [
        ({"PORT_SYNC": "maybe"}, "PORT_SYNC must be one of "),
        ({"PORT_SYNC": "on", "PORT_SYNC_POLL_SECONDS": "0"}, "PORT_SYNC_POLL_SECONDS must be "),
        ({"PORT_SYNC": "yes", "GLUETUN_CONTROL_URL": "gluetun"}, "GLUETUN_CONTROL_URL must be "),
    ],
)
def test_a_bad_port_sync_variable_aborts_the_boot_before_any_change(
    boot: Boot, monkeypatch: pytest.MonkeyPatch, variables: dict[str, str], message: str
) -> None:
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit, match=f"^{message}"):
        entry.main()
    assert boot.calls == []
    assert not boot.home.exists()


def test_port_sync_off_leaves_its_other_variables_unread(
    boot: Boot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PORT_SYNC", "off")
    monkeypatch.setenv("PORT_SYNC_POLL_SECONDS", "0")
    entry.main()
    assert boot.calls[-1] == boot.set_admin_pass()


@pytest.mark.parametrize("name", ["AMULE_EC_PASSWORD", "AMULE_API_PASSWORD"])
@pytest.mark.parametrize(
    "value", ["hunter2\n", "hun\tter2", "\rhunter2", "hunter2\x7f", "hunter2\x85"]
)
def test_a_password_with_a_control_character_aborts_the_boot_before_any_change(
    boot: Boot, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit, match=f"^{name} must not contain a control character$"):
        entry.main()
    assert boot.calls == []
    assert not boot.home.exists()


def test_a_password_may_hold_spaces_and_non_ascii_letters(
    boot: Boot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AMULE_API_PASSWORD", "mot de passé")
    entry.main()
    assert "--set-admin-pass=mot de passé" in boot.calls[-1][0]


@pytest.mark.parametrize("value", ["off", "", None])
def test_port_sync_off_or_unset_keeps_its_service_down(
    boot: Boot, monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is not None:
        monkeypatch.setenv("PORT_SYNC", value)
    entry.main()
    assert boot.port_sync_down.is_file()


@pytest.mark.parametrize("value", ["yes", "On"])
def test_port_sync_on_lets_s6_start_its_service(
    boot: Boot, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    boot.port_sync_down.touch()
    monkeypatch.setenv("PORT_SYNC", value)
    entry.main()
    assert not boot.port_sync_down.exists()


def test_port_sync_on_without_a_down_file_boots(
    boot: Boot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PORT_SYNC", "on")
    entry.main()
    assert not boot.port_sync_down.exists()
