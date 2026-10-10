from typing import cast

from vex_guards.sarif import build_sarif
from vex_guards.violations import Violation

_RULE_ID = "vex-consistency-tarfile"
_VEX_RELPATH = "security/crawler.vex.openvex.json"


def test_empty_violations_yields_valid_sarif_with_no_results() -> None:
    doc = build_sarif(_RULE_ID, [], _VEX_RELPATH)

    assert doc["$schema"] == "https://json.schemastore.org/sarif-2.1.0.json"
    assert doc["version"] == "2.1.0"

    runs = cast(list[dict[str, object]], doc["runs"])
    assert len(runs) == 1
    run = runs[0]

    tool = cast(dict[str, object], run["tool"])
    driver = cast(dict[str, object], tool["driver"])
    assert driver["name"] == "vex-consistency"

    rules = cast(list[dict[str, object]], driver["rules"])
    assert any(rule["id"] == _RULE_ID for rule in rules)

    assert run["results"] == []


def test_one_violation_yields_one_error_result_pointing_at_the_vex_file() -> None:
    violation = Violation(
        cve="CVE-2026-11940",
        message="tarfile is imported at src/p2pwatch/foo.py",
        location="packages/crawler/src/p2pwatch/foo.py",
    )

    doc = build_sarif(_RULE_ID, [violation], _VEX_RELPATH)

    runs = cast(list[dict[str, object]], doc["runs"])
    results = cast(list[dict[str, object]], runs[0]["results"])
    assert len(results) == 1
    result = results[0]

    assert result["ruleId"] == _RULE_ID
    assert result["level"] == "error"

    message = cast(dict[str, object], result["message"])
    text = cast(str, message["text"])
    assert violation.cve in text

    locations = cast(list[dict[str, object]], result["locations"])
    physical = cast(dict[str, object], locations[0]["physicalLocation"])
    artifact = cast(dict[str, object], physical["artifactLocation"])
    assert artifact["uri"] == _VEX_RELPATH
