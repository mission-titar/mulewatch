"""TDD tests for the Starlette application (composition/app.py, Task 11)."""

import re
import sqlite3
from pathlib import Path

import pytest
import yaml
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette

from catalog_matching.config import MatcherConfig
from catalog_matching.models import TargetSegment
from catalog_matching.validation import parse_matcher_config, parse_targets
from mulewatch.webui.composition.app import (
    _normalize_dir,
    _normalize_sort,
    _resolve_target_display,
    _search_bar,
    _sort_headers,
    _tier_facets,
    _to_display_rows,
    build_app,
)
from mulewatch.webui.domain.views import (
    DecisionCell,
    FileDecision,
    FileRow,
    HiddenInput,
)

# ---------------------------------------------------------------------------
# Parsed-config helpers (since P4a build_app takes already-parsed config)
# ---------------------------------------------------------------------------

TEST_HASH = "aabbccdd00112233aabbccdd00112233"
_AMULE_URL = "http://localhost:4711"


class _RecordingControl:
    """Records runtime-control intents (phase P6a). Satisfies ``CrawlerControl`` structurally;
    the throwaway instances passed to the read-path fixtures are never invoked, while the
    controls-route tests inspect ``calls``."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def force_cycle(self) -> None:
        self.calls.append("force_cycle")

    def pause(self) -> None:
        self.calls.append("pause")

    def resume(self) -> None:
        self.calls.append("resume")

    def restart(self) -> None:
        self.calls.append("restart")


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


# ---------------------------------------------------------------------------
# Fixture: app with data
# ---------------------------------------------------------------------------


@pytest.fixture
def populated_app(catalog_db: Path, local_db: Path) -> tuple[Starlette, str]:
    """Insert test data and build the Starlette app."""
    with sqlite3.connect(catalog_db) as conn:
        conn.execute(
            "INSERT INTO files VALUES (?, ?, ?)",
            (TEST_HASH, 100_000_000, None),
        )
        conn.execute(
            "INSERT INTO file_observations VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TEST_HASH,
                "keroro_s2e62a_vf.avi",
                100_000_000,
                5,
                3,
                None,
                None,
                None,
                None,
                "{}",
                "keroro",
                "2024-01-01T00:00:00",
                "node1",
            ),
        )
        conn.execute(
            "INSERT INTO match_decisions VALUES (1, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "062A", "catalog", "catalog", "2024-01-01T00:00:00", "node1"),
        )
        conn.commit()

    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-test-001"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.execute(
            "INSERT INTO scheduler_state VALUES (?, ?)",
            ("last_search_cycle", "2024-01-01T00:00:00"),
        )
        conn.commit()

    matcher_config = _matcher()
    targets = _targets()

    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"

    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        matcher_config=matcher_config,
        targets=targets,
        templates_dir=templates_dir,
        static_dir=static_dir,
        control=_RecordingControl(),
        amule_url=_AMULE_URL,
    )
    return app, TEST_HASH


@pytest.fixture
def app_no_decision(catalog_db: Path, local_db: Path) -> tuple[Starlette, str]:
    """File without a match decision (decision=None branch)."""
    with sqlite3.connect(catalog_db) as conn:
        conn.execute(
            "INSERT INTO files VALUES (?, ?, ?)",
            (TEST_HASH, 100_000_000, None),
        )
        conn.execute(
            "INSERT INTO file_observations VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TEST_HASH,
                "keroro_s2e62a_vf.avi",
                100_000_000,
                5,
                3,
                None,
                None,
                None,
                None,
                "{}",
                "keroro",
                "2024-01-01T00:00:00",
                "node1",
            ),
        )
        # No decision
        conn.commit()

    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-no-dec"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.commit()

    matcher_config = _matcher()
    targets = _targets()

    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"

    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        matcher_config=matcher_config,
        targets=targets,
        templates_dir=templates_dir,
        static_dir=static_dir,
        control=_RecordingControl(),
        amule_url=_AMULE_URL,
    )
    return app, TEST_HASH


@pytest.fixture
def app_retracted_decision(catalog_db: Path, local_db: Path) -> tuple[Starlette, str]:
    """File whose LATEST decision for target 062A is a per-target retraction sentinel
    (``target_id="062A", rule_name="", tier="retracted"``): must be treated exactly like
    ``app_no_decision`` (unmatched), never like a real decision."""
    with sqlite3.connect(catalog_db) as conn:
        conn.execute(
            "INSERT INTO files VALUES (?, ?, ?)",
            (TEST_HASH, 100_000_000, None),
        )
        conn.execute(
            "INSERT INTO file_observations VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TEST_HASH,
                "keroro_s2e62a_vf.avi",
                100_000_000,
                5,
                3,
                None,
                None,
                None,
                None,
                "{}",
                "keroro",
                "2024-01-01T00:00:00",
                "node1",
            ),
        )
        # First a real decision (was matched)...
        conn.execute(
            "INSERT INTO match_decisions VALUES (1, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "062A", "high_confidence", "download", "2024-01-01T00:00:00", "node1"),
        )
        # ...then the per-target retraction sentinel, now the LATEST row for 062A.
        conn.execute(
            "INSERT INTO match_decisions VALUES (2, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "062A", "", "retracted", "2024-01-02T00:00:00", "node1"),
        )
        conn.commit()

    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-retracted"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.commit()

    matcher_config = _matcher()
    targets = _targets()

    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"

    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        matcher_config=matcher_config,
        targets=targets,
        templates_dir=templates_dir,
        static_dir=static_dir,
        control=_RecordingControl(),
        amule_url=_AMULE_URL,
    )
    return app, TEST_HASH


@pytest.fixture
def app_no_observations(catalog_db: Path, local_db: Path) -> tuple[Starlette, str]:
    """File without observations (last_obs=None branch → link='')."""
    with sqlite3.connect(catalog_db) as conn:
        conn.execute(
            "INSERT INTO files VALUES (?, ?, ?)",
            (TEST_HASH, 100_000_000, None),
        )
        # No observations
        conn.commit()

    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-no-obs"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.commit()

    matcher_config = _matcher()
    targets = _targets()

    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"

    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        matcher_config=matcher_config,
        targets=targets,
        templates_dir=templates_dir,
        static_dir=static_dir,
        control=_RecordingControl(),
        amule_url=_AMULE_URL,
    )
    return app, TEST_HASH


@pytest.fixture
def app_unknown_target(catalog_db: Path, local_db: Path) -> tuple[Starlette, str]:
    """File with a decision for a target_id unknown to the current config."""
    with sqlite3.connect(catalog_db) as conn:
        conn.execute(
            "INSERT INTO files VALUES (?, ?, ?)",
            (TEST_HASH, 100_000_000, None),
        )
        conn.execute(
            "INSERT INTO file_observations VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TEST_HASH,
                "keroro_s2e62a_vf.avi",
                100_000_000,
                5,
                3,
                None,
                None,
                None,
                None,
                "{}",
                "keroro",
                "2024-01-01T00:00:00",
                "node1",
            ),
        )
        # target_id unknown to the current YAML config
        conn.execute(
            "INSERT INTO match_decisions VALUES (1, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "S9E999Z", "catalog", "catalog", "2024-01-01T00:00:00", "node1"),
        )
        conn.commit()

    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-unk"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.commit()

    matcher_config = _matcher()
    targets = _targets()

    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"

    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        matcher_config=matcher_config,
        targets=targets,
        templates_dir=templates_dir,
        static_dir=static_dir,
        control=_RecordingControl(),
        amule_url=_AMULE_URL,
    )
    return app, TEST_HASH


@pytest.fixture
def app_download_tier_known_target(catalog_db: Path, local_db: Path) -> tuple[Starlette, str]:
    """A non-catalog decision (tier=download) on a target_id resolvable in the current
    targets.yaml. Task 3 resolution rule: the "resolvable id" case."""
    with sqlite3.connect(catalog_db) as conn:
        conn.execute(
            "INSERT INTO files VALUES (?, ?, ?)",
            (TEST_HASH, 100_000_000, None),
        )
        conn.execute(
            "INSERT INTO file_observations VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TEST_HASH,
                "keroro_s2e62a_vf.avi",
                100_000_000,
                5,
                3,
                None,
                None,
                None,
                None,
                "{}",
                "keroro",
                "2024-01-01T00:00:00",
                "node1",
            ),
        )
        conn.execute(
            "INSERT INTO match_decisions VALUES (1, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "062A", "high_confidence", "download", "2024-01-01T00:00:00", "node1"),
        )
        conn.commit()

    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-dl"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.commit()

    matcher_config = _matcher()
    targets = _targets()

    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"

    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        matcher_config=matcher_config,
        targets=targets,
        templates_dir=templates_dir,
        static_dir=static_dir,
        control=_RecordingControl(),
        amule_url=_AMULE_URL,
    )
    return app, TEST_HASH


@pytest.fixture
def app_download_tier_unknown_target(catalog_db: Path, local_db: Path) -> tuple[Starlette, str]:
    """A non-catalog decision (tier=download) on a target_id NOT in the current
    targets.yaml. Task 3 resolution rule: the "unknown id" case."""
    with sqlite3.connect(catalog_db) as conn:
        conn.execute(
            "INSERT INTO files VALUES (?, ?, ?)",
            (TEST_HASH, 100_000_000, None),
        )
        conn.execute(
            "INSERT INTO file_observations VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TEST_HASH,
                "keroro_s9e999z_vf.avi",
                100_000_000,
                5,
                3,
                None,
                None,
                None,
                None,
                "{}",
                "keroro",
                "2024-01-01T00:00:00",
                "node1",
            ),
        )
        conn.execute(
            "INSERT INTO match_decisions VALUES (1, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "999Z", "high_confidence", "download", "2024-01-01T00:00:00", "node1"),
        )
        conn.commit()

    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-dl-unk"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.commit()

    matcher_config = _matcher()
    targets = _targets()

    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"

    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        matcher_config=matcher_config,
        targets=targets,
        templates_dir=templates_dir,
        static_dir=static_dir,
        control=_RecordingControl(),
        amule_url=_AMULE_URL,
    )
    return app, TEST_HASH


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_returns_200(populated_app: tuple[Starlette, str]) -> None:
    """/health → 200 {"status": "ok"}."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_dashboard_returns_200_with_target_id(
    populated_app: tuple[Starlette, str],
) -> None:
    """/ → 200 + contains 062A in the page."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert "062A" in resp.text


@pytest.mark.asyncio
async def test_dashboard_excludes_catalog_tier_from_coverage(
    populated_app: tuple[Starlette, str],
) -> None:
    """The only decision in ``populated_app`` is the ``keroro_large`` catch-all (tier=catalog)
    on 062A. It must NOT count as coverage: the 062A row reads none / "·" / 0, never the
    polluted partial / catalog / 1 the catch-all tie-break would otherwise produce."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert "<td>none</td>" in resp.text
    assert "<td>0</td>" in resp.text
    assert "<td>partial</td>" not in resp.text
    assert "<td>catalog</td>" not in resp.text


