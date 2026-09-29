import json
from pathlib import Path

from vex_guards.descriptors import ImageGuard, PackageAbsent, PackageMinVersion
from vex_guards.sbom import DebPackage, evaluate_image_guards, load_dpkg_packages


def _write_sbom(tmp_path: Path, artifacts: list[dict[str, str]]) -> Path:
    doc = {"artifacts": artifacts}
    path = tmp_path / "sbom.syft.json"
    path.write_text(json.dumps(doc))
    return path


def test_load_dpkg_packages_keeps_only_deb_artifacts(tmp_path: Path) -> None:
    # aMule is compiled by us and declared by hand, so Syft types it "UnknownPackage", not "deb".
    path = _write_sbom(
        tmp_path,
        [
            {"type": "deb", "name": "libcurl4t64", "version": "8.14.1-2"},
            {"type": "deb", "name": "zlib1g", "version": "1:1.3.dfsg+really1.3.1-1"},
            {"type": "UnknownPackage", "name": "amule", "version": "3.1.0"},
            {"type": "python", "name": "packaging", "version": "24.0"},
        ],
    )
    assert load_dpkg_packages(path) == [
        DebPackage("libcurl4t64", "8.14.1-2"),
        DebPackage("zlib1g", "1:1.3.dfsg+really1.3.1-1"),
    ]


def test_package_absent_passes_when_only_sibling_package_present() -> None:
    guards: dict[str, ImageGuard] = {"CVE-2024-0001": PackageAbsent("libcurl4")}
    packages = [DebPackage("libcurl4t64", "8.14.1-2")]
    assert evaluate_image_guards(guards, packages) == []


def test_package_absent_fails_when_package_present() -> None:
    guards: dict[str, ImageGuard] = {"CVE-2024-0001": PackageAbsent("libcurl4")}
    packages = [DebPackage("libcurl4", "8.14.1-2")]
    violations = evaluate_image_guards(guards, packages)
    assert len(violations) == 1
    assert violations[0].cve == "CVE-2024-0001"
    assert "libcurl4" in violations[0].message


def test_package_min_version_passes_at_or_above_minimum() -> None:
    guards: dict[str, ImageGuard] = {"CVE-2016-1405": PackageMinVersion("curl", "8.0")}
    packages = [DebPackage("curl", "8.14.1-2")]
    assert evaluate_image_guards(guards, packages) == []


def test_package_min_version_fails_below_minimum() -> None:
    guards: dict[str, ImageGuard] = {"CVE-2016-1405": PackageMinVersion("curl", "8.0")}
    packages = [DebPackage("curl", "7.88.1-10+deb12u5")]
    violations = evaluate_image_guards(guards, packages)
    assert len(violations) == 1
    assert violations[0].cve == "CVE-2016-1405"
    assert "curl" in violations[0].message


def test_package_min_version_compares_the_upstream_part_of_a_debian_version() -> None:
    # Only the upstream part compares; whole, "1:8.14.1-2" is not a valid version.
    guards: dict[str, ImageGuard] = {"CVE-2016-1405": PackageMinVersion("curl", "8.14")}
    assert evaluate_image_guards(guards, [DebPackage("curl", "1:8.14.1-2")]) == []


def test_package_min_version_handles_a_version_with_no_revision() -> None:
    # A native Debian package carries no "-revision" at all: the whole string is upstream.
    guards: dict[str, ImageGuard] = {"CVE-2016-1405": PackageMinVersion("curl", "8.0")}
    assert evaluate_image_guards(guards, [DebPackage("curl", "8.14.1")]) == []


def test_package_min_version_passes_vacuously_when_package_absent() -> None:
    guards: dict[str, ImageGuard] = {"CVE-2016-1405": PackageMinVersion("curl", "8.0")}
    packages = [DebPackage("libcurl4t64", "8.14.1-2")]
    assert evaluate_image_guards(guards, packages) == []
