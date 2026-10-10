"""Pure formatting for display (webui spec §4/§7). No I/O.

NB: building the eD2k link (with percent-encoding of the name) lives in
``catalog_matching.ed2k_link`` — a package shared by crawler+webui — to prevent
webui/crawler divergence on the canonical format (webui-security#0 regression: the
webui interpolated the raw filename, a hostile ``|`` broke the link framing)."""


def short_hash(native_id: str) -> str:
    """Truncated hash for display (first 8 characters + ellipsis)."""
    if len(native_id) <= 8:
        return native_id
    return f"{native_id[:8]}…"


_KIB = 1024
_MIB = _KIB * 1024
_GIB = _MIB * 1024
_TIB = _GIB * 1024


def human_size(size_bytes: int) -> str:
    """Human-readable file size, e.g. ``349 MB``.

    Unit convention: BINARY (1024-based) — matching this codebase's existing ``size_mb``
    convention (the matching engine's ``attr_between`` on ``size_mb`` is computed as
    ``size_bytes / (1024 * 1024)``, cf. ``adapters/matching_read.py``: "binary Mio"). The
    ``KB``/``MB``/``GB``/``TB`` labels are the familiar (if technically imprecise) ones, not
    ``KiB``/``MiB``/``GiB``/``TiB``. Rounds to the nearest whole unit — an operator-facing
    display, not a precise byte count.

    The unit is picked on the ROUNDED value, not the raw byte count: e.g. ``1024**2 - 1``
    bytes rounds to 1024 when divided by 1024 (KiB) — that must promote to ``"1 MB"``, not
    render as ``"1024 KB"``. Comparing on the un-rounded byte count against a fixed threshold
    (the previous bug) misses that a value just under a unit boundary can still round UP to
    the next unit's first whole number.
    """
    if size_bytes < _KIB:
        return f"{size_bytes} B"
    kib = round(size_bytes / _KIB)
    if kib < 1024:
        return f"{kib} KB"
    mib = round(size_bytes / _MIB)
    if mib < 1024:
        return f"{mib} MB"
    gib = round(size_bytes / _GIB)
    if gib < 1024:
        return f"{gib} GB"
    return f"{round(size_bytes / _TIB)} TB"


def short_timestamp(iso: str) -> str:
    """Trim an ISO-8601 UTC timestamp to ``YYYY-MM-DD HH:MM`` + a literal ``Z``.

    E.g. ``2026-07-03T23:45:24.104990+00:00`` → ``2026-07-03 23:45Z``. Works whether or not
    the input carries microseconds/a timezone offset: only the date and the first 5
    characters after ``T`` (``HH:MM``) are kept — everything after that (seconds,
    microseconds, offset) is dropped, not parsed. Input is assumed UTC (as stored by the
    persistence layer).

    An empty input formats to an empty string: "no timestamp" must render as nothing, since
    partitioning it would otherwise leave just the ``Z`` marker on the page. A file with no
    observation yet reaches here with ``last_seen == ""``.
    """
    if not iso:
        return ""
    date_part, _, time_part = iso.partition("T")
    return f"{date_part} {time_part[:5]}Z"


def seasonal_id(*, season: int, seasonal_number: int, letter: str) -> str:
    """Seasonal locator for display, e.g. ``S02E11A`` (zero-padded season/episode + segment
    letter). Complements the canonical ``target_id`` (e.g. ``062A``, absolute-numbered) —
    operators often think in season/episode terms rather than the absolute numbering."""
    return f"S{season:02d}E{seasonal_number:02d}{letter.upper()}"
