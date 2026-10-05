"""Tests of MatchingEngine.explain(candidates, target_id): every name of one file."""

from catalog_matching.engine import Explanation, MatchingEngine
from catalog_matching.models import FileCandidate
from catalog_matching.validation import parse_matcher_config, parse_targets

_MATCHER = {
    "tokens": {
        "keroro": {"keyword": "keroro"},
        "titar": {"keyword": "titar"},
        "ita": {"regex": r"\bITA\b"},
        "kor": {"regex": r"\bKOR\b"},
        "sample": {"regex": "sample"},
        "title_hit": {"coverage": "title", "min": 0.6},
    },
    "vetoes": ["sample", "kor", "ita"],
    "rules": [
        {"name": "by_title", "tier": "notify", "scope": "segment", "all": ["title_hit"]},
        {"name": "keroro_large", "tier": "catalog", "scope": "unattributed", "any": ["keroro"]},
        {"name": "titar_large", "tier": "catalog", "scope": "unattributed", "any": ["titar"]},
    ],
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


def _engine(max_filename_length: int = 4096) -> MatchingEngine:
    return MatchingEngine(
        parse_matcher_config(_MATCHER),
        parse_targets(_TARGETS),
        max_filename_length=max_filename_length,
    )


def _names(*filenames: str) -> list[FileCandidate]:
    return [FileCandidate(filename=name) for name in filenames]


def test_explain_known_target_with_match_returns_explanation() -> None:
    result = _engine().explain(_names("keroro_062.avi"), "062A")
    assert isinstance(result, Explanation)
    assert result.target_id == "062A"
    assert result.rules_fired == ("keroro_large",)
    assert result.vetoes_fired == ()


def test_explain_unknown_target_returns_none() -> None:
    assert _engine().explain(_names("x"), "S9E999Z") is None


def test_explain_known_target_no_rule_fired_returns_empty_explanation() -> None:
    result = _engine().explain(_names("random.txt"), "062A")
    assert isinstance(result, Explanation)
    assert result.rules_fired == ()
    assert result.tokens_matched == ()


def test_explain_unions_rules_and_tokens_over_every_name() -> None:
    result = _engine().explain(_names("titar.avi", "keroro.avi"), "062A")
    assert result is not None
    assert result.rules_fired == ("keroro_large", "titar_large")  # config order
    assert result.tokens_matched == ("keroro", "titar")  # sorted


def test_explain_lists_the_vetoes_fired_on_any_name_sorted() -> None:
    result = _engine().explain(_names("keroro ITA.avi", "keroro KOR.avi", "keroro.avi"), "062A")
    assert result is not None
    assert result.vetoes_fired == ("ita", "kor")


def test_explain_keeps_the_best_coverage_over_every_name() -> None:
    result = _engine().explain(
        _names("keroro demoiselles cambrioleuses.avi", "keroro demoiselles.avi"), "062A"
    )
    assert result is not None
    assert result.coverage_values == (("title_hit", 1.0),)


def test_explain_of_no_name_has_zero_coverage() -> None:
    result = _engine().explain([], "062A")
    assert result is not None
    assert result.coverage_values == (("title_hit", 0.0),)


def test_explain_skips_over_long_names_like_evaluate_all() -> None:
    result = _engine(max_filename_length=12).explain(
        _names("keroro.avi", "keroro ITA titar.avi"), "062A"
    )
    assert result is not None
    assert result.rules_fired == ("keroro_large",)
    assert result.vetoes_fired == ()


def test_target_returns_the_known_target_segment() -> None:
    target = _engine().target("062A")
    assert target is not None
    assert target.title == "Les demoiselles cambrioleuses"


def test_target_of_an_unknown_id_is_none() -> None:
    assert _engine().target("999Z") is None
