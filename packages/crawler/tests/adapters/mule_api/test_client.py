"""``AmuleApiClient``: auth, search, status, preferences (spec amuleapi §4)."""

import asyncio
import json
from datetime import datetime

import httpx
import pytest

from mulewatch.adapters.mule_api.client import AmuleApiClient
from mulewatch.adapters.mule_api.errors import (
    ApiAuthError,
    ApiRejectedError,
    ApiUnreachableError,
)
from mulewatch.domain.file_key import FileKey, Network
from mulewatch.ports.client_errors import (
    ClientUnreachableError,
    DownloadRejectedError,
    SearchFailedError,
)
from mulewatch.ports.client_status import ChannelStatus
from mulewatch.ports.download_client import DownloadClient, DownloadRequest, DownloadStatus
from mulewatch.ports.port_sync import KadStatus
from tests.adapters.mule_api.api_fakes import PASSWORD, TOKEN, FakeAmuleApi, error

_HASH = "8b54a3c20fae9e4b9f7e0c2c8c01b6b1"
_KEY = FileKey(Network.ED2K, _HASH)


def _client(api: FakeAmuleApi, *, password: str = PASSWORD) -> AmuleApiClient:
    return AmuleApiClient("amuled.test", 4711, password, transport=api.transport(), clock=api.clock)


async def _connected(api: FakeAmuleApi) -> AmuleApiClient:
    client = _client(api)
    await client.connect()
    return client


def _paths(api: FakeAmuleApi, path: str) -> list[httpx.URL]:
    """Every request made to one route, in order (close() adds a logout after the assertions)."""
    return [request.url for request in api.requests if request.url.path == path]


def _seconds_after_start(api: FakeAmuleApi, method: str, path: str) -> list[float]:
    """When each call to one route was made, in seconds after the search's network start."""
    assert api.search_started_at is not None
    start: datetime = api.search_started_at
    return [
        (moment - start).total_seconds()
        for moment, request in api.calls
        if request.method == method and request.url.path == path
    ]


def _progress_polls(api: FakeAmuleApi) -> list[float]:
    """The reads asking for no row: the polls of the progress envelope."""
    assert api.search_started_at is not None
    return [
        (moment - api.search_started_at).total_seconds()
        for moment, request in api.calls
        if request.url.path == "/api/v1/search/42/results"
        and request.url.params.get("limit") == "0"
    ]


def _result(name: str = "Keroro.095.avi") -> dict[str, object]:
    return {"hash": _HASH, "name": name, "size_bytes": 10, "sources": {"total": 1}}


