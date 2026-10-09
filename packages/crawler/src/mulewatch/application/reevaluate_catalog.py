"""Backfill use-case: re-evaluate the whole catalogue against the current matcher (spec §7).

APPLICATION layer. Iterates every catalogued hash's LATEST observation
(``catalog.iter_reevaluation_rows()``), rebuilds a :class:`FileCandidate` via
``candidate_from_fields`` (the single conversion, spec re-evaluation §6), and delegates to
the shared ``record_decision_if_changed`` helper (``decisions.py``, which judges the hash on
all its known names) so retraction + notify/download nudges happen identically to the live
per-observation path (spec §4). Only the counting + per-row error absorption are specific
to the backfill.

A per-row ``RepositoryError`` is logged (error level) and absorbed here: one bad file must
not abort the whole sweep (spec §7, same discipline as ``record_observation``).
"""

import logging
from dataclasses import dataclass

from catalog_matching.engine import MatchingEngine
from mulewatch.application.decisions import record_decision_if_changed
from mulewatch.domain.observation import candidate_from_fields
from mulewatch.ports.catalog_repository import CatalogRepository
from mulewatch.ports.decision_signal import DecisionSignal
from mulewatch.ports.repository_errors import RepositoryError
from mulewatch.ports.telemetry import Telemetry

_logger = logging.getLogger("mulewatch.application.reevaluate_catalog")

# One INFO progress line per this many files, so a long sweep visibly advances.
_PROGRESS_EVERY = 200


@dataclass(frozen=True)
class ReevalSummary:
    """Outcome of one backfill pass (spec §7).

    ``evaluated``: rows iterated (one per catalogued hash). ``written``: rows actually
    appended by ``record_decision_if_changed`` (a re-tiered decision OR a retraction -
    no separate retracted count, spec Task 5: "a retraction is just a written row").
    """

    evaluated: int
    written: int


async def reevaluate_catalog(
    *,
    catalog: CatalogRepository,
    engine: MatchingEngine,
    signal: DecisionSignal,
    telemetry: Telemetry,
) -> ReevalSummary:
    """Re-evaluate every catalogued hash's latest observation against ``engine``.

    Per-item isolation (spec §7): a ``RepositoryError`` on one row is logged and the sweep
    continues with the next row.
    """
    total = catalog.count_files()
    evaluated = 0
    written = 0
    for row in catalog.iter_reevaluation_rows():
        evaluated += 1
        candidate = candidate_from_fields(
            row.filename, row.size_bytes, row.media_length_sec, row.bitrate_kbps
        )
        try:
            written += await record_decision_if_changed(
                row.file,
                candidate,
                catalog=catalog,
                engine=engine,
                signal=signal,
                telemetry=telemetry,
            )
        except RepositoryError as error:
            _logger.error(
                "persistence failed on %s:%s (%s): re-evaluation skipped, sweep continues",
                row.file.network,
                row.file.native_id,
                error,
            )
        if evaluated % _PROGRESS_EVERY == 0:
            _logger.info(
                "catalogue re-evaluation: %d/%d files, %d rows written", evaluated, total, written
            )
    return ReevalSummary(evaluated=evaluated, written=written)
