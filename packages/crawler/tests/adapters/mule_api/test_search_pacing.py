"""``AmuleApiClient.search()`` paces its starts by the rules of eD2k and Kad (spec stage 2, D5).

Concurrent searches need one shared time, so these tests run on an event loop whose clock is
virtual: whenever every task waits, the loop jumps to the next timer instead of sleeping.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

import pytest

from mulewatch.adapters.mule_api.client import AmuleApiClient
from mulewatch.ports.client_errors import SearchFailedError
from tests.adapters.mule_api.api_fakes import PASSWORD, FakeAmuleApi, error
from tests.virtual_time import EPOCH, LoopClock, run_virtual

_HOUR = 3600.0
_BUDGET = 120.0
_ALREADY_SEARCHING = (
    "Unexpected error while attempting Kad search: "
    "Kademlia: Search keyword is already on search list: keroro"
)


def _api(**kwargs: Any) -> FakeAmuleApi:
    return FakeAmuleApi(clock=LoopClock(), **kwargs)


async def _connected(api: FakeAmuleApi) -> AmuleApiClient:
    client = AmuleApiClient(
        "amuled.test", 4711, PASSWORD, transport=api.transport(), clock=api.clock
    )
    await client.connect()
    return client


def _elapsed(moment: datetime) -> float:
    return (moment - EPOCH).total_seconds()


def _starts(api: FakeAmuleApi, search_type: str) -> list[tuple[float, str]]:
    """Every ``POST /search`` of one type: when, and for which query."""
    starts = []
    for moment, request in api.calls:
        if request.method == "POST" and request.url.path == "/api/v1/search":
            body = json.loads(request.content)
            if body["type"] == search_type:
                starts.append((_elapsed(moment), body["query"]))
    return starts


def _gaps(times: list[float]) -> list[float]:
    return [later - earlier for earlier, later in zip(times, times[1:], strict=False)]


def _polled_until(api: FakeAmuleApi, search_id: int) -> float:
    """When a search's progress was last read, in seconds after its start."""
    started_at = api.searches[search_id][0]
    path = f"/api/v1/search/{search_id}/results"
    return max(
        (moment - started_at).total_seconds()
        for moment, request in api.calls
        if request.url.path == path and request.url.params.get("limit") == "0"
    )


async def _search_for(api: FakeAmuleApi, keywords: list[str], channels: list[str]) -> None:
    """One task per (channel, keyword), each searching again as soon as it returns, for an hour."""
    client = await _connected(api)
    loop = asyncio.get_running_loop()

    async def task(keyword: str, channel: str) -> None:
        while loop.time() < _HOUR:
            await client.search(keyword, channel, _BUDGET)
            await asyncio.sleep(0)

    await asyncio.gather(*(task(keyword, channel) for keyword in keywords for channel in channels))
    await client.close()


# ed2k's `keroro` runs past 60 s, so both ed2k rules take turns binding.
_DURATIONS = {"keroro": 90.0, "keroro mission": 30.0, "titar": 30.0}
_TARGETS = {"keroro": "keroro", "keroro mission": "keroro", "titar": "titar"}


def _hour_of_three_keywords() -> FakeAmuleApi:
    api = _api(search_seconds=dict(_DURATIONS))
    run_virtual(_search_for(api, list(_DURATIONS), ["ed2k", "kad"]))
    return api


def test_ed2k_starts_are_a_minute_apart_or_more() -> None:
    api = _hour_of_three_keywords()

    starts = [moment for moment, _ in _starts(api, "global")]
    assert len(starts) > 30
    assert min(_gaps(starts)) >= 60


def test_no_ed2k_start_falls_while_the_previous_search_runs() -> None:
    api = _hour_of_three_keywords()

    starts = _starts(api, "global")
    for (previous, query), (start, _) in zip(starts, starts[1:], strict=False):
        assert start >= previous + _DURATIONS[query], (previous, query, start)


def test_kad_starts_on_one_target_are_a_minute_apart_whatever_keywords_share_it() -> None:
    api = _hour_of_three_keywords()

    starts = _starts(api, "kad")
    for target in ("keroro", "titar"):
        times = [moment for moment, query in starts if _TARGETS[query] == target]
        assert len(times) > 30
        assert min(_gaps(times)) >= 60, target


def test_kad_targets_and_ed2k_start_together() -> None:
    api = _hour_of_three_keywords()

    assert [moment for moment, _ in _starts(api, "kad")][:2] == [0, 0]
    assert _starts(api, "global")[0][0] == 0


def test_each_search_gets_its_budget_from_its_network_start() -> None:
    """The third ed2k keyword waits for the first two, then still polls to its end."""
    api = _hour_of_three_keywords()

    for search_id, (_, query) in api.searches.items():
        assert _polled_until(api, search_id) >= _DURATIONS[query], (search_id, query)


def test_an_hour_of_searches_never_stops_nor_frees_one() -> None:
    api = _hour_of_three_keywords()

    assert [r for r in api.requests if r.url.path.endswith("/stop")] == []
    assert [r for r in api.requests if r.method == "DELETE"] == []