@pytest.mark.asyncio
async def test_connect_logs_in_and_arms_the_bearer() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    try:
        await client.network_status()
    finally:
        await client.close()

    login = api.requests[0]
    assert login.url.path == "/api/v1/auth/login"
    # The token has to be asked for: the default reply carries only the cookie (spec §6).
    assert login.url.params.get("include_token") == "true"
    assert api.requests[1].headers["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.asyncio
async def test_connect_is_idempotent() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    await client.connect()
    await client.close()

    assert api.logins == 1


@pytest.mark.asyncio
async def test_concurrent_connects_log_in_once() -> None:
    api = FakeAmuleApi()

    async def round_trip(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0)  # suspends like a real request, letting the other callers in
        return api.handle(request)

    transport = httpx.MockTransport(round_trip)
    client = AmuleApiClient("amuled.test", 4711, PASSWORD, transport=transport, clock=api.clock)
    await asyncio.gather(*(client.connect() for _ in range(5)))
    await client.close()

    assert api.logins == 1  # one session: no AsyncClient opened for nothing


@pytest.mark.asyncio
async def test_connect_refuses_an_empty_password_without_asking() -> None:
    api = FakeAmuleApi()
    client = _client(api, password="")

    with pytest.raises(ApiAuthError):
        await client.connect()
    assert api.requests == []


@pytest.mark.asyncio
async def test_a_wrong_password_is_a_config_error_not_a_loop_case() -> None:
    api = FakeAmuleApi()
    client = _client(api, password="wrong")

    with pytest.raises(ApiAuthError):
        await client.connect()


@pytest.mark.asyncio
async def test_a_login_reply_without_a_token_is_a_config_error() -> None:
    api = FakeAmuleApi()
    api.overrides[("POST", "/api/v1/auth/login")] = lambda _: httpx.Response(
        200, json={"role": "admin"}
    )
    client = _client(api)

    with pytest.raises(ApiAuthError):
        await client.connect()


@pytest.mark.asyncio
async def test_login_disabled_is_a_daemon_that_is_not_ready() -> None:
    api = FakeAmuleApi()
    api.overrides[("POST", "/api/v1/auth/login")] = lambda _: error(
        503, "login_disabled", "no password configured"
    )
    client = _client(api)

    with pytest.raises(ApiUnreachableError):
        await client.connect()


@pytest.mark.asyncio
async def test_a_dead_transport_is_an_unreachable_daemon() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    api = FakeAmuleApi()
    api.overrides[("POST", "/api/v1/auth/login")] = refuse
    client = _client(api)

    with pytest.raises(ApiUnreachableError):
        await client.connect()


@pytest.mark.asyncio
async def test_an_operation_before_connect_is_unreachable() -> None:
    client = _client(FakeAmuleApi())

    with pytest.raises(ApiUnreachableError):
        await client.network_status()


@pytest.mark.asyncio
async def test_close_logs_out_once_and_tolerates_a_failure() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    api.overrides[("POST", "/api/v1/auth/logout")] = lambda _: error(401, "unauthorized")

    await client.close()
    await client.close()  # a second close is a no-op, not a second request

    assert [request.url.path for request in api.requests].count("/api/v1/auth/logout") == 1


@pytest.mark.asyncio
async def test_connect_after_close_logs_in_again() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    await client.close()
    await client.connect()
    await client.close()

    assert api.logins == 2


@pytest.mark.asyncio
async def test_a_stale_token_is_renewed_once() -> None:
    """§7.3: a 401 is terminal for the session, so we re-login once and never loop."""
    api = FakeAmuleApi()
    client = await _connected(api)
    stale = {"count": 0}

    def once(request: httpx.Request) -> httpx.Response:
        stale["count"] += 1
        if stale["count"] == 1:
            return error(401, "unauthorized", "credentials changed")
        return httpx.Response(200, json=api.status)

    api.overrides[("GET", "/api/v1/status")] = once
    await client.network_status()
    await client.close()

    assert api.logins == 2
    assert stale["count"] == 2


@pytest.mark.asyncio
async def test_a_second_401_ends_the_session_instead_of_looping() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    api.overrides[("GET", "/api/v1/status")] = lambda _: error(401, "unauthorized")

    with pytest.raises(ApiUnreachableError):
        await client.network_status()
    # One re-login, one retry, then it stops: the limiter locks an IP out after 30 rejects.
    assert api.logins == 2
    assert [request.url.path for request in api.requests].count("/api/v1/status") == 2


@pytest.mark.asyncio
async def test_a_rate_limited_answer_carries_its_retry_delay() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    api.overrides[("GET", "/api/v1/status")] = lambda _: error(
        429, "rate_limited", "locked out", **{"Retry-After": "300"}
    )

    with pytest.raises(ApiUnreachableError, match="300"):
        await client.network_status()


@pytest.mark.asyncio
async def test_a_broken_ec_link_is_an_unreachable_daemon() -> None:
    """503 ec_unavailable: amuleapi is up, its link to amuled is not (a NEW state)."""
    api = FakeAmuleApi()
    client = await _connected(api)
    api.overrides[("GET", "/api/v1/status")] = lambda _: error(503, "ec_unavailable", "cold")

    with pytest.raises(ApiUnreachableError):
        await client.network_status()


@pytest.mark.asyncio
async def test_a_body_that_is_not_json_is_an_unreachable_daemon() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    api.overrides[("GET", "/api/v1/status")] = lambda _: httpx.Response(200, content=b"<html>")

    with pytest.raises(ApiUnreachableError):
        await client.network_status()


@pytest.mark.asyncio
async def test_an_error_body_that_is_not_an_envelope_still_maps() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    api.overrides[("GET", "/api/v1/status")] = lambda _: httpx.Response(500, content=b"oops")

    with pytest.raises(ApiUnreachableError):
        await client.network_status()


@pytest.mark.asyncio
async def test_an_error_envelope_with_junk_fields_still_maps() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    api.overrides[("GET", "/api/v1/status")] = lambda _: httpx.Response(
        500, json={"error": {"code": 7, "message": None}}
    )

    with pytest.raises(ApiUnreachableError):
        await client.network_status()


@pytest.mark.asyncio
async def test_an_error_body_whose_error_key_is_not_an_object_still_maps() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)
    api.overrides[("GET", "/api/v1/status")] = lambda _: httpx.Response(500, json={"error": 7})

    with pytest.raises(ApiUnreachableError):
        await client.network_status()


