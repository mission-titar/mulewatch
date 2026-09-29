import json
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen

UPSTREAM = "amule-org/amule"


class Repo(Protocol):
    """Read access to one GitHub repository: its REST API and its files at a ref."""

    def json(self, path: str) -> Any: ...

    def file(self, ref: str, path: str) -> str: ...


class GitHubRepo:
    def __init__(self, name: str, token: str | None) -> None:
        self._name = name
        self._token = token

    def json(self, path: str) -> Any:
        return json.loads(self._get(path, "application/vnd.github+json"))

    def file(self, ref: str, path: str) -> str:
        try:
            content = self._get(f"contents/{path}?ref={ref}", "application/vnd.github.raw+json")
        except HTTPError as error:
            if error.code == 404:  # absent at that ref: reads as empty, so a diff shows it added
                return ""
            raise
        return content.decode()

    def _get(self, path: str, accept: str) -> bytes:
        headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = Request(f"https://api.github.com/repos/{self._name}/{path}", headers=headers)
        with urlopen(request, timeout=30) as response:
            content: bytes = response.read()
        return content
