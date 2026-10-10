from catalog_matching.config import TIERS
from p2pwatch.domain.retraction import RETRACTED_TIER


def test_retracted_tier_is_the_string_retracted() -> None:
    assert RETRACTED_TIER == "retracted"


def test_retracted_tier_is_not_a_matcher_tier() -> None:
    # RETRACTED_TIER is synthesized by the crawler, never produced by the matching
    # engine (spec §5): it must stay outside the engine's own closed tier set.
    assert RETRACTED_TIER not in TIERS
