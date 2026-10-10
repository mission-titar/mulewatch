"""Bump aMule's Dockerfile pin to the latest upstream release and write the PR body.

The workflow opens the PR afterwards, from the `version` and `body-path` step outputs.
"""

import argparse
import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

from amule_bump import BumpError
from amule_bump.body import build_body
from amule_bump.dockerfile import Pin, read_pin, rewrite_pin
from amule_bump.github import UPSTREAM, GitHubRepo, Repo
from amule_bump.release import is_newer, latest_release, peeled_commit


def main(
    argv: list[str] | None = None, repo: Repo | None = None, env: Mapping[str, str] = os.environ
) -> int:
    args = _parse_args(argv)
    repo = repo or GitHubRepo(UPSTREAM, env.get("GH_TOKEN"))
    try:
        return _bump(args, repo, env)
    except (BumpError, OSError) as error:  # OSError covers urllib errors and a missing file
        print(f"error: {error}", file=sys.stderr)
        return 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m amule_bump", description=__doc__)
    parser.add_argument("--dockerfile", type=Path, default=Path("packages/amule/Dockerfile"))
    parser.add_argument(
        "--body-file", type=Path, default=Path(tempfile.gettempdir()) / "amule-bump-pr-body.md"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the new pin and the PR body, write nothing"
    )
    return parser.parse_args(argv)


def _bump(args: argparse.Namespace, repo: Repo, env: Mapping[str, str]) -> int:
    dockerfile = args.dockerfile.read_text()
    old = read_pin(dockerfile)
    latest = latest_release(repo)
    if latest == old.version:
        print(f"aMule {old.version} is the latest release: nothing to do")
        return 0
    if not is_newer(latest, old.version):
        print(
            f"latest upstream release {latest} is older than the pinned {old.version}:"
            " nothing to do"
        )
        return 0

    new = Pin(latest, peeled_commit(repo, latest))
    body = build_body(repo, old, new)
    if args.dry_run:
        print(new.arg_lines())
        print(f"--- PR body ({len(body)} characters) ---")
        print(body, end="")
        return 0

    args.dockerfile.write_text(rewrite_pin(dockerfile, new))
    args.body_file.write_text(body)
    _announce(env.get("GITHUB_OUTPUT"), new.version, args.body_file)
    return 0


def _announce(github_output: str | None, version: str, body_file: Path) -> None:
    """Hand the new version and the body's path to the workflow's next step, when in Actions."""
    if github_output:
        with open(github_output, "a") as output:
            output.write(f"version={version}\nbody-path={body_file}\n")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
