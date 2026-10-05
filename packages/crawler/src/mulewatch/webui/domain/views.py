"""PRECOMPUTED view-models (webui spec W-D8): the templates only iterate and interpolate
these fields. No template-side logic."""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class CoverageStatus:
    status: str  # "found" | "partial" | "none"
    best_tier: str | None  # "download" | "notify" | "catalog" | None
    file_count: int


# ---------------------------------------------------------------------------
# Top nav (shared by every page via a context processor)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NavItem:
    """One entry of the shared top nav, precomputed (W-D8: no template logic).

    ``link`` is a 0-or-1 element tuple holding the href, so the template picks the rendering with
    ``{% for %}``/``{% else %}`` (the guard forbids ``{% if %}``): non-empty renders a link, empty
    means this entry IS the current page and renders as a non-link marked ``aria-current="page"``.
    That attribute is the CSS hook that bolds it, which is why no page needs an ``<h1>`` naming
    itself.
    """

    label: str
    link: tuple[str, ...]  # 0 or 1 element: the href, empty when this is the current page


# ---------------------------------------------------------------------------
# Dashboard · coverage per target
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetCoverageRow:
    """Dashboard row: a target's coverage."""

    target_id: str
    title: str
    status: str  # "found" | "partial" | "none"
    best_tier_display: str  # best_tier or "·"
    file_count: int


# ---------------------------------------------------------------------------
# File explorer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileDecision:
    """One current decision on a file: the latest match decision for a given
    ``(ed2k_hash, target_id)``, already filtered to exclude retractions and the legacy
    ``target_id == ""`` sentinel (webui spec §9). A whole-episode file carries two (``072A``
    and ``072B``); an unidentified file carries one (``tier == "catalog"``); a file with no
    current match carries none."""

    target_id: str
    tier: str


@dataclass(frozen=True)
class FileRow:
    """Summary view of a file for the explorer (paginated list)."""

    ed2k_hash: str
    size_bytes: int
    filename: str  # latest observed name
    source_count: int  # source count (latest observation)
    last_seen: str  # observed_at of the latest observation (ISO-8601 UTC)
    decisions: tuple[FileDecision, ...]  # current decisions, latest per target, 0..N


# ---------------------------------------------------------------------------
# File detail
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservationRow:
    """One entry in the observations timeline."""

    id: int
    filename: str
    size_bytes: int
    source_count: int
    complete_source_count: int
    media_length_sec: int | None
    bitrate_kbps: int | None
    keyword: str
    observed_at: str
    node_id: str


@dataclass(frozen=True)
class DecisionView:
    """Latest match decision for a file."""

    target_id: str
    rule_name: str
    tier: str
    decided_at: str
    node_id: str


@dataclass(frozen=True)
class FileDetail:
    """Full view of a file: timeline + current decisions."""

    ed2k_hash: str
    size_bytes: int
    aich_hash: str | None
    observations: tuple[ObservationRow, ...]
    decisions: tuple[DecisionView, ...]  # current decisions, latest per target, 0..N


# ---------------------------------------------------------------------------
# Node state (local.db)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DownloadRow:
    """An active or completed download (downloads table)."""

    ed2k_hash: str
    target_id: str
    state: str
    queued_at: str
    completed_at: str | None
    size_bytes: int


@dataclass(frozen=True)
class SchedulerEntry:
    """A scheduler key/value pair (precomputed for the template)."""

    key: str
    value: str


@dataclass(frozen=True)
class NodeState:
    """Full node state: downloads, scheduler, identity."""

    downloads: tuple[DownloadRow, ...]
    scheduler: Mapping[str, str]  # all scheduler_state pairs
    node_id: str | None  # None if absent from node_runtime
    created_at: str | None  # None if absent from node_runtime


# ---------------------------------------------------------------------------
# File explorer: display row (precomputed)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionCell:
    """One current decision, resolved for display: the target locator and its episode title.

    ``target`` is the canonical id joined with its seasonal locator (``"062A / S02E11A"``), a raw
    id no longer in the catalogue, or ``"unidentified"`` (the ``catalog``-tier mask). ``title`` is
    the episode title, or ``"·"`` when there is none (unidentified / unknown id)."""

    target: str
    title: str


@dataclass(frozen=True)
class FileRowDisplay:
    """Row of the paginated file list: all fields precomputed (webui spec W-D8, Task 3/9).

    A file usually carries 0, 1, or 2 current decisions (``FileRow.decisions``, already
    excluding retractions and the legacy ``target_id == ""`` sentinel: those never reach
    this layer). Each current decision becomes one ``DecisionCell`` in ``decisions_display``:

    - no decisions at all → ``decisions_display`` is empty and ``tier_display`` is the
      literal ``"·"``.
    - per decision, ``DecisionCell.target``/``.title`` resolve via
      ``composition.app._resolve_target_display``: ``tier == "catalog"`` → ``"unidentified"``
      / ``"·"`` (the ``keroro_large`` catch-all, the only catalog-tier rule); otherwise the
      target is looked up in the current catalogue: found → the canonical id joined with its
      seasonal locator (e.g. ``"062A / S02E11A"``) + the episode title; not found (a
      target_id no longer in the current targets.yaml) → the raw id + ``"·"``.
    - ``tier_display`` is the shared tier when all decisions agree, else each decision listed
      as ``"{target_id}: {tier}"`` joined with ``" · "``.
    """

    ed2k_hash: str
    short_hash: str
    filename: str
    source_count: int
    decisions_display: tuple[DecisionCell, ...]  # one per current decision, 0..N; () when none
    size_display: str  # human_size(size_bytes)
    last_seen_display: str  # short_timestamp(last_seen)
    tier_display: str  # shared tier, or "target_id: tier" per decision joined with " · "
    ed2k_link: str


