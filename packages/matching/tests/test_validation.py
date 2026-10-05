import pytest

from catalog_matching.config import (
    AllDef,
    AnyDef,
    AttrBetweenDef,
    CoverageDef,
    KeywordDef,
    NotDef,
    RegexDef,
    TokenRef,
)
from catalog_matching.validation import (
    ConfigError,
    CycleError,
    DepthExceededError,
    UnknownTokenError,
    parse_matcher_config,
    parse_targets,
    validate_config,
)


def test_parse_leaf_token_defs() -> None:
    config = parse_matcher_config(
        {
            "tokens": {
                "keroro": {"keyword": "keroro"},
                "teletoon": {"regex": "t[eé]l[eé]toon"},
                "video": {"regex": "\\.(avi|mkv)$", "flags": ""},
                "title_hit": {"coverage": "title", "min": 0.6},
                "small": {"attr_between": "size_mb", "min": 30, "max": 600},
            },
            "rules": [],
        }
    )
    assert config.tokens["keroro"] == KeywordDef(phrase="keroro")
    assert config.tokens["teletoon"] == RegexDef(pattern="t[eé]l[eé]toon", flags="i")
    assert config.tokens["video"] == RegexDef(pattern="\\.(avi|mkv)$", flags="")
    assert config.tokens["title_hit"] == CoverageDef(reference="title", min=0.6)
    assert config.tokens["small"] == AttrBetweenDef(attr="size_mb", min=30.0, max=600.0)


def test_parse_composite_token_def() -> None:
    config = parse_matcher_config(
        {
            "tokens": {
                "keroro": {"keyword": "keroro"},
                "titar": {"keyword": "titar"},
                "kt": {"any": ["keroro", "titar"]},
            },
            "rules": [],
        }
    )
    assert config.tokens["kt"] == AnyDef(operands=("keroro", "titar"))


def test_parse_not_token_def() -> None:
    config = parse_matcher_config(
        {"tokens": {"keroro": {"keyword": "keroro"}, "nk": {"not": "keroro"}}, "rules": []}
    )
    assert config.tokens["nk"] == NotDef(operand="keroro")


def test_parse_rule_with_inline_token_ref_and_condition() -> None:
    config = parse_matcher_config(
        {
            "tokens": {
                "title_hit": {"coverage": "title", "min": 0.6},
                "seg": {"regex": "0*{absolute_number}"},
            },
            "rules": [
                {
                    "name": "numero_titre",
                    "tier": "notify",
                    "all": ["seg", {"token": "title_hit", "min": 0.5}],
                }
            ],
        }
    )
    rule = config.rules[0]
    assert rule.name == "numero_titre"
    assert rule.tier == "notify"
    assert rule.condition == AllDef(operands=("seg", TokenRef(name="title_hit", min=0.5)))


def test_parse_rule_with_nested_inline_condition() -> None:
    config = parse_matcher_config(
        {
            "tokens": {"keroro": {"keyword": "keroro"}, "titar": {"keyword": "titar"}},
            "rules": [{"name": "r", "tier": "catalog", "not": {"any": ["keroro", "titar"]}}],
        }
    )
    assert config.rules[0].condition == NotDef(operand=AnyDef(operands=("keroro", "titar")))


def test_unknown_tier_raises_and_names_it() -> None:
    with pytest.raises(ConfigError, match="bogus"):
        parse_matcher_config(
            {
                "tokens": {"keroro": {"keyword": "keroro"}},
                "rules": [{"name": "r", "tier": "bogus", "any": ["keroro"]}],
            }
        )


def test_attr_between_unknown_attr_raises_and_names_it() -> None:
    with pytest.raises(ConfigError, match="codec"):
        parse_matcher_config({"tokens": {"c": {"attr_between": "codec", "min": 1}}, "rules": []})


def test_unknown_token_definition_shape_raises() -> None:
    with pytest.raises(ConfigError, match="unknown token shape"):
        parse_matcher_config({"tokens": {"x": {"frobnicate": "y"}}, "rules": []})


