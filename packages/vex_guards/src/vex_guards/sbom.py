import json
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

from packaging.version import Version

from vex_guards.descriptors import ImageGuard, PackageAbsent, PackageMinVersion
from vex_guards.violations import Violation


@dataclass(frozen=True)
class DebPackage:
    name: str
    version: str


def load_dpkg_packages(path: Path) -> list[DebPackage]:
    """The dpkg packages of a Syft JSON SBOM, in document order.

    Syft types those "deb". The rest (aMule, declared by hand; our Python wheels) does
    not come from dpkg, so a dpkg guard has nothing to say about it.
    """
    doc = json.loads(path.read_text())
    return [
        DebPackage(name=a["name"], version=a["version"])
        for a in doc["artifacts"]
        if a["type"] == "deb"
    ]


def _upstream(version: str) -> str:
    """The upstream part of a Debian ``[epoch:]upstream[-revision]`` version.

    Epoch and revision are packaging, not code, so a version claim ignores them.

    ponytail: compared as a Python version; a "~rc1" upstream would raise. Swap in a
    real Debian comparator the day one shows up.
    """
    _, _, without_epoch = version.rpartition(":")
    upstream, _, _ = without_epoch.rpartition("-")
    return upstream or without_epoch


def evaluate_image_guards(
    guards: dict[str, ImageGuard], packages: list[DebPackage]
) -> list[Violation]:
    by_name: dict[str, list[DebPackage]] = {}
    for pkg in packages:
        by_name.setdefault(pkg.name, []).append(pkg)
    violations: list[Violation] = []
    for cve, guard in guards.items():
        match guard:
            case PackageAbsent(package):
                if package in by_name:
                    violations.append(
                        Violation(cve, f"dpkg package {package!r} is present", "security")
                    )
            case PackageMinVersion(package, minimum):
                for pkg in by_name.get(package, []):
                    if Version(_upstream(pkg.version)) < Version(minimum):
                        violations.append(
                            Violation(
                                cve,
                                f"{package} {pkg.version} is below {minimum}",
                                "security",
                            )
                        )
            case _:  # pragma: no cover
                assert_never(guard)
    return violations
