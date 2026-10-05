"""Matching EVALUATION engine (cf. spec §8.5, evaluation part).

PURE domain. Takes an already-validated :class:`MatcherConfig` (Plan 2b) and some
:class:`TargetSegment`s, pre-resolves the per-target matcher trees once at construction
(via :class:`MatcherResolver`), then produces an IN-MEMORY decision for a
:class:`FileCandidate`. NO I/O, NO logging, NO DB: the "explainability logged at DEBUG" of
§8.5 = the engine RETURNS an explainable result; logging is the job of an adapter in a
later plan.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from catalog_matching.config import TIER_RANK, MatcherConfig
from catalog_matching.matchers import CoverageMatcher
from catalog_matching.models import FileCandidate, TargetSegment
from catalog_matching.resolver import MatcherResolver, ResolvedTarget


@dataclass(frozen=True)
class Explanation:
    """Why this decision (cf. spec §8.5: fired tokens/rules + coverage value).

    Concerns the SINGLE winning target. ``rules_fired``: names of the rules true for this
    target, in config order (the 1st is the winner). ``tokens_matched``: names of the
    config's named tokens that match (sorted). ``coverage_values``: for EACH coverage token
    of the config (whether it matched or not), ``(name, value(candidate))`` sorted — the
    score helps debug a threshold even below the bar. Tuples (not dicts) to stay
    FROZEN/hashable and deterministic.
    """

    target_id: str
    rules_fired: tuple[str, ...]
    tokens_matched: tuple[str, ...]
    coverage_values: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class MatchDecision:
    """File decision (cf. spec §8.5). Carries the 3 match_decisions columns (§11).

    ``target_id``/``rule_name``/``tier`` = exactly the columns that ``match_decisions``
    will persist (§11). ``decided_at``/``node_id``/``ed2k_hash`` are NOT here: they are
    persistence columns (clock + identity + content key) injected by the DB adapter of a
    later plan. ``explanation`` carries the explainability (§8.5).
    """

    target_id: str
    rule_name: str
    tier: str
    explanation: Explanation


@dataclass(frozen=True)
class DecisionRecord:
    """The 3 COMPARABLE columns of a persisted decision, without the runtime explainability.

    This is exactly what ``match_decisions`` stores (§11) — ``target_id``/``rule_name``/
    ``tier`` — read back for anti-redundancy (orchestration spec §3: only re-``record_decision``
    if the verdict CHANGES). Deliberately distinct from :class:`MatchDecision`: the read
    cannot reconstruct the ``explanation`` (not persisted), and two ``DecisionRecord``s are
    equal iff their three fields are equal (frozen dataclass → field-by-field ``==``).
    """

    target_id: str
    rule_name: str
    tier: str


def to_record(decision: MatchDecision) -> DecisionRecord:
    """Projects a :class:`MatchDecision` (just reached) onto its comparable form.

    Lets the application compare the FRESH verdict against the last known ``DecisionRecord``
    without handling the ``explanation`` (orchestration spec §3, anti-redundancy).
    """
    return DecisionRecord(
        target_id=decision.target_id, rule_name=decision.rule_name, tier=decision.tier
    )


@dataclass(frozen=True)
class DownloadCandidate:
    """READ form of a tier=download decision: ``ed2k_hash`` + ``target_id``.

    This is what ``CatalogRepository.download_decisions`` returns (download spec §5): the
    hashes whose LATEST verdict is tier=download, to be replayed by the download loop.
    Distinct from :class:`MatchDecision`/:class:`DecisionRecord`: the download loop only
    needs the hash (content key) and the ``target_id`` (for the target status lookup).
    Frozen → trivial value comparison in tests.
    """

    ed2k_hash: str
    target_id: str


# Re-export of the tier rank (source of truth ``catalog_matching.config.TIER_RANK``, shared
# with ``mulewatch.webui.domain.coverage``). The internal name stays ``_TIER_RANK`` so as not
# to break historical imports on the internal-test side.
_TIER_RANK = TIER_RANK


# Rule-name sets driving the multi-target fan-out (spec §4). A rule is ATTRIBUTABLE when its
# win pins the file to a concrete target via a number/title video signal. SEGMENT_LEVEL rules
# pin one specific segment (a title, or a lettered number); EPISODE_LEVEL rules designate the
# whole episode (a bare number) and thus every one of its segments. ATTRIBUTABLE is exactly
# their union.
_SEGMENT_LEVEL: frozenset[str] = frozenset({"id_segment_exact", "title_confirmed", "title_review"})
_EPISODE_LEVEL: frozenset[str] = frozenset({"numero_nu_confirmed", "numero_nu"})
_ATTRIBUTABLE: frozenset[str] = _SEGMENT_LEVEL | _EPISODE_LEVEL

# (resolved target, rule index, rule name, tier, the name that fired it)
_Match = tuple[ResolvedTarget, int, str, str, FileCandidate]


def _first_matching_rule(
    config: MatcherConfig,
    resolved: ResolvedTarget,
    candidate: FileCandidate,
) -> tuple[int, str, str] | None:
    """1st rule true for (candidate, resolved target) → ``(index, name, tier)`` (§8.5).

    Walks ``config.rules`` IN ORDER (index = position = priority); for each rule, evaluates
    the already-built tree ``resolved.rules[rule.name]``. Returns the 1st match; ``None`` if
    no rule matches (the target contributes nothing).
    """
    for index, rule in enumerate(config.rules):
        if resolved.rules[rule.name].matches(candidate):
            return (index, rule.name, rule.tier)
    return None


def _explain(
    config: MatcherConfig,
    resolved: ResolvedTarget,
    candidate: FileCandidate,
) -> Explanation:
    """Builds the explanation of the resolved target ``resolved`` (cf. spec §8.5).

    ``rules_fired``: rules true in config order. ``tokens_matched``: named tokens that match
    (sorted). ``coverage_values``: ``(name, value)`` of the coverage tokens (sorted). Reads
    ``CoverageMatcher.value()`` (outside the Protocol) via ``isinstance``.
    """
    rules_fired = tuple(
        rule.name for rule in config.rules if resolved.rules[rule.name].matches(candidate)
    )
    tokens_matched = tuple(
        sorted(name for name, matcher in resolved.tokens.items() if matcher.matches(candidate))
    )
    coverage_values = tuple(
        (name, matcher.value(candidate))
        for name, matcher in sorted(resolved.tokens.items())
        if isinstance(matcher, CoverageMatcher)
    )
    return Explanation(
        target_id=resolved.target.target_id,
        rules_fired=rules_fired,
        tokens_matched=tokens_matched,
        coverage_values=coverage_values,
    )


class MatchingEngine:
    """Pure facade of the evaluation engine (cf. spec §8.5). Pre-resolves targets once.

    Brute-force §8.5: each file is evaluated against ALL targets (no funnel heuristic). The
    matcher trees (regexes interpolated+compiled per target) are built ONCE at construction.
    ``max_filename_length`` bounds the filename length before matching (§8.5/§14): a longer
    name is discarded (``[]``).
    """

    def __init__(
        self,
        config: MatcherConfig,
        targets: Sequence[TargetSegment],
        *,
        max_filename_length: int = 4096,
    ) -> None:
        self._config = config
        self._max_filename_length = max_filename_length
        resolver = MatcherResolver(config)
        self._resolved: tuple[ResolvedTarget, ...] = tuple(
            resolver.resolve_all(target) for target in targets
        )
        self._resolved_by_target: dict[str, ResolvedTarget] = {
            r.target.target_id: r for r in self._resolved
        }

    def explain(self, candidate: FileCandidate, target_id: str) -> Explanation | None:
        """Explains the match of ``candidate`` against target ``target_id`` (current config).

        ``None`` if ``target_id`` is unknown to the config. Otherwise an ``Explanation``
        (empty if no rule fires). Reuses the per-target resolved matcher tree.
        """
        resolved = self._resolved_by_target.get(target_id)
        if resolved is None:
            return None
        return _explain(self._config, resolved, candidate)

    def evaluate(self, candidate: FileCandidate) -> list[MatchDecision]:
        """All decisions for ``candidate`` (spec §4); ``[]`` = file discarded."""
        return self.evaluate_all((candidate,))

    def evaluate_all(self, candidates: Iterable[FileCandidate]) -> list[MatchDecision]:
        """All decisions for ONE file known under every name in ``candidates``; ``[]`` = discard.

        Per target, the best rule over all names (min-key below, over-long names skipped).
        The attributable matches (number/title video rules) fan out per episode: a
        segment-level signal on any segment of an episode emits only those segments,
        otherwise the episode-level signal emits every segment (spec §3). With no
        attributable match, the single-winner min-key over ALL matches yields one catch-all
        decision (the ``keroro_large`` catalog row or an ``archive_candidate`` row), or ``[]``.
        """
        best: dict[str, _Match] = {}
        for candidate in candidates:
            if len(candidate.filename) > self._max_filename_length:
                continue
            for resolved in self._resolved:
                outcome = _first_matching_rule(self._config, resolved, candidate)
                if outcome is None:
                    continue
                index, rule_name, tier = outcome
                target_id = resolved.target.target_id
                current = best.get(target_id)
                if current is None or (-_TIER_RANK[tier], index) < (
                    -_TIER_RANK[current[3]],
                    current[1],
                ):
                    best[target_id] = (resolved, index, rule_name, tier, candidate)
        matches = list(best.values())
        attributable = self._fan_out(matches)
        if attributable:
            return attributable
        return self._single_winner(matches)

    def _fan_out(self, matches: list[_Match]) -> list[MatchDecision]:
        """Selects the emitted segments from the attributable matches (spec §3/§4)."""
        by_episode: dict[int, list[_Match]] = {}
        for entry in matches:
            if entry[2] not in _ATTRIBUTABLE:
                continue
            by_episode.setdefault(entry[0].target.absolute_number, []).append(entry)
        emitted: list[_Match] = []
        for group in by_episode.values():
            segment_level = [entry for entry in group if entry[2] in _SEGMENT_LEVEL]
            emitted.extend(segment_level or group)
        emitted.sort(key=lambda entry: entry[0].target.target_id)
        return [self._decision(entry) for entry in emitted]

    def _single_winner(self, matches: list[_Match]) -> list[MatchDecision]:
        """Existing min-key: one catch-all decision over ALL matches, or ``[]`` (spec §4 step 6).

        Key = (highest tier, smallest rule index, smallest ``target_id``); ``target_id`` is
        unique, so the key is a strict total order -> a unique, target-order-independent winner.
        """
        best: tuple[int, int, str] | None = None
        best_entry: _Match | None = None
        for entry in matches:
            resolved, index, _rule_name, tier, _candidate = entry
            key = (-_TIER_RANK[tier], index, resolved.target.target_id)
            if best is None or key < best:
                best = key
                best_entry = entry
        if best_entry is None:
            return []
        return [self._decision(best_entry)]

    def _decision(self, entry: _Match) -> MatchDecision:
        resolved, _index, rule_name, tier, candidate = entry
        return MatchDecision(
            target_id=resolved.target.target_id,
            rule_name=rule_name,
            tier=tier,
            explanation=_explain(self._config, resolved, candidate),
        )
