"""TDD tests for the read-only SQL console (spec §11): the ``run_query`` execution adapter and
the ``/console`` + ``/console.csv`` routes.

The adapter tests seed a real ``catalog.db`` via the writer (``open_catalog``) so ``open_reader``
has a real ``mode=ro`` file to open, exactly like the ``test_reader.py`` persistence tests.
The route tests build the app with the shared webui fixtures + a no-op control.
"""

from pathlib import Path

import pytest
import yaml
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette

from catalog_matching.config import MatcherConfig
from catalog_matching.models import TargetSegment
from catalog_matching.validation import parse_matcher_config, parse_targets
from mulewatch.adapters.persistence_sqlite.connection import open_catalog
from mulewatch.webui.adapters.sql_console import ConsoleOutcome, run_query
from mulewatch.webui.composition.app import build_app
from mulewatch.webui.domain.views import ConsoleResult, ConsoleRow, DbOption

_HASH_A = "a" * 32


class _StubControl:
    """No-op ``CrawlerControl`` (the console routes never touch it)."""

    def force_cycle(self) -> None:  # pragma: no cover - never called by console routes
        pass

    def pause(self) -> None:  # pragma: no cover
        pass

    def resume(self) -> None:  # pragma: no cover
        pass

    def restart(self) -> None:  # pragma: no cover
        pass


def _seed_catalog(path: Path) -> None:
    """Create + seed a real catalog.db via the writer. ``aich_hash`` is left NULL so a
    ``SELECT aich_hash`` exercises the ``None -> 'NULL'`` rendering."""
    writer = open_catalog(path)
    try:
        writer.execute(
            "INSERT INTO files (ed2k_hash, size_bytes, aich_hash) VALUES (?, ?, ?)",
            (_HASH_A, 10, None),
        )
    finally:
        writer.close()


# ---------------------------------------------------------------------------
# run_query · the execution adapter (spec §11)
# ---------------------------------------------------------------------------


def test_run_query_select_returns_columns_and_rows(tmp_path: Path) -> None:
    """A SELECT returns the column names and the stringified rows, no error, not truncated."""
    path = tmp_path / "catalog.db"
    _seed_catalog(path)
    outcome = run_query(
        db_path=path,
        sql="SELECT ed2k_hash, size_bytes FROM files",
        row_cap=1000,
        timeout_seconds=5.0,
    )
    assert outcome.error is None
    assert outcome.columns == ("ed2k_hash", "size_bytes")
    assert outcome.rows == ((_HASH_A, "10"),)
    assert outcome.row_count == 1
    assert outcome.truncated is False
    assert outcome.elapsed_ms >= 0


def test_run_query_renders_none_cell_as_null_literal(tmp_path: Path) -> None:
    """A NULL cell is rendered as the literal string ``NULL`` (not an empty cell)."""
    path = tmp_path / "catalog.db"
    _seed_catalog(path)
    outcome = run_query(
        db_path=path, sql="SELECT aich_hash FROM files", row_cap=1000, timeout_seconds=5.0
    )
    assert outcome.error is None
    assert outcome.rows == (("NULL",),)


def test_run_query_write_is_rejected_read_only(tmp_path: Path) -> None:
    """A write statement is structurally impossible (mode=ro + query_only) -> read-only error."""
    path = tmp_path / "catalog.db"
    _seed_catalog(path)
    outcome = run_query(
        db_path=path,
        sql="INSERT INTO files (ed2k_hash, size_bytes) VALUES ('b', 1)",
        row_cap=1000,
        timeout_seconds=5.0,
    )
    assert outcome.error is not None
    assert "read-only" in outcome.error
    assert outcome.rows == ()


def test_run_query_multi_statement_is_rejected(tmp_path: Path) -> None:
    """Two statements in one input -> single-statement error (sqlite3.ProgrammingError)."""
    path = tmp_path / "catalog.db"
    _seed_catalog(path)
    outcome = run_query(db_path=path, sql="SELECT 1; SELECT 2", row_cap=1000, timeout_seconds=5.0)
    assert outcome.error is not None
    assert "single" in outcome.error.lower()


