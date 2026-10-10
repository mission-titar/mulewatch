"""Tests for the startup backfill gate ``run_backfill_if_policy_changed`` (spec §7.1, Task 6).

A fake local-state repo (full ``LocalStateRepository`` structural satisfaction — only
``last_backfill_policy``/``set_last_backfill_policy`` are exercised here, the rest are
unused stubs) + a plain async callable standing in for ``reevaluate_catalog``. No real
SQLite/engine needed: the gate is pure orchestration over the port + the injected callable.
"""

import pytest

from p2pwatch.application.reevaluate_catalog import ReevalSummary
from p2pwatch.application.run_backfill import run_backfill_if_policy_changed

_SUMMARY = ReevalSummary(evaluated=3, written=1)


class FakeLocalRepo:
    """Full ``LocalStateRepository`` stub: only the backfill-marker methods matter here."""

    def __init__(self, *, stored_policy: str | None) -> None:
        self._stored_policy = stored_policy
        self.set_calls: list[str] = []

    def node_id(self) -> str:
        return "node"

    def last_backfill_policy(self) -> str | None:
        return self._stored_policy

    def set_last_backfill_policy(self, sha256: str) -> None:
        self.set_calls.append(sha256)
        self._stored_policy = sha256


@pytest.mark.asyncio
async def test_marker_matches_fingerprint_skips_backfill() -> None:
    local_repo = FakeLocalRepo(stored_policy="abc")
    calls = 0

    async def run_backfill() -> ReevalSummary:
        nonlocal calls
        calls += 1
        return _SUMMARY

    result = await run_backfill_if_policy_changed(
        fingerprint="abc", local_repo=local_repo, run_backfill=run_backfill
    )
    assert result is None
    assert calls == 0
    assert local_repo.set_calls == []


@pytest.mark.asyncio
async def test_marker_absent_runs_backfill_then_stores_fingerprint() -> None:
    local_repo = FakeLocalRepo(stored_policy=None)
    calls = 0

    async def run_backfill() -> ReevalSummary:
        nonlocal calls
        calls += 1
        return _SUMMARY

    result = await run_backfill_if_policy_changed(
        fingerprint="abc", local_repo=local_repo, run_backfill=run_backfill
    )
    assert result == _SUMMARY
    assert calls == 1
    assert local_repo.set_calls == ["abc"]


@pytest.mark.asyncio
async def test_marker_differs_from_fingerprint_runs_backfill() -> None:
    local_repo = FakeLocalRepo(stored_policy="old-fingerprint")
    calls = 0

    async def run_backfill() -> ReevalSummary:
        nonlocal calls
        calls += 1
        return _SUMMARY

    result = await run_backfill_if_policy_changed(
        fingerprint="new-fingerprint", local_repo=local_repo, run_backfill=run_backfill
    )
    assert result == _SUMMARY
    assert calls == 1
    assert local_repo.set_calls == ["new-fingerprint"]


@pytest.mark.asyncio
async def test_run_backfill_raising_propagates_and_marker_not_set() -> None:
    local_repo = FakeLocalRepo(stored_policy=None)

    async def boom() -> ReevalSummary:
        raise RuntimeError("backfill exploded")

    with pytest.raises(RuntimeError, match="backfill exploded"):
        await run_backfill_if_policy_changed(
            fingerprint="abc", local_repo=local_repo, run_backfill=boom
        )
    assert local_repo.set_calls == []
