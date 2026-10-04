"""Recompute a match explanation from an already-parsed config (spec W-D7 / Task 9).

Since P4a ``MatchingExplainer`` takes the crawler's ALREADY-PARSED ``MatcherConfig`` +
``targets`` tuple at construction time (the YAML → config parsing moved UP into the caller —
``__main__`` for the standalone entrypoint, ``CrawlerApp`` in-process later). It builds the
:class:`MatchingEngine` ONCE and exposes ``explain()`` to recompute a file's explanation over
all its names against that config. Sharing the crawler's own parsed matcher is what kills the
matcher-drift bug structurally (spec §8).

``size_bytes → size_mb`` (and the trivial ``int → float`` casts) reuse the crawler's ONE
canonical converter, ``mulewatch.domain.observation.candidate_from_fields`` (which encodes
DECISION 8: eMule "MB" are binary Mio) — the monolith consolidation removed the old boundary
that forced this module to reimplement it. The ONLY case that cannot go through the canonical
converter is ``size_bytes is None``: the converter requires an ``int`` (a persisted
observation always has a size), while ``explain()``'s contract still permits ``None``, so that
path builds the ``FileCandidate`` directly.
"""

from collections.abc import Iterable
from dataclasses import replace

from catalog_matching.config import MatcherConfig
from catalog_matching.engine import Explanation, MatchingEngine
from catalog_matching.models import FileCandidate, TargetSegment
from mulewatch.domain.observation import candidate_from_fields


class MatchingExplainer:
    """Build and cache a :class:`MatchingEngine` from a parsed config.

    The engine is resolved ONCE (matcher trees pre-compiled per target) at
    construction. Successive calls to ``explain()`` reuse the same engine.
    """

    def __init__(
        self, *, matcher_config: MatcherConfig, targets: tuple[TargetSegment, ...]
    ) -> None:
        self._engine = MatchingEngine(matcher_config, targets)

    def explain(
        self,
        filenames: Iterable[str],
        size_bytes: int | None,
        media_length_sec: int | None,
        bitrate_kbps: int | None,
        target_id: str,
    ) -> Explanation | None:
        """Recompute the explanation of target ``target_id`` over every name of one file.

        Each name carries the same fields, as ``record_decision_if_changed`` judges a file.
        Return ``None`` if ``target_id`` is unknown to the current config.
        """
        if size_bytes is None:
            fields = FileCandidate(
                filename="",
                size_mb=None,
                duration_sec=float(media_length_sec) if media_length_sec is not None else None,
                bitrate_kbps=float(bitrate_kbps) if bitrate_kbps is not None else None,
            )
        else:
            fields = candidate_from_fields("", size_bytes, media_length_sec, bitrate_kbps)
        names = [replace(fields, filename=name) for name in filenames]
        return self._engine.explain(names, target_id)