@pytest.mark.asyncio
async def test_files_returns_200_with_file_row(
    populated_app: tuple[Starlette, str],
) -> None:
    """/files → 200 + contains the inserted file's hash."""
    app, hash_ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    assert hash_[:8] in resp.text


@pytest.mark.asyncio
async def test_files_default_hides_unmatched(
    app_no_decision: tuple[Starlette, str],
) -> None:
    """/files defaults to matched-only → an unmatched file is hidden."""
    app, hash_ = app_no_decision
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    assert hash_[:8] not in resp.text
    assert "Showing matched files only: 0 of 1 catalogued." in resp.text
    assert "Show all catalogued files" in resp.text
    assert "show_unmatched=1" in resp.text


@pytest.mark.asyncio
async def test_files_default_hides_retracted(
    app_retracted_decision: tuple[Starlette, str],
) -> None:
    """A retracted latest decision is treated exactly like no decision: the matched-only
    default (/files) hides it and counts it as unmatched in the summary."""
    app, hash_ = app_retracted_decision
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    assert hash_[:8] not in resp.text
    assert "Showing matched files only: 0 of 1 catalogued." in resp.text


@pytest.mark.asyncio
async def test_files_retracted_shows_as_unmatched_in_all_view(
    app_retracted_decision: tuple[Starlette, str],
) -> None:
    """The all-view (show_unmatched=1) renders a retracted file as an unmatched row: "·"
    cells, never "<td>unidentified</td>", never a tier badge (e.g. never the literal
    "retracted" string in a cell)."""
    app, hash_ = app_retracted_decision
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?show_unmatched=1")
    assert resp.status_code == 200
    assert hash_[:8] in resp.text
    assert "<td>unidentified</td>" not in resp.text
    assert "<td>retracted</td>" not in resp.text
    assert "La Grenouille Cosmique" not in resp.text


@pytest.mark.asyncio
async def test_files_toggle_preserves_active_filter(
    populated_app: tuple[Starlette, str],
) -> None:
    """The matched→all toggle must carry the active q filter (spec §5: filters preserved)."""
    app, hash_ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?q=keroro")
    assert resp.status_code == 200
    assert hash_[:8] in resp.text  # the matched file still shows under the q filter
    # Jinja2 autoescapes "&" to "&amp;" inside the href attribute.
    assert 'href="/files?q=keroro&amp;show_unmatched=1">Show all catalogued files' in resp.text


@pytest.mark.asyncio
async def test_files_show_unmatched_reveals_and_toggles_back(
    app_no_decision: tuple[Starlette, str],
) -> None:
    """/files?show_unmatched=1 reveals the whole catalogue; toggle points back to matched-only."""
    app, hash_ = app_no_decision
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?show_unmatched=1")
    assert resp.status_code == 200
    assert hash_[:8] in resp.text
    assert "Showing all catalogued files: 1 catalogued (0 matched)." in resp.text
    assert "Matched only" in resp.text
    assert 'href="/files">Matched only' in resp.text  # toggle drops the param → bare /files


