"""``SqliteCatalogRepository``: ``FileObservation``/``MatchDecision`` → durable rows.

The adapter stamps what the domain ignores (data-model spec §3): ``observed_at``/
``decided_at`` (injectable clock, ``utc_now`` by default) and ``node_id`` (given at the
constructor — plan C will read it from ``LocalStateRepository``). ``raw_meta`` is serialized
as a JSON LIST of pairs (``[["0x0308", "0"], …]``), wire order and duplicates preserved,
``ensure_ascii=False``, no sorting (spec §3). ``record_observation`` makes ONE transaction
(spec §4): ``INSERT OR IGNORE`` into ``files`` (first sight wins), into
``observation_variants`` (found by its ``content_hash``) and into ``observations`` — the
OBSERVED size is ALWAYS written into the variant (deviation 1, spec §5: a size anomaly must
not become invisible).

The hash canon (32 lowercase hex, v0.5.0 canon) is validated IN PYTHON before the
transaction: ``INSERT OR IGNORE`` silently swallows a CHECK violation (documented
SQLite behavior) — without this guard, a non-canonical hash would only be stopped
by the ``foreign_keys`` pragma (opaque diagnostic), and a connection without that
pragma would commit an ORPHAN observation. The rollback catches ``BaseException``
(same discipline as ``connection._open``): a NON-sqlite failure at binding (e.g.
``UnicodeEncodeError`` on a lone surrogate) must not leave the connection
``in_transaction`` — otherwise the repository would be permanently broken.
"""

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import suppress

from catalog_matching.engine import (
    DecisionRecord,
    DownloadCandidate,
    MatchDecision,
)
from mulewatch.adapters.persistence_sqlite import sightings
from mulewatch.adapters.persistence_sqlite.connection import Clock, utc_iso, utc_now
from mulewatch.adapters.persistence_sqlite.errors import PersistenceError, wrap_sqlite_errors
from mulewatch.adapters.persistence_sqlite.variants import content_hash, iso_to_micros
from mulewatch.domain.observation import FileObservation, fold_raw_meta
from mulewatch.domain.retraction import RETRACTED_TIER
from mulewatch.ports.catalog_repository import ObservedFile, ReevalRow

_CANONICAL_HASH_RE = re.compile(r"[0-9a-f]{32}\Z")

_INSERT_FILE = "INSERT OR IGNORE INTO files (ed2k_hash, size_bytes, aich_hash) VALUES (?, ?, NULL)"

