import dataclasses
from pathlib import Path

import pytest
import yaml

from catalog_matching.config import TIERS, MatcherConfig
from catalog_matching.engine import (
    _ATTRIBUTABLE,
    _EPISODE_LEVEL,
    _SEGMENT_LEVEL,
    _TIER_RANK,
    Explanation,
    MatchDecision,
    MatchingEngine,
    _first_matching_rule,
)
from catalog_matching.models import FileCandidate, TargetSegment
from catalog_matching.resolver import MatcherResolver, ResolvedTarget
from catalog_matching.validation import parse_matcher_config


def test_tier_rank_orders_download_above_notify_above_catalog() -> None:
    assert _TIER_RANK["download"] > _TIER_RANK["notify"]
    assert _TIER_RANK["notify"] > _TIER_RANK["catalog"]


def test_tier_rank_catalog_is_lowest() -> None:
    assert _TIER_RANK["catalog"] < _TIER_RANK["download"]
    assert _TIER_RANK["catalog"] < _TIER_RANK["notify"]


def test_tier_rank_covers_exactly_the_valid_tiers() -> None:
    # Consistency: every licit tier (TIERS) has a rank, and no orphan rank.
    assert set(_TIER_RANK) == TIERS


def test_attributable_is_the_union_of_segment_and_episode_level() -> None:
    assert _ATTRIBUTABLE == _SEGMENT_LEVEL | _EPISODE_LEVEL


def test_segment_and_episode_level_sets_are_disjoint() -> None:
    assert _SEGMENT_LEVEL.isdisjoint(_EPISODE_LEVEL)


def test_attributable_names_match_the_spec() -> None:
    assert frozenset({"id_segment_exact", "title_confirmed", "title_review"}) == _SEGMENT_LEVEL
    assert frozenset({"numero_nu_confirmed", "numero_nu"}) == _EPISODE_LEVEL


def test_explanation_is_frozen_and_holds_fields() -> None:
    explanation = Explanation(
        target_id="062A",
        rules_fired=("id_segment_exact", "keroro_large"),
        tokens_matched=("is_video", "keroro", "segment_id"),
        coverage_values=(("title_hit", 1.0),),
    )
    assert explanation.target_id == "062A"
    assert explanation.rules_fired == ("id_segment_exact", "keroro_large")
    assert explanation.tokens_matched == ("is_video", "keroro", "segment_id")
    assert explanation.coverage_values == (("title_hit", 1.0),)
    with pytest.raises(dataclasses.FrozenInstanceError):
        explanation.target_id = "062B"  # type: ignore[misc]


def test_match_decision_is_frozen_and_holds_persisted_columns_plus_explanation() -> None:
    explanation = Explanation(
        target_id="062A",
        rules_fired=("id_segment_exact",),
        tokens_matched=("keroro",),
        coverage_values=(),
    )
    decision = MatchDecision(
        target_id="062A",
        rule_name="id_segment_exact",
        tier="download",
        explanation=explanation,
    )
    # The three columns that match_decisions will persist (spec §11).
    assert decision.target_id == "062A"
    assert decision.rule_name == "id_segment_exact"
    assert decision.tier == "download"
    assert decision.explanation is explanation
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.tier = "notify"  # type: ignore[misc]


_TARGET_62A = TargetSegment(
    season=2,
    seasonal_number=11,
    absolute_number=62,
    segment="a",
    title="Les demoiselles cambrioleuses",
    status="partial",
)

# Minimal config with two rules of distinct index to exercise "1st true" and "loop".
_TWO_RULE_RAW: dict[str, object] = {
    "tokens": {
        "is_video": {"regex": r"\.(avi|mkv)$"},
        "seg": {"regex": r"n[°o]?\s*0*{absolute_number}\s*{segment}"},
        "keroro": {"keyword": "keroro"},
    },
    "rules": [
        {"name": "exact", "tier": "download", "all": ["is_video", "seg"]},
        {"name": "large", "tier": "catalog", "any": ["keroro"]},
    ],
}


def _resolve(raw: dict[str, object], target: TargetSegment) -> tuple[MatcherConfig, ResolvedTarget]:
    config = parse_matcher_config(raw)
    resolved = MatcherResolver(config).resolve_all(target)
    return config, resolved