def test_token_def_with_multiple_keys_raises() -> None:
    with pytest.raises(ConfigError, match="exactly one type-key"):
        parse_matcher_config({"tokens": {"x": {"keyword": "a", "regex": "b"}}, "rules": []})


def test_override_on_non_coverage_token_raises() -> None:
    with pytest.raises(ConfigError, match="kw"):
        parse_matcher_config(
            {
                "tokens": {"kw": {"keyword": "keroro"}},
                "rules": [{"name": "r", "tier": "catalog", "all": [{"token": "kw", "min": 0.5}]}],
            }
        )


def test_rule_without_condition_key_raises() -> None:
    with pytest.raises(ConfigError, match="condition"):
        parse_matcher_config(
            {
                "tokens": {"keroro": {"keyword": "keroro"}},
                "rules": [{"name": "r", "tier": "catalog"}],
            }
        )


def test_rule_with_two_condition_keys_raises() -> None:
    with pytest.raises(ConfigError, match="exactly one condition"):
        parse_matcher_config(
            {
                "tokens": {"keroro": {"keyword": "keroro"}},
                "rules": [{"name": "r", "tier": "catalog", "all": ["keroro"], "any": ["keroro"]}],
            }
        )


def test_rule_with_empty_all_is_rejected() -> None:
    # config-validation#0: AllMatcher([]).matches() == all([]) == True → a rule 'all: []'
    # would unconditionally match EVERY file (here in tier=download → auto-download).
    # EBNF §8.3 requires >=1 operand; fail-fast must reject it at load time.
    with pytest.raises(ConfigError, match="at least one operand"):
        parse_matcher_config({"rules": [{"name": "pwn", "tier": "download", "all": []}]})


def test_rule_with_empty_any_is_rejected() -> None:
    # Same fail-fast gap for 'any: []' (EBNF §8.3: >=1 operand), degenerate config.
    with pytest.raises(ConfigError, match="at least one operand"):
        parse_matcher_config({"rules": [{"name": "pwn", "tier": "download", "any": []}]})


def test_coverage_min_out_of_unit_range_is_rejected() -> None:
    # config-validation#2: min/fuzz are logical fractions [0, 1]. 'min: 5.0' (typo for
    # 0.5) would make the rule silently INERT (value() <= 1 always false → never matches)
    # with no signal at load time → fail-fast §8.4.
    with pytest.raises(ConfigError, match=r"\[0, 1\]"):
        parse_matcher_config({"tokens": {"t": {"coverage": "title", "min": 5.0}}, "rules": []})


def test_coverage_fuzz_out_of_unit_range_is_rejected() -> None:
    # 'fuzz: 99' (typo for 0.99) → ratio/100 >= fuzz always false → value=0, mute rule.
    with pytest.raises(ConfigError, match=r"\[0, 1\]"):
        parse_matcher_config(
            {"tokens": {"t": {"coverage": "title", "min": 0.5, "fuzz": 99}}, "rules": []}
        )


def test_coverage_override_min_out_of_unit_range_is_rejected() -> None:
    # Same bound for a min/fuzz override on a TokenRef (coverage).
    with pytest.raises(ConfigError, match=r"\[0, 1\]"):
        parse_matcher_config(
            {
                "tokens": {"cov": {"coverage": "title", "min": 0.5}},
                "rules": [{"name": "r", "tier": "catalog", "all": [{"token": "cov", "min": 5.0}]}],
            }
        )


def test_attr_between_min_greater_than_max_is_rejected() -> None:
    # config-validation#1: {attr_between: size_mb, min: 600, max: 30} = EMPTY range → the rule is
    # mute forever (input error). OPEN bounds (min only / max only) stay valid — it is
    # deliberate and tested; only min > max is rejected.
    with pytest.raises(ConfigError, match="min.*>.*max"):
        parse_matcher_config(
            {"tokens": {"sz": {"attr_between": "size_mb", "min": 600, "max": 30}}, "rules": []}
        )