def test_an_ed2k_search_that_never_finishes_holds_the_next_start_one_budget() -> None:
    api = _api(progress={"state": "running"})

    async def main() -> None:
        client = await _connected(api)
        await asyncio.gather(
            client.search("keroro", "ed2k", _BUDGET), client.search("titar", "ed2k", _BUDGET)
        )

    run_virtual(main())

    assert [moment for moment, _ in _starts(api, "global")] == [0, _BUDGET]


def test_concurrent_callers_of_one_kad_target_each_get_their_own_slot() -> None:
    api = _api()

    async def main() -> None:
        client = await _connected(api)
        await client.search("keroro", "kad", _BUDGET)
        # Both wait for the target's next slot: whoever wakes first must not leave it free.
        await asyncio.gather(
            client.search("keroro 1", "kad", _BUDGET), client.search("keroro 2", "kad", _BUDGET)
        )

    run_virtual(main())

    assert [moment for moment, _ in _starts(api, "kad")] == [0, 60, 120]


@pytest.mark.parametrize(
    ("first", "second", "shared"),
    [
        ("keroro 62", "keroro mission", True),
        ("Keroro.062", "[VF] KERORO", True),
        ("62 keroro", "keroro", True),
        ("ok/titar", "titar", True),
        ("été", "ÉTÉ", True),
        ("éa", "éa titar", True),  # 3 UTF-8 bytes in 2 characters: a word for Kad
        ("keroro", "titar", False),
        ("keroros", "keroro", False),
    ],
)
def test_kad_keys_a_keyword_on_its_first_word_of_three_bytes(
    first: str, second: str, shared: bool
) -> None:
    api = _api()

    async def main() -> None:
        client = await _connected(api)
        await client.search(first, "kad", _BUDGET)
        await client.search(second, "kad", _BUDGET)

    run_virtual(main())

    assert [moment for moment, _ in _starts(api, "kad")] == [0, 60 if shared else 0]


@pytest.mark.parametrize("keyword", ["62", "ok", "é", "62 ok", "VF-62"])
def test_a_keyword_without_a_kad_target_neither_spins_nor_searches(keyword: str) -> None:
    api = _api()
    returned_at: list[float] = []

    async def main() -> None:
        client = await _connected(api)
        loop = asyncio.get_running_loop()
        while loop.time() < _HOUR and len(returned_at) <= 60:  # bounded, should it spin
            assert await client.search(keyword, "kad", _BUDGET) == ()
            returned_at.append(loop.time())
            await asyncio.sleep(0)

    run_virtual(main())

    assert len([moment for moment in returned_at if moment < _HOUR]) == 60
    assert _starts(api, "kad") == []


def test_a_keyword_without_a_kad_target_is_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="mulewatch.adapters.mule_api.client")

    async def main() -> None:
        client = await _connected(_api())
        for keyword in ("62", "62", "ok", "62"):
            await client.search(keyword, "kad", _BUDGET)

    run_virtual(main())

    assert [record.getMessage().count("no Kad target") for record in caplog.records] == [1, 1]
    assert ["'62'" in record.getMessage() for record in caplog.records] == [True, False]


def test_a_kad_target_held_elsewhere_is_retried_each_poll_until_it_frees() -> None:
    api = _api(search_seconds={"keroro": 30})
    refusals = [error(400, "amuled_rejected", _ALREADY_SEARCHING)] * 2
    api.overrides[("POST", "/api/v1/search")] = lambda request: (
        refusals.pop(0) if refusals else api._start_search(request)
    )

    async def main() -> None:
        client = await _connected(api)
        await client.search("keroro", "kad", _BUDGET)

    run_virtual(main())

    assert [moment for moment, _ in _starts(api, "kad")] == [0, 5, 10]
    assert _polled_until(api, 42) >= 30  # its budget runs from the start at 10 s


def test_a_kad_target_held_past_one_budget_fails_the_channel_at_that_budget() -> None:
    api = _api()
    api.overrides[("POST", "/api/v1/search")] = lambda _: error(
        400, "amuled_rejected", _ALREADY_SEARCHING
    )
    failed_at: list[float] = []

    async def main() -> None:
        client = await _connected(api)
        with pytest.raises(SearchFailedError):
            await client.search("keroro", "kad", _BUDGET)
        failed_at.append(asyncio.get_running_loop().time())

    run_virtual(main())

    assert failed_at == [_BUDGET]
    assert [moment for moment, _ in _starts(api, "kad")][-2:] == [_BUDGET - 5, _BUDGET]


def test_any_other_kad_refusal_fails_the_channel_at_once() -> None:
    api = _api()
    api.overrides[("POST", "/api/v1/search")] = lambda _: error(
        400, "amuled_rejected", "Kad is not running"
    )

    async def main() -> None:
        client = await _connected(api)
        with pytest.raises(SearchFailedError):
            await client.search("keroro", "kad", _BUDGET)

    run_virtual(main())

    assert len(_starts(api, "kad")) == 1