@pytest.mark.asyncio
async def test_file_detail_with_decision_returns_200(
    populated_app: tuple[Starlette, str],
) -> None:
    """/files/{hash} with a decision → 200, contains the ed2k link + explanation info."""
    app, hash_ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    assert "ed2k://" in resp.text
    assert "062A" in resp.text


@pytest.mark.asyncio
async def test_file_detail_without_decision_returns_200(
    app_no_decision: tuple[Starlette, str],
) -> None:
    """/files/{hash} without a decision → 200, empty branches rendered."""
    app, hash_ = app_no_decision
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    assert "ed2k://" in resp.text
    assert "No matching decision." in resp.text
    assert "No explanation available." in resp.text


@pytest.mark.asyncio
async def test_file_detail_retracted_shows_no_decision(
    app_retracted_decision: tuple[Starlette, str],
) -> None:
    """/files/{hash} whose LATEST decision is a retraction renders exactly like a file with
    no decision at all: "No matching decision.", never a tier/target/rule badge, and never
    the literal "retracted" string anywhere in the page."""
    app, hash_ = app_retracted_decision
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    assert "No matching decision." in resp.text
    assert "No explanation available." in resp.text
    assert "retracted" not in resp.text
    assert "062A" not in resp.text  # the earlier, pre-retraction decision must not leak through


@pytest.mark.asyncio
async def test_file_detail_explanation_none_unknown_target(
    app_unknown_target: tuple[Starlette, str],
) -> None:
    """/files/{hash} with a target_id unknown to the config → 200 (explanation=None)."""
    app, hash_ = app_unknown_target
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    assert "ed2k://" in resp.text


@pytest.mark.asyncio
async def test_file_detail_unknown_hash_returns_404(
    populated_app: tuple[Starlette, str],
) -> None:
    """/files/{hash} nonexistent (32 hex) → 404."""
    app, _ = populated_app
    unknown = "00000000000000000000000000000000"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{unknown}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_targets_shortcut_returns_200(
    populated_app: tuple[Starlette, str],
) -> None:
    """/targets/{target_id} → 200 (shortcut to filtered /files)."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/targets/062A")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_target_page_has_no_summary_line(
    populated_app: tuple[Starlette, str],
) -> None:
    """/targets/{id} shares files.html, but the matched/all summary is meaningless on a
    target-scoped page: the summary line and its toggle must not render there."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/targets/062A")
    assert resp.status_code == 200
    assert "files-summary" not in resp.text
    assert "Showing matched files only" not in resp.text
    assert "Show all catalogued files" not in resp.text


@pytest.mark.asyncio
async def test_node_returns_200_with_node_info(
    populated_app: tuple[Starlette, str],
) -> None:
    """/node → 200 + contains the node id."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/node")
    assert resp.status_code == 200
    assert "node-test-001" in resp.text


@pytest.mark.asyncio
async def test_files_non_numeric_page_defaults_to_1(
    populated_app: tuple[Starlette, str],
) -> None:
    """/files?page=abc → 200 (non-numeric page → default 1)."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?page=abc")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_file_detail_no_observations_returns_200(
    app_no_observations: tuple[Starlette, str],
) -> None:
    """/files/{hash} without observations → 200; no ed2k link, empty branch rendered."""
    app, hash_ = app_no_observations
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    assert "ed2k://" not in resp.text
    assert "No observations." in resp.text


@pytest.mark.asyncio
async def test_base_nav_uses_singular_node_href(
    populated_app: tuple[Starlette, str],
) -> None:
    """The base nav contains href="/node" (singular) and NOT href="/nodes"."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert 'href="/node"' in resp.text
    assert 'href="/nodes"' not in resp.text


@pytest.mark.asyncio
async def test_node_page_renders_scheduler_state(
    populated_app: tuple[Starlette, str],
) -> None:
    """/node → the scheduler_state key last_search_cycle appears in the HTML."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/node")
    assert resp.status_code == 200
    assert "last_search_cycle" in resp.text


@pytest.fixture
def app_with_media_obs(catalog_db: Path, local_db: Path) -> tuple[Starlette, str]:
    """File with an observation having non-null media_length_sec and bitrate_kbps."""
    with sqlite3.connect(catalog_db) as conn:
        conn.execute(
            "INSERT INTO files VALUES (?, ?, ?)",
            (TEST_HASH, 100_000_000, None),
        )
        conn.execute(
            "INSERT INTO file_observations VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TEST_HASH,
                "keroro_s2e62a_vf.avi",
                100_000_000,
                5,
                3,
                1320,  # media_length_sec = 22 minutes
                192,  # bitrate_kbps
                None,
                None,
                "{}",
                "keroro",
                "2024-01-01T00:00:00",
                "node1",
            ),
        )
        conn.execute(
            "INSERT INTO match_decisions VALUES (1, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "062A", "catalog", "catalog", "2024-01-01T00:00:00", "node1"),
        )
        conn.commit()

    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-media"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.commit()

    matcher_config = _matcher()
    targets = _targets()

    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"

    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        matcher_config=matcher_config,
        targets=targets,
        templates_dir=templates_dir,
        static_dir=static_dir,
        control=_RecordingControl(),
        amule_url=_AMULE_URL,
    )
    return app, TEST_HASH


@pytest.mark.asyncio
async def test_file_detail_with_media_fields_returns_200(
    app_with_media_obs: tuple[Starlette, str],
) -> None:
    """/files/{hash} with media_length_sec + bitrate_kbps → 200, explanation computed."""
    app, hash_ = app_with_media_obs
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    assert "062A" in resp.text


@pytest.mark.asyncio
async def test_file_detail_unknown_hash_returns_styled_404(
    populated_app: tuple[Starlette, str],
) -> None:
    """/files/{hash} nonexistent → 404 with the styled HTML template (contains 'not found')."""
    app, _ = populated_app
    unknown = "00000000000000000000000000000000"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{unknown}")
    assert resp.status_code == 404
    assert "not found" in resp.text.lower()


@pytest.fixture
def app_with_hostile_filename(catalog_db: Path, local_db: Path) -> tuple[Starlette, str]:
    """File whose name contains a ``|`` (eD2k link separator). A webui-security#0
    regression: without percent-encoding, the ``|`` in the name shifts size/hash and the
    link points elsewhere."""
    hostile = "weird|name.avi"
    with sqlite3.connect(catalog_db) as conn:
        conn.execute("INSERT INTO files VALUES (?, ?, ?)", (TEST_HASH, 100_000_000, None))
        conn.execute(
            "INSERT INTO file_observations VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TEST_HASH,
                hostile,
                100_000_000,
                5,
                3,
                None,
                None,
                None,
                None,
                "{}",
                "keroro",
                "2024-01-01T00:00:00",
                "node1",
            ),
        )
        conn.commit()
    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-hostile"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.commit()
    matcher_config = _matcher()
    targets = _targets()
    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"
    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        matcher_config=matcher_config,
        targets=targets,
        templates_dir=templates_dir,
        static_dir=static_dir,
        control=_RecordingControl(),
        amule_url=_AMULE_URL,
    )
    return app, TEST_HASH


