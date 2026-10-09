"""Per-observation pipeline: record → delegate to the shared decision helper (spec §4).

APPLICATION layer (orchestration spec §4): orchestrates PORTS (sync repos + pure engine
+ async nudge hub), does no I/O itself. For EACH observation (spec §4):

1. ``record_observation`` ALWAYS (periodic re-observation is the goal, spec §3/§6).
2. ``record_decision_if_changed`` (``decisions.py``) does the rest: evaluate via the pure
   engine, compare against the last known verdict, and append (or retract) + emit + nudge
   only on a genuine change. Identical verdict, or a file that was never matched and still
   isn't → no write, no emit, no nudge.

The repos are SYNCHRONOUS, called DIRECTLY (spec §3: sub-ms, no ``to_thread`` in the
MVP; accepted consequence: DB writes are de facto serialized on the event loop).
A ``RepositoryError`` (a PORT contract, never an adapter) on ONE observation is
LOGGED and ABSORBED here: the function returns ``0`` and the cycle continues (spec §7) - a
single corrupt/failed obs does not bring down the whole sweep, but the failure stays
VISIBLE (``error``-level log, so a persistent failure gets noticed).
"""

import logging

from catalog_matching.engine import MatchingEngine
from mulewatch.application.decisions import record_decision_if_changed
from mulewatch.domain.observability.events import ObservationRecorded
from mulewatch.domain.observation import FileObservation
from mulewatch.ports.catalog_repository import CatalogRepository
from mulewatch.ports.decision_signal import DecisionSignal
from mulewatch.ports.repository_errors import RepositoryError
from mulewatch.ports.telemetry import Telemetry

_logger = logging.getLogger("mulewatch.application.record_observations")


async def record_observation(
    observation: FileObservation,
    *,
    catalog: CatalogRepository,
    engine: MatchingEngine,
    signal: DecisionSignal,
    telemetry: Telemetry,
    network: str,
) -> int:
    """Process ONE observation (spec §7). Returns the number of rows written (0..N).

    Emits ``ObservationRecorded`` (always) and one ``DecisionsRecorded`` when a row was written.
    A ``RepositoryError`` is absorbed (log + ``0``), the cycle continues (spec §7)."""
    try:
        catalog.record_observation(observation)
        await telemetry.emit(ObservationRecorded(network=network))
        return await record_decision_if_changed(
            observation.file,
            observation.to_candidate(),
            catalog=catalog,
            engine=engine,
            signal=signal,
            telemetry=telemetry,
        )
    except RepositoryError as error:
        _logger.error(
            "persistence failed on %s:%s (%s): observation skipped, cycle continues",
            observation.file.network,
            observation.file.native_id,
            error,
        )
        return 0