def test_run_query_row_cap_truncates_and_flags(tmp_path: Path) -> None:
    """More rows than the cap: keep the first ``row_cap`` and flag truncation."""
    path = tmp_path / "catalog.db"
    _seed_catalog(path)
    outcome = run_query(
        db_path=path,
        sql="WITH RECURSIVE r(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM r WHERE x < 5)"
        " SELECT x FROM r",
        row_cap=2,
        timeout_seconds=5.0,
    )
    assert outcome.error is None
    assert outcome.truncated is True
    assert outcome.row_count == 2
    assert outcome.rows == (("1",), ("2",))


def test_run_query_runaway_is_aborted_by_timeout(tmp_path: Path) -> None:
    """A runaway query (huge recursive aggregate) is aborted by the wall-clock timeout, and the
    error is the timeout message, distinct from a generic SQL error."""
    path = tmp_path / "catalog.db"
    _seed_catalog(path)
    outcome = run_query(
        db_path=path,
        sql="WITH RECURSIVE r(i) AS (SELECT 0 UNION ALL SELECT i + 1 FROM r WHERE i < 100000000)"
        " SELECT count(*) FROM r",
        row_cap=1000,
        timeout_seconds=0.05,
    )
    assert outcome.error is not None
    assert "time limit" in outcome.error
    assert "SQL error" not in outcome.error


def test_run_query_malformed_sql_yields_error_not_exception(tmp_path: Path) -> None:
    """A syntax error is absorbed into an error result (not raised) - the read-only console
    boundary absorbs arbitrary operator SQL."""
    path = tmp_path / "catalog.db"
    _seed_catalog(path)
    outcome = run_query(db_path=path, sql="SELECT FROM", row_cap=1000, timeout_seconds=5.0)
    assert outcome.error is not None
    assert "syntax" in outcome.error.lower()


def test_run_query_other_sqlite_error_is_absorbed(tmp_path: Path) -> None:
    """A non-Operational, non-Programming sqlite3.Error (here a DataError: blob too big) is
    caught by the console catch-all and absorbed into an error result."""
    path = tmp_path / "catalog.db"
    _seed_catalog(path)
    outcome = run_query(
        db_path=path,
        sql="SELECT length(zeroblob(1000000000000))",
        row_cap=1000,
        timeout_seconds=5.0,
    )
    assert outcome.error is not None
    assert "too big" in outcome.error


def test_run_query_statement_without_result_columns_is_safe(tmp_path: Path) -> None:
    """A read-only statement that returns no result set (cursor.description is None, e.g. a
    comment-only input) yields empty columns/rows, never a crash."""
    path = tmp_path / "catalog.db"
    _seed_catalog(path)
    outcome = run_query(db_path=path, sql="-- just a comment", row_cap=1000, timeout_seconds=5.0)
    assert outcome.error is None
    assert outcome.columns == ()
    assert outcome.rows == ()
    assert outcome.row_count == 0


# ---------------------------------------------------------------------------
# View-models
# ---------------------------------------------------------------------------


def test_console_row_holds_cells() -> None:
    assert ConsoleRow(cells=("a", "b")).cells == ("a", "b")


def test_console_result_holds_fields() -> None:
    result = ConsoleResult(
        columns=("x",),
        rows=(ConsoleRow(cells=("1",)),),
        row_count=1,
        elapsed_ms=3,
        truncated=(),
    )
    assert result.columns == ("x",)
    assert result.row_count == 1
    assert result.truncated == ()


def test_db_option_precomputes_selected_attr() -> None:
    assert DbOption(
        value="catalog", label="catalog.db", selected_attr="selected"
    ).selected_attr == ("selected")


def test_console_outcome_defaults_are_empty() -> None:
    """An error outcome only sets ``error``; the rest default to empty/zero."""
    outcome = ConsoleOutcome(error="boom")
    assert outcome.columns == ()
    assert outcome.rows == ()
    assert outcome.row_count == 0
    assert outcome.truncated is False