def test_token_ref_missing_name_raises() -> None:
    with pytest.raises(ConfigError, match="token"):
        parse_matcher_config(
            {
                "tokens": {"keroro": {"keyword": "keroro"}},
                "rules": [{"name": "r", "tier": "catalog", "all": [{"min": 0.5}]}],
            }
        )


def test_operand_wrong_type_raises() -> None:
    with pytest.raises(ConfigError, match="operand"):
        parse_matcher_config(
            {
                "tokens": {"keroro": {"keyword": "keroro"}},
                "rules": [{"name": "r", "tier": "catalog", "all": [123]}],
            }
        )


def test_parse_targets_builds_segments_with_per_segment_status() -> None:
    targets = parse_targets(
        {
            "episodes": [
                {
                    "season": 2,
                    "seasonal_number": 11,
                    "absolute_number": 62,
                    "segments": [
                        {"letter": "A", "title": "Les demoiselles", "status": "found"},
                        {"letter": "B", "title": "Le grand combat"},
                    ],
                }
            ]
        }
    )
    assert len(targets) == 2
    a, b = targets
    assert a.target_id == "062A"
    assert a.seasonal_number == 11
    assert a.absolute_number == 62
    assert a.status == "found"  # status SPECIFIC to segment A
    assert b.target_id == "062B"
    assert b.status == "lost"  # default, B not marked


def test_parse_targets_requires_seasonal_number() -> None:
    with pytest.raises(ConfigError, match="seasonal_number"):
        parse_targets(
            {
                "episodes": [
                    {
                        "season": 1,
                        "absolute_number": 5,
                        "segments": [{"letter": "a", "title": "x"}],
                    }
                ]
            }
        )


def test_parse_targets_requires_absolute_number() -> None:
    with pytest.raises(ConfigError, match="absolute_number"):
        parse_targets(
            {
                "episodes": [
                    {
                        "season": 1,
                        "seasonal_number": 5,
                        "segments": [{"letter": "a", "title": "x"}],
                    }
                ]
            }
        )


def test_parse_targets_default_status_is_lost() -> None:
    targets = parse_targets(
        {
            "episodes": [
                {
                    "season": 1,
                    "seasonal_number": 5,
                    "absolute_number": 5,
                    "segments": [{"letter": "a", "title": "x"}],
                }
            ]
        }
    )
    assert targets[0].status == "lost"


def test_parse_targets_episode_without_segments() -> None:
    targets = parse_targets(
        {"episodes": [{"season": 1, "seasonal_number": 1, "absolute_number": 1}]}
    )
    assert targets == ()


def test_parse_targets_duplicate_target_id_raises() -> None:
    with pytest.raises(ConfigError, match="duplicate"):
        parse_targets(
            {
                "episodes": [
                    {
                        "season": 2,
                        "seasonal_number": 11,
                        "absolute_number": 62,
                        "segments": [
                            {"letter": "a", "title": "x"},
                            {"letter": "A", "title": "y"},
                        ],
                    }
                ]
            }
        )


def test_parse_targets_builds_mono_episode_target() -> None:
    targets = parse_targets(
        {
            "episodes": [
                {
                    "season": 1,
                    "seasonal_number": 10,
                    "absolute_number": 10,
                    "segments": [{"letter": "A", "title": "x"}],
                }
            ]
        }
    )
    assert [t.target_id for t in targets] == ["010A"]


def test_parse_targets_builds_two_segment_targets() -> None:
    targets = parse_targets(
        {
            "episodes": [
                {
                    "season": 2,
                    "seasonal_number": 11,
                    "absolute_number": 62,
                    "segments": [
                        {"letter": "A", "title": "x"},
                        {"letter": "B", "title": "y"},
                    ],
                }
            ]
        }
    )
    assert [t.target_id for t in targets] == ["062A", "062B"]


