from pathlib import Path
from typing import Any

import pytest
import yaml

from catalog_matching.config import RegexDef
from catalog_matching.engine import MatchingEngine
from catalog_matching.matchers import RegexMatcher
from catalog_matching.models import FileCandidate
from catalog_matching.validation import parse_matcher_config, parse_targets

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
# The matcher policy has a single source of truth: the deployment config (operator-
# editable). So the golden corpus validates the policy ACTUALLY shipped, not a copy.
_MATCHER = Path(__file__).resolve().parents[3] / "deploy" / "matcher.yml"


def _engine() -> MatchingEngine:
    config = parse_matcher_config(yaml.safe_load(_MATCHER.read_text(encoding="utf-8")))
    targets = parse_targets(
        yaml.safe_load((_FIXTURES / "golden_targets.yaml").read_text(encoding="utf-8"))
    )
    return MatchingEngine(config, targets)


def _corpus_cases() -> list[dict[str, Any]]:
    raw: dict[str, Any] = yaml.safe_load(
        (_FIXTURES / "golden_corpus.yaml").read_text(encoding="utf-8")
    )
    cases = raw["cases"]
    assert isinstance(cases, list)
    return [dict(case) for case in cases]


_CASES = _corpus_cases()


@pytest.mark.parametrize("case", _CASES, ids=[str(c["id"]) for c in _CASES])
def test_golden_corpus(case: dict[str, Any]) -> None:
    engine = _engine()
    decisions = engine.evaluate(FileCandidate(filename=str(case["filename"])))
    if case.get("discarded", False):
        assert decisions == [], f"{case['id']}: expected discarded, got {decisions}"
        return
    if "decisions" in case:
        expected = [
            (str(d["target_id"]), str(d["tier"]), str(d["rule_name"])) for d in case["decisions"]
        ]
        got = [(d.target_id, d.tier, d.rule_name) for d in decisions]
        assert got == expected, f"{case['id']}: fan-out mismatch: {got} != {expected}"
        return
    if case.get("unidentified", False):
        assert len(decisions) == 1, f"{case['id']}: expected one decision, got {decisions}"
        decision = decisions[0]
        assert decision.tier == "catalog", (
            f"{case['id']}: expected catalog tier, got {decision.tier}"
        )
        assert decision.rule_name == "keroro_large", (
            f"{case['id']}: expected keroro_large, got {decision.rule_name}"
        )
        return
    assert len(decisions) == 1, f"{case['id']}: expected one decision, got {decisions}"
    decision = decisions[0]
    assert decision.tier == case["tier"], f"{case['id']}: tier"
    assert decision.target_id == case["target_id"], f"{case['id']}: target"
    assert decision.rule_name == case["rule_name"], f"{case['id']}: rule"


def test_corpus_covers_every_tier_and_a_discard() -> None:
    # Completeness guard: the corpus exercises the 3 tiers + at least one discard.
    # An ``unidentified`` case IS the catalog tier (it just does not pin the arbitrary target_id).
    tiers = {c["tier"] for c in _CASES if "tier" in c}
    if any(c.get("unidentified", False) for c in _CASES):
        tiers.add("catalog")
    assert {"download", "notify", "catalog"} <= tiers
    assert any(c.get("discarded", False) for c in _CASES)


def test_corpus_has_a_multi_decision_fan_out_case() -> None:
    # The fan-out contract (spec §3) is exercised: at least one case emits >1 decision.
    assert any("decisions" in c and len(c["decisions"]) > 1 for c in _CASES)


# --- is_archive token contract (shipped policy) ------------------------------------
# The catalogue's media gate keeps is_video OR is_archive. is_archive is meant for the
# GENERIC archives that may ship an episode inside them; comic-book containers (.cbz/.cbr,
# technically ZIP/RAR) are deliberately NOT archives so mangas never enter the catalogue.


def _is_archive_matcher() -> RegexMatcher:
    config = parse_matcher_config(yaml.safe_load(_MATCHER.read_text(encoding="utf-8")))
    token = config.tokens["is_archive"]
    assert isinstance(token, RegexDef)  # the shipped is_archive is a plain regex token
    return RegexMatcher(token.pattern, token.flags)