@dataclass(frozen=True)
class PageNav:
    """Paginated navigation of a list: precomputed handler-side (spec W-D8: no logic in the
    template). ``prev_url``/``next_url`` are ``None`` when the end is reached; the template
    iterates ``(url,) if url`` to render the link when it exists.
    """

    page: int
    prev_url: str | None
    next_url: str | None


@dataclass(frozen=True)
class FilesSummary:
    """Precomputed summary line for the /files explorer (spec W-D8: no template logic).

    ``summary_text`` states how many files are shown vs. catalogued; ``toggle_label`` +
    ``toggle_url`` flip between matched-only (default) and the whole catalogue.
    """

    summary_text: str
    toggle_label: str
    toggle_url: str


@dataclass(frozen=True)
class SortHeader:
    """A sortable column header, fully precomputed (W-D8). ``url`` re-sorts by this column
    (flipping direction when it is the active one, else a per-column default direction),
    preserving every active filter; ``indicator`` is ``""``, ``"asc"``, or ``"desc"`` (the
    template renders it as ``data-sort`` and CSS draws the arrow)."""

    label: str
    url: str
    indicator: str  # "" | "asc" | "desc"


@dataclass(frozen=True)
class SortHeaders:
    """The five sortable headers of the /files table, one attribute per column so the fixed
    thead can interpolate ``{{ headers.name.url }}`` etc. with no template logic (W-D8)."""

    name: SortHeader
    size: SortHeader
    sources: SortHeader
    last_seen: SortHeader
    tier: SortHeader


@dataclass(frozen=True)
class TierFacet:
    """One tier-filter option with its live count, precomputed (W-D8). ``label`` is the display
    tier (``catalog`` masked to ``"unidentified"``, or the literal ``"all"`` reset);
    ``count_display`` is ``"(<n>)"`` for a tier and ``""`` for the reset; ``url`` selects (or, for
    the reset, clears) this tier while preserving other params and resetting page;
    ``selected_flag`` is ``"1"`` or ``""`` (rendered as ``data-selected`` for CSS)."""

    label: str
    count_display: str
    url: str
    selected_flag: str


@dataclass(frozen=True)
class HiddenInput:
    """One hidden form field carried by the search GET form (name/value already stringified)."""

    name: str
    value: str


@dataclass(frozen=True)
class SearchBar:
    """The filename search form, precomputed (W-D8): ``query`` prefills the text input, ``hidden``
    carries every active param except ``q`` and ``page`` so submitting a search preserves them."""

    query: str
    hidden: tuple[HiddenInput, ...]


@dataclass(frozen=True)
class FilterBar:
    """The /files filter bar: the search form + the tier facet. Passed as a 0-or-1-element tuple
    so ``handle_target`` can reuse ``files.html`` with an empty bar (like ``summaries``)."""

    searchbar: SearchBar
    facets: tuple[TierFacet, ...]


# ---------------------------------------------------------------------------
# File detail · display view (precomputed)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# SQL console (spec §11) · precomputed for the logic-free template
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConsoleRow:
    """One result row: its cells already stringified (NULL rendered as the literal "NULL")."""

    cells: tuple[str, ...]


@dataclass(frozen=True)
class ConsoleResult:
    """A successful console query result, all fields precomputed for the template (W-D8).

    ``truncated`` is a 0-or-1 element message tuple so the template can render the truncation
    banner with ``{% for t in res.truncated %}`` (the guard forbids ``{% if %}``).
    """

    columns: tuple[str, ...]
    rows: tuple[ConsoleRow, ...]
    row_count: int
    elapsed_ms: int
    truncated: tuple[str, ...]  # 0 or 1 message: the truncation banner text


@dataclass(frozen=True)
class DbOption:
    """One ``<select>`` option for the console DB picker. ``selected_attr`` is precomputed as the
    literal ``"selected"`` or ``""`` so the template renders ``{{ o.selected_attr }}`` with no
    logic."""

    value: str
    label: str
    selected_attr: str  # "selected" or ""


@dataclass(frozen=True)
class FileDetailDisplay:
    """Full view of a file: all fields precomputed for the template.

    ``decisions`` holds 0..N current decisions (latest per target, excluding retractions and
    the legacy ``target_id == ""`` sentinel) to allow the iteration
    ``{% for d in file.decisions %}`` in the template (the guard forbids {% if %}).
    ``explanation_notes`` is empty if there is no explanation, containing a single element
    (the text note) otherwise: allows conditional iteration without {% if %}.
    """

    ed2k_hash: str
    size_bytes: int
    aich_hash_display: str  # aich_hash or "·"
    observations: tuple[ObservationRow, ...]
    decisions: tuple[DecisionView, ...]  # 0..N elements: for template iteration
    ed2k_link: str  # precomputed from the latest observation
    # Explanation fields (None if no explanation available)
    explanation_target_id: str | None
    explanation_rules_fired: tuple[str, ...]
    explanation_tokens_matched: tuple[str, ...]
    explanation_notes: tuple[str, ...]  # 0 or 1 element: the text note itself