def test_first_matching_rule_returns_index_zero_when_first_rule_true() -> None:
    config, resolved = _resolve(_TWO_RULE_RAW, _TARGET_62A)
    candidate = FileCandidate(filename="Keroro N°062A.avi")
    assert _first_matching_rule(config, resolved, candidate) == (0, "exact", "download")


def test_first_matching_rule_skips_to_later_rule_when_first_false() -> None:
    config, resolved = _resolve(_TWO_RULE_RAW, _TARGET_62A)
    # Not video + no 062A segment -> "exact" false; "keroro" true -> 2nd rule.
    candidate = FileCandidate(filename="keroro autre chose.txt")
    assert _first_matching_rule(config, resolved, candidate) == (1, "large", "catalog")


def test_first_matching_rule_returns_none_when_no_rule_true() -> None:
    config, resolved = _resolve(_TWO_RULE_RAW, _TARGET_62A)
    candidate = FileCandidate(filename="naruto 062.txt")
    assert _first_matching_rule(config, resolved, candidate) is None


# --- Canonical config §8.3 (reused by several tests) ---
# Single source of truth: the deployment matcher config (operator-editable), not an inline
# copy. So these tests validate the policy actually shipped. Cf. test_golden_corpus.
_CANONICAL_MATCHER = Path(__file__).resolve().parents[3] / "deploy" / "matcher.yml"
_CANONICAL_RAW: dict[str, object] = yaml.safe_load(_CANONICAL_MATCHER.read_text(encoding="utf-8"))

_TARGET_62B = TargetSegment(
    season=2,
    seasonal_number=11,
    absolute_number=62,
    segment="b",
    title="Le grand combat sous-marin",
    status="lost",
)

_REAL_62A_FILENAME = (
    "[TV] KERORO MISSION TITAR N°062A « Les demoiselles cambrioleuses » "
    "[Dimanche 21 septembre 2008 à 16H50 sur TELETOON].avi"
)


def _canonical_engine() -> MatchingEngine:
    config = parse_matcher_config(_CANONICAL_RAW)
    return MatchingEngine(config, (_TARGET_62A, _TARGET_62B))


def test_evaluate_real_62a_is_download_via_first_rule_on_62a() -> None:
    decisions = _canonical_engine().evaluate(FileCandidate(filename=_REAL_62A_FILENAME))
    assert len(decisions) == 1
    assert decisions[0].tier == "download"
    assert decisions[0].rule_name == "id_segment_exact"
    assert decisions[0].target_id == "062A"


def test_evaluate_discards_non_keroro_file() -> None:
    decisions = _canonical_engine().evaluate(FileCandidate(filename="Naruto épisode 062 VF.avi"))
    assert decisions == []


def test_evaluate_returns_empty_list_not_none_for_discard() -> None:
    # The discard sentinel is now [] (a list), never None (spec §4).
    decisions = _canonical_engine().evaluate(FileCandidate(filename="totally unrelated.txt"))
    assert decisions == []
    assert isinstance(decisions, list)


def test_evaluate_highest_tier_comes_from_a_different_target() -> None:
    # "keroro N°062B.avi": 62A -> catalog (keroro_large, not attributable); 62B -> download
    # (id_segment_exact). Only 62B is attributable -> the sole emitted decision is 62B.
    decisions = _canonical_engine().evaluate(FileCandidate(filename="keroro N°062B.avi"))
    assert len(decisions) == 1
    assert decisions[0].tier == "download"
    assert decisions[0].rule_name == "id_segment_exact"
    assert decisions[0].target_id == "062B"


def test_evaluate_notify_tier_via_title_review() -> None:
    # Close title, NO source marker -> title_review (notify, segment-level) on 062A only.
    candidate = FileCandidate(filename="KERORO Les demoiselles cambrioleuses.avi")
    decisions = _canonical_engine().evaluate(candidate)
    assert len(decisions) == 1
    assert decisions[0].tier == "notify"
    assert decisions[0].rule_name == "title_review"
    assert decisions[0].target_id == "062A"


