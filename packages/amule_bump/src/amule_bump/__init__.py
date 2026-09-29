"""Bump the aMule pin in packages/crawler/Dockerfile to the latest upstream release."""


class BumpError(Exception):
    """A precondition the bump cannot work around; main() prints it and exits 1."""
