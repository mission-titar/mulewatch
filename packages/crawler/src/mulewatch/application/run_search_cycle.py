"""One search cycle: status → coverage → keywords → fan-out → drain → advance (§4).

APPLICATION layer. ``run_search_cycle`` runs ONE cycle (spec §4):

  1. ``network_status`` of EACH worker → aggregated ``effective_coverage`` (logged).
  2. ``generate_keywords(config_keywords)`` → sentinels; ``shuffle_for_cycle`` (seed =
     node_id + cycle index).
  3. enqueue a ``SearchTask`` (keyword × channel) into a shared ``asyncio.Queue``.
  4. N workers drain in parallel (one per instance); one sentinel per worker.
  5. queue drained → ``write_cycle_state`` (index = N+1, last_full_cycle_at) AND
     ``save_channel_backoff`` (snapshot of the SHARED registry) - AT THE SAME TIME (spec §3/§7).

The pool degenerates to a sequential loop at N=1 (spec §3). The workers share the
queue; each reads until the ``None`` sentinel. The ``TaskGroup`` supervises: a
cancellation (shutdown, spec §6) lands at the next network ``await``, never mid DB
write (sync repos). The backoff (shared registry, mutated by the workers during
the cycle) is PERSISTED only at the END of the cycle, exactly like ``cycle_index``: a kill
mid-way replays the cycle AND re-arms the backoff from the previous cycle's state (consistent -
the index does not advance mid-cycle either, spec §7).

NEVER RAISES (aligned with ``run_download_cycle``): a
``RepositoryError`` on the end-of-cycle writes (``write_cycle_state``/``save_channel_backoff``)
is ABSORBED → log + return without advancing the index; the cycle will be replayed. Without this
net, the exception propagates out of the supervising ``TaskGroup`` which cancels ALL sibling
loops (download/port-sync) → app crash on a transient persistence failure.
"""

import asyncio
import logging
from collections.abc import Sequence

from mulewatch.application.edge_state import EdgeState
from mulewatch.application.search_worker import BackoffRegistry, SearchTask, SearchWorker
from mulewatch.domain.observability.events import (
    AllInstancesBlind,
    ConnectedInstancesSampled,
    SearchCapabilitySampled,
    SearchCycleCompleted,
)
from mulewatch.domain.search.coverage import Coverage, effective_coverage
from mulewatch.domain.search.cycle import Rng, shuffle_for_cycle
from mulewatch.domain.search.keywords import generate_keywords
from mulewatch.ports.client_errors import ClientUnreachableError
from mulewatch.ports.clock import Clock
from mulewatch.ports.mule_client import MuleClient
from mulewatch.ports.port_sync import KadStatus
from mulewatch.ports.repository_errors import RepositoryError
from mulewatch.ports.scheduler_state_repository import SchedulerStateRepository
from mulewatch.ports.telemetry import Telemetry

_logger = logging.getLogger("mulewatch.application.run_search_cycle")


def _is_search_capable(*, ed2k_high: bool, kad_status: KadStatus) -> bool:
    """Can an instance make a search SUCCEED? (HighID OR Kad CONNECTED).

    APPLICATION translation of ``NetworkStatus`` (port) into a pure boolean, before calling the
    domain ``effective_coverage`` (which does not know ``NetworkStatus`` - dependency rule, the
    domain never imports a port).
    """
    return ed2k_high or kad_status == KadStatus.CONNECTED


async def _aggregate_coverage(
    clients: Sequence[MuleClient], telemetry: Telemetry, edge: EdgeState
) -> None:
    """Samples connected{network} + search-capable gauges + aggregated coverage (logged, §7)."""
    capable: list[bool] = []
    ed2k_count = 0
    kad_count = 0
    for client in clients:
        # An instance unreachable at sampling time (EC stream dead / not yet connected) must NOT
        # bring down the whole cycle: we count it as NOT search-capable (the aggregate will then
        # report BLIND/DEGRADED, the true state). We do NOT (re)connect here - the worker
        # owns the connection cycle and its anti-ban backoff (reconnecting every cycle would
        # hammer a down daemon and short-circuit that backoff, spec §3/§7).
        try:
            status = await client.network_status()
        except ClientUnreachableError as error:
            _logger.warning("instance unreachable at status readout (%s): not capable", error)
            capable.append(False)
            continue
        if status.ed2k_high:
            ed2k_count += 1
        if status.kad_status == KadStatus.CONNECTED:
            kad_count += 1
        capable.append(_is_search_capable(ed2k_high=status.ed2k_high, kad_status=status.kad_status))
    await telemetry.emit(ConnectedInstancesSampled(network="ed2k", count=ed2k_count))
    await telemetry.emit(ConnectedInstancesSampled(network="kad", count=kad_count))
    coverage = effective_coverage(capable)
    # Current-state binary gauge, sampled EVERY cycle (independent of the edge-triggered
    # AllInstancesBlind notification below): 1 when we can search now, 0 when all blind.
    await telemetry.emit(SearchCapabilitySampled(capable=coverage != Coverage.BLIND))
    if coverage == Coverage.BLIND:
        _logger.warning("effective_coverage=%s (blind)", coverage)
        await telemetry.emit(AllInstancesBlind(first_occurrence=edge.enter("coverage_blind")))
    else:
        _logger.info("effective_coverage=%s (%d instance(s))", coverage, len(capable))
        edge.leave("coverage_blind")


