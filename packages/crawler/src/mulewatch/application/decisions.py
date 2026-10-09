"""Shared decision helper: evaluate → set-diff → record / retract → nudge → emit (spec §7).

APPLICATION layer, PURE orchestration (no ``try/except`` here: a ``RepositoryError`` is a
port contract each CALLER absorbs on its own terms, cf. ``record_observation`` and the backfill
use-case). Used by BOTH the per-observation pipeline (``record_observations.py``) and the
startup catalogue re-evaluation, so the set-diff + retraction + nudge logic is written once.

Set diff keyed by ``(file, target_id)`` (spec §7). The file is judged on EVERY name the
catalog knows for it (``engine.evaluate_all``, spec amuleapi-migration §8.4), so its names no
longer retract each other. A fresh decision is persisted (and nudged) only when it differs from
the file's LATEST persisted :class:`DecisionRecord` for THAT target; a target that dropped out
of the fresh set is retracted, unless it is already retracted (no-op). Returns the number of
rows written (0..N; a decision OR a retraction each counts as one). The rows written are emitted
once, as one ``DecisionsRecorded`` for the file (spec researcher-notifications §2).
"""

from dataclasses import replace

from catalog_matching.engine import MatchingEngine, to_record
from catalog_matching.models import FileCandidate
from mulewatch.application.run_download_cycle import DOWNLOAD_NUDGE_SUBJECT
from mulewatch.domain.file_key import FileKey
from mulewatch.domain.observability.events import DecisionChange, DecisionsRecorded
from mulewatch.domain.retraction import RETRACTED_TIER
from mulewatch.ports.catalog_repository import CatalogRepository, ObservedFile
from mulewatch.ports.decision_signal import DecisionSignal
from mulewatch.ports.telemetry import Telemetry


async def record_decision_if_changed(
    file: FileKey,
    candidate: FileCandidate,
    *,
    catalog: CatalogRepository,
    engine: MatchingEngine,
    signal: DecisionSignal,
    telemetry: Telemetry,
) -> int:
    """Judge the file on all its known names (``candidate`` gives the other fields).

    Returns the number of rows written (0..N). May propagate ``RepositoryError`` (pure
    orchestration; the caller absorbs it)."""
    names = catalog.known_filenames(file)
    fresh = engine.evaluate_all(replace(candidate, filename=name) for name in names)
    persisted = catalog.last_decisions(file)
    changes: list[DecisionChange] = []

    def change(target_id: str, after: str) -> DecisionChange:
        target = engine.target(target_id)
        before = persisted.get(target_id)
        return DecisionChange(
            target_id,
            "" if target is None else target.title,
            None if before is None else before.tier,
            after,
        )

    fresh_ids: set[str] = set()
    for decision in fresh:
        fresh_ids.add(decision.target_id)
        if persisted.get(decision.target_id) == to_record(decision):
            continue
        catalog.record_decision(file, decision)
        changes.append(change(decision.target_id, decision.tier))
        if decision.tier == "download":
            signal.signal(DOWNLOAD_NUDGE_SUBJECT)
    for target_id in sorted(persisted):
        if persisted[target_id].tier == RETRACTED_TIER or target_id in fresh_ids:
            continue
        catalog.record_retraction(file, target_id)
        changes.append(change(target_id, RETRACTED_TIER))
    if changes:
        # No name left means only retractions were written, which are never notified.
        best = catalog.best_observation(file) or ObservedFile(candidate.filename, 0)
        await telemetry.emit(
            DecisionsRecorded(file.native_id, best.filename, best.size_bytes, tuple(changes))
        )
    return len(changes)