def test_evaluate_tiebreak_same_tier_lowest_target_id_wins() -> None:
    # "Keroro" filler only -> 62A and 62B BOTH give keroro_large (catalog, not attributable)
    # -> single-winner fallback -> tie-break by target_id: 062A < 062B.
    decisions = _canonical_engine().evaluate(FileCandidate(filename="Keroro rediffusion.mkv"))
    assert len(decisions) == 1
    assert decisions[0].tier == "catalog"
    assert decisions[0].rule_name == "keroro_large"
    assert decisions[0].target_id == "062A"


# --- Tie-break by rule INDEX (isolated from target_id) ---
# Two download rules; the target with the LARGER target_id matches the rule with the
# SMALLER index. If only target_id broke ties, the wrong target would win; index must prevail.
_INDEX_TIEBREAK_RAW: dict[str, object] = {
    "tokens": {
        "is_video": {"regex": r"\.(avi|mkv)$"},
        "seg": {"regex": r"n[°o]?\s*0*{absolute_number}\s*{segment}"},
        "title_hit": {"coverage": "title", "min": 0.6},
    },
    "rules": [
        {"name": "by_segment", "tier": "download", "all": ["is_video", "seg"]},
        {
            "name": "by_title",
            "tier": "download",
            "all": ["is_video", {"token": "title_hit", "min": 0.6}],
        },
    ],
}


def test_evaluate_tiebreak_same_tier_lowest_rule_index_wins_over_target_id() -> None:
    config = parse_matcher_config(_INDEX_TIEBREAK_RAW)
    # target_high: large target_id (099Z), matches by_segment (index 0).
    target_high = TargetSegment(
        season=2, seasonal_number=99, absolute_number=99, segment="z", title="zzz unrelated"
    )
    # target_low: small target_id (001A), matches by_title (index 1).
    target_low = TargetSegment(
        season=2,
        seasonal_number=1,
        absolute_number=1,
        segment="a",
        title="Les demoiselles cambrioleuses",
    )
    engine = MatchingEngine(config, (target_low, target_high))
    candidate = FileCandidate(filename="N°099Z Les demoiselles cambrioleuses.avi")
    # by_segment / by_title are NOT attributable rule names -> single-winner fallback.
    # Index 0 (by_segment on 099Z) prevails over index 1 (by_title on 001A), DESPITE
    # 001A < 099Z: the rule index breaks the tie BEFORE the target_id.
    decisions = engine.evaluate(candidate)
    assert len(decisions) == 1
    assert decisions[0].rule_name == "by_segment"
    assert decisions[0].target_id == "099Z"


def test_evaluate_rejects_filename_over_max_length() -> None:
    config = parse_matcher_config(_CANONICAL_RAW)
    engine = MatchingEngine(config, (_TARGET_62A,), max_filename_length=16)
    assert engine.evaluate(FileCandidate(filename="Keroro N°062A.avi")) == []


def test_evaluate_accepts_filename_at_or_below_max_length() -> None:
    config = parse_matcher_config(_CANONICAL_RAW)
    engine = MatchingEngine(config, (_TARGET_62A,), max_filename_length=4096)
    decisions = engine.evaluate(FileCandidate(filename="Keroro N°062A.avi"))
    assert len(decisions) == 1
    assert decisions[0].tier == "download"


def test_engine_resolves_each_target_once_at_construction() -> None:
    # Pre-resolution happens at construction: evaluate does not rebuild a tree.
    config = parse_matcher_config(_CANONICAL_RAW)
    engine = MatchingEngine(config, (_TARGET_62A, _TARGET_62B))
    assert len(engine._resolved) == 2
    assert {rt.target.target_id for rt in engine._resolved} == {"062A", "062B"}


def test_evaluate_explanation_lists_coverage_value_even_below_threshold() -> None:
    decisions = _canonical_engine().evaluate(FileCandidate(filename="keroro rediffusion.mkv"))
    assert len(decisions) == 1
    coverage_names = [name for name, _ in decisions[0].explanation.coverage_values]
    assert "title_hit" in coverage_names
    assert "title_hit" not in decisions[0].explanation.tokens_matched


