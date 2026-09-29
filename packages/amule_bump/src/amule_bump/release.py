import re

from amule_bump import BumpError
from amule_bump.github import Repo


def latest_release(repo: Repo) -> str:
    tag = str(repo.json("releases/latest")["tag_name"])
    # The tag ends up in a URL, a branch name and the Dockerfile: accept a plain version only.
    if not _is_plain_version(tag):
        raise BumpError(f"unexpected upstream release tag {tag!r}")
    return tag


def is_newer(candidate: str, pinned: str) -> bool:
    return _version(candidate) > _version(pinned)


def peeled_commit(repo: Repo, tag: str) -> str:
    """The commit the tag names, never the annotated tag object: BuildKit's --checksum wants it."""
    target = repo.json(f"git/ref/tags/{tag}")["object"]
    while target["type"] == "tag":
        target = repo.json(f"git/tags/{target['sha']}")["object"]
    if target["type"] != "commit":
        raise BumpError(f"tag {tag} points at a {target['type']}, not a commit")
    return str(target["sha"])


def _version(text: str) -> tuple[int, ...]:
    if not _is_plain_version(text):
        raise BumpError(f"not a plain X.Y.Z version: {text!r}")
    return tuple(int(part) for part in text.split("."))


def _is_plain_version(text: str) -> bool:
    return re.fullmatch(r"\d+\.\d+\.\d+", text) is not None