@pytest.mark.asyncio
async def test_empty_filter_param_does_not_silently_zero_results(
    populated_app: tuple[Starlette, str],
) -> None:
    # webui-security#0 regression (filters): ``?target=`` (empty, common with a <select>
    # that has an empty option) must be treated as "no filter", not as ``target = ''`` which
    # matches 0 results with no message.
    app, hash_ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?target=&tier=&q=")
    assert resp.status_code == 200
    assert hash_[:8] in resp.text  # the inserted file is rendered despite the empty filters


@pytest.mark.asyncio
async def test_page_zero_is_clamped_to_first_page(
    populated_app: tuple[Starlette, str],
) -> None:
    # webui-security#2 regression: ``?page=0`` → OFFSET=-50 (SQLite returned page 1 by
    # luck). Now ``max(1, page)`` clamps.
    app, hash_ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?page=0")
    assert resp.status_code == 200
    assert hash_[:8] in resp.text
    assert "Page 1" in resp.text


@pytest.mark.asyncio
async def test_security_headers_are_set_on_every_response(
    populated_app: tuple[Starlette, str],
) -> None:
    # webui-security#3: CSP + X-Content-Type-Options + Referrer-Policy set by middleware.
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.headers["Content-Security-Policy"] == "default-src 'self'"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "no-referrer"


@pytest.mark.asyncio
async def test_files_page_shows_pagination_navigation(catalog_db: Path, local_db: Path) -> None:
    # webui-security#1: the page lists 50 files max; when it is FULL, a "Next →" link
    # must be rendered (heuristic: we don't have the total count).
    with sqlite3.connect(catalog_db) as conn:
        for i in range(50):
            ed2k = f"{i:032d}"
            conn.execute("INSERT INTO files VALUES (?, ?, ?)", (ed2k, 100, None))
            conn.execute(
                "INSERT INTO file_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    i + 1,
                    ed2k,
                    f"file-{i}.bin",
                    100,
                    1,
                    1,
                    None,
                    None,
                    None,
                    None,
                    "{}",
                    "kw",
                    "2024-01-01T00:00:00",
                    "node1",
                ),
            )
            conn.execute(
                "INSERT INTO match_decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (i + 1, ed2k, "062A", "catalog", "catalog", "2024-01-01T00:00:00", "node1"),
            )
        conn.commit()
    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-paged"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.commit()
    matcher_config = _matcher()
    targets = _targets()
    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"
    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        matcher_config=matcher_config,
        targets=targets,
        templates_dir=templates_dir,
        static_dir=static_dir,
        control=_RecordingControl(),
        amule_url=_AMULE_URL,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp1 = await client.get("/files")
        resp2 = await client.get("/files?page=2")
    # page 1: full page → "Next" present, "Previous" absent.
    assert "Next" in resp1.text
    assert "Previous" not in resp1.text
    # page 2: not full (0 files) → "Previous" present, "Next" absent.
    assert "Previous" in resp2.text
    assert "Next" not in resp2.text


@pytest.mark.asyncio
async def test_hostile_filename_is_escaped_in_ed2k_link(
    app_with_hostile_filename: tuple[Starlette, str],
) -> None:
    # webui-security#0 regression: a hostile ``|`` in the name must be percent-encoded
    # (%7C). Without this escaping, the link would have 6 ``|`` (instead of 5 structural
    # separators) and size/hash would be shifted → unusable file / pointing elsewhere.
    app, hash_ = app_with_hostile_filename
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    # The link in the response must contain %7C (and NOT a raw ``|`` in the middle of the name).
    assert "%7C" in resp.text
    # We extract the first occurrence of ``ed2k://`` up to the next whitespace/`"` to
    # verify it has EXACTLY 5 ``|`` (the 5 structural separators of the canonical link).
    start = resp.text.index("ed2k://")
    end = start
    while end < len(resp.text) and resp.text[end] not in ('"', "<", " ", "\n"):
        end += 1
    link = resp.text[start:end]
    assert link.count("|") == 5


# ---------------------------------------------------------------------------
# Unit tests · _resolve_target_display / _to_display_rows (Task 3 resolution rule)
# ---------------------------------------------------------------------------

_SEGMENT_062A = TargetSegment(
    season=2, seasonal_number=11, absolute_number=62, segment="a", title="La Grenouille Cosmique"
)
_SEGMENT_062B = TargetSegment(
    season=2, seasonal_number=11, absolute_number=62, segment="b", title="Duel Contre Giroro"
)
_SEGMENT_BY_ID = {_SEGMENT_062A.target_id: _SEGMENT_062A}
_SEGMENTS_AB = {s.target_id: s for s in (_SEGMENT_062A, _SEGMENT_062B)}


def _file_row(*, decisions: tuple[FileDecision, ...]) -> FileRow:
    return FileRow(
        ed2k_hash=TEST_HASH,
        size_bytes=1024,
        filename="f.avi",
        source_count=1,
        last_seen="2024-01-01T00:00:00",
        decisions=decisions,
    )


def test_resolve_target_display_empty_decisions_is_empty_list() -> None:
    assert _resolve_target_display(_file_row(decisions=()), _SEGMENT_BY_ID) == []


def test_resolve_target_display_catalog_decision_is_unidentified() -> None:
    row = _file_row(decisions=(FileDecision(target_id="062A", tier="catalog"),))
    assert _resolve_target_display(row, _SEGMENT_BY_ID) == [("unidentified", "·")]


def test_resolve_target_display_resolvable_id_joins_seasonal_locator_and_title() -> None:
    row = _file_row(decisions=(FileDecision(target_id="062A", tier="download"),))
    assert _resolve_target_display(row, _SEGMENT_BY_ID) == [
        ("062A / S02E11A", "La Grenouille Cosmique")
    ]


def test_resolve_target_display_unknown_id_falls_back_to_raw_id() -> None:
    row = _file_row(decisions=(FileDecision(target_id="999Z", tier="download"),))
    assert _resolve_target_display(row, _SEGMENT_BY_ID) == [("999Z", "·")]


def test_resolve_target_display_two_segments_returns_a_pair_each() -> None:
    row = _file_row(decisions=(FileDecision("062A", "download"), FileDecision("062B", "download")))
    assert _resolve_target_display(row, _SEGMENTS_AB) == [
        ("062A / S02E11A", "La Grenouille Cosmique"),
        ("062B / S02E11B", "Duel Contre Giroro"),
    ]


def test_to_display_rows_empty_decisions_all_dashes() -> None:
    [display] = _to_display_rows([_file_row(decisions=())], _SEGMENTS_AB)
    assert display.decisions_display == ()
    assert display.tier_display == "·"