_INSERT_VARIANT = """
INSERT OR IGNORE INTO observation_variants (
    ed2k_hash, filename, size_bytes, media_length_sec, bitrate_kbps, raw_meta, keyword, node_id,
    content_hash
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_OBSERVATION = """
INSERT OR IGNORE INTO observations (variant_id, observed_at, source_count)
SELECT variant_id, ?, ? FROM observation_variants WHERE content_hash = ?
"""

_INSERT_DECISION = """
INSERT INTO match_decisions (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)
VALUES (?, ?, ?, ?, ?, ?)
"""

# Latest verdict per (ed2k_hash, target_id) for one hash (set-diff anti-redundancy, spec §7).
# ROW_NUMBER per target, order (decided_at, id) DESCENDING (most recent = rank 1); keep rank 1.
# INCLUDES a target whose latest tier is 'retracted' (no tier filter); EXCLUDES the legacy
# target_id='' sentinel (not a real target). The idx_match_decisions_ed2k_hash index serves
# the filter.
_SELECT_LAST_DECISIONS = """
SELECT target_id, rule_name, tier FROM (
    SELECT
        target_id, rule_name, tier,
        ROW_NUMBER() OVER (PARTITION BY target_id ORDER BY decided_at DESC, id DESC) AS rn
    FROM match_decisions
    WHERE ed2k_hash = ? AND target_id <> ''
) WHERE rn = 1
"""

# Latest verdict per (ed2k_hash, target_id), kept when tier=download (download spec §5,
# multi-target §6). Window: ROW_NUMBER per (hash, target_id), order (decided_at, id)
# DESCENDING (most recent = rank 1); keep rank 1 AND tier='download'. PARTITION BY the FULL
# key so a whole-episode file with BOTH segments in download yields BOTH candidates. Stable
# sort by (hash, target_id) for a deterministic result.
_SELECT_DOWNLOAD_DECISIONS = """
SELECT ed2k_hash, target_id FROM (
    SELECT
        ed2k_hash, target_id, tier,
        ROW_NUMBER() OVER (
            PARTITION BY ed2k_hash, target_id ORDER BY decided_at DESC, id DESC
        ) AS rn
    FROM match_decisions
) WHERE rn = 1 AND tier = 'download'
ORDER BY ed2k_hash, target_id
"""

_COUNT_FILES = "SELECT COUNT(*) FROM files"


class SqliteCatalogRepository:
    """SQLite implementation of the ``CatalogRepository`` port (STRUCTURAL satisfaction)."""

    def __init__(
        self, connection: sqlite3.Connection, node_id: str, *, clock: Clock = utc_now
    ) -> None:
        self._connection = connection
        self._node_id = node_id
        self._clock = clock

    def record_observation(self, observation: FileObservation) -> None:
        """ONE transaction: file (first sight wins), its variant, the stamped observation."""
        if not _CANONICAL_HASH_RE.fullmatch(observation.ed2k_hash):
            raise PersistenceError(f"non-canonical eD2k hash: {observation.ed2k_hash!r}")
        pairs = fold_raw_meta(
            observation.raw_meta,
            observation.codec,
            observation.file_type,
            observation.complete_source_count,
        )
        variant = (
            observation.ed2k_hash,
            observation.filename,
            observation.size_bytes,
            observation.media_length_sec,
            observation.bitrate_kbps,
            json.dumps(pairs, ensure_ascii=False),
            observation.keyword,
            self._node_id,
        )
        key = content_hash(*variant)
        observed_at = iso_to_micros(utc_iso(self._clock()))
        with wrap_sqlite_errors():
            self._connection.execute("BEGIN")
            try:
                self._connection.execute(
                    _INSERT_FILE, (observation.ed2k_hash, observation.size_bytes)
                )
                self._connection.execute(_INSERT_VARIANT, (*variant, key))
                self._connection.execute(
                    _INSERT_OBSERVATION, (observed_at, observation.source_count, key)
                )
                self._connection.execute("COMMIT")
            except BaseException:
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                raise

    def record_decision(self, ed2k_hash: str, decision: MatchDecision) -> None:
        """INSERT alone (autocommit); unknown file → FK violated → ``PersistenceError``.

        Only the 3 columns of ``MatchDecision`` are persisted (engine spec);
        ``explanation`` is runtime explainability, NEVER a column.
        """
        if not _CANONICAL_HASH_RE.fullmatch(ed2k_hash):
            raise PersistenceError(f"non-canonical eD2k hash: {ed2k_hash!r}")
        with wrap_sqlite_errors():
            self._connection.execute(
                _INSERT_DECISION,
                (
                    ed2k_hash,
                    decision.target_id,
                    decision.rule_name,
                    decision.tier,
                    utc_iso(self._clock()),
                    self._node_id,
                ),
            )

    def record_retraction(self, ed2k_hash: str, target_id: str) -> None:
        """Appends a per-target ``retracted`` decision (spec §7).

        Mirrors ``record_decision`` (same canonical-hash guard, same autocommit ``INSERT``): a
        file that no longer matches ``target_id`` gets an appended
        ``(target_id, rule_name="", tier=RETRACTED_TIER)`` row instead of a mutation, per the
        append-only invariant. Retracting one target leaves the file's other targets intact.
        Unknown file → FK violated → ``PersistenceError``.
        """
        if not _CANONICAL_HASH_RE.fullmatch(ed2k_hash):
            raise PersistenceError(f"non-canonical eD2k hash: {ed2k_hash!r}")
        with wrap_sqlite_errors():
            self._connection.execute(
                _INSERT_DECISION,
                (ed2k_hash, target_id, "", RETRACTED_TIER, utc_iso(self._clock()), self._node_id),
            )

    def last_decisions(self, ed2k_hash: str) -> dict[str, DecisionRecord]:
        """Latest verdict per target for this hash (set-diff anti-redundancy, spec §7) — READ.

        Maps ``target_id`` → its latest :class:`DecisionRecord`. INCLUDES a target whose latest
        tier is ``retracted`` (the application's set-diff skips re-retracting it); EXCLUDES the
        legacy ``target_id=""`` sentinel. The hash is NOT validated canonical (harmless read: a
        non-canonical hash matches nothing → ``{}``).
        """
        with wrap_sqlite_errors():
            rows = self._connection.execute(_SELECT_LAST_DECISIONS, (ed2k_hash,)).fetchall()
        return {
            row[0]: DecisionRecord(target_id=row[0], rule_name=row[1], tier=row[2]) for row in rows
        }

    def download_decisions(self) -> tuple[DownloadCandidate, ...]:
        """``(hash, target_id)`` whose LATEST verdict is tier=download, to replay (download §5).

        Keyed per ``(hash, target_id)``: a whole-episode file matching both segments now yields
        MULTIPLE :class:`DownloadCandidate` for the SAME hash (one per target). READ.
        """
        with wrap_sqlite_errors():
            rows = self._connection.execute(_SELECT_DOWNLOAD_DECISIONS).fetchall()
        return tuple(DownloadCandidate(ed2k_hash=row[0], target_id=row[1]) for row in rows)

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None:
        """Name and size of the latest sighting (the ed2k link), or ``None`` (read)."""
        with wrap_sqlite_errors():
            latest = sightings.latest_sighting(self._connection, ed2k_hash)
        if latest is None:
            return None
        return ObservedFile(filename=latest.names[0], size_bytes=latest.size_bytes)

    def best_observation(self, ed2k_hash: str) -> ObservedFile | None:
        """The clean name (most sources, then latest) and size, or ``None`` (read)."""
        with wrap_sqlite_errors():
            best = sightings.best_name(self._connection, ed2k_hash)
        return None if best is None else ObservedFile(filename=best[0], size_bytes=best[1])

    def known_filenames(self, ed2k_hash: str) -> tuple[str, ...]:
        """Every distinct name this hash was observed under, sorted (read)."""
        with wrap_sqlite_errors():
            return sightings.known_names(self._connection, ed2k_hash)

    def count_files(self) -> int:
        """Number of catalogued hashes, the re-evaluation progress total (read)."""
        with wrap_sqlite_errors():
            row = self._connection.execute(_COUNT_FILES).fetchone()
        return int(row[0])

    def iter_reevaluation_rows(self) -> Iterator[ReevalRow]:
        """Every seen hash's latest sighting, streamed (backfill spec §6)."""
        with wrap_sqlite_errors():
            for latest in sightings.iter_latest_sightings(self._connection):
                yield ReevalRow(
                    ed2k_hash=latest.ed2k_hash,
                    filename=latest.names[0],
                    size_bytes=latest.size_bytes,
                    media_length_sec=latest.media_length_sec,
                    bitrate_kbps=latest.bitrate_kbps,
                )
