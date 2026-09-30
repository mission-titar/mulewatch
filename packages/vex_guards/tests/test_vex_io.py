import json
from pathlib import Path

import pytest

from vex_guards import repo
from vex_guards.vex_io import all_claims, load_claims


def _write(tmp_path: Path, statements: list[dict[str, object]]) -> Path:
    doc = {"@context": "https://openvex.dev/ns/v0.2.0", "statements": statements}
    path = tmp_path / "x.vex.openvex.json"
    path.write_text(json.dumps(doc))
    return path


def test_load_claims_keeps_not_affected_only(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            {
                "vulnerability": {"name": "CVE-1"},
                "status": "not_affected",
                "justification": "vulnerable_code_not_present",
            },
            {"vulnerability": {"name": "CVE-2"}, "status": "affected"},
        ],
    )
    assert load_claims(path) == {"CVE-1": "vulnerable_code_not_present"}


def test_all_claims_reads_the_shipped_vex_documents() -> None:
    claims = all_claims(list(repo.vex_files().values()))
    assert claims["CVE-2025-15366"] == "vulnerable_code_not_in_execute_path"


def test_all_claims_merges_and_agrees_on_shared_cves(tmp_path: Path) -> None:
    # Two documents claiming the same CVE with the same justification merge silently.
    statement: dict[str, object] = {
        "vulnerability": {"name": "CVE-SHARED"},
        "status": "not_affected",
        "justification": "vulnerable_code_not_present",
    }
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    first = _write(dir_a, [statement])
    second = _write(dir_b, [statement])

    assert all_claims([first, second]) == {"CVE-SHARED": "vulnerable_code_not_present"}


def test_all_claims_raises_on_conflicting_justifications(tmp_path: Path) -> None:
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    first = _write(
        dir_a,
        [
            {
                "vulnerability": {"name": "CVE-SHARED"},
                "status": "not_affected",
                "justification": "vulnerable_code_not_present",
            }
        ],
    )
    second = _write(
        dir_b,
        [
            {
                "vulnerability": {"name": "CVE-SHARED"},
                "status": "not_affected",
                "justification": "vulnerable_code_not_in_execute_path",
            }
        ],
    )
    with pytest.raises(ValueError):
        all_claims([first, second])
