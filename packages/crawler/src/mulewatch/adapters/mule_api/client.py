"""Drives ``amuled`` over amuleapi, satisfying ``MuleClient`` and ``DownloadClient``.

``search()`` waits for its own search and paces its starts by the networks' rules. Beyond pacing,
no retry but the single re-login a ``401`` mandates and Kad's "already on search list": the
adapter signals, the caller decides.
"""

import asyncio
import json
import logging
import re
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from catalog_matching.ed2k_link import build_ed2k_link
from mulewatch.adapters.mule_api.errors import (
    ApiAuthError,
    ApiKadExhaustedError,
    ApiRejectedError,
    ApiUnreachableError,
    error_from_response,
)
from mulewatch.adapters.mule_api.mapping import (
    map_client_status,
    map_download_status,
    map_network_status,
    map_search_results,
    map_shared_download,
)
from mulewatch.domain.file_key import Network
from mulewatch.domain.observation import FileObservation
from mulewatch.ports.client_status import ClientStatus
from mulewatch.ports.clock import Clock
from mulewatch.ports.download_client import DownloadRequest, DownloadStatus
from mulewatch.ports.port_sync import NetworkStatus

# Rows asked for per list request. Every list route caps at 100 when `limit` is omitted, which
# would silently truncate the shared-file sweep completion detection depends on (§7.5).
_PAGE_SIZE = 500

# How often search() reads a search's progress: local traffic with our own daemon.
_POLL_INTERVAL_SECONDS = 5.0

_SEARCH_TYPES = {"ed2k": "global", "kad": "kad"}

# Least time between two ed2k starts, and between two Kad starts on one target (spec stage 2, D5):
# rules of the networks, not settings, since a key would invite lowering them until the ban.
_START_SPACING = timedelta(seconds=60)

# Kad's keyword separators (aMule `SearchManager.h:128`).
_KAD_SEPARATORS = re.compile(r'[ ()\[\]{}<>,._\-!?:;\\/"]')

# Kad refuses a target still on its search list: "not yet", someone else holds it.
_KAD_TARGET_HELD = "already on search list"

_LONG_AGO = datetime.min.replace(tzinfo=UTC)

_logger = logging.getLogger("mulewatch.adapters.mule_api.client")


