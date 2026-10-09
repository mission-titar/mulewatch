"""Observation of a file seen on the network. PURE domain.

``raw_meta`` is the per-network catch-all of ``(name, value)`` pairs: no metadata field is lost.
"""

from dataclasses import dataclass

from catalog_matching.models import FileCandidate
from mulewatch.domain.file_key import FileKey

RawMeta = tuple[tuple[str, str | int | None], ...]

# DECISION 8: the "MB" shown by eMule clients are binary (MiB).
_BYTES_PER_MIB = 1024 * 1024


def candidate_from_fields(
    filename: str,
    size_bytes: int,
    media_length_sec: int | None,
    bitrate_kbps: int | None,
) -> FileCandidate:
    """Bridge to the matching engine: unit conversions (bytes -> MiB, int -> float).

    Single source of the conversion (spec re-evaluation §6): both ``FileObservation.
    to_candidate`` (live path) and the re-evaluation backfill (``ReevalRow`` ->
    candidate) call this.
    """
    duration = float(media_length_sec) if media_length_sec is not None else None
    bitrate = float(bitrate_kbps) if bitrate_kbps is not None else None
    return FileCandidate(
        filename=filename,
        size_mb=size_bytes / _BYTES_PER_MIB,
        duration_sec=duration,
        bitrate_kbps=bitrate,
    )


def fold_raw_meta(
    raw_meta: tuple[tuple[str, str], ...],
    codec: str | None,
    file_type: str | None,
    complete_source_count: int,
) -> RawMeta:
    """``raw_meta`` with the three eD2k-only fields appended, absent ones as ``None``. The
    amuleapi mapper and the catalog migration share it, so both store the same pairs."""
    return (
        *raw_meta,
        ("codec", codec),
        ("file_type", file_type),
        ("complete_source_count", complete_source_count),
    )


@dataclass(frozen=True)
class FileObservation:
    """A file observed during a search (the file, never the person); ``keyword`` is its provenance.
    Media fields are ``None`` when the network did not report them (self-declared, unreliable)."""

    file: FileKey
    filename: str
    size_bytes: int
    source_count: int
    keyword: str
    media_length_sec: int | None = None
    bitrate_kbps: int | None = None
    raw_meta: RawMeta = ()

    def to_candidate(self) -> FileCandidate:
        """Bridge to the matching engine (delegates to ``candidate_from_fields``)."""
        return candidate_from_fields(
            self.filename, self.size_bytes, self.media_length_sec, self.bitrate_kbps
        )


@dataclass(frozen=True)
class Sighting:
    """A file seen once: one observation."""

    ed2k_hash: str
    names: tuple[str, ...]
    observation_count: int
    first_seen: str
    last_seen: str
    source_count_min: int
    source_count_max: int
    size_bytes: int
    media_length_sec: int | None
    bitrate_kbps: int | None
    keyword: str | None