# --- search(): one call per search ---------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("channel", "search_type"), [("ed2k", "global"), ("kad", "kad")])
async def test_search_starts_the_keyword_on_the_channels_search_type(
    channel: str, search_type: str
) -> None:
    api = FakeAmuleApi()
    client = await _connected(api)

    await client.search("keroro", channel, 120)
    await client.close()

    start = api.requests[1]
    assert start.url.path == "/api/v1/search"
    assert json.loads(start.content) == {"query": "keroro", "type": search_type}


def test_the_client_declares_the_channels_it_searches() -> None:
    assert _client(FakeAmuleApi()).channels == ("ed2k", "kad")


@pytest.mark.asyncio
async def test_search_returns_the_results_with_their_keyword_and_counts_what_it_drops() -> None:
    rows = [_result(f"Keroro.{index:04d}.avi") | {"hash": f"{index:032x}"} for index in range(501)]
    api = FakeAmuleApi(results=[*rows, {"hash": "nope"}])
    client = await _connected(api)

    observations = await client.search("keroro", "ed2k", 120)
    await client.close()

    assert len(observations) == 501
    assert {observation.keyword for observation in observations} == {"keroro"}
    assert client.skipped_entries_total == 1


@pytest.mark.asyncio
async def test_search_returns_as_soon_as_amuled_reports_it_finished() -> None:
    api = FakeAmuleApi(search_seconds={"keroro": 12})
    client = await _connected(api)

    await client.search("keroro", "ed2k", 120)
    await client.close()

    assert _progress_polls(api) == [0, 5, 10, 15]


@pytest.mark.asyncio
async def test_search_stops_waiting_at_its_budget_and_returns_what_it_holds() -> None:
    api = FakeAmuleApi(progress={"state": "running"}, results=[_result()])
    client = await _connected(api)

    observations = await client.search("keroro", "ed2k", 12)
    await client.close()

    assert _progress_polls(api) == [0, 5, 10, 12]
    assert len(observations) == 1


@pytest.mark.asyncio
async def test_search_never_stops_nor_frees_its_search() -> None:
    """amuled expires a Kad search at 45 s, and stopping one takes it off Kad's list."""
    api = FakeAmuleApi(progress={"state": "running"})
    client = await _connected(api)

    await client.search("keroro", "kad", 120)
    await client.close()

    assert _paths(api, "/api/v1/search/42/stop") == []
    assert [request for request in api.requests if request.method == "DELETE"] == []


@pytest.mark.asyncio
async def test_a_kad_search_is_widened_from_its_second_poll_until_kad_refuses() -> None:
    api = FakeAmuleApi(progress={"state": "running"})
    answers = [httpx.Response(202)] * 4 + [error(409, "kad_more_exhausted")]
    api.overrides[("POST", "/api/v1/search/42/more")] = lambda _: answers.pop(0)
    client = await _connected(api)

    await client.search("keroro", "kad", 120)
    await client.close()

    assert _seconds_after_start(api, "POST", "/api/v1/search/42/more") == [5, 10, 15, 20, 25]


@pytest.mark.asyncio
async def test_an_ed2k_search_is_never_widened() -> None:
    api = FakeAmuleApi(progress={"state": "running"})
    client = await _connected(api)

    await client.search("keroro", "ed2k", 120)
    await client.close()

    assert _paths(api, "/api/v1/search/42/more") == []


@pytest.mark.asyncio
async def test_a_failed_widening_leaves_the_search_standing() -> None:
    """A reask on a search already finished answers 400 bad_request, an unreachable daemon."""
    api = FakeAmuleApi(progress={"state": "running"}, results=[_result()])
    api.overrides[("POST", "/api/v1/search/42/more")] = lambda _: error(400, "bad_request")
    client = await _connected(api)

    observations = await client.search("keroro", "kad", 120)
    await client.close()

    assert len(observations) == 1
    assert len(_paths(api, "/api/v1/search/42/more")) == 1


@pytest.mark.asyncio
async def test_a_search_the_daemon_refuses_fails_its_channel() -> None:
    api = FakeAmuleApi()
    api.overrides[("POST", "/api/v1/search")] = lambda _: error(
        400, "amuled_rejected", "not connected to any server"
    )
    client = await _connected(api)

    with pytest.raises(SearchFailedError):
        await client.search("keroro", "ed2k", 120)


