"""One task per (client, channel, keyword), each searching again as soon as it may: the client
paces its own starts, the core sets no interval (spec stage 2, D4).

The backoff is saved after a search whenever it differs from what was last saved (D7).
"""

import asyncio
import logging
from collections.abc import Sequence

from mulewatch.application.search_worker import BackoffRegistry, SearchTask, SearchWorker
from mulewatch.domain.search.keywords import generate_keywords
from mulewatch.ports.clock import Clock
from mulewatch.ports.repository_errors import RepositoryError
from mulewatch.ports.scheduler_state_repository import SchedulerStateRepository

_logger = logging.getLogger("mulewatch.application.search_tasks")


async def run_search_tasks(
    *,
    workers: Sequence[SearchWorker],
    keywords: Sequence[str],
    resumed: asyncio.Event,
    backoff: BackoffRegistry,
    scheduler_state: SchedulerStateRepository,
    clock: Clock,
) -> None:
    """Runs every task until cancelled."""
    saved = backoff.snapshot()

    def save() -> None:
        nonlocal saved
        current = backoff.snapshot()
        if current == saved:
            return
        try:
            scheduler_state.save_channel_backoff(current)
        except RepositoryError as error:
            _logger.error("backoff not saved (%s): retried after the next search", error)
            return
        saved = current

    async def search_forever(worker: SearchWorker, task: SearchTask) -> None:
        while True:
            await resumed.wait()
            wait = worker.seconds_until_ready(task.channel)
            if wait > 0:
                await clock.sleep(wait)
                continue
            await worker.run_task(task)
            save()
            await asyncio.sleep(0)  # a search that never suspends must not starve the loop

    texts = [keyword.text for keyword in generate_keywords(keywords)]
    async with asyncio.TaskGroup() as group:
        for worker in workers:
            for channel in worker.channels:
                for text in texts:
                    group.create_task(search_forever(worker, SearchTask(text, channel)))
