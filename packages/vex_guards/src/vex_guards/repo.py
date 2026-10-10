"""Repo-relative paths the guard checks read: source trees and VEX files.

Everything is derived from the repo root (four parents up from this module), so
the checks stay correct no matter the working directory the gate runs from.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]


def repo_root() -> Path:
    return _ROOT


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_ROOT))
    except ValueError:
        return str(path)


_TOOLING_PACKAGES = {"vex_guards", "amule_bump"}


def source_dirs() -> list[Path]:
    packages = (_ROOT / "packages").glob("*/src")
    return sorted(d for d in packages if d.parent.name not in _TOOLING_PACKAGES)


def vex_files() -> dict[str, Path]:
    return {
        image: _ROOT / "security" / f"{image}.vex.openvex.json" for image in ("crawler", "amule")
    }
