"""The amuleapi endpoint shared by the api / download / orchestration integration suites.

The daemon is provided by the CALLER (env vars below), not started by the test process:
testcontainers' own bridge network is unusable on some hosts. See docs/contributing/testing.md.
"""

import os
from typing import NamedTuple

import pytest

HOST_VAR = "MULEWATCH_TEST_API_HOST"
PORT_VAR = "MULEWATCH_TEST_API_PORT"
PASSWORD_VAR = "MULEWATCH_TEST_API_PASSWORD"

_SKIP_REASON = (
    f"{HOST_VAR} is not set: these suites need an amuleapi you provide. No third-party\n"
    "image ships amuleapi, so the daemon to start is our own image:\n"
    "  docker run -d --rm --name mulewatch-test-amuled -p 4711:4711 \\\n"
    "      -e PUID=$(id -u) -e PGID=$(id -g) -e AMULE_EC_PASSWORD=indexer-ec-test \\\n"
    "      -e AMULE_API_PASSWORD=indexer-api-test ghcr.io/mission-titar/mulewatch:latest\n"
    f"then export {HOST_VAR}=127.0.0.1 {PORT_VAR}=4711 {PASSWORD_VAR}=indexer-api-test\n"
    "(full instructions: docs/contributing/testing.md)"
)


class ApiEndpoint(NamedTuple):
    host: str
    port: int
    password: str


@pytest.fixture(scope="session")
def amuled() -> ApiEndpoint:
    host = os.environ.get(HOST_VAR)
    if host is None:
        # CI always provides a daemon: a skip there would silently revive the dead-suite problem.
        if os.environ.get("CI"):
            pytest.fail(_SKIP_REASON)
        pytest.skip(_SKIP_REASON)
    return ApiEndpoint(
        host=host,
        port=int(os.environ.get(PORT_VAR, "4711")),
        password=os.environ.get(PASSWORD_VAR, "indexer-api-test"),
    )
