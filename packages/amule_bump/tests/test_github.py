import io
from email.message import Message
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from amule_bump import github
from amule_bump.github import GitHubRepo


class FakeUrlopen:
    """Stands in for urlopen: records the request, then answers with a body or an HTTP error."""

    def __init__(self, body: bytes = b"", status: int = 200) -> None:
        self.body = body
        self.status = status
        self.request: Request | None = None

    def __call__(self, request: Request, timeout: float) -> io.BytesIO:
        self.request = request
        if self.status != 200:
            raise HTTPError(request.full_url, self.status, "error", Message(), None)
        return io.BytesIO(self.body)


@pytest.fixture
def urlopen(monkeypatch: pytest.MonkeyPatch) -> FakeUrlopen:
    fake = FakeUrlopen()
    monkeypatch.setattr(github, "urlopen", fake)
    return fake


def test_json_gets_the_repo_api_path_with_the_token(urlopen: FakeUrlopen) -> None:
    urlopen.body = b'{"tag_name": "3.1.0"}'
    assert GitHubRepo("amule-org/amule", "secret").json("releases/latest") == {"tag_name": "3.1.0"}
    assert urlopen.request is not None
    assert (
        urlopen.request.full_url == "https://api.github.com/repos/amule-org/amule/releases/latest"
    )
    assert urlopen.request.get_header("Authorization") == "Bearer secret"


def test_requests_go_anonymous_without_a_token(urlopen: FakeUrlopen) -> None:
    urlopen.body = b"{}"
    GitHubRepo("amule-org/amule", None).json("releases/latest")
    assert urlopen.request is not None
    assert not urlopen.request.has_header("Authorization")


def test_file_gets_the_raw_content_at_a_ref(urlopen: FakeUrlopen) -> None:
    urlopen.body = "## Version 3.1.0 é\n".encode()
    text = GitHubRepo("amule-org/amule", None).file("3.1.0", "docs/CHANGELOG.md")
    assert text == "## Version 3.1.0 é\n"
    assert urlopen.request is not None
    assert urlopen.request.full_url == (
        "https://api.github.com/repos/amule-org/amule/contents/docs/CHANGELOG.md?ref=3.1.0"
    )
    assert urlopen.request.get_header("Accept") == "application/vnd.github.raw+json"


def test_file_absent_at_the_ref_reads_as_empty(urlopen: FakeUrlopen) -> None:
    urlopen.status = 404
    assert GitHubRepo("amule-org/amule", None).file("3.0.1", "docs/api/REFERENCE.md") == ""


def test_file_lets_any_other_http_error_through(urlopen: FakeUrlopen) -> None:
    urlopen.status = 403
    with pytest.raises(HTTPError):
        GitHubRepo("amule-org/amule", None).file("3.1.0", "docs/CHANGELOG.md")
