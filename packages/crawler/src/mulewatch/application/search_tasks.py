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


class SearchTasks:
    """The backoff last saved is the state the tasks share."""

    def __init__(
        self,
        *,
        workers: Sequence[SearchWorker],
        keywords: Sequence[str],
        resumed: asyncio.Event,
        backoff: BackoffRegistry,
        scheduler_state: SchedulerStateRepository,
        clock: Clock,
    ) -> None:
        self._workers = workers
        self._keywords = keywords
        self._resumed = resumed
        self._backoff = backoff
        self._scheduler_state = scheduler_state
        self._clock = clock
        self._saved = backoff.snapshot()

    async def run(self) -> None:
        """Runs every task until cancelled."""
        texts = [keyword.text for keyword in generate_keywords(self._keywords)]
        async with asyncio.TaskGroup() as group:
            for worker in self._workers:
                for channel in worker.channels:
                    for text in texts:
                        group.create_task(self._search_forever(worker, SearchTask(text, channel)))

    async def _search_forever(self, worker: SearchWorker, task: SearchTask) -> None:
        while True:
            await self._resumed.wait()
            wait = worker.seconds_until_ready(task.channel)
            if wait > 0:
                await self._clock.sleep(wait)
                continue
            await worker.run_task(task)
            self._save()
            await asyncio.sleep(0)  # a search that never suspends must not starve the loop

    def _save(self) -> None:
        current = self._backoff.snapshot()
        if current == self._saved:
            return
        try:
            self._scheduler_state.save_channel_backoff(current)
        except RepositoryError as error:
            _logger.error("backoff not saved (%s): retried after the next search", error)
            return
        self._saved = current