# ---------------------------------------------------------------------------
# Route fixtures
# ---------------------------------------------------------------------------


def _targets() -> tuple[TargetSegment, ...]:
    return parse_targets(
        yaml.safe_load(
            """\
episodes:
  - season: 2
    seasonal_number: 11
    absolute_number: 62
    segments:
      - letter: a
        title: "La Grenouille Cosmique"
"""
        )
    )


def _matcher() -> MatcherConfig:
    return parse_matcher_config(
        yaml.safe_load(
            """\
tokens:
  keroro:
    keyword: keroro
rules:
  - name: catalog
    tier: catalog
    any:
      - keroro
"""
        )
    )


def _build(catalog_db: Path, local_db: Path) -> Starlette:
    matcher_config = _matcher()
    targets = _targets()
    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"
    return build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        matcher_config=matcher_config,
        targets=targets,
        templates_dir=templates_dir,
        static_dir=static_dir,
        control=_StubControl(),
        amule_url="http://localhost:4711",
    )


@pytest.fixture
def console_app(catalog_db: Path, local_db: Path) -> Starlette:
    """App wired against real (schema-only) DBs, plus one catalog file + one scheduler row so
    both DBs return something readable through the console."""
    import sqlite3

    with sqlite3.connect(catalog_db) as conn:
        conn.execute("INSERT INTO files VALUES (?, ?, ?)", (_HASH_A, 10, None))
        conn.commit()
    with sqlite3.connect(local_db) as conn:
        conn.execute(
            "INSERT INTO scheduler_state VALUES (?, ?)", ("last_search_cycle", "2024-01-01")
        )
        conn.commit()
    return _build(catalog_db, local_db)


# ---------------------------------------------------------------------------
# GET /console
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_console_renders_empty_form(console_app: Starlette) -> None:
    """GET /console -> 200 with the form (textarea, Run, Download CSV, db selector), no result."""
    async with AsyncClient(
        transport=ASGITransport(app=console_app), base_url="http://test"
    ) as client:
        resp = await client.get("/console")
    assert resp.status_code == 200
    assert 'name="sql"' in resp.text
    assert 'name="db"' in resp.text
    assert ">Run<" in resp.text
    assert "Download CSV" in resp.text
    assert 'formaction="/console.csv"' in resp.text
    assert 'class="console-error"' not in resp.text
    assert 'class="console-table"' not in resp.text


@pytest.mark.asyncio
async def test_get_console_defaults_to_catalog_selected(console_app: Starlette) -> None:
    """GET /console selects the catalog DB by default."""
    async with AsyncClient(
        transport=ASGITransport(app=console_app), base_url="http://test"
    ) as client:
        resp = await client.get("/console")
    assert resp.status_code == 200
    assert '<option value="catalog" selected>' in resp.text


@pytest.mark.asyncio
async def test_console_nav_entry_links_from_elsewhere_and_is_active_on_console(
    console_app: Starlette,
) -> None:
    """The base nav carries a Console entry: a link (href="/console") from any other page, and
    the active non-link on /console itself, where that href is gone by construction."""
    async with AsyncClient(
        transport=ASGITransport(app=console_app), base_url="http://test"
    ) as client:
        elsewhere = await client.get("/")
        on_console = await client.get("/console")
    assert elsewhere.status_code == 200
    assert '<a href="/console">Console</a>' in elsewhere.text
    assert on_console.status_code == 200
    assert '<span aria-current="page">Console</span>' in on_console.text
    assert '<a href="/console">Console</a>' not in on_console.text


