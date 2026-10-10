"""Pure derivation of a target's coverage status (webui spec §5). No I/O.

The tier order comes from ``catalog_matching.config.TIER_RANK`` (source of truth shared
with the matching engine). Without this sharing, the webui reinvented its own divergent
table — a 4th tier or a rename would have silently skewed the coverage.
"""

from collections.abc import Sequence

from catalog_matching.config import TIER_RANK
from p2pwatch.webui.domain.views import CoverageStatus


def coverage_for(target_id: str, decisions: Sequence[tuple[bytes, str]]) -> CoverageStatus:
    """``decisions`` = ``(file_id, tier)`` of the latest decisions for this target.

    Catalog-tier decisions are the ``keroro_large`` catch-all: any keroro file with no numeric
    token resolves to the smallest ``target_id`` (``001A``) via the engine tie-break, so counting
    them would make one episode look partially recovered while the catalog has identified nothing.
    They do NOT count as coverage of an episode (same ``tier == "catalog"`` criterion the ``/files``
    "unidentified" mask uses in ``composition.app._resolve_target_display``).
    """
    identified = [d for d in decisions if d[1] != "catalog"]
    if not identified:
        return CoverageStatus(status="none", best_tier=None, file_count=0)
    # TIER_RANK: increasing integer = stronger tier (download > notify); an unknown tier falls
    # back to ``-1`` (below the weakest), neutral with respect to the choice.
    best = max(identified, key=lambda d: TIER_RANK.get(d[1], -1))[1]
    status = "found" if best == "download" else "partial"
    return CoverageStatus(status=status, best_tier=best, file_count=len(identified))