def test_regex_with_date_alt_placeholder_is_rejected() -> None:
    # {date_alt} removed: a token using it fails at load time (unknown placeholder).
    with pytest.raises(ConfigError, match="date_alt"):
        parse_matcher_config({"tokens": {"air": {"regex": "{date_alt}"}}, "rules": []})


def test_parse_targets_missing_episodes_raises() -> None:
    with pytest.raises(ConfigError, match="episodes"):
        parse_targets({})


def test_parse_targets_missing_required_segment_field_raises() -> None:
    with pytest.raises(ConfigError, match="title"):
        parse_targets(
            {
                "episodes": [
                    {
                        "season": 1,
                        "seasonal_number": 5,
                        "absolute_number": 5,
                        "segments": [{"letter": "a"}],
                    }
                ]
            }
        )


# --- Branch-coverage leftovers ---


def test_token_def_non_mapping_raises() -> None:
    """_require_mapping raises ConfigError if the token def is not a mapping."""
    with pytest.raises(ConfigError, match="mapping"):
        parse_matcher_config({"tokens": {"x": "not-a-dict"}, "rules": []})


def test_composite_token_def_multiple_condition_keys_raises() -> None:
    """_parse_condition raises if a composite token contains 2+ condition keys."""
    with pytest.raises(ConfigError, match="exactly one condition"):
        parse_matcher_config({"tokens": {"x": {"all": ["a"], "any": ["b"]}}, "rules": []})


def test_all_body_non_list_raises() -> None:
    """_parse_condition raises if the body of 'all' is not a list."""
    with pytest.raises(ConfigError, match="list"):
        parse_matcher_config(
            {
                "tokens": {"keroro": {"keyword": "keroro"}},
                "rules": [{"name": "r", "tier": "catalog", "all": "keroro"}],
            }
        )


def test_coverage_token_missing_min_raises() -> None:
    """_parse_token_def raises if a coverage does not declare 'min'."""
    with pytest.raises(ConfigError, match="min"):
        parse_matcher_config({"tokens": {"t": {"coverage": "title"}}, "rules": []})


def test_rule_without_name_raises() -> None:
    """_parse_rule raises if a rule has no 'name'."""
    with pytest.raises(ConfigError, match="name"):
        parse_matcher_config(
            {
                "tokens": {"keroro": {"keyword": "keroro"}},
                "rules": [{"tier": "catalog", "any": ["keroro"]}],
            }
        )


def test_rules_non_list_raises() -> None:
    """parse_matcher_config raises if 'rules' is not a list."""
    with pytest.raises(ConfigError, match="rules"):
        parse_matcher_config({"tokens": {}, "rules": "not-a-list"})


def test_token_ref_with_coverage_token_no_override_ok() -> None:
    """TokenRef on a coverage without min/fuzz override: valid, no error."""
    config = parse_matcher_config(
        {
            "tokens": {"title_hit": {"coverage": "title", "min": 0.6}},
            "rules": [{"name": "r", "tier": "catalog", "all": [{"token": "title_hit"}]}],
        }
    )
    assert config.rules[0].name == "r"


# --- Task 6: graph validation (DAG, depth, RE2 compile-check) ---


def test_unknown_token_reference_raises_and_names_it() -> None:
    with pytest.raises(UnknownTokenError, match="ghost"):
        parse_matcher_config(
            {
                "tokens": {"kt": {"any": ["keroro", "ghost"]}, "keroro": {"keyword": "keroro"}},
                "rules": [],
            }
        )


def test_unknown_token_in_rule_raises() -> None:
    with pytest.raises(UnknownTokenError, match="ghost"):
        parse_matcher_config(
            {
                "tokens": {"keroro": {"keyword": "keroro"}},
                "rules": [{"name": "r", "tier": "catalog", "all": ["keroro", "ghost"]}],
            }
        )


def test_direct_cycle_is_detected_and_named() -> None:
    with pytest.raises(CycleError) as excinfo:
        parse_matcher_config({"tokens": {"a": {"any": ["b"]}, "b": {"any": ["a"]}}, "rules": []})
    message = str(excinfo.value)
    assert "a -> b -> a" in message or "b -> a -> b" in message


