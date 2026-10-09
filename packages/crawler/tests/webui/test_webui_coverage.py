from mulewatch.webui.domain.coverage import coverage_for


def test_no_decision_is_none() -> None:
    status = coverage_for("062A", [])
    assert status.status == "none"
    assert status.best_tier is None
    assert status.file_count == 0


def test_download_tier_is_found() -> None:
    """Mixed case: the catalog-tier decision is excluded, so only the ``download`` file counts."""
    status = coverage_for("062A", [(b"h1", "download"), (b"h2", "catalog")])
    assert status.status == "found"
    assert status.best_tier == "download"
    assert status.file_count == 1


def test_only_weak_tiers_is_partial() -> None:
    status = coverage_for("062A", [(b"h1", "catalog"), (b"h2", "notify")])
    assert status.status == "partial"
    assert status.best_tier == "notify"
    assert status.file_count == 1


def test_only_catalog_tier_is_none() -> None:
    """Catalog-tier decisions are the ``keroro_large`` catch-all: any unidentified keroro file
    with no numeric token resolves to the smallest target_id (``001A``) via the engine
    tie-break. They must NOT count as coverage of an episode, so a target whose only decisions
    are catalog-tier reads none/0 (mirrors the ``/files`` "unidentified" mask)."""
    status = coverage_for("001A", [(b"h1", "catalog"), (b"h2", "catalog")])
    assert status.status == "none"
    assert status.best_tier is None
    assert status.file_count == 0
