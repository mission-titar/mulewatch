"""Port-sync's environment variables: `enabled` for the boot step, `load` for its settings.

A bad value exits naming the variable, as the boot step's own variables do.
"""

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

# gluetun's own booleans (gosettings' parseBool), since PORT_SYNC receives VPN_PORT_FORWARDING.
_ON = ("enabled", "yes", "on", "true")
_OFF = ("disabled", "no", "off", "false", "")


@dataclass(frozen=True)
class Settings:
    gluetun_control_url: str = "http://localhost:8000"
    poll_seconds: int = 60
    restart_min_seconds: int = 300


def enabled(env: Mapping[str, str]) -> bool:
    """Return whether `PORT_SYNC` turns port-sync on; unset is off."""
    value = env.get("PORT_SYNC", "")
    if value.lower() in _ON:
        return True
    if value.lower() in _OFF:
        return False
    sys.exit(f"PORT_SYNC must be one of {', '.join(_ON + _OFF[:-1])}, got {value!r}")


def load(env: Mapping[str, str]) -> Settings:
    """Return the settings from `env`, an unset or empty variable taking its default."""
    default = Settings()
    url = env.get("GLUETUN_CONTROL_URL") or default.gluetun_control_url
    try:
        parts = urlsplit(url)
        # .port raises on a non-numeric or out-of-range port, which urlsplit leaves unchecked.
        valid = parts.scheme in ("http", "https") and bool(parts.hostname) and parts.port != 0
    except ValueError:
        valid = False
    if not valid:
        sys.exit(f"GLUETUN_CONTROL_URL must be an http or https URL with a host, got {url!r}")
    return Settings(
        gluetun_control_url=url,
        poll_seconds=_seconds(env, "PORT_SYNC_POLL_SECONDS", default.poll_seconds),
        restart_min_seconds=_seconds(
            env, "PORT_SYNC_RESTART_MIN_SECONDS", default.restart_min_seconds
        ),
    )


def _seconds(env: Mapping[str, str], name: str, default: int) -> int:
    value = env.get(name)
    if not value:
        return default
    # isascii + isdigit: int() would also take "-1", "+1", "1_000", " 1" and non-ASCII digits.
    if not (value.isascii() and value.isdigit()) or int(value) == 0:
        sys.exit(f"{name} must be a positive integer, got {value!r}")
    return int(value)
