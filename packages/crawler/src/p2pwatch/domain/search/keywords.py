"""Generation of search keywords from config (PURE, spec search-simplification).

PURE domain: no I/O. Keywords are provided by config (``crawler.yml``,
``search.keywords``) — by default ``keroro`` (wide net) + ``titar`` (FR sentinel,
jackpot-proof). ``generate_keywords`` is deterministic: same list → same tuple, ORDERED
and DEDUPLICATED (first seen wins).
"""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchKeyword:
    """A keyword to search + its provenance. FROZEN and hashable → trivial deduplication."""

    text: str
    origin: str


def generate_keywords(keywords: Sequence[str]) -> tuple[SearchKeyword, ...]:
    """ORDERED and DEDUPLICATED list of keywords (spec search-simplification).

    Order = input order; deduplication by ``text`` (first seen wins); empty strings are
    ignored. ``origin`` = the text itself (provenance = config keyword).
    """
    seen: set[str] = set()
    result: list[SearchKeyword] = []
    for text in keywords:
        if text and text not in seen:
            seen.add(text)
            result.append(SearchKeyword(text=text, origin=text))
    return tuple(result)