@pytest.mark.asyncio
async def test_a_search_reply_without_an_id_fails_its_channel() -> None:
    api = FakeAmuleApi()
    api.overrides[("POST", "/api/v1/search")] = lambda _: httpx.Response(202, json={"query": "k"})
    client = await _connected(api)

    with pytest.raises(SearchFailedError):
        await client.search("keroro", "ed2k", 120)


# --- status and preferences ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_network_status_maps_the_daemon_state() -> None:
    api = FakeAmuleApi(
        status={
            "ed2k": {"state": "connected", "high_id": True, "user_id": 42},
            "kad": {"state": "connected", "firewalled_tcp": False},
        }
    )
    client = await _connected(api)

    status = await client.network_status()
    await client.close()

    assert status.ed2k_id == 42
    assert status.ed2k_high is True
    assert status.kad_status is KadStatus.CONNECTED


@pytest.mark.asyncio
async def test_status_reads_the_channels_and_the_daemon_version() -> None:
    api = FakeAmuleApi(
        status={
            "ec_connected": True,
            "ed2k": {"state": "connected", "high_id": False},
            "kad": {"state": "connecting"},
        },
        version={"daemon_version": "GIT rev. 3.0.1-773-g500293ba3"},
    )
    client = await _connected(api)

    status = await client.status()
    await client.close()

    assert status.version == "GIT rev. 3.0.1-773-g500293ba3"
    assert status.channels == (
        ChannelStatus(channel="ed2k", on_network=True, connectable=False),
        ChannelStatus(channel="kad", on_network=False, connectable=None),
    )


@pytest.mark.parametrize("ec_connected", [{"ec_connected": False}, {}])
@pytest.mark.asyncio
async def test_status_without_amuled_behind_amuleapi_is_unreachable(
    ec_connected: dict[str, bool],
) -> None:
    # amuleapi answers from its cache with the last states: a dead amuled reads "connected".
    stale = {
        "ed2k": {"state": "connected", "high_id": True},
        "kad": {"state": "connected", "firewalled_tcp": False},
    }
    client = await _connected(FakeAmuleApi(status={**ec_connected, **stale}))

    with pytest.raises(ClientUnreachableError):
        await client.status()
    await client.close()


@pytest.mark.asyncio
async def test_get_listen_port_reads_the_connection_preferences() -> None:
    client = await _connected(FakeAmuleApi())

    assert await client.get_listen_port() == 4662
    await client.close()


@pytest.mark.asyncio
async def test_preferences_without_a_tcp_port_are_unusable() -> None:
    api = FakeAmuleApi(preferences={"connection": {}})
    client = await _connected(api)

    with pytest.raises(ApiUnreachableError):
        await client.get_listen_port()


@pytest.mark.asyncio
async def test_set_listen_port_moves_tcp_and_udp_together() -> None:
    api = FakeAmuleApi()
    client = await _connected(api)

    await client.set_listen_port(51820)
    await client.close()

    assert api.patched == [{"connection": {"tcp_port": 51820, "udp_port": 51820}}]


# --- downloads -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_link_the_daemon_refuses_is_reported() -> None:
    """The bulk envelope answers 2xx even when the one item inside it failed."""
    api = FakeAmuleApi()
    api.overrides[("POST", "/api/v1/downloads")] = lambda _: httpx.Response(
        207,
        json={
            "results": [
                {
                    "id": "ed2k://x",
                    "ok": False,
                    "error": {"code": "amuled_rejected", "message": "malformed"},
                }
            ]
        },
    )
    client = await _connected(api)

    with pytest.raises(ApiRejectedError, match="malformed"):
        await client.start(DownloadRequest(file=_KEY, filename="x", size_bytes=1))


@pytest.mark.asyncio
async def test_a_bulk_envelope_without_an_outcome_is_reported() -> None:
    api = FakeAmuleApi()
    api.overrides[("POST", "/api/v1/downloads")] = lambda _: httpx.Response(202, json={})
    client = await _connected(api)

    with pytest.raises(ApiRejectedError):
        await client.start(DownloadRequest(file=_KEY, filename="x", size_bytes=1))


@pytest.mark.asyncio
async def test_downloads_drops_an_entry_with_no_usable_hash() -> None:
    api = FakeAmuleApi(downloads=[{"size_bytes": 10}, {"hash": _HASH}])
    client = await _connected(api)

    downloads = await client.downloads()
    await client.close()

    assert [download.file for download in downloads] == [_KEY]


