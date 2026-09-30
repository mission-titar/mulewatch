"""reconcile_conf: minimal conf on a fresh install, EC and amuleapi keys reconciled every boot."""

import configparser
import hashlib

from mulewatch.amule_config.conf import reconcile_conf

DIGEST = hashlib.md5(b"hunter2").hexdigest()


def _section(content: str, name: str) -> dict[str, str]:
    parser = configparser.RawConfigParser(strict=False)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string(content)
    return dict(parser[name])


def test_fresh_install_writes_the_minimal_conf_as_amule_formats_it() -> None:
    assert reconcile_conf(None, DIGEST) == (
        "[eMule]\n"
        "IncomingDir=/downloads/incoming\n"
        "TempDir=/downloads/temp\n"
        "\n"
        "[ExternalConnect]\n"
        "AcceptExternalConnections=1\n"
        f"ECPassword={DIGEST}\n"
        "\n"
        "[AmuleApi]\n"
        "Enabled=1\n"
        "BindAddress=0.0.0.0\n"
        "HttpPort=4711\n"
        "\n"
    )


def test_a_stale_digest_is_replaced_and_operator_keys_survive() -> None:
    existing = (
        "[eMule]\nIncomingDir=/downloads/incoming\nMaxUpload=42\n\n"
        "[ExternalConnect]\nAcceptExternalConnections=1\n"
        "ECPassword=0000000000000000000000000000dead\nECPort=4712\n"
    )
    content = reconcile_conf(existing, DIGEST)
    assert _section(content, "ExternalConnect")["ECPassword"] == DIGEST
    assert _section(content, "eMule")["MaxUpload"] == "42"
    assert _section(content, "ExternalConnect")["ECPort"] == "4712"


def test_a_missing_key_goes_into_its_section_without_swallowing_the_next() -> None:
    content = reconcile_conf("[ExternalConnect]\nECPort=4712\n\n[eMule]\nMaxUpload=42\n", DIGEST)
    assert _section(content, "ExternalConnect") == {"ECPort": "4712", "ECPassword": DIGEST}
    assert _section(content, "eMule") == {"MaxUpload": "42"}


def test_missing_ec_and_amuleapi_sections_are_appended() -> None:
    content = reconcile_conf("[eMule]\nMaxUpload=42\n", DIGEST)
    assert _section(content, "ExternalConnect") == {"ECPassword": DIGEST}
    assert _section(content, "AmuleApi")["HttpPort"] == "4711"


def test_an_existing_file_gets_no_minimal_conf() -> None:
    assert "IncomingDir" not in reconcile_conf("[eMule]\nMaxUpload=42\n", DIGEST)


def test_a_disabled_or_moved_amuleapi_is_put_back() -> None:
    content = reconcile_conf("[AmuleApi]\nEnabled=0\nHttpPort=4713\nBindAddress=::1\n", DIGEST)
    assert _section(content, "AmuleApi") == {
        "Enabled": "1",
        "HttpPort": "4711",
        "BindAddress": "0.0.0.0",
    }


def test_a_same_named_key_in_another_section_is_left_alone() -> None:
    existing = "[Obfuscation]\nECPassword=notthisone\n\n[ExternalConnect]\nECPort=4712\n"
    content = reconcile_conf(existing, DIGEST)
    assert _section(content, "Obfuscation")["ECPassword"] == "notthisone"
    assert _section(content, "ExternalConnect")["ECPassword"] == DIGEST


def test_a_second_boot_changes_nothing() -> None:
    first = reconcile_conf(None, DIGEST)
    assert reconcile_conf(first, DIGEST) == first


def test_a_percent_sign_survives_without_interpolation() -> None:
    assert "Nick=100%s\n" in reconcile_conf("[eMule]\nNick=100%s\n", DIGEST)


def test_a_duplicate_key_does_not_abort_and_the_last_value_wins() -> None:
    content = reconcile_conf("[eMule]\nMaxUpload=1\nMaxUpload=42\n", DIGEST)
    assert _section(content, "eMule") == {"MaxUpload": "42"}
