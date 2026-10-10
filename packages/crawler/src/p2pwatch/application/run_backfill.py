"""Startup gate for the catalogue backfill: skip when the policy is unchanged (spec §7.1).

APPLICATION layer, pure orchestration (no I/O of its own — delegates to the injected
``local_repo`` port and the ``run_backfill`` callable, so the composition root decides what
"running the backfill" concretely means, e.g. ``reevaluate_catalog``).

Crash safety (spec §7.1): the marker is stored ONLY after ``run_backfill`` returns
successfully. If it raises, the exception propagates here UNCHANGED and the marker is left
untouched — the next start sees the same (or no) fingerprint and retries the full pass.
"""

from collections.abc import Awaitable, Callable

from p2pwatch.application.reevaluate_catalog import ReevalSummary
from p2pwatch.ports.local_state_repository import LocalStateRepository


async def run_backfill_if_policy_changed(
    *,
    fingerprint: str,
    local_repo: LocalStateRepository,
    run_backfill: Callable[[], Awaitable[ReevalSummary]],
) -> ReevalSummary | None:
    """Runs the backfill iff the stored policy fingerprint differs from ``fingerprint``.

    ``None`` when skipped (the marker already equals ``fingerprint``); the backfill's
    ``ReevalSummary`` otherwise, once the marker has been updated to ``fingerprint``.
    """
    if local_repo.last_backfill_policy() == fingerprint:
        return None
    summary = await run_backfill()
    local_repo.set_last_backfill_policy(fingerprint)
    return summary
