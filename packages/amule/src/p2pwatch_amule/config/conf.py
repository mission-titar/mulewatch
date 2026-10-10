"""amule.conf: the file the container boots with (`reconcile_conf`), and its listen port."""

import configparser
import io

CONFIG_DIR = "/home/amule/.aMule"
INCOMING_DIR = "/downloads/incoming"
TEMP_DIR = "/downloads/temp"
# aMule's DEFAULT_TCP_PORT (src/Preferences.cpp), what amuled binds when `Port` is absent.
DEFAULT_PORT = 4662


class _CaseSensitiveParser(configparser.RawConfigParser):
    # aMule keys are case-sensitive (ECPassword, not ecpassword); the default lowercases them.
    def optionxform(self, optionstr: str) -> str:
        return optionstr


def reconcile_conf(existing: str | None, ec_digest: str) -> str:
    """Return `existing` (None when absent) with our keys reconciled, every other key kept."""
    parser = _parser()
    if existing is None:
        # An absent key takes aMule's declared default: only the wrong ones go in (ECPort is fine).
        parser["eMule"] = {"IncomingDir": INCOMING_DIR, "TempDir": TEMP_DIR}
        parser["ExternalConnect"] = {"AcceptExternalConnections": "1"}
    else:
        parser.read_string(existing)

    # Reconciled on EVERY boot: a stale digest locks amuleapi out of a healthy-looking daemon, and
    # a moved HttpPort leaves the published 4711 dead. Operator comments are lost on rewrite.
    if not parser.has_section("ExternalConnect"):
        parser.add_section("ExternalConnect")
    parser["ExternalConnect"]["ECPassword"] = ec_digest

    # amuled starts amuleapi itself with a one-off EC token: no second password, no amuleapi.conf.
    if not parser.has_section("AmuleApi"):
        parser.add_section("AmuleApi")
    parser["AmuleApi"].update({"Enabled": "1", "BindAddress": "0.0.0.0", "HttpPort": "4711"})
    return _written(parser)


def listen_port(conf: str) -> int:
    """Return the TCP port amuled binds, `[eMule] Port`."""
    parser = _parser()
    parser.read_string(conf)
    return parser.getint("eMule", "Port", fallback=DEFAULT_PORT)


def with_listen_port(conf: str, port: int) -> str:
    """Return `conf` with `[eMule] Port` and `UDPPort` set to `port`, every other key kept."""
    parser = _parser()
    parser.read_string(conf)
    if not parser.has_section("eMule"):
        parser.add_section("eMule")
    parser["eMule"].update({"Port": str(port), "UDPPort": str(port)})
    return _written(parser)


def _parser() -> _CaseSensitiveParser:
    # RawConfigParser: no %-interpolation, so a password or a path containing % survives.
    # strict=False: a hand-edited file with a duplicate key must not abort the boot.
    return _CaseSensitiveParser(strict=False)


def _written(parser: _CaseSensitiveParser) -> str:
    # aMule writes `Key=value`, not `Key = value`.
    out = io.StringIO()
    parser.write(out, space_around_delimiters=False)
    return out.getvalue()
