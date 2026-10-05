"""Tests of MatchingEngine.explain(candidate, target_id) (cf. spec W-D7/§7)."""

from catalog_matching.engine import Explanation, MatchingEngine
from catalog_matching.models import FileCandidate
from catalog_matching.validation import parse_matcher_config, parse_targets

_MATCHER = {
    "tokens": {"keroro": {"keyword": "keroro"}, "titar": {"keyword": "titar"}},
    "rules": [{"name": "keroro_large", "tier": "catalog", "any": ["keroro", "titar"]}],
}
_TARGETS = {
    "episodes": [
        {
            "season": 2,
            "seasonal_number": 11,
            "absolute_number": 62,
            "segments": [{"letter": "A", "title": "Les demoiselles cambrioleuses"}],
        }
    ]
}


def _engine() -> MatchingEngine:
    return MatchingEngine(parse_matcher_config(_MATCHER), parse_targets(_TARGETS))


def test_explain_known_target_with_match_returns_explanation() -> None:
    result = _engine().explain(FileCandidate(filename="keroro_062.avi"), "062A")
    assert isinstance(result, Explanation)
    assert result.target_id == "062A"
    assert "keroro_large" in result.rules_fired


def test_explain_unknown_target_returns_none() -> None:
    assert _engine().explain(FileCandidate(filename="x"), "S9E999Z") is None


def test_explain_known_target_no_rule_fired_returns_empty_explanation() -> None:
    result = _engine().explain(FileCandidate(filename="random.txt"), "062A")
    assert isinstance(result, Explanation)
    assert result.rules_fired == ()