# ---------------------------------------------------------------------------
# POST /console
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_console_valid_select_renders_result(console_app: Starlette) -> None:
    """POST a valid SELECT -> 200, result table with the value, SQL echoed, db still selected."""
    async with AsyncClient(
        transport=ASGITransport(app=console_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/console", data={"sql": "SELECT ed2k_hash FROM files", "db": "catalog"}
        )
    assert resp.status_code == 200
    assert _HASH_A in resp.text  # the row value rendered
    assert "SELECT ed2k_hash FROM files" in resp.text  # SQL echoed into the textarea
    assert '<option value="catalog" selected>' in resp.text
    assert 'class="console-table"' in resp.text


@pytest.mark.asyncio
async def test_post_console_bogus_db_shows_error_no_query(console_app: Starlette) -> None:
    """POST db=bogus -> 200 with an error banner, no query run (no result table)."""
    async with AsyncClient(
        transport=ASGITransport(app=console_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/console", data={"sql": "SELECT ed2k_hash FROM files", "db": "bogus"}
        )
    assert resp.status_code == 200
    assert 'class="console-error"' in resp.text
    assert 'class="console-table"' not in resp.text
    assert _HASH_A not in resp.text


@pytest.mark.asyncio
async def test_post_console_local_db_is_queried(console_app: Starlette) -> None:
    """POST db=local reads local.db (the seeded scheduler_state row comes back)."""
    async with AsyncClient(
        transport=ASGITransport(app=console_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/console", data={"sql": "SELECT key FROM scheduler_state", "db": "local"}
        )
    assert resp.status_code == 200
    assert "last_search_cycle" in resp.text
    assert '<option value="local" selected>' in resp.text


@pytest.mark.asyncio
async def test_post_console_query_error_shows_error_banner(console_app: Starlette) -> None:
    """A SQL error POSTed to /console renders in the error banner, still HTTP 200."""
    async with AsyncClient(
        transport=ASGITransport(app=console_app), base_url="http://test"
    ) as client:
        resp = await client.post("/console", data={"sql": "SELECT FROM", "db": "catalog"})
    assert resp.status_code == 200
    assert 'class="console-error"' in resp.text
    assert 'class="console-table"' not in resp.text


@pytest.mark.asyncio
async def test_post_console_truncation_banner(console_app: Starlette) -> None:
    """A result over the row cap renders the truncation banner (multi-target end to end via a
    tiny generated set only needs the cap, so we rely on the default cap being small enough)."""
    async with AsyncClient(
        transport=ASGITransport(app=console_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/console",
            data={
                "sql": "WITH RECURSIVE r(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM r"
                " WHERE x < 2000) SELECT x FROM r",
                "db": "catalog",
            },
        )
    assert resp.status_code == 200
    assert 'class="console-truncated"' in resp.text


# ---------------------------------------------------------------------------
# POST /console.csv
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_console_csv_valid_select(console_app: Starlette) -> None:
    """POST a valid SELECT to /console.csv -> text/csv attachment with the CSV body."""
    async with AsyncClient(
        transport=ASGITransport(app=console_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/console.csv", data={"sql": "SELECT ed2k_hash FROM files", "db": "catalog"}
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    assert "query.csv" in resp.headers["content-disposition"]
    assert "ed2k_hash" in resp.text  # header row
    assert _HASH_A in resp.text  # data row


@pytest.mark.asyncio
async def test_post_console_csv_bad_db_returns_400(console_app: Starlette) -> None:
    """POST /console.csv with an unknown db -> 400 plain text with the error."""
    async with AsyncClient(
        transport=ASGITransport(app=console_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/console.csv", data={"sql": "SELECT ed2k_hash FROM files", "db": "bogus"}
        )
    assert resp.status_code == 400
    assert resp.headers["content-type"].startswith("text/plain")
    assert resp.text != ""


@pytest.mark.asyncio
async def test_post_console_csv_query_error_returns_400(console_app: Starlette) -> None:
    """POST /console.csv with a SQL error -> 400 plain text with the error message."""
    async with AsyncClient(
        transport=ASGITransport(app=console_app), base_url="http://test"
    ) as client:
        resp = await client.post("/console.csv", data={"sql": "SELECT FROM", "db": "catalog"})
    assert resp.status_code == 400
    assert "syntax" in resp.text.lower()
