import pytest

from amule_bump import BumpError
from amule_bump.release import is_newer, latest_release, peeled_commit
from fakes import FakeRepo

TAG_OBJECT = "6ccbfdf785390c25c8b01652684f5b06fb18e034"
COMMIT = "909d304d993ee07df6c6f6acf501a6d791d53666"


def test_latest_release_returns_the_tag_name() -> None:
    repo = FakeRepo(json={"releases/latest": {"tag_name": "3.1.0"}})
    assert latest_release(repo) == "3.1.0"


@pytest.mark.parametrize("tag", ["v3.1.0", "3.1", "3.1.0-rc1", "3.1.0\n", "../3.1.0"])
def test_latest_release_rejects_a_tag_that_is_not_a_plain_version(tag: str) -> None:
    repo = FakeRepo(json={"releases/latest": {"tag_name": tag}})
    with pytest.raises(BumpError, match="unexpected upstream release tag"):
        latest_release(repo)


@pytest.mark.parametrize(
    ("candidate", "pinned", "expected"),
    [
        ("3.1.0", "3.0.1", True),
        ("3.10.0", "3.9.0", True),
        ("3.1.0", "3.1.0", False),
        ("3.0.1", "3.1.0", False),
    ],
)
def test_is_newer_compares_versions_numerically(
    candidate: str, pinned: str, expected: bool
) -> None:
    assert is_newer(candidate, pinned) is expected


def test_is_newer_rejects_a_pinned_value_that_is_not_a_plain_version() -> None:
    with pytest.raises(BumpError, match="not a plain X.Y.Z version: 'main'"):
        is_newer("3.1.0", "main")


def test_peeled_commit_follows_an_annotated_tag_to_its_commit() -> None:
    repo = FakeRepo(
        json={
            "git/ref/tags/3.1.0": {"object": {"type": "tag", "sha": TAG_OBJECT}},
            f"git/tags/{TAG_OBJECT}": {"object": {"type": "commit", "sha": COMMIT}},
        }
    )
    assert peeled_commit(repo, "3.1.0") == COMMIT


def test_peeled_commit_takes_a_lightweight_tag_as_is() -> None:
    repo = FakeRepo(json={"git/ref/tags/3.1.0": {"object": {"type": "commit", "sha": COMMIT}}})
    assert peeled_commit(repo, "3.1.0") == COMMIT


def test_peeled_commit_fails_on_a_tag_that_does_not_name_a_commit() -> None:
    repo = FakeRepo(json={"git/ref/tags/3.1.0": {"object": {"type": "tree", "sha": COMMIT}}})
    with pytest.raises(BumpError, match="tag 3.1.0 points at a tree, not a commit"):
        peeled_commit(repo, "3.1.0")
