"""Read-only reads of the catalog (webui spec W-D6 / §6).

``CatalogReader`` exposes four reads:

- ``target_coverage()`` — per ``target_id``, the list of ``(ed2k_hash, tier)`` from each
  file's LATEST match decision **per target** (ROW_NUMBER window PARTITION BY
  ``(ed2k_hash, target_id)``), so a whole-episode file contributes to every target it
  matches. The legacy ``target_id=''`` sentinel and per-target ``retracted`` rows are
  excluded.
- ``list_files()`` — filtered paginated explorer (files ⨝ latest observation ⨝
  latest decision, optional filters + LIMIT/OFFSET).
- ``count_files()`` — ``(matched, total)`` counts over the same filtered source, for the
  /files summary line.
- ``file_detail()``: all observations, compacted days, known names + current decisions for a
  given hash; ``None`` if the hash is unknown.

All SQL lives in module constants, parameterized (no value interpolation).
"""

import json
import sqlite3

from catalog_matching.config import TIER_RANK
from mulewatch.adapters.persistence_sqlite.catalog_repository import SELECT_KNOWN_FILENAMES
from mulewatch.webui.domain.views import (
    DecisionView,
    FileDecision,
    FileDetail,
    FileRow,
    ObservationRow,
    RangeRow,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAGE_SIZE = 50
_PAGE_SIZE = PAGE_SIZE  # historical alias (internal) — the public value is used by the handler

# Sort allowlist (webui spec §3.1): a query-param key maps to a FIXED ORDER BY expression; no
# param value is ever interpolated into SQL. ``tier`` sorts by the file's strongest tier rank
# (``dec.best_tier_rank``, MAX of the TIER_RANK CASE). Direction maps through a fixed set too.
SORT_COLUMNS: dict[str, str] = {
    "name": "obs.filename",
    "size": "f.size_bytes",
    "sources": "obs.source_count",
    "last_seen": "obs.observed_at",
    "tier": "dec.best_tier_rank",
}
SORT_DIRECTIONS: dict[str, str] = {"asc": "ASC", "desc": "DESC"}
DEFAULT_SORT = "last_seen"
DEFAULT_DIR = "desc"


def _tier_rank_case(column: str) -> str:
    """Generate a SQL CASE mapping a tier column to its ``TIER_RANK`` integer, from the trusted
    constant. ``TIER_RANK`` keys are the closed ``TIERS`` enum and its values are ints, so
    interpolating them is safe (no user input); this keeps ONE source of truth for tier order
    (the file's strongest tier = ``MAX`` of this expression)."""
    whens = " ".join(f"WHEN '{tier}' THEN {rank}" for tier, rank in TIER_RANK.items())
    return f"CASE {column} {whens} END"


_TIER_RANK_CASE = _tier_rank_case("ld.tier")

# Latest decision per (hash, target_id) via ROW_NUMBER window: a whole-episode file holds
# one CURRENT decision per target it satisfies, so it must contribute to each of them, not
# just the single most-recent row across all its targets. The legacy target_id='' sentinel
# (pre-per-target retraction model) is excluded, and a per-target "retracted" latest decision
# (retracted == unmatched for that target) is dropped too.
_SQL_COVERAGE = """\
SELECT
    ed2k_hash,
    target_id,
    tier
FROM (
    SELECT
        md.ed2k_hash,
        md.target_id,
        md.tier,
        ROW_NUMBER() OVER (
            PARTITION BY md.ed2k_hash, md.target_id
            ORDER BY md.decided_at DESC, md.id DESC
        ) AS rn
    FROM match_decisions AS md
)
WHERE rn = 1
AND target_id != ''
AND tier != 'retracted'
ORDER BY target_id, ed2k_hash
"""

# The "latest per group" CTEs shared by the explorer list + counter, each folding an
# append-only table to its current rows (latest wins, tie-break on id).
#
# ``latest_obs`` is driven by ``files`` and seeks each file's newest observation through
# ``idx_file_observations_hash_observed`` (migration 0004), rather than numbering the table
# with a ROW_NUMBER() window like the CTEs below. A window must number EVERY row, so it walks
# all of ``file_observations`` (1.18M rows for 1402 files on the real node) to keep one row per
# file: 2.8s per query, ~10s per /files render. The seek form needs that index to pay off (it
# is slower than the window without it), which is why the two must not be split up. The other
# CTEs keep their window: ``match_decisions`` is small (hundreds of rows), so it is not worth
# the same treatment until it grows.
#
# A file compacted down to ranges falls back to its latest range: first name (the array is
# sorted) and last_observed_at, but a NULL source_count (a range has no single latest count).
# COALESCE only runs that lookup when the file has no raw observation.
#
# Being files-driven, ``latest_obs`` holds one row per catalogued file, INCLUDING a file with
# no observation yet (all-NULL columns) where the window form simply had no row. Every consumer
# below LEFT JOINs it onto ``files``, which absorbs the difference (NULL columns either way);
# read it on its own, though, and it counts files, not observed files.
#
# ``latest_dec`` keeps the latest decision per (hash, target_id), dropping the legacy
# ``target_id == ''`` sentinel and any target whose latest row is a ``retracted`` marker;
# ``dec_agg`` folds those to ONE row per hash, target_ids/tiers ``char(31)``-joined and both
# ordered by target_id so the two lists stay index-aligned (spec §9, rendering A).
_LATEST_RANGE = """FROM file_observation_ranges AS r
            WHERE r.ed2k_hash = f.ed2k_hash
            ORDER BY r.last_observed_at DESC, r.id DESC
            LIMIT 1"""

_SQL_CTES = f"""\
WITH latest_dec AS (
    SELECT ed2k_hash, target_id, tier
    FROM (
        SELECT
            md.ed2k_hash,
            md.target_id,
            md.tier,
            ROW_NUMBER() OVER (
                PARTITION BY md.ed2k_hash, md.target_id
                ORDER BY md.decided_at DESC, md.id DESC
            ) AS rn
        FROM match_decisions AS md
    )
    WHERE rn = 1
    AND target_id != ''
    AND tier != 'retracted'
),
dec_agg AS (
    SELECT
        ld.ed2k_hash,
        group_concat(ld.target_id, char(31) ORDER BY ld.target_id) AS target_ids,
        group_concat(ld.tier, char(31) ORDER BY ld.target_id) AS tiers,
        MAX({_TIER_RANK_CASE}) AS best_tier_rank
    FROM latest_dec AS ld
    GROUP BY ld.ed2k_hash
),
latest_obs AS (
    SELECT
        f.ed2k_hash AS ed2k_hash,
        COALESCE(obs.filename, (
            SELECT json_extract(r.filenames, '$[0]') {_LATEST_RANGE}
        )) AS filename,
        obs.source_count AS source_count,
        COALESCE(obs.observed_at, (
            SELECT r.last_observed_at {_LATEST_RANGE}
        )) AS observed_at
    FROM files AS f
    LEFT JOIN file_observations AS obs ON obs.id = (
        SELECT o.id
        FROM file_observations AS o
        WHERE o.ed2k_hash = f.ed2k_hash
        ORDER BY o.observed_at DESC, o.id DESC
        LIMIT 1
    )
)
"""

# Shared source: files ⨝ latest observation ⨝ current decisions (aggregated), all pre-folded
# by the CTEs above, so this is a plain star-join driven by ``files``.
_SQL_FILES_SOURCE = """\
FROM files AS f
LEFT JOIN latest_obs AS obs ON obs.ed2k_hash = f.ed2k_hash
LEFT JOIN dec_agg AS dec ON dec.ed2k_hash = f.ed2k_hash
"""

# Explorer: files + latest joins, driven by files. Optional filters added in list_files().
_SQL_LIST_FILES_BASE = (
    _SQL_CTES
    + """\
SELECT
    f.ed2k_hash,
    f.size_bytes,
    obs.filename,
    obs.source_count,
    obs.observed_at AS last_seen,
    dec.target_ids,
    dec.tiers
"""
    + _SQL_FILES_SOURCE
)

# Counter for the /files summary: file-based totals over the same source + filters (the
# matched-only clause is deliberately absent). ``matched`` = files with at least one current
# decision (``dec.target_ids`` is non-NULL). COUNT(DISTINCT …) keeps both counts file-based
# and yields 0 (not NULL) on an empty catalogue.
_SQL_COUNT_FILES_BASE = (
    _SQL_CTES
    + """\
SELECT
    COUNT(DISTINCT f.ed2k_hash) AS total,
    COUNT(DISTINCT CASE WHEN dec.target_ids IS NOT NULL THEN f.ed2k_hash END) AS matched
"""
    + _SQL_FILES_SOURCE
)

# Tier facet counts (webui spec §3.3): one row per tier, ``COUNT(DISTINCT ed2k_hash)`` files that
# have at least one CURRENT decision of that tier. Grouped over ``latest_dec`` (per-decision), so
# a multi-tier file counts once under each of its tiers. The join to ``latest_obs`` is only there
# for the ``query`` filter clause. The tier filter itself is NEVER applied here (a facet shows the
# count you would get by choosing each option).
_SQL_TIER_COUNTS_BASE = (
    _SQL_CTES
    + """\
SELECT ld.tier AS tier, COUNT(DISTINCT ld.ed2k_hash) AS n
FROM latest_dec AS ld
JOIN files AS f ON f.ed2k_hash = ld.ed2k_hash
LEFT JOIN latest_obs AS obs ON obs.ed2k_hash = ld.ed2k_hash
"""
)

# All observations of a file (timeline), chronological order.
_SQL_OBSERVATIONS = """\
SELECT
    id,
    filename,
    size_bytes,
    source_count,
    complete_source_count,
    media_length_sec,
    bitrate_kbps,
    keyword,
    observed_at,
    node_id
FROM file_observations
WHERE ed2k_hash = ?
ORDER BY observed_at ASC, id ASC
"""

# All current decisions of a file: latest per (ed2k_hash, target_id), excluding the legacy
# ``target_id == ''`` sentinel and any target whose latest row is a ``retracted`` marker.
_SQL_FILE_DECISIONS = """\
SELECT target_id, rule_name, tier, decided_at, node_id
FROM (
    SELECT
        md.target_id,
        md.rule_name,
        md.tier,
        md.decided_at,
        md.node_id,
        ROW_NUMBER() OVER (
            PARTITION BY md.target_id
            ORDER BY md.decided_at DESC, md.id DESC
        ) AS rn
    FROM match_decisions AS md
    WHERE md.ed2k_hash = ?
)
WHERE rn = 1
AND target_id != ''
AND tier != 'retracted'
ORDER BY target_id
"""

# Compacted days of a file, oldest first (the last one is its latest range).
_SQL_RANGES = """\
SELECT bucket, filenames, observation_count, source_count_min, source_count_max
FROM file_observation_ranges
WHERE ed2k_hash = ?
ORDER BY last_observed_at ASC, id ASC
"""

# Basic lookup on files (for file_detail).
_SQL_FILE = """\
SELECT ed2k_hash, size_bytes, aich_hash
FROM files
WHERE ed2k_hash = ?
"""


def _filter_clauses(
    target: str | None,
    tier: str | None,
    query: str | None,
) -> tuple[list[str], list[str]]:
    """Shared WHERE clauses + params for the explorer list and its counter.

    ``target``/``tier`` match a file if ANY of its current decisions matches (EXISTS over the
    ``latest_dec`` CTE), so a whole-episode file appears under each of its targets. The
    ``target`` clause additionally excludes ``catalog``-tier rows (they are pinned to ``001A``),
    consistent with ``coverage_for``. The matched-only clause and LIMIT/OFFSET are list-specific
    and are NOT built here.
    """
    clauses: list[str] = []
    params: list[str] = []
    if target is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM latest_dec AS fdt"
            " WHERE fdt.ed2k_hash = f.ed2k_hash AND fdt.target_id = ? AND fdt.tier != 'catalog')"
        )
        params.append(target)
    if tier is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM latest_dec AS fdt"
            " WHERE fdt.ed2k_hash = f.ed2k_hash AND fdt.tier = ?)"
        )
        params.append(tier)
    if query is not None:
        # The latest name, or any name kept in a compacted range.
        clauses.append(
            "(obs.filename LIKE ? OR EXISTS (SELECT 1 FROM file_observation_ranges AS qr,"
            " json_each(qr.filenames) AS qn WHERE qr.ed2k_hash = f.ed2k_hash AND qn.value LIKE ?))"
        )
        params.extend([f"%{query}%"] * 2)
    return clauses, params


