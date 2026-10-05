"""Matching engine models (cf. spec §7, §8)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FileCandidate:
    """A file observed on the network, candidate for matching.

    ``filename`` is the raw observed basename. The optional attributes are the
    metadata (self-declared, hence unreliable, cf. spec §10.1) used by
    ``attr_between``; ``None`` = missing.
    """

    filename: str
    size_mb: float | None = None
    duration_sec: float | None = None
    bitrate_kbps: float | None = None


@dataclass(frozen=True)
class TargetSegment:
    """A target episode segment (segment granularity, cf. spec §7).

    Provides ``{season} {seasonal_number} {absolute_number} {segment} {title}`` to the
    interpolation of regex patterns.
    """

    season: int
    seasonal_number: int
    absolute_number: int
    segment: str
    title: str
    status: str = "lost"

    @property
    def target_id(self) -> str:
        """Stable segment id: zero-padded absolute number + segment letter, e.g. ``062A``."""
        return f"{self.absolute_number:03d}{self.segment.upper()}"
