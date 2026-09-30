import difflib
import re
import textwrap

from amule_bump.dockerfile import Pin
from amule_bump.github import UPSTREAM, Repo

GITHUB_BODY_LIMIT = 65536
CHANGELOG_LIMIT = 20_000
DIFF_LIMIT = 18_000
WATCHED_FILES = ("cmake/options.cmake", "docs/api/REFERENCE.md")
TRUNCATED = "[truncated, see the compare link above]"

CHECKLIST = """## Checklist

- [ ] Read the changelog: anything that changes the daemon's defaults or the network behaviour?
- [ ] `options.cmake` diff: a new switch to set explicitly in the Dockerfile's CMake options?
- [ ] `REFERENCE.md` diff: does the `mule_api` adapter need a change (with its tests first)?
- [ ] `amule_config`: does any setting we override (or rely on the default of) change default?
- [ ] CI green on amd64 and arm64.
- [ ] Merge, then tag a release (`vX.Y.Z - aMule {version}`): the image only changes on a tag.
"""


def build_body(repo: Repo, old: Pin, new: Pin) -> str:
    upstream = f"https://github.com/{UPSTREAM}"
    parts = [
        f"Bumps aMule from {old.version} to [{new.version}]({upstream}/releases/tag/{new.version})"
        f" (commit `{new.commit}`). Full upstream diff:"
        f" {upstream}/compare/{old.version}...{new.version}",
        "## Changelog",
        _changelog_part(repo, new),
    ]
    for path in WATCHED_FILES:
        parts += [f"## `{path}` ({old.version} to {new.version})", _diff_part(repo, old, new, path)]
    parts.append(CHECKLIST.format(version=new.version))
    return "\n\n".join(parts)


def changelog_section(changelog: str, version: str) -> str:
    """From the `## Version <version> ` heading down to the next `## Version ` one."""
    kept, inside = [], False
    for line in changelog.splitlines(keepends=True):
        if line.startswith("## Version "):
            inside = line.startswith(f"## Version {version} ")
        if inside:
            kept.append(line)
    return "".join(kept).strip()


def tag_diff(repo: Repo, old: Pin, new: Pin, path: str) -> str:
    before = repo.file(old.version, path).splitlines()
    after = repo.file(new.version, path).splitlines()
    labels = (f"{old.version}/{path}", f"{new.version}/{path}")
    diff = difflib.unified_diff(before, after, *labels, lineterm="")
    return "".join(f"{line}\n" for line in diff)


def wrap(text: str, width: int = 100) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        lines += textwrap.wrap(line, width, break_long_words=False, break_on_hyphens=False) or [""]
    return "\n".join(lines) + "\n"


def clip(text: str, limit: int) -> str:
    """Whole lines up to `limit` characters: GitHub rejects a PR body over 65536."""
    if len(text) <= limit:
        return text
    kept = text[:limit].rpartition("\n")[0]
    return f"{kept}\n{TRUNCATED}\n"


def fenced(text: str, language: str) -> str:
    # Upstream "#123" and "@name" would otherwise link to our issues and ping our users.
    longest_backtick_run = max((len(run) for run in re.findall("`+", text)), default=0)
    fence = "`" * max(4, longest_backtick_run + 1)
    return f"{fence}{language}\n{text.removesuffix('\n')}\n{fence}"


def _changelog_part(repo: Repo, new: Pin) -> str:
    section = changelog_section(repo.file(new.version, "docs/CHANGELOG.md"), new.version)
    if not section:
        return f"No `## Version {new.version}` section in upstream's docs/CHANGELOG.md."
    return fenced(clip(wrap(section), CHANGELOG_LIMIT), "text")


def _diff_part(repo: Repo, old: Pin, new: Pin, path: str) -> str:
    diff = tag_diff(repo, old, new, path)
    if not diff:
        return "No change between the two tags."
    return fenced(clip(diff, DIFF_LIMIT), "diff")
