"""Entry point `python -m mulewatch.amule_config`: run once by the entrypoint, as root.

Exits naming the first missing variable among PUID, PGID, AMULE_EC_PASSWORD, AMULE_API_PASSWORD.
"""

import grp
import hashlib
import os
import pwd
import subprocess
import sys
from collections.abc import Callable

from mulewatch.amule_config.conf import INCOMING_DIR, TEMP_DIR, reconcile_conf

HOME_DIR = "/home/amule"
CONFIG_DIR = "/home/amule/.aMule"


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"{name} is required")
    return value


def _missing(lookup: Callable[[str], object]) -> bool:
    try:
        lookup("amule")
    except KeyError:
        return True
    return False


def main() -> None:
    puid = _required("PUID")
    pgid = _required("PGID")
    ec_password = _required("AMULE_EC_PASSWORD")
    api_password = _required("AMULE_API_PASSWORD")

    # -o tolerates a uid/gid a Debian system account already holds: PUID/PGID only have to match
    # the host's ownership of the bind mounts.
    if _missing(grp.getgrnam):
        subprocess.run(["groupadd", "-o", "-g", pgid, "amule"], check=True)
    if _missing(pwd.getpwnam):
        useradd = ["useradd", "-o", "-u", puid, "-g", pgid, "-M", "-d", HOME_DIR]
        subprocess.run([*useradd, "-s", "/usr/sbin/nologin", "amule"], check=True)

    # Not recursive: the mounts can hold hundreds of gigabytes of part files the operator owns.
    owned = [HOME_DIR, CONFIG_DIR, INCOMING_DIR, TEMP_DIR]
    for directory in owned:
        os.makedirs(directory, exist_ok=True)
    for directory in owned:
        os.chown(directory, int(puid), int(pgid))

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
    os.chown(conf_path, int(puid), int(pgid))
    os.chmod(conf_path, 0o600)

    # A non-loopback BindAddress needs an admin password or amuleapi refuses to start. Run as
    # amule so amuleapi-passwords (0600) lands with the ownership amuled's amuleapi expects.
    subprocess.run(
        ["amuleapi", f"--config-dir={CONFIG_DIR}", f"--set-admin-pass={api_password}"],
        user=int(puid),
        group=int(pgid),
        # Empty, not omitted: an omitted list keeps root's supplementary groups, group 0 included.
        extra_groups=[],
        env={**os.environ, "HOME": HOME_DIR},
        check=True,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