async def _worker_loop(
    worker: SearchWorker,
    queue: "asyncio.LifoQueue[SearchTask | None]",
    n_workers: int,
) -> None:
    """Drains the queue until the ``None`` sentinel (one worker).

    JITTERED inter-keyword PAUSE (spec §5/§7) BETWEEN two items, NEVER after the last:
    if the queue is already empty after this item, we skip the pause (no point sleeping before
    exiting / waiting for a sentinel). The pause spaces out one worker's searches
    → ``amuled`` avoids getting banned from an eD2k server.

    SKIP ⇒ RE-ENQUEUE (spec §14 "NO LOSS", logic-search#0): if the pulled task hits
    a backoff key of THIS worker (instance OR channel), it is PUT BACK on the queue
    (with self added to ``skipped_by``) then the event loop is yielded via ``asyncio.sleep(0)``
    so a healthy peer takes it. When ALL workers have refused (``len(skipped_by) >=
    n_workers``), the task is dropped with ``SearchTaskDropped`` (visibility rather than
    silence). Without this logic, a backed-off worker would synchronously drain the remaining
    tasks while a healthy peer stayed parked on a network ``await`` (queue.get on a
    non-empty queue does NOT YIELD - no internal ``await``).
    """
    while True:
        task = await queue.get()
        try:
            if task is None:
                return
            if worker.is_blocked_for(task):
                # Idempotent union: re-picking the same task by oneself does not grow
                # ``skipped_by`` (frozenset). With LIFO the re-enqueue is on top →
                # picked by a PEER next round → the set grows until the drop.
                new_skipped = task.skipped_by | {worker.instance_name}
                if len(new_skipped) >= n_workers:
                    await worker.report_dropped(task)
                    continue
                queue.put_nowait(
                    SearchTask(keyword=task.keyword, channel=task.channel, skipped_by=new_skipped)
                )
                await asyncio.sleep(0)  # yield → a peer can take the task
                continue
            await worker.run_task(task)
            if not queue.empty():  # still real items → we space out before the next
                await worker.pause_between_items()
        finally:
            queue.task_done()


async def run_search_cycle(
    *,
    workers: Sequence[SearchWorker],
    clients: Sequence[MuleClient],
    keywords: Sequence[str],
    rng: Rng,
    node_id: str,
    cycle_index: int,
    scheduler_state: SchedulerStateRepository,
    backoff: BackoffRegistry,
    clock: Clock,
    telemetry: Telemetry,
    edge: EdgeState,
) -> None:
    """Runs ONE full cycle (spec §4); persists the advance + backoff at the END (spec §7)."""
    started = clock.now()
    await _aggregate_coverage(clients, telemetry, edge)
    generated = generate_keywords(keywords)
    texts = tuple(keyword.text for keyword in generated)
    ordered = shuffle_for_cycle(texts, rng, node_id, cycle_index)
    channels = tuple(dict.fromkeys(channel for worker in workers for channel in worker.channels))
    # LIFO (logic-search#0): a task re-enqueued by a backed-off worker must be
    # immediately available to a PEER (not re-pulled by the same worker via FIFO →
    # infinite loop when all instances are backed off). With LIFO, the re-enqueue
    # is on top → the next worker takes it → or they all refused it and we DROP.
    queue: asyncio.LifoQueue[SearchTask | None] = asyncio.LifoQueue()
    for text in ordered:
        for channel in channels:
            queue.put_nowait(SearchTask(keyword=text, channel=channel))
    _logger.info(
        "cycle %d: %d keyword(s) × %d channels = %d task(s)",
        cycle_index,
        len(ordered),
        len(channels),
        queue.qsize(),
    )
    n_workers = len(workers)
    async with asyncio.TaskGroup() as group:
        for worker in workers:
            group.create_task(_worker_loop(worker, queue, n_workers))
        await queue.join()
        for _ in workers:
            queue.put_nowait(None)
    # END of cycle: index AND backoff persisted TOGETHER (spec §3/§7). A RepositoryError
    # here is ABSORBED (error-boundary#1) → the index does not advance, the cycle will be
    # replayed next round (append-only state, no corruption). Without this net, the exception
    # propagates out of the supervising TaskGroup which cancels ALL sibling loops → app
    # crash on a transient persistence failure. Aligned with run_download ("NEVER RAISES").
    try:
        scheduler_state.write_cycle_state(cycle_index + 1, clock.now())
        scheduler_state.save_channel_backoff(backoff.snapshot())
    except RepositoryError as error:
        _logger.error("cycle %d end repo failure (%s): cycle replayable", cycle_index, error)
        return
    duration = (clock.now() - started).total_seconds()
    await telemetry.emit(SearchCycleCompleted(cycle_index=cycle_index, duration_seconds=duration))
    _logger.info("cycle %d done", cycle_index)
