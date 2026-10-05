"""Load-time validation (fail-fast): parsed YAML schema -> config model.

PURE domain: receives already-parsed structures (``dict``/``list``), does not import
``yaml``, does not touch the disk. Covers spec §8.4 (load-time validation) on the schema
side + local validations (closed tier, ``attr_between`` enum, coverage-only override). Graph
validation (DAG/depth) and the regex compile-check are added in Task 6.
"""

import re
from typing import Any

from catalog_matching.config import (
    TIERS,
    AllDef,
    AnyDef,
    AttrBetweenDef,
    Condition,
    CoverageDef,
    KeywordDef,
    MatcherConfig,
    NotDef,
    Operand,
    RegexDef,
    Rule,
    TokenDef,
    TokenRef,
)
from catalog_matching.interpolation import InterpolationError, interpolate
from catalog_matching.matchers import ATTR_NAMES
from catalog_matching.models import TargetSegment

_CONDITION_KEYS = ("all", "any", "not")


class ConfigError(Exception):
    """Fatal configuration error at load time (schema, tier, enum, override)."""


class UnknownTokenError(ConfigError):
    """A reference points to a token missing from the table."""


class CycleError(ConfigError):
    """The token->token reference graph contains a cycle (the message names it)."""


class DepthExceededError(ConfigError):
    """The resolution depth exceeds the bound (default 32)."""


