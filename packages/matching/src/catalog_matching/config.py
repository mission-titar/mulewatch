"""Matching-engine config model — tagged union of frozen dataclasses.

Represents the §8.3 grammar (named leaf + composite tokens, operands at point of use,
rules). PURE domain: no I/O, no YAML library import. Construction from parsed YAML
(dict/list) is done by ``validation.py``.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

# --- LEAF token defs (4 types, cf. spec §8.2) ---


@dataclass(frozen=True)
class KeywordDef:
    """``{ keyword: "mission titar" }`` — phrase searched as a contiguous subsequence."""

    phrase: str


@dataclass(frozen=True)
class RegexDef:
    """``{ regex: "...", flags: "i" }`` — RE2 pattern interpolated then compiled per target."""

    pattern: str
    flags: str = "i"


@dataclass(frozen=True)
class CoverageDef:
    """``{ coverage: title, min: 0.6, fuzz: 0.85 }`` — fuzzy token coverage.

    ``reference`` is the config keyword (``title`` = the target's title); bound to
    ``target.title`` at resolution. ``min``/``fuzz`` overridable at point of use.
    """

    reference: str
    min: float
    fuzz: float = 0.85


@dataclass(frozen=True)
class AttrBetweenDef:
    """``{ attr_between: size_mb, min: 30, max: 600 }`` — bound on a numeric attribute."""

    attr: str
    min: float | None = None
    max: float | None = None


# --- COMPOSITE defs / conditions (cf. spec §8.3: all/any/not) ---
# Reused in three contexts: named composite token, rule body, inline condition
# `{condition}`. Operands mix bare names, TokenRef, and inline conditions.

# PEP 695 type alias (`type ...`), lazily evaluated: referencing
# TokenRef/AllDef/AnyDef/NotDef — defined below and mutually recursive — is therefore
# valid, and mypy --strict resolves the recursion.
type Operand = str | TokenRef | AllDef | AnyDef | NotDef


@dataclass(frozen=True)
class AllDef:
    """``all: [operand, ...]`` — conjunction."""

    operands: tuple[Operand, ...]


@dataclass(frozen=True)
class AnyDef:
    """``any: [operand, ...]`` — disjunction."""

    operands: tuple[Operand, ...]


@dataclass(frozen=True)
class NotDef:
    """``not: operand`` — negation of a single operand."""

    operand: Operand


@dataclass(frozen=True)
class TokenRef:
    """Operand at point of use ``{ token: name, min?, fuzz? }`` (cf. EBNF §8.3).

    References a named token; non-null ``min``/``fuzz`` override the parameters of a
    ``coverage`` token (and ONLY coverage — validated at load time).
    """

    name: str
    min: float | None = None
    fuzz: float | None = None


# Union of token defs nameable in the `tokens` table.
TokenDef = KeywordDef | RegexDef | CoverageDef | AttrBetweenDef | AllDef | AnyDef | NotDef

# A rule body / inline operand `{condition}` is a composite condition.
Condition = AllDef | AnyDef | NotDef

# Closed set of tiers (cf. spec §8.3 EBNF: tier).
TIERS: frozenset[str] = frozenset({"catalog", "notify", "download"})

# Tier rank (spec §8.5: "highest tier, download>notify>catalog"). Higher integer =
# STRONGER tier. Source of truth shared by ``engine.MatchingEngine`` (best-decision
# selection) AND ``mulewatch.webui.domain.coverage`` (display: strongest tier → best
# coverage). Without this sharing, the webui reinvented its own rank with a divergent
# convention — a 4th tier or a rename would have silently skewed the display.
# Tested invariant: ``set(TIER_RANK) == TIERS``.
TIER_RANK: dict[str, int] = {"catalog": 0, "notify": 1, "download": 2}


@dataclass(frozen=True)
class Rule:
    """Ordered rule: ``{ name, tier, <condition> }`` (cf. spec §8.3)."""

    name: str
    tier: str
    condition: Condition


@dataclass(frozen=True)
class MatcherConfig:
    """Validated matcher config: table of named tokens + ordered rules."""

    tokens: Mapping[str, TokenDef] = field(default_factory=dict)
    rules: tuple[Rule, ...] = ()