def test_self_cycle_is_detected() -> None:
    with pytest.raises(CycleError, match="loop"):
        parse_matcher_config({"tokens": {"loop": {"any": ["loop"]}}, "rules": []})


def test_acyclic_composite_graph_validates() -> None:
    config = parse_matcher_config(
        {
            "tokens": {
                "keroro": {"keyword": "keroro"},
                "titar": {"keyword": "titar"},
                "kt": {"any": ["keroro", "titar"]},
                "deep": {"all": ["kt", "keroro"]},
            },
            "rules": [{"name": "r", "tier": "catalog", "any": ["deep"]}],
        }
    )
    assert "deep" in config.tokens


def test_regex_compile_check_rejects_bad_pattern() -> None:
    with pytest.raises(ConfigError, match="not compilable"):
        parse_matcher_config({"tokens": {"bad": {"regex": "(unbalanced"}}, "rules": []})


def test_regex_unknown_placeholder_rejected_at_load() -> None:
    with pytest.raises(ConfigError, match="bogus"):
        parse_matcher_config({"tokens": {"bad": {"regex": "n {bogus}"}}, "rules": []})


def test_regex_with_known_placeholders_validates() -> None:
    config = parse_matcher_config(
        {"tokens": {"seg": {"regex": "n[°o]?\\s*0*{absolute_number}\\s*{segment}"}}, "rules": []}
    )
    assert "seg" in config.tokens


def test_depth_within_bound_validates() -> None:
    # Chain a -> b -> c (depth 3) with max_depth=3: OK.
    config = parse_matcher_config(
        {
            "tokens": {
                "c": {"keyword": "x"},
                "b": {"any": ["c"]},
                "a": {"any": ["b"]},
            },
            "rules": [],
        }
    )
    validate_config(config, max_depth=3)


def test_depth_exceeded_raises() -> None:
    config = parse_matcher_config(
        {
            "tokens": {
                "c": {"keyword": "x"},
                "b": {"any": ["c"]},
                "a": {"any": ["b"]},
            },
            "rules": [],
        }
    )
    with pytest.raises(DepthExceededError, match="depth"):
        validate_config(config, max_depth=2)


def test_default_max_depth_is_32() -> None:
    # A chain of 33 tokens exceeds the default 32.
    tokens: dict[str, object] = {"t0": {"keyword": "x"}}
    for i in range(1, 34):
        tokens[f"t{i}"] = {"any": [f"t{i - 1}"]}
    with pytest.raises(DepthExceededError):
        parse_matcher_config({"tokens": tokens, "rules": []})


def test_deep_chain_root_first_caught_by_dfs_guard() -> None:
    # Root-first order: _check_acyclic's DFS descends the whole chain, so the
    # len(stack)>=max_depth guard raises DepthExceededError before any RecursionError.
    tokens: dict[str, object] = {}
    for i in range(40, 0, -1):  # t40 (root) defined first, t0 (leaf) last
        tokens[f"t{i}"] = {"any": [f"t{i - 1}"]}
    tokens["t0"] = {"keyword": "x"}
    with pytest.raises(DepthExceededError):
        parse_matcher_config({"tokens": tokens, "rules": []})


def test_empty_config_validates() -> None:
    # Empty token table: _max_resolution_depth must return 0 (default), no error.
    config = parse_matcher_config({"tokens": {}, "rules": []})
    assert config.tokens == {}


def test_coverage_override_forward_reference_in_composite_validates() -> None:
    # Regression: a composite token referencing {token: cov, min: …} where cov is
    # defined AFTER must NOT be rejected (override validation deferred to the graph).
    config = parse_matcher_config(
        {
            "tokens": {
                "combo": {"any": [{"token": "title_hit", "min": 0.4}, "keroro"]},
                "title_hit": {"coverage": "title", "min": 0.6},
                "keroro": {"keyword": "keroro"},
            },
            "rules": [],
        }
    )
    assert "combo" in config.tokens