def test_to_display_rows_two_segments_aggregate_cells_shared_tier() -> None:
    row = _file_row(decisions=(FileDecision("062A", "download"), FileDecision("062B", "download")))
    [display] = _to_display_rows([row], _SEGMENTS_AB)
    assert display.decisions_display == (
        DecisionCell(target="062A / S02E11A", title="La Grenouille Cosmique"),
        DecisionCell(target="062B / S02E11B", title="Duel Contre Giroro"),
    )
    assert display.tier_display == "download"


def test_to_display_rows_two_segments_differing_tiers_lists_per_target() -> None:
    row = _file_row(decisions=(FileDecision("062A", "download"), FileDecision("062B", "notify")))
    [display] = _to_display_rows([row], _SEGMENTS_AB)
    assert display.tier_display == "062A: download · 062B: notify"


def test_to_display_rows_computes_size_and_last_seen_display() -> None:
    row = FileRow(
        ed2k_hash=TEST_HASH,
        size_bytes=1024,
        filename="f.avi",
        source_count=1,
        last_seen="2026-07-03T23:45:24.104990+00:00",
        decisions=(),
    )
    [display] = _to_display_rows([row], _SEGMENTS_AB)
    assert display.size_display == "1 KB"
    assert display.last_seen_display == "2026-07-03 23:45Z"


# ---------------------------------------------------------------------------
# HTTP-level tests · the resolution rule end-to-end through both routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_files_catalog_tier_shows_unidentified(
    populated_app: tuple[Starlette, str],
) -> None:
    """populated_app's decision is tier=catalog → the /files list must show "unidentified",
    not the resolved id/title."""
    app, hash_ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    assert hash_[:8] in resp.text
    # Scoped to the table cell (not the static tier legend, which also mentions the word).
    assert '<div class="cell-line">unidentified</div>' in resp.text
    assert "La Grenouille Cosmique" not in resp.text


@pytest.mark.asyncio
async def test_files_resolvable_target_shows_seasonal_locator_and_title(
    app_download_tier_known_target: tuple[Starlette, str],
) -> None:
    app, _ = app_download_tier_known_target
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    assert "062A / S02E11A" in resp.text
    assert "La Grenouille Cosmique" in resp.text


@pytest.mark.asyncio
async def test_files_table_is_scroll_wrapped_and_name_has_full_title(
    app_download_tier_known_target: tuple[Starlette, str],
) -> None:
    """The table is wrapped for horizontal scroll on narrow desktop widths, and the (possibly
    truncated) filename keeps its full text in a title= tooltip."""
    app, _ = app_download_tier_known_target
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    assert '<div class="files-scroll">' in resp.text
    assert 'class="cell-name" title="keroro_s2e62a_vf.avi"' in resp.text


@pytest.mark.asyncio
async def test_files_unknown_target_shows_raw_id_and_dash_title(
    app_download_tier_unknown_target: tuple[Starlette, str],
) -> None:
    app, _ = app_download_tier_unknown_target
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    assert "999Z" in resp.text
    # The static tier legend also mentions the word "unidentified": scope to the cell.
    assert '<div class="cell-line">unidentified</div>' not in resp.text
    assert "La Grenouille Cosmique" not in resp.text


@pytest.mark.asyncio
async def test_files_no_decision_shows_dashes(
    app_no_decision: tuple[Starlette, str],
) -> None:
    """No decision at all → target and title both render as "·" cells, never an
    "unidentified" cell value. The row is only visible with show_unmatched (the
    matched-only default hides it, cf. test_files_default_hides_unmatched)."""
    app, hash_ = app_no_decision
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?show_unmatched=1")
    assert resp.status_code == 200
    assert hash_[:8] in resp.text
    # The Target header tooltip also mentions "unidentified": scope to the cell so we
    # assert on the row value, not the static legend text.
    assert '<div class="cell-line">unidentified</div>' not in resp.text
    assert "La Grenouille Cosmique" not in resp.text


@pytest.mark.asyncio
async def test_files_tier_header_has_tooltip_and_no_legacy_legend(
    populated_app: tuple[Starlette, str],
) -> None:
    """The tier meanings now live in a "?" header tooltip on the Tier column, replacing the
    old free-standing <p class="tier-legend"> block."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    # The legacy legend block is gone.
    assert 'class="tier-legend"' not in resp.text
    assert "Tier legend" not in resp.text
    # A focusable "?" trigger describes a tooltip that enumerates the three tiers.
    assert 'aria-describedby="tip-tier"' in resp.text
    assert 'id="tip-tier"' in resp.text
    assert 'role="tooltip"' in resp.text
    assert "automatically queued for download" in resp.text
    assert "flagged for manual review" in resp.text


@pytest.mark.asyncio
async def test_files_nonobvious_columns_have_header_tooltips(
    populated_app: tuple[Starlette, str],
) -> None:
    """The other non-obvious columns (Target, Sources) also carry a "?" header tooltip
    explaining their values."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    assert 'id="tip-target"' in resp.text
    assert 'id="tip-sources"' in resp.text
    # A distinctive phrase from each of the two tooltips.
    assert "the episode this file is matched to" in resp.text  # target
    assert "peers" in resp.text  # sources


@pytest.mark.asyncio
async def test_target_page_resolves_title_via_segment_mapping(
    app_download_tier_known_target: tuple[Starlette, str],
) -> None:
    """/targets/{id} shares _to_display_rows with /files: confirm handle_target ALSO
    threads the segment_by_id mapping (both call sites, per the brief)."""
    app, _ = app_download_tier_known_target
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/targets/062A")
    assert resp.status_code == 200
    assert "062A / S02E11A" in resp.text
    assert "La Grenouille Cosmique" in resp.text


# ---------------------------------------------------------------------------
# Whole-episode end-to-end (Task 4, the multi-target headline behavior):
# one recovered whole-episode file resolves BOTH lost segments, renders as
# ONE <tr> with an aggregated Target/Title/Tier cell, and appears under EACH
# of its targets.
# ---------------------------------------------------------------------------


def _targets_ab() -> tuple[TargetSegment, ...]:
    return parse_targets(
        yaml.safe_load(
            """\
episodes:
  - season: 3
    seasonal_number: 6
    absolute_number: 72
    segments:
      - letter: a
        title: "Le Defi"
      - letter: b
        title: "Duel Contre Giroro"
"""
        )
    )


