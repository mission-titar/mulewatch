"""A fake amuleapi served through ``httpx.MockTransport``, so the tests drive the real stack.

Its list routes default ``limit`` to 100 like the real ones, which is what makes a client that
forgets to ask for more fail here instead of on a node with a hundred shared files.
"""

import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import httpx

from mulewatch.ports.clock import Clock
from tests.application.fakes import FakeClock

PASSWORD = "s3cret"
TOKEN = "eyJhbGciOi.fake.token"

# The real server's default page size. Unasked-for, it is what a forgetful client receives.
DEFAULT_LIMIT = 100

_SEARCH_ROUTE = re.compile(r"^/api/v1/search/(\d+)/")


def error(status: int, code: str, message: str = "nope", **headers: str) -> httpx.Response:
    """A response in the surface's error envelope."""
    return httpx.Response(
        status, json={"error": {"code": code, "message": message}}, headers=headers
    )


class FakeAmuleApi:
    """Routes the handful of endpoints the adapter uses; everything else is a 404."""

    def __init__(
        self,
        *,
        password: str = PASSWORD,
        results: list[dict[str, Any]] | None = None,
        downloads: list[dict[str, Any]] | None = None,
        shared: list[dict[str, Any]] | None = None,
        status: dict[str, Any] | None = None,
        version: dict[str, Any] | None = None,
        preferences: dict[str, Any] | None = None,
        progress: dict[str, Any] | None = None,
        search_seconds: dict[str, float] | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.password = password
        self.results = results if results is not None else []
        self.downloads = downloads if downloads is not None else []
        self.shared = shared if shared is not None else []
        self.status = status if status is not None else {"ed2k": {}, "kad": {}}
        self.version = version if version is not None else {"daemon_version": "3.0.1"}
        self.preferences = (
            preferences if preferences is not None else {"connection": {"tcp_port": 4662}}
        )
        self.progress = progress if progress is not None else {"state": "finished", "percent": 100}
        # Per query: a search reports `running` that long after its start, then `finished`.
        self.search_seconds = search_seconds if search_seconds is not None else {}
        self.search_started_at: datetime | None = None
        # Search id -> (start, query). Ids count up from 42, so a lone search is still 42.
        self.searches: dict[int, tuple[datetime, str]] = {}
        self.clock: Clock = clock or FakeClock()
        self.requests: list[httpx.Request] = []
        self.calls: list[tuple[datetime, httpx.Request]] = []
        self.logins = 0
        self.logouts = 0
        self.added_links: list[str] = []
        self.patched: list[dict[str, Any]] = []
        # Per-route overrides: a callable wins over the default behaviour, once per call.
        self.overrides: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.calls.append((self.clock.now(), request))
        route = (request.method, request.url.path)
        override = self.overrides.get(route)
        if override is not None:
            return override(request)
        handler = _ROUTES.get((request.method, _SEARCH_ROUTE.sub("/api/v1/search/{id}/", route[1])))
        if handler is None:
            return error(404, "not_found", f"no route {route}")
        unauthenticated = route[1] in ("/api/v1/auth/login", "/api/v1/health")
        if not unauthenticated and request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return error(401, "unauthorized", "missing or stale token")
        return handler(self, request)

    # --- routes ---------------------------------------------------------------------------

    def _login(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("password") != self.password:
            return error(401, "invalid_credentials", "no role matched")
        self.logins += 1
        payload: dict[str, Any] = {"role": "admin", "expires_at": 0, "session_id": "s"}
        if request.url.params.get("include_token") == "true":
            payload["token"] = TOKEN
        return httpx.Response(200, json=payload)

    def _logout(self, request: httpx.Request) -> httpx.Response:
        self.logouts += 1
        return httpx.Response(204)

    def _start_search(self, request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content).get("query")
        search_id = 42 + len(self.searches)
        self.search_started_at = self.clock.now()
        self.searches[search_id] = (self.search_started_at, query)
        return httpx.Response(202, json={"search_id": search_id, "query": query})

    def _search_results(self, request: httpx.Request) -> httpx.Response:
        search_id = _search_id(request)
        window = _page(request, self.results)
        return httpx.Response(
            200,
            json={"results": window, "search_id": search_id, "progress": self._progress(search_id)},
        )

    def _progress(self, search_id: int) -> dict[str, Any]:
        started_at, query = self.searches[search_id]
        seconds = self.search_seconds.get(query)
        if seconds is None:
            return self.progress
        ends_at = started_at + timedelta(seconds=seconds)
        return {"state": "running" if self.clock.now() < ends_at else "finished"}

    def _more_search(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(202)

    def _get_status(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=self.status)

    def _get_version(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=self.version)

    def _get_preferences(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=self.preferences)

    def _patch_preferences(self, request: httpx.Request) -> httpx.Response:
        self.patched.append(json.loads(request.content))
        return httpx.Response(200, json=self.preferences)

    def _add_links(self, request: httpx.Request) -> httpx.Response:
        links = json.loads(request.content)["links"]
        self.added_links.extend(links)
        return httpx.Response(202, json={"results": [{"id": link, "ok": True} for link in links]})

    def _get_downloads(self, request: httpx.Request) -> httpx.Response:
        window = _page(request, self.downloads)
        return httpx.Response(200, json={"downloads": window, "total": len(self.downloads)})

    def _get_shared(self, request: httpx.Request) -> httpx.Response:
        window = _page(request, self.shared)
        return httpx.Response(200, json={"shared": window, "total": len(self.shared)})


def _search_id(request: httpx.Request) -> int:
    match = _SEARCH_ROUTE.match(request.url.path)
    assert match is not None
    return int(match.group(1))


def _page(request: httpx.Request, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keyset paging on ``hash``, with the surface's own 100-row default for ``limit``."""
    params = request.url.params
    after = params.get("after")
    window = rows
    if after is not None:
        window = [row for row in rows if str(row.get("hash", "")) > after]
    limit = int(params.get("limit", DEFAULT_LIMIT))
    return window[:limit]


_ROUTES: dict[tuple[str, str], Callable[[FakeAmuleApi, httpx.Request], httpx.Response]] = {
    ("POST", "/api/v1/auth/login"): FakeAmuleApi._login,
    ("POST", "/api/v1/auth/logout"): FakeAmuleApi._logout,
    ("POST", "/api/v1/search"): FakeAmuleApi._start_search,
    ("GET", "/api/v1/search/{id}/results"): FakeAmuleApi._search_results,
    ("POST", "/api/v1/search/{id}/more"): FakeAmuleApi._more_search,
    ("GET", "/api/v1/status"): FakeAmuleApi._get_status,
    ("GET", "/api/v1/version"): FakeAmuleApi._get_version,
    ("GET", "/api/v1/preferences"): FakeAmuleApi._get_preferences,
    ("PATCH", "/api/v1/preferences"): FakeAmuleApi._patch_preferences,
    ("POST", "/api/v1/downloads"): FakeAmuleApi._add_links,
    ("GET", "/api/v1/downloads"): FakeAmuleApi._get_downloads,
    ("GET", "/api/v1/shared"): FakeAmuleApi._get_shared,
}
