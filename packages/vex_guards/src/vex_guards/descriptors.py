"""Guard descriptors, their two families, and the ``family`` classifier.

A guard is a small frozen dataclass that names one falsifiable premise behind a
VEX ``not_affected`` claim (e.g. "``tarfile`` is never imported"). Guards split
into two families: ``source`` guards assert something about our own code path,
``image`` guards assert something about the packages present in the image (dpkg
packages, or components declared by hand like aMule).
"""

from dataclasses import dataclass
from typing import Literal, TypeGuard, assert_never


@dataclass(frozen=True)
class ModuleNotImported:
    module: str


@dataclass(frozen=True)
class SubprocessDenies:
    program: str


@dataclass(frozen=True)
class PackageAbsent:
    package: str


@dataclass(frozen=True)
class PackageMinVersion:
    package: str
    minimum: str


@dataclass(frozen=True)
class DeclaredMinVersion:
    package: str
    minimum: str


SourceGuard = ModuleNotImported | SubprocessDenies
ImageGuard = PackageAbsent | PackageMinVersion | DeclaredMinVersion
Guard = SourceGuard | ImageGuard

Family = Literal["source", "image"]

JUSTIFICATION_BY_FAMILY: dict[Family, str] = {
    "source": "vulnerable_code_not_in_execute_path",
    "image": "vulnerable_code_not_present",
}


def family(guard: Guard) -> Family:
    match guard:
        case ModuleNotImported() | SubprocessDenies():
            return "source"
        case PackageAbsent() | PackageMinVersion() | DeclaredMinVersion():
            return "image"
        case _:  # pragma: no cover
            assert_never(guard)


def is_source_guard(guard: Guard) -> TypeGuard[SourceGuard]:
    return family(guard) == "source"


def is_image_guard(guard: Guard) -> TypeGuard[ImageGuard]:
    return family(guard) == "image"
