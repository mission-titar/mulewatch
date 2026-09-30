from typing import get_args

from vex_guards.descriptors import (
    JUSTIFICATION_BY_FAMILY,
    DeclaredMinVersion,
    Guard,
    ImageGuard,
    ModuleNotImported,
    PackageAbsent,
    PackageMinVersion,
    SourceGuard,
    SubprocessDenies,
    family,
    is_image_guard,
    is_source_guard,
)


def test_source_descriptors_report_source_family() -> None:
    for guard in (ModuleNotImported("tarfile"), SubprocessDenies("ffmpeg")):
        assert family(guard) == "source"


def test_image_descriptors_report_image_family() -> None:
    for guard in (
        PackageAbsent("nghttp2"),
        PackageMinVersion("clamav", "0.99"),
        DeclaredMinVersion("amule", "2.1.2"),
    ):
        assert family(guard) == "image"


def test_justification_by_family_is_closed() -> None:
    assert JUSTIFICATION_BY_FAMILY == {
        "source": "vulnerable_code_not_in_execute_path",
        "image": "vulnerable_code_not_present",
    }


def test_guard_union_covers_both_families() -> None:
    assert set(get_args(Guard)) == set(get_args(SourceGuard)) | set(get_args(ImageGuard))


def test_is_source_guard_matches_only_the_source_family() -> None:
    assert is_source_guard(ModuleNotImported("tarfile")) is True
    assert is_source_guard(PackageAbsent("nghttp2")) is False


def test_is_image_guard_matches_only_the_image_family() -> None:
    assert is_image_guard(PackageAbsent("nghttp2")) is True
    assert is_image_guard(ModuleNotImported("tarfile")) is False