@pytest.fixture
def app_whole_episode(catalog_db: Path, local_db: Path) -> tuple[Starlette, str]:
    """One file matched to BOTH 072A and 072B (two current decisions, tier download) against a
    two-segment targets.yaml: the core multi-target end-to-end fixture (spec §9)."""
    with sqlite3.connect(catalog_db) as conn:
        conn.execute("INSERT INTO files VALUES (?, ?, ?)", (TEST_HASH, 170_000_000, None))
        conn.execute(
            "INSERT INTO file_observations VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TEST_HASH,
                "keroro_072_vf.avi",
                170_000_000,
                7,
                3,
                None,
                None,
                None,
                None,
                "{}",
                "keroro",
                "2024-01-01T00:00:00",
                "node1",
            ),
        )
        conn.execute(
            "INSERT INTO match_decisions VALUES (1, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "072A", "numero_nu_confirmed", "download", "2024-01-01T00:00:00", "node1"),
        )
        conn.execute(
            "INSERT INTO match_decisions VALUES (2, ?, ?, ?, ?, ?, ?)",
            (TEST_HASH, "072B", "numero_nu_confirmed", "download", "2024-01-01T00:00:00", "node1"),
        )
        conn.commit()

    with sqlite3.connect(local_db) as conn:
        conn.execute("INSERT INTO node_runtime VALUES (?, ?)", ("node_id", "node-whole"))
        conn.execute(
            "INSERT INTO node_runtime VALUES (?, ?)", ("created_at", "2024-01-01T00:00:00")
        )
        conn.commit()

    matcher_config = _matcher()
    targets = _targets_ab()

    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"
    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        matcher_config=matcher_config,
        targets=targets,
        templates_dir=templates_dir,
        static_dir=static_dir,
        control=_RecordingControl(),
        amule_url=_AMULE_URL,
    )
    return app, TEST_HASH


@pytest.mark.asyncio
async def test_files_whole_episode_renders_one_row_with_aggregated_targets(
    app_whole_episode: tuple[Starlette, str],
) -> None:
    app, hash_ = app_whole_episode
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    assert resp.status_code == 200
    assert hash_[:8] in resp.text
    assert '<div class="cell-line">072A / S03E06A</div>' in resp.text
    assert '<div class="cell-line">072B / S03E06B</div>' in resp.text
    assert '<div class="cell-line cell-title" title="Le Defi">Le Defi</div>' in resp.text
    assert (
        '<div class="cell-line cell-title" title="Duel Contre Giroro">Duel Contre Giroro</div>'
        in resp.text
    )
    assert "<td>download</td>" in resp.text


@pytest.mark.asyncio
async def test_whole_episode_appears_under_each_target(
    app_whole_episode: tuple[Starlette, str],
) -> None:
    app, hash_ = app_whole_episode
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp_a = await client.get("/targets/072A")
        resp_b = await client.get("/targets/072B")
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    assert hash_[:8] in resp_a.text
    assert hash_[:8] in resp_b.text


@pytest.mark.asyncio
async def test_file_detail_whole_episode_shows_both_targets(
    app_whole_episode: tuple[Starlette, str],
) -> None:
    app, hash_ = app_whole_episode
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    assert "072A" in resp.text
    assert "072B" in resp.text


# ---------------------------------------------------------------------------
# Runtime controls (phase P6a): GET renders, each POST dispatches one intent and
# redirects (PRG), the ?done banner maps a known code to a message (both branches).
# ---------------------------------------------------------------------------


@pytest.fixture
def controls_app(catalog_db: Path, local_db: Path) -> tuple[Starlette, _RecordingControl]:
    """App wired with a recording control, for the runtime-control route tests. The controls
    routes never read the DB, so no seeding is needed."""
    matcher_config = _matcher()
    targets = _targets()

    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"
    control = _RecordingControl()
    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        matcher_config=matcher_config,
        targets=targets,
        templates_dir=templates_dir,
        static_dir=static_dir,
        control=control,
        amule_url=_AMULE_URL,
    )
    return app, control


@pytest.mark.asyncio
async def test_controls_get_renders_the_four_action_forms(
    controls_app: tuple[Starlette, _RecordingControl],
) -> None:
    """GET /controls -> 200 with a POST form for each of the four in-scope controls."""
    app, _ = controls_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/controls")
    assert resp.status_code == 200
    assert 'action="/controls/force-cycle"' in resp.text
    assert 'action="/controls/pause"' in resp.text
    assert 'action="/controls/resume"' in resp.text
    assert 'action="/controls/restart"' in resp.text
    # P6b controls are explicitly out of scope: no re-evaluate / requeue actions here.
    assert "reevaluate" not in resp.text
    assert "requeue" not in resp.text


@pytest.mark.asyncio
async def test_controls_nav_entry_links_from_elsewhere_and_is_active_on_controls(
    controls_app: tuple[Starlette, _RecordingControl],
) -> None:
    """The base nav carries a Controls entry: a link (href="/controls") from any other page, and
    the active non-link on /controls itself, where that href is gone by construction."""
    app, _ = controls_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        elsewhere = await client.get("/")
        on_controls = await client.get("/controls")
    assert elsewhere.status_code == 200
    assert '<a href="/controls">Controls</a>' in elsewhere.text
    assert on_controls.status_code == 200
    assert '<span aria-current="page">Controls</span>' in on_controls.text
    assert '<a href="/controls">Controls</a>' not in on_controls.text


@pytest.mark.asyncio
async def test_controls_get_without_done_shows_no_banner(
    controls_app: tuple[Starlette, _RecordingControl],
) -> None:
    """GET /controls (no ?done) renders no status banner (empty messages tuple)."""
    app, _ = controls_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/controls")
    assert resp.status_code == 200
    assert "control-banner" not in resp.text


@pytest.mark.asyncio
async def test_controls_get_unknown_done_shows_no_banner(
    controls_app: tuple[Starlette, _RecordingControl],
) -> None:
    """An unknown ?done code maps to no message -> no banner (covers the mapping's miss branch)."""
    app, _ = controls_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/controls?done=bogus")
    assert resp.status_code == 200
    assert "control-banner" not in resp.text


@pytest.mark.asyncio
async def test_controls_get_known_done_shows_banner_message(
    controls_app: tuple[Starlette, _RecordingControl],
) -> None:
    """A known ?done code renders its human message in a banner (covers the mapping's hit
    branch)."""
    app, _ = controls_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/controls?done=force-cycle")
    assert resp.status_code == 200
    assert "control-banner" in resp.text
    assert "Cycle forced. A new search cycle starts shortly." in resp.text


@pytest.mark.asyncio
async def test_post_force_cycle_dispatches_and_redirects(
    controls_app: tuple[Starlette, _RecordingControl],
) -> None:
    app, control = controls_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/controls/force-cycle")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/controls?done=force-cycle"
    assert control.calls == ["force_cycle"]


@pytest.mark.asyncio
async def test_post_pause_dispatches_and_redirects(
    controls_app: tuple[Starlette, _RecordingControl],
) -> None:
    app, control = controls_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/controls/pause")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/controls?done=paused"
    assert control.calls == ["pause"]


@pytest.mark.asyncio
async def test_post_resume_dispatches_and_redirects(
    controls_app: tuple[Starlette, _RecordingControl],
) -> None:
    app, control = controls_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/controls/resume")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/controls?done=resumed"
    assert control.calls == ["resume"]


