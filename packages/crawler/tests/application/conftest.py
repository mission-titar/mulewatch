"""Shared fixtures for the application tests: real engine + real SQLite repos (spec §8)."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from catalog_matching.engine import MatchingEngine
from catalog_matching.validation import parse_matcher_config, parse_targets
from p2pwatch.adapters.config.yaml_loader import load_yaml
from p2pwatch.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from p2pwatch.adapters.persistence_sqlite.connection import open_catalog

_REPO_ROOT = Path(__file__).resolve().parents[4]
# Matcher: single source of truth = deployment config. Targets: the §7 subset of the golden
# corpus (matching fixture, distinct from the full prod catalog).
_MATCHER = _REPO_ROOT / "deploy" / "matcher.yml"
_FIXTURES = _REPO_ROOT / "packages" / "matching" / "tests" / "fixtures"
_NODE = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def engine() -> MatchingEngine:
    """REAL engine: deployment matcher + golden-corpus targets (shared fixtures)."""
    config = parse_matcher_config(load_yaml(_MATCHER))
    targets = parse_targets(load_yaml(_FIXTURES / "golden_targets.yaml"))
    return MatchingEngine(config, targets)


@pytest.fixture
def catalog_connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_catalog(tmp_path / "catalog.db")
    yield connection
    connection.close()


@pytest.fixture
def catalog(catalog_connection: sqlite3.Connection) -> SqliteCatalogRepository:
    return SqliteCatalogRepository(catalog_connection, _NODE)
