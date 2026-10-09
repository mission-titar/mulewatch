"""The error contract every client adapter raises under, so the application never imports one."""


class ClientError(Exception):
    """Base of the client error contract."""


class ClientUnreachableError(ClientError):
    """The client's API does not answer: the caller degrades and backs off the client."""


class SearchFailedError(ClientError):
    """The client refused a search: the channel backs off."""


class DownloadRejectedError(ClientError):
    """The client refused a download: that download fails."""
