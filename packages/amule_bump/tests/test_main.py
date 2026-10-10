from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest

from amule_bump import __main__ as cli
from amule_bump.__main__ import main
from fakes import FakeRepo

OLD_COMMIT = "02db0d7faecfc377694ff6242bc23346185990ed"
NEW_COMMIT = "909d304d993ee07df6c6f6acf501a6d791d53666"
DOCKERFILE = f"FROM debian\nARG AMULE_VERSION=3.0.1\nARG AMULE_COMMIT={OLD_COMMIT}\n"


def upstream(latest: str) -> FakeRepo:
    return FakeRepo(
        json={
            "releases/latest": {"tag_name": latest},
            f"git/ref/tags/{latest}": {"object": {"type": "commit", "sha": NEW_COMMIT}},
        },
        files={("3.1.0", "docs/CHANGELOG.md"): "## Version 3.1.0 - REST\n- amuleapi\n"},
    )


@pytest.fixture
def dockerfile(tmp_path: Path) -> Path:
    path = tmp_path / "Dockerfile"
    path.write_text(DOCKERFILE)
    return path


def run(dockerfile: Path, repo: FakeRepo, *flags: str, env: dict[str, str] | None = None) -> int:
    body = dockerfile.parent / "body.md"
    argv = ["--dockerfile", str(dockerfile), "--body-file", str(body), *flags]
    return main(argv, repo, env or {})


def test_same_version_is_nothing_to_do(
    dockerfile: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pinned = dockerfile.read_text().replace("3.0.1", "3.1.0")
    dockerfile.write_text(pinned)
    output = dockerfile.parent / "output"
    assert run(dockerfile, upstream("3.1.0"), env={"GITHUB_OUTPUT": str(output)}) == 0
    assert capsys.readouterr().out == "aMule 3.1.0 is the latest release: nothing to do\n"
    assert dockerfile.read_text() == pinned
    assert not (dockerfile.parent / "body.md").exists()
    assert not output.exists()


def test_an_older_upstream_release_is_nothing_to_do(
    dockerfile: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(dockerfile, upstream("3.0.0")) == 0
    assert capsys.readouterr().out == (
        "latest upstream release 3.0.0 is older than the pinned 3.0.1: nothing to do\n"
    )
    assert dockerfile.read_text() == DOCKERFILE


def test_a_new_release_rewrites_the_pin_and_writes_the_body(dockerfile: Path) -> None:
    assert run(dockerfile, upstream("3.1.0")) == 0
    assert dockerfile.read_text() == (
        f"FROM debian\nARG AMULE_VERSION=3.1.0\nARG AMULE_COMMIT={NEW_COMMIT}\n"
    )
    body = (dockerfile.parent / "body.md").read_text()
    assert body.startswith("Bumps aMule from 3.0.1 to [3.1.0]")
    assert "- amuleapi" in body


def test_a_new_release_is_announced_to_the_workflow(dockerfile: Path) -> None:
    output = dockerfile.parent / "output"
    output.write_text("earlier=kept\n")
    run(dockerfile, upstream("3.1.0"), env={"GITHUB_OUTPUT": str(output)})
    body = dockerfile.parent / "body.md"
    assert output.read_text() == f"earlier=kept\nversion=3.1.0\nbody-path={body}\n"


def test_dry_run_prints_the_new_pin_and_body_and_writes_nothing(
    dockerfile: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = dockerfile.parent / "output"
    assert run(dockerfile, upstream("3.1.0"), "--dry-run", env={"GITHUB_OUTPUT": str(output)}) == 0
    printed = capsys.readouterr().out
    assert printed.startswith(
        f"ARG AMULE_VERSION=3.1.0\nARG AMULE_COMMIT={NEW_COMMIT}\n--- PR body ("
    )
    assert "Bumps aMule from 3.0.1 to [3.1.0]" in printed
    assert dockerfile.read_text() == DOCKERFILE
    assert not (dockerfile.parent / "body.md").exists()
    assert not output.exists()


def test_a_bad_pin_fails_with_a_clear_message(
    dockerfile: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dockerfile.write_text("FROM debian\n")
    assert run(dockerfile, upstream("3.1.0")) == 1
    assert capsys.readouterr().err == ("error: expected one 'ARG AMULE_VERSION=' line, found 0\n")


def test_a_missing_dockerfile_fails_with_a_clear_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path / "absent", upstream("3.1.0")) == 1
    assert capsys.readouterr().err.startswith("error: [Errno 2] No such file or directory")


def test_an_http_failure_fails_with_a_clear_message(
    dockerfile: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class DownRepo(FakeRepo):
        def json(self, path: str) -> object:
            raise HTTPError(path, 503, "Service Unavailable", Message(), None)

    assert run(dockerfile, DownRepo()) == 1
    assert capsys.readouterr().err == "error: HTTP Error 503: Service Unavailable\n"


def test_the_default_repo_is_upstream_authenticated_by_gh_token(
    dockerfile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[tuple[str, str | None]] = []

    def fake_repo(name: str, token: str | None) -> FakeRepo:
        opened.append((name, token))
        return upstream("3.0.1")

    monkeypatch.setattr(cli, "GitHubRepo", fake_repo)
    argv = ["--dockerfile", str(dockerfile)]
    assert main(argv, env={"GH_TOKEN": "secret"}) == 0
    assert opened == [("amule-org/amule", "secret")]


def test_the_default_dockerfile_is_the_amule_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pinned = tmp_path / "packages/amule/Dockerfile"
    pinned.parent.mkdir(parents=True)
    pinned.write_text(DOCKERFILE)
    monkeypatch.chdir(tmp_path)
    assert main(["--body-file", str(tmp_path / "body.md")], upstream("3.1.0"), {}) == 0
    assert "ARG AMULE_VERSION=3.1.0" in pinned.read_text()
