import dataclasses

import pytest

from vex_guards.violations import Violation


def test_violation_carries_cve_message_and_location() -> None:
    violation = Violation(
        cve="CVE-2026-11940",
        message="tarfile is imported at src/p2pwatch/foo.py",
        location="packages/crawler/src/p2pwatch/foo.py",
    )
    assert violation.cve == "CVE-2026-11940"
    assert violation.message == "tarfile is imported at src/p2pwatch/foo.py"
    assert violation.location == "packages/crawler/src/p2pwatch/foo.py"


def test_violation_is_frozen() -> None:
    violation = Violation(cve="CVE-2026-11940", message="boom", location="a/b.py")
    with pytest.raises(dataclasses.FrozenInstanceError):
        # A frozen-dataclass write. mypy rejects it (read-only), which is the whole point of the
        # test; ruff B010 would reject setattr with a constant, so a direct assignment is correct.
        violation.cve = "CVE-2026-11972"  # type: ignore[misc]
