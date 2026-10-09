from mulewatch.adapters.mule_api.errors import (
    ApiAuthError,
    ApiRejectedError,
    ApiUnreachableError,
)
from mulewatch.ports.client_errors import (
    ClientError,
    ClientUnreachableError,
    DownloadRejectedError,
    SearchFailedError,
)


def test_unreachable_search_failed_and_download_rejected_are_client_errors() -> None:
    assert issubclass(ClientUnreachableError, ClientError)
    assert issubclass(SearchFailedError, ClientError)
    assert issubclass(DownloadRejectedError, ClientError)


def test_search_failed_and_download_rejected_are_distinct() -> None:
    assert not issubclass(SearchFailedError, DownloadRejectedError)
    assert not issubclass(DownloadRejectedError, SearchFailedError)


def test_transport_failures_are_unreachable() -> None:
    assert issubclass(ApiUnreachableError, ClientUnreachableError)


def test_a_refused_operation_fails_the_search_or_the_download_not_the_client() -> None:
    # amuleapi answers a refused search and a refused link with the same code.
    assert issubclass(ApiRejectedError, SearchFailedError)
    assert issubclass(ApiRejectedError, DownloadRejectedError)
    assert not issubclass(ApiRejectedError, ClientUnreachableError)


def test_auth_error_is_not_a_loop_error() -> None:
    # An auth failure is a config problem (fail-fast at startup), not a loop case.
    assert issubclass(ApiAuthError, ClientError)
    assert not issubclass(ApiAuthError, ClientUnreachableError)
    assert not issubclass(ApiAuthError, SearchFailedError)
    assert not issubclass(ApiAuthError, DownloadRejectedError)