def test_explanation_on_real_62a_lists_fired_rules_tokens_and_coverage() -> None:
    decisions = _canonical_engine().evaluate(FileCandidate(filename=_REAL_62A_FILENAME))
    assert len(decisions) == 1
    explanation = decisions[0].explanation
    assert explanation.target_id == "062A"
    # The real 62A fires on 4 rules. segment_id_loose does not fire (the trailing letter of
    # "062A" fails its digit boundary), so numero_nu_confirmed / numero_nu are absent;
    # archive_candidate is absent (no archive).
    assert explanation.rules_fired == (
        "id_segment_exact",
        "title_confirmed",
        "title_review",
        "keroro_large",
    )
    assert "title_hit" in explanation.tokens_matched
    assert "keroro" in explanation.tokens_matched
    assert "segment_id" in explanation.tokens_matched
    assert explanation.tokens_matched == tuple(sorted(explanation.tokens_matched))
    assert explanation.coverage_values == (("title_hit", 1.0),)


def test_explanation_single_rule_fired_and_no_coverage_token() -> None:
    raw: dict[str, object] = {
        "tokens": {
            "is_video": {"regex": r"\.(avi|mkv)$"},
            "seg": {"regex": r"n[°o]?\s*0*{absolute_number}\s*{segment}"},
        },
        "rules": [{"name": "only", "tier": "download", "all": ["is_video", "seg"]}],
    }
    engine = MatchingEngine(parse_matcher_config(raw), (_TARGET_62A,))
    decisions = engine.evaluate(FileCandidate(filename="N°062A.avi"))
    assert len(decisions) == 1
    assert decisions[0].explanation.rules_fired == ("only",)
    assert decisions[0].explanation.coverage_values == ()
    assert decisions[0].explanation.tokens_matched == ("is_video", "seg")


# --- Multi-target fan-out (spec §3/§4/§11) ------------------------------------------------
_TARGET_94A = TargetSegment(
    season=2,
    seasonal_number=43,
    absolute_number=94,
    segment="a",
    title="La Terre est à nous !",
)


def _fanout_engine() -> MatchingEngine:
    # Canonical prod policy over a bi-segment episode (62A/62B) plus a mono episode (94A):
    # enough to exercise every §3/§11 fan-out branch against the shipped matcher.yml.
    config = parse_matcher_config(_CANONICAL_RAW)
    return MatchingEngine(config, (_TARGET_62A, _TARGET_62B, _TARGET_94A))


def _triples(decisions: list[MatchDecision]) -> list[tuple[str, str, str]]:
    return [(d.target_id, d.tier, d.rule_name) for d in decisions]


def test_evaluate_bare_number_fans_out_to_both_segments() -> None:
    # §11 clean bare number: no segment-level signal -> both segments emitted (rule 2).
    decisions = _fanout_engine().evaluate(FileCandidate(filename="Keroro 62.avi"))
    assert _triples(decisions) == [
        ("062A", "notify", "numero_nu"),
        ("062B", "notify", "numero_nu"),
    ]


def test_evaluate_title_a_plus_bare_number_pins_segment_a_only() -> None:
    # §11 title A + bare number: the segment-level title cuts the fan-out (rule 1) -> A only.
    decisions = _fanout_engine().evaluate(
        FileCandidate(filename="Keroro 62 Les demoiselles cambrioleuses.avi")
    )
    assert _triples(decisions) == [("062A", "notify", "title_review")]


def test_evaluate_both_segment_titles_emit_both_segments() -> None:
    # §11 both titles present: each title pins its own segment (rule 1) -> both emitted.
    decisions = _fanout_engine().evaluate(
        FileCandidate(
            filename="Keroro Les demoiselles cambrioleuses Le grand combat sous-marin.avi"
        )
    )
    assert _triples(decisions) == [
        ("062A", "notify", "title_review"),
        ("062B", "notify", "title_review"),
    ]


def test_evaluate_bare_number_with_source_marker_fans_out_to_download() -> None:
    # §3 row 3: bare number + source marker -> both segments in download (no tier cap).
    decisions = _fanout_engine().evaluate(FileCandidate(filename="Keroro 62 teletoon.avi"))
    assert _triples(decisions) == [
        ("062A", "download", "numero_nu_confirmed"),
        ("062B", "download", "numero_nu_confirmed"),
    ]


