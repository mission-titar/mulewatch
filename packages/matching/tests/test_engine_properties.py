import random

from catalog_matching.engine import _TIER_RANK, MatchDecision, MatchingEngine
from catalog_matching.models import FileCandidate, TargetSegment
from catalog_matching.validation import parse_matcher_config

# CONSTANT seed: every run is identical (zero flakiness, cf. spec §16).
_SEED = 20260610

_CANONICAL_RAW: dict[str, object] = {
    "tokens": {
        "keroro": {"keyword": "keroro"},
        "titar": {"keyword": "titar"},
        "keroro_titar": {"any": ["keroro", "titar"]},
        "teletoon": {"regex": "t[eé]l[eé]toon"},
        "segment_id": {"regex": r"n[°o]?\s*0*{absolute_number}\s*{segment}"},
        "foreign_lang": {
            "regex": (
                r"\b(ITA|KOR|Korean|Italiano|Coreano|VOSTFR|VOSTA|Subs?FR|"
                r"Espa[nñ]ol|English\s?Dub|ENG)\b"
            ),
        },
        "french_safe": {"not": "foreign_lang"},
        "title_hit": {"coverage": "title", "min": 0.6},
        "is_video": {"regex": r"\.(avi|mkv|mp4|mpg|ogm)$"},
    },
    "rules": [
        {
            "name": "id_segment_exact",
            "tier": "download",
            "all": ["french_safe", "is_video", "segment_id", "keroro"],
        },
        {
            "name": "teletoon_titre",
            "tier": "download",
            "all": ["french_safe", "teletoon", {"token": "title_hit", "min": 0.6}],
        },
        {
            "name": "numero_titre",
            "tier": "notify",
            "all": ["french_safe", "segment_id", {"token": "title_hit", "min": 0.5}],
        },
        {"name": "keroro_large", "tier": "catalog", "all": ["french_safe", "keroro_titar"]},
    ],
}


def _targets() -> list[TargetSegment]:
    return [
        TargetSegment(
            season=2,
            seasonal_number=11,
            absolute_number=62,
            segment="a",
            title="Les demoiselles cambrioleuses",
            status="partial",
        ),
        TargetSegment(
            season=2,
            seasonal_number=11,
            absolute_number=62,
            segment="b",
            title="Le grand combat sous-marin",
            status="lost",
        ),
        TargetSegment(
            season=1,
            seasonal_number=5,
            absolute_number=5,
            segment="a",
            title="Un titre quelconque",
            status="lost",
        ),
    ]


_FILENAMES = [
    "[TV] KERORO MISSION TITAR N°062A « Les demoiselles cambrioleuses »"
    " [21 septembre 2008 TELETOON].avi",
    "KERORO N°062A Les demoiselles cambrioleuses.txt",
    "Keroro Gunso opening.mkv",
    "Naruto épisode 062 VF.avi",
    "keroro mission titar 062b grand combat.avi",
]


def test_property_decision_invariant_under_target_reordering() -> None:
    # P1: reordering targets NEVER changes the decision (§8.5 determinism).
    config = parse_matcher_config(_CANONICAL_RAW)
    rng = random.Random(_SEED)
    base_targets = _targets()
    reference_engine = MatchingEngine(config, base_targets)
    for filename in _FILENAMES:
        candidate = FileCandidate(filename=filename)
        expected = reference_engine.evaluate(candidate)
        for _ in range(20):  # 20 seeded permutations per file (deterministic)
            shuffled = base_targets[:]
            rng.shuffle(shuffled)
            got = MatchingEngine(config, shuffled).evaluate(candidate)
            assert got == expected, f"decision depends on target order for {filename!r}"


def _max_tier(decisions: list[MatchDecision]) -> int | None:
    return max((_TIER_RANK[d.tier] for d in decisions), default=None)


def test_property_higher_priority_rule_never_lowers_tier() -> None:
    # P2: prepending a higher-priority rule never lowers the strongest resulting tier (§16).
    config_base = parse_matcher_config(_CANONICAL_RAW)
    raw_boosted = {
        "tokens": dict(_CANONICAL_RAW["tokens"]),  # type: ignore[call-overload]
        "rules": [
            {"name": "boost_keroro_download", "tier": "download", "any": ["keroro_titar"]},
            *_CANONICAL_RAW["rules"],  # type: ignore[misc]
        ],
    }
    config_boosted = parse_matcher_config(raw_boosted)
    targets = _targets()
    engine_base = MatchingEngine(config_base, targets)
    engine_boosted = MatchingEngine(config_boosted, targets)
    for filename in _FILENAMES:
        candidate = FileCandidate(filename=filename)
        base_tier = _max_tier(engine_base.evaluate(candidate))
        if base_tier is None:
            continue  # a discarded file may stay discarded
        boosted_tier = _max_tier(engine_boosted.evaluate(candidate))
        assert boosted_tier is not None, f"{filename!r}: decided without boost, discarded with it?!"
        assert boosted_tier >= base_tier, f"{filename!r}: a higher-priority rule LOWERED the tier"
