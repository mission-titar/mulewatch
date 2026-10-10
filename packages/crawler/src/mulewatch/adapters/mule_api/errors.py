"""Adapter errors, inheriting the port contract so the application never imports this adapter.

A refused operation fails the search or the download, anything else marks the daemon
unreachable, and a refused login stays outside both: it is a config problem, the crawler fails
fast on it.
"""

import json

import httpx

from mulewatch.ports.client_errors import (
    ClientAuthError,
    ClientError,
    ClientUnreachableError,
    DownloadRejectedError,
    SearchFailedError,
)

# Error codes that report a failed OPERATION rather than a failed daemon: the request is dead,
# the transport is not. `not_found` is the search evicted from amuled's 20-entry ring (§7.2).
_OPERATION_CODES = frozenset({"amuled_rejected", "not_found"})


class ApiError(ClientError):
    """Base of all amuleapi adapter errors (under the port contract)."""


class ApiUnreachableError(ApiError, ClientUnreachableError):
    """Transport dead, amuleapi down, or its EC link to amuled down -> instance down."""


class ApiAuthError(ApiError, ClientAuthError):
    """Login refused (wrong admin password) - not a loop case, a config one."""


class ApiRejectedError(ApiError, SearchFailedError, DownloadRejectedError):
    """The daemon refused the operation and said so cleanly; carries its message."""


class ApiKadExhaustedError(ApiRejectedError):
    """``409 kad_more_exhausted``: Kad will not widen this search again."""


def error_from_response(response: httpx.Response) -> ApiError:
    """A non-2xx response -> the adapter error the port contract calls for."""
    code, message = _envelope(response)
    detail = (
        f"{response.request.method} {response.request.url.path}: "
        f"{response.status_code} {code}: {message}"
    )
    if code == "rate_limited":
        # The adapter never waits out a refusal (the caller owns the backoff), so honouring
        # Retry-After means surfacing it rather than acting on it.
        detail += f" (retry after {response.headers.get('Retry-After', 'an unstated delay')}s)"
    if code == "kad_more_exhausted":
        return ApiKadExhaustedError(detail)
    if code in _OPERATION_CODES:
        return ApiRejectedError(detail)
    return ApiUnreachableError(detail)


def _envelope(response: httpx.Response) -> tuple[str, str]:
    """``{"error": {code, message}}`` -> the pair, or placeholders if the body is not one."""
    try:
        payload = json.loads(response.content)
    except ValueError:  # JSONDecodeError is one
        return "", response.reason_phrase
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return "", response.reason_phrase
    code = error.get("code")
    message = error.get("message")
    return (
        code if isinstance(code, str) else "",
        message if isinstance(message, str) else response.reason_phrase,
    )
