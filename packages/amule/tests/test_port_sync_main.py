"""Port-sync's loop: amuled is never left down, and the port is written only while it is down."""

import logging
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from p2pwatch_amule.config.conf import listen_port, with_listen_port
from p2pwatch_amule.port_sync import __main__ as port_sync
from p2pwatch_amule.port_sync.settings import Settings

DOWN = ["s6-svc", "-wD", "-T", "60000", "-d", "/etc/services.d/amuled"]
UP = ["s6-svc", "-u", "/etc/services.d/amuled"]
CONF = "[eMule]\nPort=4662\nUDPPort=4672\nMaxUpload=42\n\n[AmuleApi]\nEnabled=1\n\n"


class Node:
    """amule.conf under tmp_path, gluetun's answer, and a fake s6 recording its commands."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.conf = tmp_path / "amule.conf"
        self.conf.write_text(CONF)
        self.forwarded: int | None = 51820
        self.commands: list[list[str]] = []
        self.down_exit = 0
        self.on_down: Callable[[], None] = lambda: None
        monkeypatch.setattr(port_sync, "CONF_PATH", str(self.conf))
        monkeypatch.setattr(port_sync, "forwarded_port", self._forwarded_port)
        monkeypatch.setattr(subprocess, "run", self._run)
        for name in (
            "GLUETUN_CONTROL_URL",
            "PORT_SYNC_POLL_SECONDS",
            "PORT_SYNC_RESTART_MIN_SECONDS",
        ):
            monkeypatch.delenv(name, raising=False)

    def _forwarded_port(self, url: str) -> int | None:
        assert url == "http://localhost:8000"
        return self.forwarded

    def _run(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.commands.append(argv)
        if argv == DOWN:
            self.on_down()
            return subprocess.CompletedProcess(argv, self.down_exit, "", "timed out")
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Node:
    return Node(tmp_path, monkeypatch)


def test_a_new_port_stops_amuled_writes_both_ports_and_starts_it(node: Node) -> None:
    assert port_sync.sync(Settings(), None, now=1000.0) == 1000.0
    assert node.commands == [DOWN, UP]
    assert node.conf.read_text() == with_listen_port(CONF, 51820)
    assert listen_port(node.conf.read_text()) == 51820


def test_the_file_is_written_only_once_amuled_is_down(node: Node) -> None:
    ports_seen_at_down: list[int] = []
    node.on_down = lambda: ports_seen_at_down.append(listen_port(node.conf.read_text()))
    port_sync.sync(Settings(), None, now=1000.0)
    assert ports_seen_at_down == [4662]


def test_the_write_keeps_what_amuled_flushed_on_exit(node: Node) -> None:
    def flush() -> None:
        node.conf.write_text(CONF.replace("MaxUpload=42", "MaxUpload=42\nNick=flushed"))

    node.on_down = flush
    port_sync.sync(Settings(), None, now=1000.0)
    assert node.conf.read_text() == with_listen_port(
        CONF.replace("MaxUpload=42", "MaxUpload=42\nNick=flushed"), 51820
    )


def test_the_written_file_stays_private_to_its_owner(node: Node) -> None:
    port_sync.sync(Settings(), None, now=1000.0)
    assert node.conf.stat().st_mode & 0o777 == 0o600


def test_a_down_wait_that_fails_writes_nothing_and_still_starts_amuled(
    node: Node, caplog: pytest.LogCaptureFixture
) -> None:
    node.down_exit = 99
    with caplog.at_level(logging.ERROR):
        assert port_sync.sync(Settings(), None, now=1000.0) == 1000.0
    assert node.commands == [DOWN, UP]
    assert node.conf.read_text() == CONF
    assert "exited 99: timed out" in caplog.text


def test_a_write_that_raises_still_starts_amuled_and_is_logged(
    node: Node, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def replace(source: str, destination: str) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(os, "replace", replace)
    with caplog.at_level(logging.ERROR):
        assert port_sync.sync(Settings(), None, now=1000.0) == 1000.0
    assert node.commands == [DOWN, UP]
    assert node.conf.read_text() == CONF
    assert "No space left on device" in caplog.text


def test_the_same_port_calls_no_s6_command(node: Node) -> None:
    node.forwarded = 4662
    assert port_sync.sync(Settings(), 5.0, now=1000.0) == 5.0
    assert node.commands == []


def test_no_forwarded_port_calls_no_s6_command(node: Node) -> None:
    node.forwarded = None
    assert port_sync.sync(Settings(), None, now=1000.0) is None
    assert node.commands == []


def test_a_restart_within_the_window_calls_no_s6_command(node: Node) -> None:
    assert port_sync.sync(Settings(), 1000.0, now=1299.0) == 1000.0
    assert node.commands == []


def test_a_restart_once_the_window_is_over_goes_ahead(node: Node) -> None:
    assert port_sync.sync(Settings(), 1000.0, now=1300.0) == 1300.0
    assert node.commands == [DOWN, UP]


class _Stop(Exception):
    pass


def test_the_start_sends_up_before_the_first_round_then_polls_every_60s(
    node: Node, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []

    def forwarded_port(url: str) -> int | None:
        node.commands.append(["GET", url])
        return 4662

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise _Stop

    monkeypatch.setattr(port_sync, "forwarded_port", forwarded_port)
    monkeypatch.setattr(time, "sleep", sleep)
    with pytest.raises(_Stop):
        port_sync.main()
    gluetun = ["GET", "http://localhost:8000"]
    assert node.commands == [UP, gluetun, gluetun]
    assert sleeps == [60, 60]


def test_the_restart_time_carries_from_one_round_to_the_next(
    node: Node, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = iter([1000.0, 1100.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        # The first restart wrote 51820; gluetun moving again must wait for the window.
        node.forwarded = 51821
        if len(sleeps) == 2:
            raise _Stop

    monkeypatch.setattr(time, "sleep", sleep)
    with pytest.raises(_Stop):
        port_sync.main()
    assert node.commands == [UP, DOWN, UP]
