"""``CatalogRepository`` port: the catalog's durable memory (spec data-model §4).

SYNCHRONOUS Protocol (spec §3: a local write is sub-millisecond; if plan C wants to
isolate itself, it will wrap it in ``asyncio.to_thread`` without touching this layer).
The port imports ONLY the domain. Stubs fit on ONE line (the ``def`` runs at class
creation: covered). The adapter stamps ``observed_at``/``decided_at``/``node_id`` — that
is why ``record_decision`` receives the hash ALONGSIDE the decision (``MatchDecision`` does
not carry the content key, by principle: a domain without persistence columns).
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from catalog_matching.engine import DecisionRecord, DownloadCandidate, MatchDecision
from mulewatch.domain.observation import FileObservation


@dataclass(frozen=True)
class ObservedFile:
    """Minimal READ shape of an observation: name + size (to build an ed2k link).

    The download loop (spec §5) reads the LATEST observation of a hash to rebuild its ed2k
    link (``build_ed2k_link(filename, size_bytes, hash)``). We return only the two required
    fields — not the whole ``FileObservation`` (the rest is useless for download).
    """

    filename: str
    size_bytes: int


@dataclass(frozen=True)
class ReevalRow:
    """One hash's latest observation, enough to rebuild a :class:`FileCandidate`.

    Read by the re-evaluation backfill (spec re-evaluation §6): each row feeds
    ``domain.observation.candidate_from_fields`` to re-run the matching engine against
    the whole catalogue, one row per catalogued hash.
    """

    ed2k_hash: str
    filename: str
    size_bytes: int
    media_length_sec: int | None
    bitrate_kbps: int | None


class CatalogRepository(Protocol):
    """Sync catalog write contract (append-only; the adapter reports, it does not decide).

    ``last_decisions`` (set-diff anti-redundancy, spec §7) returns the latest
    :class:`DecisionRecord` PER TARGET for a hash (including a target whose latest tier is
    ``retracted``; excluding the legacy ``target_id=""`` sentinel), for multi-target matching.
    ``download_decisions`` (spec download §5) returns the :class:`DownloadCandidate` whose
    LATEST verdict is tier=download (to be replayed by the download loop). ``last_observation``
    returns the most recent :class:`ObservedFile` of a hash (name+size for the ed2k link), or
    ``None``. ``best_observation`` returns the file's clean name (the most sourced, then the
    latest) with its size, or ``None``. ``iter_reevaluation_rows`` streams every hash's latest
    observation as a :class:`ReevalRow` (spec re-evaluation §6), for the startup backfill to
    rebuild a candidate per hash; ``count_files`` is its progress total.
    ``known_filenames`` returns every distinct name of a hash, sorted.
    These reads are harmless (no write).
    ``record_retraction`` (spec §7) appends a per-target ``match_decisions`` row
    (``rule_name=""``, ``tier="retracted"``) marking ``target_id`` as no longer matching this
    file — the append-only table has no delete, so exclusion is an appended row.
    """

    def record_observation(self, observation: FileObservation) -> None: ...

    def record_decision(self, ed2k_hash: str, decision: MatchDecision) -> None: ...

    def record_retraction(self, ed2k_hash: str, target_id: str) -> None: ...

    def last_decisions(self, ed2k_hash: str) -> dict[str, DecisionRecord]: ...

    def download_decisions(self) -> tuple[DownloadCandidate, ...]: ...

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None: ...

    def best_observation(self, ed2k_hash: str) -> ObservedFile | None: ...

    def known_filenames(self, ed2k_hash: str) -> tuple[str, ...]: ...

    def count_files(self) -> int: ...

    def iter_reevaluation_rows(self) -> Iterator[ReevalRow]: ...