@pytest.mark.asyncio
async def test_post_restart_dispatches_and_redirects(
    controls_app: tuple[Starlette, _RecordingControl],
) -> None:
    app, control = controls_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/controls/restart")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/controls?done=restart"
    assert control.calls == ["restart"]


@pytest.mark.asyncio
async def test_post_force_cycle_followed_lands_on_banner(
    controls_app: tuple[Starlette, _RecordingControl],
) -> None:
    """Following the redirect lands on the controls page with the force-cycle banner (end-to-end
    PRG)."""
    app, control = controls_app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
    ) as client:
        resp = await client.post("/controls/force-cycle")
    assert resp.status_code == 200
    assert "Cycle forced. A new search cycle starts shortly." in resp.text
    assert control.calls == ["force_cycle"]


# ---------------------------------------------------------------------------
# Bidirectional column sorting (Task 3): normalizers + header builder (direct)
# and the sortable <thead> end-to-end.
# ---------------------------------------------------------------------------


def test_normalize_sort_valid_unknown_missing() -> None:
    assert _normalize_sort("size") == "size"
    assert _normalize_sort("bogus") == "last_seen"
    assert _normalize_sort(None) == "last_seen"


def test_normalize_dir_valid_unknown_missing() -> None:
    assert _normalize_dir("asc") == "asc"
    assert _normalize_dir("bogus") == "desc"
    assert _normalize_dir(None) == "desc"


def test_sort_headers_default_state_no_filters() -> None:
    headers = _sort_headers(sort="last_seen", direction="desc", filters={})
    # active column: last_seen, showing desc; its link flips to asc. sort=last_seen is the
    # DEFAULT sort so it is OMITTED from the URL (the omit-defaults invariant); only dir=asc shows.
    assert headers.last_seen.indicator == "desc"
    assert headers.last_seen.url == "/files?dir=asc"
    # inactive columns: no indicator, per-column default direction, sort/dir omitted when default
    assert headers.name.indicator == ""
    assert headers.name.url == "/files?sort=name&dir=asc"  # Name default dir is asc
    assert headers.size.url == "/files?sort=size"  # Size default dir desc == DEFAULT_DIR -> omitted
    assert headers.tier.url == "/files?sort=tier"


def test_sort_headers_active_column_flips_and_preserves_filters() -> None:
    headers = _sort_headers(sort="size", direction="asc", filters={"q": "keroro", "tier": "notify"})
    # active column size, currently asc -> indicator asc, link flips to desc (== default -> omitted)
    assert headers.size.indicator == "asc"
    assert headers.size.url == "/files?q=keroro&tier=notify&sort=size"
    # an inactive column keeps the filters and sets its own sort + default dir
    assert headers.name.indicator == ""
    assert headers.name.url == "/files?q=keroro&tier=notify&sort=name&dir=asc"


@pytest.fixture
def sortable_app(catalog_db: Path, local_db: Path) -> tuple[Starlette, list[str]]:
    """App over three matched files with distinct sizes (300/200/100), for the sort-order test.
    Returns (app, hashes-in-descending-size-order)."""
    big, mid, small = "a" * 32, "b" * 32, "c" * 32
    with sqlite3.connect(catalog_db) as conn:
        for h, size, name in ((big, 300, "x.avi"), (mid, 200, "y.avi"), (small, 100, "z.avi")):
            conn.execute("INSERT INTO files VALUES (?, ?, ?)", (h, size, None))
            conn.execute(
                "INSERT INTO file_observations VALUES"
                " (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    h,
                    name,
                    size,
                    1,
                    0,
                    None,
                    None,
                    None,
                    None,
                    "{}",
                    "keroro",
                    "2024-01-01T00:00:00",
                    "n",
                ),
            )
            conn.execute(
                "INSERT INTO match_decisions VALUES (NULL, ?, ?, ?, ?, ?, ?)",
                (h, "062A", "rule", "download", "2024-01-01T00:00:00", "n"),
            )
        conn.commit()
    import mulewatch.webui

    templates_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "templates"
    static_dir = Path(mulewatch.webui.__file__).parent / "adapters" / "static"
    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        matcher_config=_matcher(),
        targets=_targets(),
        templates_dir=templates_dir,
        static_dir=static_dir,
        control=_RecordingControl(),
        amule_url=_AMULE_URL,
    )
    return app, [big, mid, small]


@pytest.mark.asyncio
async def test_files_sort_by_size_orders_rows(sortable_app: tuple[Starlette, list[str]]) -> None:
    app, ordered_desc = sortable_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?sort=size&dir=asc")
    assert resp.status_code == 200
    # smallest first: the three short-hashes appear in ascending-size order in the body
    positions = [resp.text.index(h[:8]) for h in reversed(ordered_desc)]
    assert positions == sorted(positions)


@pytest.mark.asyncio
async def test_files_renders_sortable_header_link(populated_app: tuple[Starlette, str]) -> None:
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    # the Name header is a sort link with a data-sort attribute (default state -> not active)
    assert 'href="/files?sort=name&amp;dir=asc"' in resp.text
    assert 'data-sort="desc"' in resp.text  # last_seen is the active default column


@pytest.mark.asyncio
async def test_target_page_headers_are_plain_text(
    app_download_tier_known_target: tuple[Starlette, str],
) -> None:
    app, _ = app_download_tier_known_target
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/targets/062A")
    assert resp.status_code == 200
    # no sort links on a target page (headers tuple is empty -> the {% else %} plain label renders)
    assert "?sort=" not in resp.text


@pytest.mark.asyncio
async def test_sort_header_links_preserve_target_and_tier_filters(
    app_download_tier_known_target: tuple[Starlette, str],
) -> None:
    """An active target + tier filter is carried into every sort-header link (the ordered
    ``filters`` dict is threaded into each header URL, then sort/dir override for that column)."""
    app, _ = app_download_tier_known_target
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?target=062A&tier=download")
    assert resp.status_code == 200
    # the Name header keeps both filters (autoescaped & -> &amp;) then adds its own sort + dir
    assert "target=062A&amp;tier=download&amp;sort=name&amp;dir=asc" in resp.text


# ---------------------------------------------------------------------------
# Filename search + tier facet (Task 4): the two builders (direct) and the
# rendered search form + facet chips end-to-end.
# ---------------------------------------------------------------------------


def test_tier_facets_all_reset_and_masks_catalog() -> None:
    facets = _tier_facets(
        counts={"download": 1, "notify": 2, "catalog": 5}, active_tier=None, base={}
    )
    assert facets[0].label == "all"
    assert facets[0].selected_flag == "1"  # no tier active → all selected
    assert facets[0].url == "/files"
    labels = [f.label for f in facets[1:]]
    assert labels == ["download", "notify", "unidentified"]  # fixed order, catalog masked
    assert facets[1].count_display == "(1)"
    assert facets[1].url == "/files?tier=download"