def _split_concat(concat: str | None) -> list[str]:
    """Split a ``char(31)``-joined aggregate (``group_concat``) into parts. ``None`` (a file
    with no current decision → the LEFT JOIN yields NULL) → an empty list."""
    return concat.split("\x1f") if concat is not None else []


# ---------------------------------------------------------------------------
# CatalogReader
# ---------------------------------------------------------------------------


class CatalogReader:
    """Read-only access to the catalog via a SQLite connection (see ``reader.open_reader``)."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    # ------------------------------------------------------------------
    # Coverage
    # ------------------------------------------------------------------

    def target_coverage(self) -> dict[str, list[tuple[str, str]]]:
        """Return, for each ``target_id``, the list of ``(ed2k_hash, tier)``
        from each file's LATEST match decision **per target** (a whole-episode
        file appears under every target it currently matches).
        """
        rows = self._conn.execute(_SQL_COVERAGE).fetchall()
        result: dict[str, list[tuple[str, str]]] = {}
        for row in rows:
            target_id: str = row["target_id"]
            entry = (row["ed2k_hash"], row["tier"])
            if target_id not in result:
                result[target_id] = []
            result[target_id].append(entry)
        return result

    # ------------------------------------------------------------------
    # Filtered paginated explorer
    # ------------------------------------------------------------------

    def list_files(
        self,
        *,
        target: str | None,
        tier: str | None,
        query: str | None,
        page: int,
        matched_only: bool = False,
        sort: str = DEFAULT_SORT,
        direction: str = DEFAULT_DIR,
    ) -> list[FileRow]:
        """Return a page of ``FileRow`` (size ``_PAGE_SIZE``) with optional filters.

        Filters:
        - ``target`` : keep a file if ANY of its current decisions matches this target_id.
        - ``tier``   : keep a file if ANY of its current decisions has this tier.
        - ``query``  : substring (LIKE ``%query%``) of the latest name or of any compacted one.
        - ``matched_only``: when true, keep only files with at least one current decision
          (retractions and the legacy ``target_id == ''`` sentinel never produce one).
          Default false = whole catalogue.
        - ``page``   : page number (1-based).

        Sort:
        - ``sort``     : one of the ``SORT_COLUMNS`` allowlist keys
          (``name``/``size``/``sources``/``last_seen``/``tier``); an unknown key falls back to
          ``DEFAULT_SORT`` (``last_seen``). ``tier`` sorts by the file's strongest tier rank.
        - ``direction``: ``asc`` or ``desc``; an unknown value falls back to ``DEFAULT_DIR``
          (``desc``). SQLite sorts NULLs first in ASC, so a NULL ``filename``/``observed_at``
          (a file with no observation) clusters predictably. The ``ed2k_hash`` tiebreak keeps
          paging stable.
        """
        clauses, str_params = _filter_clauses(target, tier, query)
        if matched_only:
            # A file is matched iff it has at least one current (non-retracted) decision;
            # ``dec.target_ids`` is NULL for a file with none.
            clauses.append("dec.target_ids IS NOT NULL")
        params: list[str | int] = [*str_params]

        sql = _SQL_LIST_FILES_BASE
        if clauses:
            sql += "WHERE " + " AND ".join(clauses) + "\n"
        # ORDER BY from the allowlist (spec §3.1): unknown sort/direction fall back to the
        # default; the ed2k_hash tiebreak keeps paging stable. Both operands come from fixed
        # maps, never from the raw param, so the f-string is injection-safe.
        column_expr = SORT_COLUMNS.get(sort, SORT_COLUMNS[DEFAULT_SORT])
        dir_sql = SORT_DIRECTIONS.get(direction, SORT_DIRECTIONS[DEFAULT_DIR])
        sql += f"ORDER BY {column_expr} {dir_sql}, f.ed2k_hash\n"
        sql += "LIMIT ? OFFSET ?\n"
        params.append(_PAGE_SIZE)
        params.append((page - 1) * _PAGE_SIZE)

        rows = self._conn.execute(sql, params).fetchall()
        result: list[FileRow] = []
        for row in rows:
            target_ids = _split_concat(row["target_ids"])
            tiers = _split_concat(row["tiers"])
            decisions = tuple(
                FileDecision(target_id=t, tier=ti) for t, ti in zip(target_ids, tiers, strict=True)
            )
            result.append(
                FileRow(
                    ed2k_hash=row["ed2k_hash"],
                    size_bytes=row["size_bytes"],
                    # A file with no observation LEFT JOINs to NULLs; an unknown count stays None.
                    filename=row["filename"] or "",
                    source_count=row["source_count"],
                    last_seen=row["last_seen"] or "",
                    decisions=decisions,
                )
            )
        return result

    def count_files(
        self,
        *,
        target: str | None,
        tier: str | None,
        query: str | None,
    ) -> tuple[int, int]:
        """Return ``(matched, total)`` file counts in the current filter scope.

        ``total`` = files matching the ``target/tier/query`` filters (the
        matched-only clause is deliberately NOT applied); ``matched`` = of those, how many
        have a match decision. Feeds the /files summary line.
        """
        clauses, params = _filter_clauses(target, tier, query)
        sql = _SQL_COUNT_FILES_BASE
        if clauses:
            sql += "WHERE " + " AND ".join(clauses) + "\n"
        row = self._conn.execute(sql, params).fetchone()
        matched: int = row["matched"]
        total: int = row["total"]
        return (matched, total)

    def tier_counts(
        self,
        *,
        target: str | None,
        query: str | None,
    ) -> dict[str, int]:
        """Return ``{tier: file_count}`` for the tier facet, applying the ``target``/``query``
        filters but NEVER a tier filter (facet-lite: each option shows the count you
        would get by choosing it). A file counts under every tier it currently holds a decision
        in (a multi-tier file appears in two facets). ``{}`` on an empty/undecided catalogue.
        """
        clauses, params = _filter_clauses(target, None, query)
        sql = _SQL_TIER_COUNTS_BASE
        if clauses:
            sql += "WHERE " + " AND ".join(clauses) + "\n"
        sql += "GROUP BY ld.tier\n"
        rows = self._conn.execute(sql, params).fetchall()
        return {row["tier"]: row["n"] for row in rows}

    # ------------------------------------------------------------------
    # Detail
    # ------------------------------------------------------------------

    def file_detail(self, ed2k_hash: str) -> FileDetail | None:
        """Return the full detail of a file, or ``None`` if unknown."""
        file_row = self._conn.execute(_SQL_FILE, (ed2k_hash,)).fetchone()
        if file_row is None:
            return None

        obs_rows = self._conn.execute(_SQL_OBSERVATIONS, (ed2k_hash,)).fetchall()
        dec_rows = self._conn.execute(_SQL_FILE_DECISIONS, (ed2k_hash,)).fetchall()
        range_rows = self._conn.execute(_SQL_RANGES, (ed2k_hash,)).fetchall()
        name_rows = self._conn.execute(SELECT_KNOWN_FILENAMES, {"hash": ed2k_hash}).fetchall()

        decisions = tuple(
            DecisionView(
                target_id=row["target_id"],
                rule_name=row["rule_name"],
                tier=row["tier"],
                decided_at=row["decided_at"],
                node_id=row["node_id"],
            )
            for row in dec_rows
        )

        return FileDetail(
            ed2k_hash=file_row["ed2k_hash"],
            size_bytes=file_row["size_bytes"],
            aich_hash=file_row["aich_hash"],
            observations=tuple(
                ObservationRow(
                    id=row["id"],
                    filename=row["filename"],
                    size_bytes=row["size_bytes"],
                    source_count=row["source_count"],
                    complete_source_count=row["complete_source_count"],
                    media_length_sec=row["media_length_sec"],
                    bitrate_kbps=row["bitrate_kbps"],
                    keyword=row["keyword"],
                    observed_at=row["observed_at"],
                    node_id=row["node_id"],
                )
                for row in obs_rows
            ),
            decisions=decisions,
            ranges=tuple(
                RangeRow(
                    bucket=row["bucket"],
                    filenames=tuple(json.loads(row["filenames"])),
                    observation_count=row["observation_count"],
                    source_count_min=row["source_count_min"],
                    source_count_max=row["source_count_max"],
                )
                for row in range_rows
            ),
            known_filenames=tuple(row[0] for row in name_rows),
        )
