import dataclasses

import pytest

from catalog_matching.config import (
    AllDef,
    AnyDef,
    AttrBetweenDef,
    CoverageDef,
    KeywordDef,
    MatcherConfig,
    NotDef,
    RegexDef,
    Rule,
    TokenRef,
)


def test_keyword_def_holds_phrase() -> None:
    assert KeywordDef(phrase="keroro").phrase == "keroro"


def test_regex_def_defaults_flags_to_i() -> None:
    assert RegexDef(pattern="teletoon").flags == "i"
    assert RegexDef(pattern="teletoon", flags="").flags == ""


def test_coverage_def_defaults() -> None:
    cov = CoverageDef(reference="title", min=0.6)
    assert cov.reference == "title"
    assert cov.min == 0.6
    assert cov.fuzz == 0.85


def test_attr_between_def_holds_bounds() -> None:
    ab = AttrBetweenDef(attr="size_mb", min=30.0, max=600.0)
    assert ab.attr == "size_mb"
    assert ab.min == 30.0
    assert ab.max == 600.0
    assert AttrBetweenDef(attr="size_mb").min is None
    assert AttrBetweenDef(attr="size_mb").max is None


def test_composite_defs_hold_operands() -> None:
    comp = AnyDef(operands=("keroro", "titar"))
    assert comp.operands == ("keroro", "titar")
    assert AllDef(operands=()).operands == ()
    assert NotDef(operand="keroro").operand == "keroro"


def test_token_ref_overrides_default_to_none() -> None:
    ref = TokenRef(name="title_hit")
    assert ref.name == "title_hit"
    assert ref.min is None
    assert ref.fuzz is None
    assert TokenRef(name="title_hit", min=0.4).min == 0.4


def test_rule_holds_name_tier_condition() -> None:
    rule = Rule(name="keroro_large", tier="catalog", condition=AnyDef(operands=("keroro_titar",)))
    assert rule.name == "keroro_large"
    assert rule.tier == "catalog"
    assert isinstance(rule.condition, AnyDef)


def test_matcher_config_holds_tokens_and_rules() -> None:
    config = MatcherConfig(
        tokens={"keroro": KeywordDef(phrase="keroro")},
        rules=(Rule(name="r", tier="catalog", condition=AnyDef(operands=("keroro",))),),
    )
    assert config.tokens["keroro"] == KeywordDef(phrase="keroro")
    assert len(config.rules) == 1


def test_defs_are_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        KeywordDef(phrase="x").phrase = "y"  # type: ignore[misc]


def test_matcher_config_is_frozen() -> None:
    config = MatcherConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.rules = ()  # type: ignore[misc]