class AmuleApiClient:
    """``skipped_entries_total`` counts discard EVENTS, not unique entries: the readout is
    cumulative, so one unusable entry re-seen every cycle counts every time."""

    channels: tuple[str, ...] = tuple(_SEARCH_TYPES)

    def __init__(
        self,
        host: str,
        port: int,
        password: str,
        *,
        timeout: float = 10.0,
        clock: Clock,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = f"http://{host}:{port}/api/v1"
        self._password = password
        self._timeout = timeout
        self._transport = transport
        self._clock = clock
        self._http: httpx.AsyncClient | None = None
        self._connect_lock = asyncio.Lock()
        # Held from an ed2k start to its end: aMule's core keeps one ed2k search anchor.
        self._ed2k_lock = asyncio.Lock()
        self._ed2k_next_start = _LONG_AGO
        self._kad_next_start: dict[str, datetime] = {}
        self._targetless_logged: set[str] = set()
        self.skipped_entries_total = 0

    async def connect(self) -> None:
        """Opens the session and logs in. IDEMPOTENT, and serialized: callers arriving during a
        login wait for it rather than open a session each."""
        async with self._connect_lock:
            if self._http is not None:
                return
            if not self._password:
                raise ApiAuthError("empty amuleapi password (refused before asking the daemon)")
            http = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout),
                transport=self._transport,
            )
            try:
                await self._login(http)
            except Exception:
                await http.aclose()
                raise
            self._http = http

    async def close(self) -> None:
        """Revokes the token, then closes the session. Bypasses the 401 rule on purpose: a
        rejected logout means the session is already gone, so re-logging in to end it is waste."""
        http, self._http = self._http, None
        if http is None:
            return
        with suppress(ApiUnreachableError):
            await _send(http, "POST", "/auth/logout")
        await http.aclose()

    async def search(
        self, keyword: str, channel: str, budget_seconds: float
    ) -> tuple[FileObservation, ...]:
        """Waits for the network's next allowed start, then polls until amuled reports the search
        finished or ``budget_seconds`` after its start. Never stops nor frees it: amuled does."""
        search_type = _SEARCH_TYPES[channel]
        if search_type == "kad":
            search_id = await self._start_kad(keyword, budget_seconds)
            if search_id is None:
                return ()
            await self._await_end(search_id, budget_seconds, widen=True)
        else:
            async with self._ed2k_lock:
                await self._sleep_until(self._ed2k_next_start)
                self._ed2k_next_start = self._clock.now() + _START_SPACING
                search_id = await self._post_search(keyword, search_type)
                await self._await_end(search_id, budget_seconds, widen=False)
        rows = await self._collect(f"/search/{search_id}/results", "results")
        observations, skipped = map_search_results(rows, keyword)
        self.skipped_entries_total += skipped
        return observations

    async def _start_kad(self, keyword: str, budget_seconds: float) -> int | None:
        """Starts in the target's next free slot, reserved before sleeping; ``None`` without a
        target. A held target is retried each poll, for one budget from the first attempt."""
        target = _kad_target(keyword)
        # A keyword without a target splits on a separator or is under 3 bytes: never a target.
        slot_key = target or keyword
        now = self._clock.now()
        slot = max(now, self._kad_next_start.get(slot_key, now))
        self._kad_next_start[slot_key] = slot + _START_SPACING
        await self._sleep_until(slot)
        if target is None:
            if keyword not in self._targetless_logged:
                self._targetless_logged.add(keyword)
                _logger.info(
                    "no Kad target in %r (no word of 3 bytes): not searched on Kad", keyword
                )
            return None
        give_up_at = self._clock.now() + timedelta(seconds=budget_seconds)
        while True:
            try:
                return await self._post_search(keyword, "kad")
            except ApiRejectedError as refusal:
                remaining = (give_up_at - self._clock.now()).total_seconds()
                if _KAD_TARGET_HELD not in str(refusal) or remaining <= 0:
                    raise
                await self._clock.sleep(min(_POLL_INTERVAL_SECONDS, remaining))

    async def _sleep_until(self, moment: datetime) -> None:
        delay = (moment - self._clock.now()).total_seconds()
        if delay > 0:
            await self._clock.sleep(delay)

    async def _await_end(self, search_id: int, budget_seconds: float, *, widen: bool) -> None:
        """Polls until amuled reports the search finished, or ``budget_seconds`` after now."""
        deadline = self._clock.now() + timedelta(seconds=budget_seconds)
        polls = 0
        while await self._search_state(search_id) != "finished":
            remaining = (deadline - self._clock.now()).total_seconds()
            if remaining <= 0:
                break
            if widen and polls:  # first poll: Kad has queried nobody yet, a reask is wasted
                widen = await self._widen(search_id)
            polls += 1
            await self._clock.sleep(min(_POLL_INTERVAL_SECONDS, remaining))

    async def status(self) -> ClientStatus:
        """Raises unreachable unless amuleapi reaches amuled: otherwise it answers from its cache,
        and a dead amuled would read as connected."""
        status = await self._call("GET", "/status")
        if status.get("ec_connected") is not True:
            raise ApiUnreachableError("GET /status: amuleapi does not reach amuled")
        return map_client_status(status, await self._call("GET", "/version"))

    async def network_status(self) -> NetworkStatus:
        """Network status: the one GET that carries both networks and our own eD2k id."""
        return map_network_status(await self._call("GET", "/status"))

    async def get_listen_port(self) -> int:
        """amuled's current eD2k TCP listen port (port-sync High-ID, design §2.3/§4.2)."""
        payload = await self._call("GET", "/preferences")
        connection = payload.get("connection")
        port = connection.get("tcp_port") if isinstance(connection, dict) else None
        if not isinstance(port, int) or isinstance(port, bool):
            raise ApiUnreachableError("GET /preferences without connection.tcp_port")
        return port

    async def set_listen_port(self, port: int) -> None:
        """Writes the TCP and UDP port preferences together, as EC did. A preference is not a
        rebind: amuled listens on the new port only after a restart."""
        await self._call(
            "PATCH", "/preferences", body={"connection": {"tcp_port": port, "udp_port": port}}
        )

    async def start(self, request: DownloadRequest) -> None:
        """Queues the file by its ed2k link. The route is a bulk one, so a refused link comes back
        per item INSIDE a 2xx: the envelope reports the failure, never the status code."""
        if request.file.network is not Network.ED2K:
            raise ApiRejectedError(f"aMule downloads ed2k files only, not {request.file.network}")
        link = build_ed2k_link(request.filename, request.size_bytes, request.file.native_id)
        payload = await self._call("POST", "/downloads", body={"links": [link]})
        results = payload.get("results")
        outcome = results[0] if isinstance(results, list) and results else None
        if not isinstance(outcome, dict) or outcome.get("ok") is not True:
            raise ApiRejectedError(f"POST /downloads refused the link: {_outcome_reason(outcome)}")

    async def downloads(self) -> tuple[DownloadStatus, ...]:
        """The queue with its completed entries, then the shared files it no longer lists. Queue
        first: a file cleared between the two reads is then shared, not missing from both."""
        rows = await self._collect("/downloads", "downloads", params={"status": "all"})
        queue = [status for row in rows if (status := map_download_status(row)) is not None]
        listed = {status.file for status in queue}
        shared = [
            status
            for row in await self._collect("/shared", "shared")
            if (status := map_shared_download(row)) is not None and status.file not in listed
        ]
        return (*queue, *shared)

    async def _post_search(self, keyword: str, search_type: str) -> int:
        payload = await self._call("POST", "/search", body={"query": keyword, "type": search_type})
        search_id = payload.get("search_id")
        if not isinstance(search_id, int) or isinstance(search_id, bool):
            raise ApiRejectedError("POST /search answered without a search_id")
        return search_id

    async def _search_state(self, search_id: int) -> object:
        """``progress.state``, read with zero rows: the envelope travels with the results."""
        payload = await self._call("GET", f"/search/{search_id}/results", params={"limit": 0})
        progress = payload.get("progress")
        return progress.get("state") if isinstance(progress, dict) else None

    async def _widen(self, search_id: int) -> bool:
        """Asks Kad for more; ``False`` once it refuses or fails, the search standing either way.
        A reask on a finished search answers ``400 bad_request``, mapped to unreachable."""
        try:
            await self._call("POST", f"/search/{search_id}/more")
        except ApiKadExhaustedError:
            return False
        except (ApiRejectedError, ApiUnreachableError) as failure:
            _logger.info("widening Kad search %d failed (%s)", search_id, failure)
            return False
        return True

    # --- transport --------------------------------------------------------------------------

    async def _call(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One request on the connected session, decoded into a JSON object."""
        if self._http is None:
            raise ApiUnreachableError("amuleapi client not connected (call connect() first)")
        return await self._request(self._http, method, path, body=body, params=params)

    async def _request(
        self,
        http: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Sends, renewing the token ONCE on a 401. Never twice: amuleapi locks an IP out for
        300 s after 30 rejected tokens, and only a successful login clears that state."""
        response = await _send(http, method, path, body=body, params=params)
        if response.status_code == httpx.codes.UNAUTHORIZED:
            await self._login(http)
            response = await _send(http, method, path, body=body, params=params)
        if not response.is_success:
            raise error_from_response(response)
        return _decode(response)

    async def _login(self, http: httpx.AsyncClient) -> None:
        """Mints a token and arms the Authorization header (the bearer wins over the cookie)."""
        response = await _send(
            http,
            "POST",
            "/auth/login",
            body={"password": self._password},
            params={"include_token": "true"},
        )
        if response.status_code == httpx.codes.UNAUTHORIZED:
            raise ApiAuthError("amuleapi refused the admin password")
        if not response.is_success:
            raise error_from_response(response)
        token = _decode(response).get("token")
        if not isinstance(token, str) or not token:
            raise ApiAuthError("POST /auth/login answered without a token")
        http.headers["Authorization"] = f"Bearer {token}"

    async def _collect(
        self, path: str, envelope: str, *, params: dict[str, Any] | None = None
    ) -> list[Any]:
        """Sweeps a whole list route by KEYSET paging on ``hash``. Never ``offset``: it is a
        position, so a row deleted below the cursor is skipped and then reported by nothing."""
        rows: list[Any] = []
        page_params = {**(params or {}), "sort": "hash", "limit": _PAGE_SIZE}
        while True:
            payload = await self._call("GET", path, params=page_params)
            page = payload.get(envelope)
            if not isinstance(page, list):  # an under-report would restart what it hides
                raise ApiUnreachableError(f"GET {path} answered without its {envelope!r} list")
            rows.extend(page)
            if len(page) < _PAGE_SIZE:
                return rows
            anchor = page[-1].get("hash") if isinstance(page[-1], dict) else None
            if not isinstance(anchor, str):
                return rows  # no anchor to advance on: stop rather than re-read page one
            page_params = {**page_params, "after": anchor}


async def _send(
    http: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    """One HTTP roundtrip; a transport failure is an unreachable daemon, never a crash."""
    try:
        return await http.request(method, path, json=body, params=params)
    except httpx.HTTPError as failure:
        raise ApiUnreachableError(f"{method} {path}: {failure}") from failure


def _decode(response: httpx.Response) -> dict[str, Any]:
    """A 2xx body → its JSON object. A 204 carries none; anything unreadable is a dead hop."""
    if not response.content:
        return {}
    try:
        payload = json.loads(response.content)
    except ValueError as failure:  # JSONDecodeError is one
        raise ApiUnreachableError(f"{response.request.url.path}: unreadable body") from failure
    return payload if isinstance(payload, dict) else {}


def _kad_target(keyword: str) -> str | None:
    """Kad's key for a keyword search: its first word of 3 UTF-8 bytes or more, lowercased
    (aMule ``SearchManager.cpp:283-292``)."""
    words = _KAD_SEPARATORS.split(keyword)
    return next((word.lower() for word in words if len(word.encode()) >= 3), None)


def _outcome_reason(outcome: dict[str, Any] | None) -> str:
    """The per-item error message of a bulk envelope, or a safe label if it carries none."""
    error = outcome.get("error") if isinstance(outcome, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    return message if isinstance(message, str) else "no per-item outcome reported"