@pytest.mark.asyncio
async def test_downloads_pages_the_shared_files_past_the_first_hundred() -> None:
    """§7.5: the newest shared file is the one we wait for, and it is the one that falls out."""
    rows = [{"hash": f"{index:032x}", "name": f"{index}.avi"} for index in range(501)]
    api = FakeAmuleApi(shared=rows)
    client = await _connected(api)

    shared = await client.downloads()
    await client.close()

    assert len(shared) == 501
    assert shared[-1].file.native_id == f"{500:032x}"
    # Keyset paging, never offset: a row removed mid-sweep must not shift the window (§7.5).
    sweep = _paths(api, "/api/v1/shared")
    assert all(url.params.get("offset") is None for url in sweep)
    assert sweep[-1].params.get("after") == f"{499:032x}"


@pytest.mark.asyncio
async def test_a_page_whose_last_row_has_no_anchor_stops_the_sweep() -> None:
    rows: list[dict[str, object]] = [{"hash": f"{index:032x}"} for index in range(500)]
    rows[-1] = {"name": "anchorless"}
    api = FakeAmuleApi(shared=rows)
    client = await _connected(api)

    shared = await client.downloads()
    await client.close()

    assert len(shared) == 499


@pytest.mark.asyncio
async def test_a_list_envelope_that_is_not_a_list_stops_the_sweep() -> None:
    api = FakeAmuleApi()
    api.overrides[("GET", "/api/v1/shared")] = lambda _: httpx.Response(200, json={"shared": 7})
    client = await _connected(api)

    assert await client.downloads() == ()
    await client.close()


@pytest.mark.asyncio
async def test_start_queues_the_files_ed2k_link() -> None:
    api = FakeAmuleApi()
    client: DownloadClient = await _connected(api)

    await client.start(DownloadRequest(file=_KEY, filename="Keroro 095.avi", size_bytes=10))
    await client.close()

    assert api.added_links == [f"ed2k://|file|Keroro%20095.avi|10|{_HASH}|/"]


@pytest.mark.asyncio
async def test_a_start_the_daemon_refuses_rejects_the_download() -> None:
    api = FakeAmuleApi()
    api.overrides[("POST", "/api/v1/downloads")] = lambda _: httpx.Response(
        207, json={"results": [{"id": "ed2k://x", "ok": False}]}
    )
    client = await _connected(api)

    with pytest.raises(DownloadRejectedError):
        await client.start(DownloadRequest(file=_KEY, filename="Keroro.095.avi", size_bytes=10))


def _downloading(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "hash": _HASH,
        "size_bytes": 10,
        "completed_bytes": 4,
        "status": "downloading",
        "sources": {"total": 1, "transferring": 1},
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_downloads_reads_the_whole_queue_before_the_shared_files() -> None:
    """Queue first: a file cleared between the two reads is then in /shared, not in neither."""
    api = FakeAmuleApi(downloads=[_downloading()])
    client = await _connected(api)

    downloads = await client.downloads()
    await client.close()

    lists = ("/api/v1/downloads", "/api/v1/shared")
    assert [request.url.path for request in api.requests if request.url.path in lists] == [*lists]
    assert _paths(api, "/api/v1/downloads")[0].params.get("status") == "all"
    assert downloads == (
        DownloadStatus(
            file=_KEY,
            bytes_done=4,
            bytes_total=10,
            completed=False,
            waiting_reason=None,
            failure_reason=None,
        ),
    )


@pytest.mark.asyncio
async def test_a_shared_file_the_queue_no_longer_lists_is_completed() -> None:
    cleared = FileKey(Network.ED2K, f"{1:032x}")
    api = FakeAmuleApi(shared=[{"hash": cleared.native_id, "size_bytes": 10}])
    client = await _connected(api)

    downloads = await client.downloads()
    await client.close()

    assert [(download.file, download.completed) for download in downloads] == [(cleared, True)]


@pytest.mark.asyncio
async def test_a_full_partfile_still_completing_is_not_completed_though_shared() -> None:
    """amuled shares partfiles too: neither the bytes nor /shared say it is verified and moved."""
    api = FakeAmuleApi(
        downloads=[_downloading(completed_bytes=10, status="completing")],
        shared=[{"hash": _HASH, "size_bytes": 10}],
    )
    client = await _connected(api)

    downloads = await client.downloads()
    await client.close()

    assert [(download.file, download.completed) for download in downloads] == [(_KEY, False)]


@pytest.mark.asyncio
async def test_downloads_drops_the_rows_without_a_usable_hash() -> None:
    api = FakeAmuleApi(downloads=[{"status": "completed"}], shared=[{"size_bytes": 10}])
    client = await _connected(api)

    assert await client.downloads() == ()
    await client.close()