@pytest.mark.parametrize("filename", ["x.zip", "x.7z", "x.rar", "x.r01", "x.z01", "x.part1.rar"])
def test_is_archive_matches_generic_archive_extensions(filename: str) -> None:
    assert _is_archive_matcher().matches(FileCandidate(filename=filename)) is True


@pytest.mark.parametrize("filename", ["x.cbz", "x.cbr"])
def test_is_archive_rejects_comic_book_formats(filename: str) -> None:
    # cbz/cbr are comic formats, not the generic archives is_archive is meant to catch;
    # adding them to is_archive would silently re-catalogue mangas.
    assert _is_archive_matcher().matches(FileCandidate(filename=filename)) is False


# --- single-catalog-rule invariant (cross-package: relied on by mulewatch.webui) ---------
# mulewatch.webui.domain.views.FileRowDisplay documents that ANY decision with tier=="catalog"
# is displayed as "unidentified" (DecisionCell.target) / "·" (DecisionCell.title), and it
# relies on the prod policy having exactly ONE catalog-tier rule, the target-agnostic catch-all
# (keroro_large). If a second catalog-tier rule were ever added, the webui would silently
# mislabel a real, identified episode as "unidentified" instead of showing its title. This
# test guards that invariant directly against the shipped policy so a future editor gets a
# loud failure instead of a silent webui regression.


def test_prod_policy_has_exactly_one_catalog_tier_rule_named_keroro_large() -> None:
    config = parse_matcher_config(yaml.safe_load(_MATCHER.read_text(encoding="utf-8")))
    catalog_rules = [rule for rule in config.rules if rule.tier == "catalog"]
    assert len(catalog_rules) == 1, (
        "expected exactly one catalog-tier rule (webui's tier=='catalog' -> 'unidentified' "
        f"display relies on this) but found {len(catalog_rules)}: "
        f"{[rule.name for rule in catalog_rules]}"
    )
    assert catalog_rules[0].name == "keroro_large"


# --- episode_number token contract (shipped policy) --------------------------------------
# episode_number is the target-agnostic bare-number arm (Approach 1): a 2-3 digit run bordered by
# non-alphanumerics on BOTH sides, minus dates (month names, numeric d/m) and hex-bordered
# digits. keroro_large requires { not: episode_number }, so an out-of-range bare number that
# reaches the catch-all is evicted. Seasonal SxE / NNxNN forms (no A/B letter) are NOT bare
# numbers, so they stay in the catalog as "unidentified".


def _episode_number_matcher() -> RegexMatcher:
    config = parse_matcher_config(yaml.safe_load(_MATCHER.read_text(encoding="utf-8")))
    token = config.tokens["episode_number"]
    assert isinstance(token, RegexDef)
    return RegexMatcher(token.pattern, token.flags)


@pytest.mark.parametrize("filename", ["[Keroro].104.avi", "Keroro 155 rediffusion.mkv"])
def test_episode_number_matches_a_bare_number(filename: str) -> None:
    assert _episode_number_matcher().matches(FileCandidate(filename=filename)) is True


@pytest.mark.parametrize(
    "filename",
    [
        "Keroro 21 septembre 2008 TELETOON.avi",  # date veto (month name)
        "Keroro 2008 rediffusion.avi",  # 4-digit year, no 2-3 digit bounded run
        "Keroro 640x480 BDRip.mkv",  # resolution, 3 digits per side
        "Keroro rediffusion [D6A10367].avi",  # hex CRC, digits bordered by letters
        "Keroro s01e29.avi",  # seasonal form: bare-only does not read it (Approach 1)
        "Keroro 01x37.avi",  # seasonal x-form: bare-only does not read it (Approach 1)
    ],
)
def test_episode_number_ignores_non_episode_numbers(filename: str) -> None:
    assert _episode_number_matcher().matches(FileCandidate(filename=filename)) is False