def test_tier_facets_selected_and_preserves_base() -> None:
    facets = _tier_facets(
        counts={"download": 1, "notify": 2},
        active_tier="notify",
        base={"q": "keroro", "sort": "size"},
    )
    notify = next(f for f in facets if f.label == "notify")
    assert notify.selected_flag == "1"
    assert notify.url == "/files?q=keroro&sort=size&tier=notify"
    assert facets[0].url == "/files?q=keroro&sort=size"  # all-reset keeps base, drops tier


def test_tier_facets_only_present_tiers_appear() -> None:
    facets = _tier_facets(counts={"notify": 3}, active_tier=None, base={})
    assert [f.label for f in facets] == ["all", "notify"]


def test_search_bar_prefill_and_hidden_excludes_q() -> None:
    bar = _search_bar(query="keroro", hidden_state={"tier": "notify", "sort": "size"})
    assert bar.query == "keroro"
    assert bar.hidden == (
        HiddenInput(name="tier", value="notify"),
        HiddenInput(name="sort", value="size"),
    )


def test_search_bar_no_query_is_empty_string() -> None:
    bar = _search_bar(query=None, hidden_state={})
    assert bar.query == ""
    assert bar.hidden == ()


@pytest.mark.asyncio
async def test_files_renders_search_prefill_and_facet(
    populated_app: tuple[Starlette, str],
) -> None:
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files?q=keroro&show_unmatched=1")
    assert 'name="q"' in resp.text
    assert 'value="keroro"' in resp.text  # prefill
    assert 'name="show_unmatched"' in resp.text  # carried as a hidden input
    # populated_app's single file is a catalog-tier decision → facet shows "unidentified (1)"
    assert "unidentified" in resp.text
    assert 'href="/files?q=keroro&amp;show_unmatched=1&amp;tier=catalog"' in resp.text


@pytest.mark.asyncio
async def test_files_filter_bar_wraps_facets_then_search(
    populated_app: tuple[Starlette, str],
) -> None:
    """The facet chips and the search form share one .filter-bar row, chips first.

    The order is the layout contract: the row is a flex line and the form is pushed right by
    `margin-left: auto`, which only lands the form on the right if it is the LAST child.
    """
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/files")
    row = re.search(r'<div class="filter-bar">(.*?)</div>', resp.text, re.DOTALL)
    assert row is not None  # the wrapper is a div now, no longer the form itself
    inner = row.group(1)
    facet_at = inner.find('<nav class="tier-facet"')
    form_at = inner.find('<form class="files-search"')
    assert facet_at != -1  # the chips live inside the row
    assert form_at != -1  # the form lives inside the row, and carries files-search
    assert facet_at < form_at  # chips left, search right
    assert 'role="search"' in inner  # the form stays a landmark after the reclassing


@pytest.mark.asyncio
async def test_target_page_has_no_filter_bar(
    app_download_tier_known_target: tuple[Starlette, str],
) -> None:
    app, _ = app_download_tier_known_target
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/targets/062A")
    assert 'name="q"' not in resp.text  # no search form on a target page
    assert "tier-facet" not in resp.text  # no facet on a target page
    assert "filter-bar" not in resp.text  # the wrapper itself renders nothing either
    assert "files-search" not in resp.text


# ---------------------------------------------------------------------------
# Top nav: active state + the page titles it replaces
# ---------------------------------------------------------------------------

# The five nav destinations, in render order: (path, label). Mirrors the table wired in
# composition.app; a test that drifts from it fails on the label or the href.
_NAV_ENTRIES: tuple[tuple[str, str], ...] = (
    ("/", "Dashboard"),
    ("/files", "Files"),
    ("/node", "Nodes"),
    ("/controls", "Controls"),
    ("/console", "Console"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "label"), _NAV_ENTRIES)
async def test_nav_marks_only_the_current_page_entry_as_active(
    populated_app: tuple[Starlette, str], path: str, label: str
) -> None:
    """On each nav destination: its own entry is a non-link marked aria-current="page" (the CSS
    hook that bolds it), its nav link is gone, every other entry stays a link, and exactly one
    entry is active."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(path)
    assert resp.status_code == 200
    assert f'<span aria-current="page">{label}</span>' in resp.text
    assert f'<a href="{path}">{label}</a>' not in resp.text
    for other_path, other_label in _NAV_ENTRIES:
        if other_path == path:
            continue
        assert f'<a href="{other_path}">{other_label}</a>' in resp.text
    assert resp.text.count('aria-current="page"') == 1


@pytest.mark.asyncio
async def test_nav_links_to_the_amule_web_ui(populated_app: tuple[Starlette, str]) -> None:
    """The container publishes two web surfaces (design §9): the nav carries a link to amuleweb
    so the operator can reach it from here. It is an EXTERNAL entry, so it never goes active."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
    assert f'<a href="{_AMULE_URL}">aMule</a>' in resp.text
    assert resp.text.count('aria-current="page"') == 1  # still only the current page


@pytest.mark.asyncio
async def test_nav_amule_link_uses_the_configured_base(catalog_db: Path, local_db: Path) -> None:
    """The base is configurable so the link survives a reverse proxy in front of 8080: mulewatch
    cannot infer from the request how amuleweb is reachable from the operator's browser."""
    import mulewatch.webui

    webui_dir = Path(mulewatch.webui.__file__).parent
    app = build_app(
        catalog_db=catalog_db,
        local_db=local_db,
        matcher_config=_matcher(),
        targets=_targets(),
        templates_dir=webui_dir / "adapters" / "templates",
        static_dir=webui_dir / "adapters" / "static",
        control=_RecordingControl(),
        amule_url="https://mule.example.org/amule",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
    assert '<a href="https://mule.example.org/amule">aMule</a>' in resp.text


@pytest.mark.asyncio
async def test_nav_on_a_non_nav_path_renders_every_entry_as_a_link(
    populated_app: tuple[Starlette, str],
) -> None:
    """A page that is NOT a nav destination (here /files/{hash}, the file detail) leaves every
    entry a link and marks none active: the active entry is an EXACT path match, not a prefix."""
    app, hash_ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/files/{hash_}")
    assert resp.status_code == 200
    for path, label in _NAV_ENTRIES:
        assert f'<a href="{path}">{label}</a>' in resp.text
    assert "aria-current" not in resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [path for path, _ in _NAV_ENTRIES])
async def test_nav_pages_render_no_page_title(
    populated_app: tuple[Starlette, str], path: str
) -> None:
    """The five nav destinations carry no <h1>: the title duplicated the nav, whose bold current
    entry now names the page. Their <h2> section headings stay."""
    app, _ = populated_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(path)
    assert resp.status_code == 200
    assert "<h1>" not in resp.text


@pytest.mark.asyncio
async def test_target_page_renders_its_own_heading(
    app_download_tier_known_target: tuple[Starlette, str],
) -> None:
    """/targets/{id} reuses files.html but is NOT a nav destination, so nothing else would name
    it: it passes its own heading in the ``headings`` tuple (files.html itself renders none)."""
    app, _ = app_download_tier_known_target
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/targets/062A")
    assert resp.status_code == 200
    assert "<h1>Files for target 062A</h1>" in resp.text