def test_evaluate_mono_episode_bare_number_emits_single_segment() -> None:
    # §11 mono episode: a single-segment episode fans out to exactly one segment.
    decisions = _fanout_engine().evaluate(FileCandidate(filename="Keroro 94.avi"))
    assert _triples(decisions) == [("094A", "notify", "numero_nu")]


def test_evaluate_out_of_range_bare_number_is_evicted() -> None:
    # §11 out of range: a bare number beyond the last target reaches keroro_large, whose
    # { not: episode_number } guard vetoes it (Approach 1 eviction) -> discarded, no catalog
    # fallback. Seasonal SxE / NNxNN forms without a letter are NOT bare numbers and still
    # fall back to catalog (see the golden corpus).
    decisions = _fanout_engine().evaluate(FileCandidate(filename="Keroro 130.avi"))
    assert decisions == []


def test_evaluate_lettered_segment_pins_that_segment_only() -> None:
    # §3 row 4: a lettered number (N°062A) is segment-level -> only that segment (rule 1).
    decisions = _fanout_engine().evaluate(FileCandidate(filename="Keroro N°062A.avi"))
    assert _triples(decisions) == [("062A", "download", "id_segment_exact")]


# --- evaluate_all: one file known under several names (spec amuleapi-migration §8.4) ---


def _union(*names: str) -> list[tuple[str, str, str]]:
    engine = _fanout_engine()
    forward = engine.evaluate_all([FileCandidate(filename=name) for name in names])
    backward = engine.evaluate_all([FileCandidate(filename=name) for name in reversed(names)])
    assert _triples(forward) == _triples(backward)
    return _triples(forward)


def test_evaluate_all_same_target_keeps_the_highest_tier_whatever_the_name_order() -> None:
    # title_review (notify) and title_confirmed (download) both on 062A: download wins.
    assert _union(
        "Keroro Les demoiselles cambrioleuses.avi",
        "Keroro Les demoiselles cambrioleuses teletoon.avi",
    ) == [("062A", "download", "title_confirmed")]


def test_evaluate_all_same_target_same_tier_keeps_the_smallest_rule_index() -> None:
    # id_segment_exact (index 0) and title_confirmed (index 1), both download on 062A.
    assert _union("Keroro Les demoiselles cambrioleuses teletoon.avi", "Keroro N°062A.avi") == [
        ("062A", "download", "id_segment_exact")
    ]


def test_evaluate_all_attribution_from_one_name_drops_another_names_catch_all() -> None:
    # "Keroro rediffusion.mkv" alone is catch-all 062A; the other name attributes 062B.
    assert _union("Keroro N°062B.avi", "Keroro rediffusion.mkv") == [
        ("062B", "download", "id_segment_exact")
    ]


def test_evaluate_all_without_attribution_yields_one_catch_all_winner() -> None:
    assert _union("Keroro rediffusion.mkv", "random.txt") == [("062A", "catalog", "keroro_large")]


def test_evaluate_all_segment_signal_of_one_name_cuts_the_fan_out_of_another() -> None:
    # Same per-episode rule as a single name: a segment-level signal pins that segment only.
    assert _union("Keroro 62.avi", "Keroro Les demoiselles cambrioleuses.avi") == [
        ("062A", "notify", "title_review")
    ]


def test_evaluate_all_skips_an_over_long_name_but_keeps_the_others() -> None:
    config = parse_matcher_config(_CANONICAL_RAW)
    engine = MatchingEngine(config, (_TARGET_62A,), max_filename_length=20)
    decisions = engine.evaluate_all(
        [FileCandidate(filename="Keroro N°062A.avi"), FileCandidate(filename="x" * 21)]
    )
    assert _triples(decisions) == [("062A", "download", "id_segment_exact")]


def test_evaluate_all_explains_with_the_winning_name() -> None:
    winner = FileCandidate(filename="Keroro N°062A.avi")
    decisions = _fanout_engine().evaluate_all(
        [FileCandidate(filename="Keroro rediffusion.mkv"), winner]
    )
    assert decisions[0].explanation == _fanout_engine().explain(winner, "062A")


def test_evaluate_all_of_no_name_is_a_discard() -> None:
    assert _fanout_engine().evaluate_all([]) == []
