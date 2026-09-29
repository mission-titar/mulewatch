from pathlib import Path

from vex_guards import repo


def test_repo_root_contains_the_packages_dir() -> None:
    assert (repo.repo_root() / "packages").is_dir()


def test_display_path_renders_an_in_repo_path_relative() -> None:
    # The in-repo branch: a path under the repo root folds to its repo-relative form.
    assert repo.display_path(repo.repo_root() / "packages") == "packages"


def test_display_path_renders_an_out_of_repo_path_verbatim() -> None:
    # The ValueError fallback: a path outside the repo is echoed as-is.
    assert repo.display_path(Path("/tmp/x")) == "/tmp/x"


def test_source_dirs_are_the_shipped_packages_only() -> None:
    names = {p.parent.name for p in repo.source_dirs()}
    assert names == {"crawler", "matching"}


def test_vex_files_point_at_security_dir() -> None:
    files = repo.vex_files()
    assert set(files) == {"crawler"}
    assert files["crawler"].name == "crawler.vex.openvex.json"
    assert files["crawler"].is_file()
