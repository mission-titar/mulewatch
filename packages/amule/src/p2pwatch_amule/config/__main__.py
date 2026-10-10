"""Entry point `python -m p2pwatch_amule.config`: run once by the entrypoint, as root.

Before any change, exits naming the first variable missing or invalid, port-sync's included.
"""

import grp
import hashlib
import os
import pwd
import subprocess
import sys
import unicodedata
from collections.abc import Callable

from p2pwatch_amule.config.conf import INCOMING_DIR, TEMP_DIR, reconcile_conf
from p2pwatch_amule.port_sync import settings

HOME_DIR = "/home/amule"
CONFIG_DIR = "/home/amule/.aMule"


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"{name} is required")
    return value


def _password(name: str) -> str:
    value = _required(name)
    # Cc: the C0 and C1 controls and DEL, which no typed password holds.
    if any(unicodedata.category(char) == "Cc" for char in value):
        sys.exit(f"{name} must not contain a control character")
    return value


def _required_id(name: str) -> int:
    value = _required(name)
    # isascii + isdigit: int() would also take "-1", "+1", "1_000", " 1" and non-ASCII digits.
    if not (value.isascii() and value.isdigit()):
        sys.exit(f"{name} must be a numeric id, got {value!r}")
    return int(value)


def _missing(lookup: Callable[[str], object]) -> bool:
    try:
        lookup("amule")
    except KeyError:
        return True
    return False


def main() -> None:
    puid = _required_id("PUID")
    pgid = _required_id("PGID")
    ec_password = _password("AMULE_EC_PASSWORD")
    api_password = _password("AMULE_API_PASSWORD")
    if settings.enabled(os.environ):
        settings.load(os.environ)

    # -o tolerates a uid/gid a Debian system account already holds: PUID/PGID only have to match
    # the host's ownership of the bind mounts.
    if _missing(grp.getgrnam):
        subprocess.run(["groupadd", "-o", "-g", str(pgid), "amule"], check=True)
    if _missing(pwd.getpwnam):
        useradd = ["useradd", "-o", "-u", str(puid), "-g", str(pgid), "-M", "-d", HOME_DIR]
        subprocess.run([*useradd, "-s", "/usr/sbin/nologin", "amule"], check=True)

    # Not recursive: the mounts can hold hundreds of gigabytes of part files the operator owns.
    owned = [HOME_DIR, CONFIG_DIR, INCOMING_DIR, TEMP_DIR]
    for directory in owned:
        os.makedirs(directory, exist_ok=True)
    for directory in owned:
        os.chown(directory, puid, pgid)

    conf_path = os.path.join(CONFIG_DIR, "amule.conf")
    try:
        with open(conf_path) as handle:
            existing: str | None = handle.read()
    except OSError:
        existing = None
    # ECPassword is read as-is (only the GUI hashes what you type): the file holds the digest.
    digest = hashlib.md5(ec_password.encode()).hexdigest()
    with open(conf_path, "w") as handle:
        handle.write(reconcile_conf(existing, digest))
    os.chown(conf_path, puid, pgid)
    os.chmod(conf_path, 0o600)

    # A non-loopback BindAddress needs an admin password or amuleapi refuses to start. Run as
    # amule so amuleapi-passwords (0600) lands with the ownership amuled's amuleapi expects.
    subprocess.run(
        ["amuleapi", f"--config-dir={CONFIG_DIR}", f"--set-admin-pass={api_password}"],
        user=puid,
        group=pgid,
        # Empty, not omitted: an omitted list keeps root's supplementary groups, group 0 included.
        extra_groups=[],
        env={**os.environ, "HOME": HOME_DIR},
        check=True,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
