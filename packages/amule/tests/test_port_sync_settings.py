"""Port-sync's variables: PORT_SYNC read as gluetun reads booleans, the others with defaults."""

import re

import pytest

from p2pwatch_amule.port_sync.settings import Settings, enabled, load


@pytest.mark.parametrize("value", ["enabled", "yes", "on", "true", "On", "YES", "True"])
def test_gluetuns_true_values_turn_port_sync_on(value: str) -> None:
    assert enabled({"PORT_SYNC": value}) is True


@pytest.mark.parametrize("value", ["disabled", "no", "off", "false", "Off", "FALSE", ""])
def test_gluetuns_false_values_and_an_empty_one_turn_it_off(value: str) -> None:
    assert enabled({"PORT_SYNC": value}) is False


def test_an_unset_port_sync_is_off() -> None:
    assert enabled({}) is False


@pytest.mark.parametrize("value", ["maybe", "1", "0", " on"])
def test_any_other_value_exits_naming_port_sync(value: str) -> None:
    with pytest.raises(
        SystemExit, match=f"^PORT_SYNC must be one of .*, got {re.escape(repr(value))}$"
    ):
        enabled({"PORT_SYNC": value})


def test_no_variable_set_gives_todays_values() -> None:
    assert load({}) == Settings(
        gluetun_control_url="http://localhost:8000", poll_seconds=60, restart_min_seconds=300
    )


def test_an_empty_variable_takes_its_default() -> None:
    env = {
        "GLUETUN_CONTROL_URL": "",
        "PORT_SYNC_POLL_SECONDS": "",
        "PORT_SYNC_RESTART_MIN_SECONDS": "",
    }
    assert load(env) == load({})


def test_each_variable_overrides_its_default() -> None:
    env = {
        "GLUETUN_CONTROL_URL": "https://gluetun:9999/prefix",
        "PORT_SYNC_POLL_SECONDS": "5",
        "PORT_SYNC_RESTART_MIN_SECONDS": "30",
    }
    assert load(env) == Settings(
        gluetun_control_url="https://gluetun:9999/prefix", poll_seconds=5, restart_min_seconds=30
    )


@pytest.mark.parametrize("name", ["PORT_SYNC_POLL_SECONDS", "PORT_SYNC_RESTART_MIN_SECONDS"])
@pytest.mark.parametrize("value", ["0", "-1", "1.5", "60s", "1_000", " 60", "١٠"])
def test_a_number_that_is_not_a_positive_integer_exits_naming_it(name: str, value: str) -> None:
    message = f"{name} must be a positive integer, got {value!r}"
    with pytest.raises(SystemExit, match=f"^{re.escape(message)}$"):
        load({name: value})


@pytest.mark.parametrize("value", ["localhost:8000", "ftp://gluetun:8000", "http://", "http:///v1"])
def test_a_url_without_an_http_scheme_and_a_host_exits_naming_it(value: str) -> None:
    message = f"GLUETUN_CONTROL_URL must be an http or https URL with a host, got {value!r}"
    with pytest.raises(SystemExit, match=f"^{re.escape(message)}$"):
        load({"GLUETUN_CONTROL_URL": value})