def _require_mapping(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{what}: mapping expected, got {type(value).__name__}")
    return value


def _require_key(mapping: dict[str, Any], key: str, what: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"{what}: key {key!r} missing")
    return mapping[key]


def _parse_operand(raw: Any) -> Operand:
    """An operand: bare token name (str), ``{token: …}`` (TokenRef), or inline condition."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        if any(key in raw for key in _CONDITION_KEYS):
            return _parse_condition(raw)
        # Treated as a token-ref (with or without the "token" key) - _parse_token_ref
        # will raise ConfigError if the "token" key is missing or invalid.
        return _parse_token_ref(raw)
    raise ConfigError(f"operand of invalid type: {type(raw).__name__} ({raw!r})")


def _require_unit_fraction(value: float, what: str) -> float:
    """Validates that a logical fraction (coverage/fuzz threshold) is in [0, 1] (fail-fast §8.4).

    Outside [0, 1], the rule is silently inert (threshold never reached): that is a config
    error (typically a percentage entered for a fraction), rejected at load time.
    """
    if not 0.0 <= value <= 1.0:
        raise ConfigError(f"{what} must be in [0, 1], got {value}")
    return value


def _parse_token_ref(raw: dict[str, Any]) -> TokenRef:
    name = raw.get("token")
    if not isinstance(name, str):
        raise ConfigError(f"operand {{token: …}} without a valid token name: {raw!r}")
    min_value = raw.get("min")
    fuzz_value = raw.get("fuzz")
    # The legality of a min/fuzz override (coverage-only, cf. EBNF §8.3) is checked at the
    # graph level (validate_config), not here: parsing stays purely structural and independent
    # of the token definition order (forward references allowed). The [0, 1] BOUNDS, on the
    # other hand, are structural → validated here.
    return TokenRef(
        name=name,
        min=(
            None
            if min_value is None
            else _require_unit_fraction(float(min_value), f"'min' override of {name!r}")
        ),
        fuzz=(
            None
            if fuzz_value is None
            else _require_unit_fraction(float(fuzz_value), f"'fuzz' override of {name!r}")
        ),
    )


def _parse_condition(raw: dict[str, Any]) -> Condition:
    present = [key for key in _CONDITION_KEYS if key in raw]
    if len(present) != 1:
        raise ConfigError(f"exactly one condition (all/any/not) expected, got {present!r}")
    key = present[0]
    body = raw[key]
    if key == "not":
        return NotDef(operand=_parse_operand(body))
    if not isinstance(body, list):
        raise ConfigError(f"'{key}:' expects a list of operands, got {type(body).__name__}")
    if not body:
        # EBNF §8.3: operand (',' operand)* = at least one operand. An empty list would give
        # AllMatcher([]).matches()==all([])==True (matches EVERYTHING) or AnyMatcher([])==False
        # (mute): degenerate config, rejected at load time (fail-fast §8.4). all([])/any([])
        # remains the legitimate internal semantics of the combinators - it is the empty CONFIG
        # that we forbid.
        raise ConfigError(f"'{key}:' requires at least one operand (empty list received)")
    operands = tuple(_parse_operand(item) for item in body)
    if key == "all":
        return AllDef(operands=operands)
    return AnyDef(operands=operands)


def _require_float(mapping: dict[str, Any], key: str) -> float | None:
    """Reads an optional float bound (``None`` if absent)."""
    value = mapping.get(key)
    return None if value is None else float(value)


def _parse_token_def(raw: Any) -> TokenDef:
    """Dispatch of a token def: composite (all/any/not) or leaf (4 types).

    Reads ALL the def's ancillary keys (``flags`` of regex, ``min``/``fuzz`` of coverage,
    ``min``/``max`` of attr_between), not only the type-key.
    """
    mapping = _require_mapping(raw, "token definition")
    if any(key in mapping for key in _CONDITION_KEYS):
        return _parse_condition(mapping)
    leaf_keys = [k for k in ("keyword", "regex", "coverage", "attr_between") if k in mapping]
    if len(leaf_keys) > 1:
        raise ConfigError(f"a leaf token has exactly one type-key, got {sorted(leaf_keys)}")
    if "keyword" in mapping:
        return KeywordDef(phrase=str(mapping["keyword"]))
    if "regex" in mapping:
        flags = mapping.get("flags", "i")
        return RegexDef(pattern=str(mapping["regex"]), flags=str(flags))
    if "coverage" in mapping:
        min_value = _require_float(mapping, "min")
        if min_value is None:
            raise ConfigError("a coverage token must declare 'min'")
        fuzz_value = _require_float(mapping, "fuzz")
        fuzz = 0.85 if fuzz_value is None else _require_unit_fraction(fuzz_value, "coverage 'fuzz'")
        return CoverageDef(
            reference=str(mapping["coverage"]),
            min=_require_unit_fraction(min_value, "coverage 'min'"),
            fuzz=fuzz,
        )
    if "attr_between" in mapping:
        attr = str(mapping["attr_between"])
        if attr not in ATTR_NAMES:
            raise ConfigError(
                f"unknown attr_between: {attr!r} (expected one of {sorted(ATTR_NAMES)})"
            )
        min_bound = _require_float(mapping, "min")
        max_bound = _require_float(mapping, "max")
        # Open bounds (min only / max only / none) are legitimate; only an explicit inverted
        # range min > max is an EMPTY range (rule mute forever) → fail-fast §8.4.
        if min_bound is not None and max_bound is not None and min_bound > max_bound:
            raise ConfigError(
                f"attr_between {attr!r}: min ({min_bound}) > max ({max_bound}) (empty range)"
            )
        return AttrBetweenDef(attr=attr, min=min_bound, max=max_bound)
    raise ConfigError(f"unknown token shape: keys {sorted(mapping)}")


def _parse_rule(raw: Any) -> Rule:
    mapping = _require_mapping(raw, "rule")
    name = str(mapping.get("name", ""))
    if not name:
        raise ConfigError(f"rule without 'name': {raw!r}")
    tier = mapping.get("tier")
    if tier not in TIERS:
        raise ConfigError(f"unknown tier for rule {name!r}: {tier!r} (expected {sorted(TIERS)})")
    present = [key for key in _CONDITION_KEYS if key in mapping]
    if not present:
        raise ConfigError(f"rule {name!r} without a condition (all/any/not)")
    if len(present) != 1:
        raise ConfigError(f"rule {name!r}: exactly one condition expected, got {present!r}")
    return Rule(name=name, tier=str(tier), condition=_parse_condition(mapping))


def parse_matcher_config(raw: dict[str, Any]) -> MatcherConfig:
    """Builds a validated (schema) :class:`MatcherConfig` from a parsed YAML dict."""
    tokens_raw = _require_mapping(raw.get("tokens", {}), "'tokens' section")
    tokens: dict[str, TokenDef] = {}
    for token_name, token_raw in tokens_raw.items():
        tokens[str(token_name)] = _parse_token_def(token_raw)
    rules_raw = raw.get("rules", [])
    if not isinstance(rules_raw, list):
        raise ConfigError(f"'rules' section: list expected, got {type(rules_raw).__name__}")
    rules = tuple(_parse_rule(rule_raw) for rule_raw in rules_raw)
    config = MatcherConfig(tokens=tokens, rules=rules)
    validate_config(config)
    return config


_DEFAULT_MAX_DEPTH = 32

# Probe target for the compile-check: provides season/seasonal_number/absolute_number/
# segment/title so that the interpolation of any RegexDef is testable at load time.
_PROBE_TARGET = TargetSegment(
    season=2,
    seasonal_number=11,
    absolute_number=62,
    segment="a",
    title="probe",
)


def _operand_refs(operand: Operand) -> tuple[str, ...]:
    """Names of tokens directly referenced by an operand (str, TokenRef or inline)."""
    if isinstance(operand, str):
        return (operand,)
    if isinstance(operand, TokenRef):
        return (operand.name,)
    if isinstance(operand, NotDef):
        return _operand_refs(operand.operand)
    # AllDef | AnyDef
    refs: list[str] = []
    for child in operand.operands:
        refs.extend(_operand_refs(child))
    return tuple(refs)


def _def_refs(token_def: TokenDef) -> tuple[str, ...]:
    """Names of tokens directly referenced by a def (empty for a leaf)."""
    if isinstance(token_def, AllDef | AnyDef):
        refs: list[str] = []
        for child in token_def.operands:
            refs.extend(_operand_refs(child))
        return tuple(refs)
    if isinstance(token_def, NotDef):
        return _operand_refs(token_def.operand)
    return ()


def _operand_token_refs(operand: Operand) -> tuple[TokenRef, ...]:
    """All TokenRefs (with their overrides) reachable from an operand."""
    if isinstance(operand, TokenRef):
        return (operand,)
    if isinstance(operand, NotDef):
        return _operand_token_refs(operand.operand)
    if isinstance(operand, AllDef | AnyDef):
        refs: list[TokenRef] = []
        for child in operand.operands:
            refs.extend(_operand_token_refs(child))
        return tuple(refs)
    return ()  # str (bare name) -> no TokenRef


def _def_token_refs(token_def: TokenDef) -> tuple[TokenRef, ...]:
    """All TokenRefs of a composite def (empty for a leaf)."""
    if isinstance(token_def, AllDef | AnyDef):
        refs: list[TokenRef] = []
        for child in token_def.operands:
            refs.extend(_operand_token_refs(child))
        return tuple(refs)
    if isinstance(token_def, NotDef):
        return _operand_token_refs(token_def.operand)
    return ()


def _check_overrides_target_coverage(config: MatcherConfig) -> None:
    """A min/fuzz override is only legal on a coverage token (cf. EBNF §8.3).

    Checked HERE (not at parsing) so as not to depend on the definition order: a forward
    reference to a coverage token must be accepted.
    """
    refs: list[TokenRef] = []
    for token_def in config.tokens.values():
        refs.extend(_def_token_refs(token_def))
    for rule in config.rules:
        refs.extend(_operand_token_refs(rule.condition))
    for ref in refs:
        if (ref.min is not None or ref.fuzz is not None) and not isinstance(
            config.tokens.get(ref.name), CoverageDef
        ):
            raise ConfigError(f"min/fuzz override forbidden on non-coverage token {ref.name!r}")


def _check_references_exist(config: MatcherConfig) -> None:
    """Every reference (in a composite token OR a rule) must exist."""
    known = set(config.tokens)
    for token_def in config.tokens.values():
        for ref in _def_refs(token_def):
            if ref not in known:
                raise UnknownTokenError(f"reference to an unknown token: {ref!r}")
    for rule in config.rules:
        for ref in _operand_refs(rule.condition):
            if ref not in known:
                raise UnknownTokenError(
                    f"rule {rule.name!r}: reference to an unknown token: {ref!r}"
                )


def _check_acyclic(config: MatcherConfig, max_depth: int) -> None:
    """Detects a cycle in the token->token graph and NAMES it (cf. spec §8.4).

    The ``len(stack) >= max_depth`` guard also bounds the recursion: a path deeper than
    ``max_depth`` is already a depth violation, raised cleanly here (avoids a Python
    ``RecursionError`` on a pathological chain).
    """
    graph = {name: _def_refs(token_def) for name, token_def in config.tokens.items()}
    visiting: set[str] = set()
    done: set[str] = set()
    stack: list[str] = []

    def walk(name: str) -> None:
        if name in done:
            return
        if name in visiting:
            cycle = stack[stack.index(name) :] + [name]
            raise CycleError(f"reference cycle: {' -> '.join(cycle)}")
        if len(stack) >= max_depth:
            tail = " -> ".join([*stack[-3:], name])
            raise DepthExceededError(f"resolution depth > {max_depth} (chain: … -> {tail})")
        visiting.add(name)
        stack.append(name)
        for ref in graph.get(name, ()):  # ref exists (checked by _check_references_exist)
            walk(ref)
        stack.pop()
        visiting.discard(name)
        done.add(name)

    for token_name in graph:
        walk(token_name)


def _max_resolution_depth(config: MatcherConfig) -> int:
    """Maximum depth of a token (leaf = 1). Assumes the graph is acyclic."""
    graph = {name: _def_refs(token_def) for name, token_def in config.tokens.items()}
    memo: dict[str, int] = {}

    def depth(name: str) -> int:
        if name in memo:
            return memo[name]
        refs = graph.get(name, ())
        result = 1 if not refs else 1 + max(depth(ref) for ref in refs)
        memo[name] = result
        return result

    return max((depth(name) for name in graph), default=0)


def _check_regexes_compile(config: MatcherConfig) -> None:
    """Each RegexDef interpolates (known placeholders) and compiles under ``re`` (cf. §8.4)."""
    for name, token_def in config.tokens.items():
        if not isinstance(token_def, RegexDef):
            continue
        try:
            pattern = interpolate(token_def.pattern, _PROBE_TARGET)
        except InterpolationError as exc:
            raise ConfigError(f"token {name!r}: invalid interpolation: {exc}") from exc
        if "i" in token_def.flags:
            pattern = "(?i)" + pattern
        try:
            re.compile(pattern, re.ASCII)
        except re.error as exc:
            raise ConfigError(f"token {name!r}: regex not compilable: {exc}") from exc


def validate_config(config: MatcherConfig, *, max_depth: int = _DEFAULT_MAX_DEPTH) -> None:
    """Validates the graph (references, DAG, depth) and the regexes (cf. spec §8.4).

    Raises :class:`UnknownTokenError`, :class:`CycleError`, :class:`DepthExceededError`
    or :class:`ConfigError` (regex/interpolation). To be called after schema parsing.
    """
    _check_references_exist(config)
    _check_overrides_target_coverage(config)
    _check_acyclic(config, max_depth)
    depth = _max_resolution_depth(config)
    if depth > max_depth:
        raise DepthExceededError(
            f"resolution depth {depth} > max {max_depth} (default {_DEFAULT_MAX_DEPTH})"
        )
    _check_regexes_compile(config)


def parse_targets(raw: dict[str, Any]) -> tuple[TargetSegment, ...]:
    """Builds the :class:`TargetSegment`s from parsed ``targets.yaml`` (cf. spec §7)."""
    episodes = raw.get("episodes")
    if not isinstance(episodes, list):
        raise ConfigError("'episodes' section: list expected")
    segments: list[TargetSegment] = []
    for episode in episodes:
        ep = _require_mapping(episode, "episode")
        season = int(_require_key(ep, "season", "episode"))
        seasonal_number = int(_require_key(ep, "seasonal_number", "episode"))
        absolute_number = int(_require_key(ep, "absolute_number", "episode"))
        seg_list = ep.get("segments", [])
        for seg in seg_list:
            seg_map = _require_mapping(seg, "segment")
            segments.append(
                TargetSegment(
                    season=season,
                    seasonal_number=seasonal_number,
                    absolute_number=absolute_number,
                    segment=str(_require_key(seg_map, "letter", "segment")),
                    title=str(_require_key(seg_map, "title", "segment")),
                    status=str(seg_map.get("status", "lost")),
                )
            )
    result = tuple(segments)
    seen: set[str] = set()
    for segment in result:
        if segment.target_id in seen:
            raise ConfigError(
                f"duplicate target_id: {segment.target_id!r}. Target segments must be "
                f"unique (note: the segment letter is uppercased by target_id, so 'a' and 'A' "
                f"collide). The evaluation engine depends on it (deterministic tie-break)."
            )
        seen.add(segment.target_id)
    return result
