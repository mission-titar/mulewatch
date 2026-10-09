import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from mulewatch.adapters.persistence_sqlite import connection as connection_module
from mulewatch.adapters.persistence_sqlite.connection import (
    Migration,
    _apply_migrations,
    _load_scripts,
    open_catalog,
    open_local,
    utc_iso,
    utc_now,
)
from mulewatch.adapters.persistence_sqlite.errors import MigrationError, PersistenceError
from tests.catalog_rows import insert_decision, insert_file

_CATALOG_TABLES = {"files", "observation_variants", "observations", "match_decisions"}
_LOCAL_TABLES = {
    "node_runtime",
    "downloads",
    "scheduler_state",
    "backfill_state",
}

# Canonical 32-char lowercase hex hash (satisfies the CHECK constraint).
_CANONICAL_HASH = "a" * 32

# PRAGMA temp_store: 1 = FILE (0 = the compile-time default).
_TEMP_STORE_FILE = 1

_NO_TRANSACTION = "-- migration: no-transaction\n"


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def test_open_catalog_creates_the_tables_and_versions_the_schema(tmp_path: Path) -> None:
    connection = open_catalog(tmp_path / "catalog.db")
    try:
        assert _table_names(connection) == _CATALOG_TABLES
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 7
    finally:
        connection.close()


def test_open_catalog_registers_the_variant_functions_before_migrating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "catalog").mkdir()
    (tmp_path / "catalog" / "0001_probe.sql").write_text(
        "CREATE TABLE probe AS SELECT iso_to_micros('1970-01-01T00:00:00.000001+00:00') AS us;"
    )
    monkeypatch.setattr(connection_module, "_MIGRATIONS", tmp_path)
    connection = open_catalog(tmp_path / "catalog.db")
    try:
        assert connection.execute("SELECT us FROM probe").fetchone()[0] == 1
    finally:
        connection.close()


def test_open_local_creates_the_tables_and_versions_the_schema(tmp_path: Path) -> None:
    connection = open_local(tmp_path / "local.db")
    try:
        assert _table_names(connection) == _LOCAL_TABLES
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
    finally:
        connection.close()


def test_open_applies_wal_and_foreign_keys_and_recursive_triggers_pragmas(tmp_path: Path) -> None:
    connection = open_catalog(tmp_path / "catalog.db")
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA recursive_triggers").fetchone()[0] == 1
    finally:
        connection.close()


def test_open_local_applies_recursive_triggers_pragma(tmp_path: Path) -> None:
    connection = open_local(tmp_path / "local.db")
    try:
        assert connection.execute("PRAGMA recursive_triggers").fetchone()[0] == 1
    finally:
        connection.close()


def test_foreign_keys_are_enforced(tmp_path: Path) -> None:
    connection = open_catalog(tmp_path / "catalog.db")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            insert_decision(connection, "absent", "t", "catalog")
    finally:
        connection.close()


def test_reopen_is_idempotent_and_keeps_data(tmp_path: Path) -> None:
    path = tmp_path / "catalog.db"
    first = open_catalog(path)
    insert_file(first, _CANONICAL_HASH, 1)
    first.close()
    second = open_catalog(path)  # versions already applied: NO script replays
    try:
        assert second.execute("PRAGMA user_version").fetchone()[0] == 7
        assert second.execute("SELECT count(*) FROM files").fetchone()[0] == 1
    finally:
        second.close()


def test_in_memory_database_is_refused_because_wal_is_required() -> None:
    # :memory: reports journal_mode='memory' (empirically verified) -> flat refusal.
    with pytest.raises(PersistenceError, match="WAL"):
        open_catalog(":memory:")


def test_unopenable_path_raises_persistence_error(tmp_path: Path) -> None:
    with pytest.raises(PersistenceError):
        open_catalog(tmp_path)  # a directory is not a database


def test_database_newer_than_the_code_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "catalog.db"
    open_catalog(path).close()
    raw = sqlite3.connect(path, autocommit=True)
    raw.execute("PRAGMA user_version = 99")
    raw.close()
    with pytest.raises(MigrationError, match="99"):
        open_catalog(path)


def test_apply_migrations_with_no_scripts_is_a_noop(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "vide.db", autocommit=True)
    try:
        _apply_migrations(connection, ())
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        connection.close()


def test_migrations_sort_through_temporary_files(tmp_path: Path) -> None:
    """The probe records the ``temp_store`` in force while a migration runs: an in-memory sort
    is unbounded (+697 MB for one index at 11.5M rows), a file-backed one is not."""
    connection = sqlite3.connect(tmp_path / "probe.db", autocommit=True)
    script = (
        "CREATE TABLE probe (temp_store INTEGER);\n"
        "INSERT INTO probe SELECT temp_store FROM pragma_temp_store();"
    )
    try:
        _apply_migrations(connection, (Migration(1, script),))
        assert connection.execute("SELECT temp_store FROM probe").fetchone()[0] == _TEMP_STORE_FILE
    finally:
        connection.close()


def test_temp_store_is_restored_after_migrations(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "restore.db", autocommit=True)
    try:
        before = connection.execute("PRAGMA temp_store").fetchone()[0]
        _apply_migrations(connection, (Migration(1, "CREATE TABLE t (x INTEGER);"),))
        assert connection.execute("PRAGMA temp_store").fetchone()[0] == before
    finally:
        connection.close()


def test_temp_store_is_restored_even_when_a_migration_fails(tmp_path: Path) -> None:
    """``open_catalog``'s caller may catch MigrationError and keep the connection's settings."""
    connection = sqlite3.connect(tmp_path / "restore_fail.db", autocommit=True)
    try:
        before = connection.execute("PRAGMA temp_store").fetchone()[0]
        with pytest.raises(MigrationError):
            _apply_migrations(connection, (Migration(1, "INSERT INTO nonexistent VALUES (1);"),))
        assert connection.execute("PRAGMA temp_store").fetchone()[0] == before
    finally:
        connection.close()


def test_failed_script_is_rolled_back_and_version_unchanged(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "echec.db", autocommit=True)
    bad_script = "CREATE TABLE disparait (x INTEGER);\nINSERT INTO inexistante VALUES (1);"
    try:
        with pytest.raises(MigrationError, match="migration 2"):
            _apply_migrations(
                connection,
                (Migration(1, "CREATE TABLE survit (x INTEGER);"), Migration(2, bad_script)),
            )
        # Migration 1 has ITS OWN transaction (applied); migration 2 is ENTIRELY rolled back.
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert _table_names(connection) == {"survit"}
    finally:
        connection.close()


def test_load_scripts_orders_by_name_and_skips_non_sql(tmp_path: Path) -> None:
    (tmp_path / "0002_second.sql").write_text("B", encoding="utf-8")
    (tmp_path / "0001_premier.sql").write_text("A", encoding="utf-8")
    (tmp_path / "README.md").write_text("ignored", encoding="utf-8")
    assert _load_scripts(tmp_path) == (Migration(1, "A"), Migration(2, "B"))


def test_load_scripts_rejects_a_non_numeric_prefix(tmp_path: Path) -> None:
    (tmp_path / "abcd_mauvais.sql").write_text("X", encoding="utf-8")
    with pytest.raises(MigrationError, match="abcd_mauvais.sql"):
        _load_scripts(tmp_path)


def test_utc_iso_is_fixed_width_and_normalizes_to_utc() -> None:
    # Fixed width (microseconds ALWAYS written) => lexicographic order == chronological.
    paris = timezone(timedelta(hours=2))
    moment = datetime(2026, 6, 11, 14, 0, 0, tzinfo=paris)
    assert utc_iso(moment) == "2026-06-11T12:00:00.000000+00:00"


def test_utc_now_returns_an_aware_utc_datetime() -> None:
    now = utc_now()
    assert now.tzinfo == UTC


# --- MANDATED AMENDMENT: recursive_triggers guards append-only against INSERT OR REPLACE ---


def test_insert_or_replace_on_existing_hash_raises_integrity_error(tmp_path: Path) -> None:
    """INSERT OR REPLACE on an existing files row must raise (triggers guarded by
    recursive_triggers=ON, spec §3 amendment).  The trigger fires on the implicit DELETE
    that REPLACE performs internally, surfacing as sqlite3.IntegrityError."""
    connection = open_catalog(tmp_path / "catalog.db")
    try:
        insert_file(connection, _CANONICAL_HASH, 1)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT OR REPLACE INTO files (ed2k_hash, size_bytes) VALUES (?, 2)",
                (_CANONICAL_HASH,),
            )
    finally:
        connection.close()


# --- Hardening (adversarial review post-6c389b1) ---


def test_load_scripts_rejects_duplicate_version_numbers(tmp_path: Path) -> None:
    (tmp_path / "0001_a.sql").write_text("A", encoding="utf-8")
    (tmp_path / "0001_b.sql").write_text("B", encoding="utf-8")
    with pytest.raises(MigrationError, match="increasing"):
        _load_scripts(tmp_path)


def test_load_scripts_rejects_non_padded_prefixes_that_misorder(tmp_path: Path) -> None:
    # Lexicographic sort: "10_b.sql" < "2_a.sql" -> versions (10, 2), not increasing.
    (tmp_path / "2_a.sql").write_text("A", encoding="utf-8")
    (tmp_path / "10_b.sql").write_text("B", encoding="utf-8")
    with pytest.raises(MigrationError, match="increasing"):
        _load_scripts(tmp_path)


def test_stray_commit_in_a_script_is_detected_before_stamping(tmp_path: Path) -> None:
    # A COMMIT inside a script closes the runner's envelope: detection BEFORE the stamp,
    # version unchanged, NO partial state (the attack script only does COMMIT).
    connection = sqlite3.connect(tmp_path / "commit.db", autocommit=True)
    try:
        with pytest.raises(MigrationError, match="migration 2"):
            _apply_migrations(
                connection,
                (Migration(1, "CREATE TABLE survit (x INTEGER);"), Migration(2, "COMMIT;")),
            )
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert _table_names(connection) == {"survit"}
    finally:
        connection.close()


def test_stray_commit_followed_by_a_failure_keeps_the_version_unstamped(tmp_path: Path) -> None:
    # Attack variant 2: a stray COMMIT THEN a statement that fails. The work
    # committed by the stray COMMIT is unrecoverable, but the version MUST NOT
    # be stamped (otherwise the migration would be marked applied even though it failed).
    connection = sqlite3.connect(tmp_path / "commit2.db", autocommit=True)
    script = "CREATE TABLE t (x INTEGER);\nCOMMIT;\nINSERT INTO inexistante VALUES (1);"
    try:
        with pytest.raises(MigrationError, match="migration 1"):
            _apply_migrations(connection, (Migration(1, script),))
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        connection.close()


def test_open_closes_the_connection_on_a_non_persistence_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # FileNotFoundError is neither sqlite3.Error nor PersistenceError: it must
    # propagate AS IS, but the connection must not leak (unconditional close).
    captured: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def capturing_connect(database: str | Path, *, autocommit: bool) -> sqlite3.Connection:
        connection = real_connect(database, autocommit=autocommit)
        captured.append(connection)
        return connection

    def exploding_load_scripts(directory: object) -> tuple[Migration, ...]:
        raise FileNotFoundError("repertoire de migrations disparu")

    monkeypatch.setattr(sqlite3, "connect", capturing_connect)
    monkeypatch.setattr(connection_module, "_load_scripts", exploding_load_scripts)
    with pytest.raises(FileNotFoundError):
        open_catalog(tmp_path / "catalog.db")
    assert len(captured) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="[Cc]losed"):
        captured[0].execute("SELECT 1")


def test_utc_iso_rejects_a_naive_datetime() -> None:
    with pytest.raises(ValueError, match="aware"):
        utc_iso(datetime(2026, 6, 11, 14, 0, 0))


# --- The no-transaction directive and the restored pragmas (stage 1 spec, D8) ---


def test_load_scripts_reads_the_no_transaction_directive(tmp_path: Path) -> None:
    (tmp_path / "0001_plain.sql").write_text("A", encoding="utf-8")
    (tmp_path / "0002_vacuum.sql").write_text(_NO_TRANSACTION + "VACUUM;", encoding="utf-8")
    assert _load_scripts(tmp_path) == (
        Migration(1, "A", transactional=True),
        Migration(2, _NO_TRANSACTION + "VACUUM;", transactional=False),
    )


def test_load_scripts_rejects_a_misspelled_directive(tmp_path: Path) -> None:
    (tmp_path / "0001_typo.sql").write_text(
        "-- migration: no-transction\nVACUUM;", encoding="utf-8"
    )
    with pytest.raises(MigrationError, match="0001_typo.sql.*no-transction"):
        _load_scripts(tmp_path)


def test_vacuum_without_the_directive_fails_inside_the_runner_transaction(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "0001_vacuum.sql").write_text("VACUUM;", encoding="utf-8")
    connection = sqlite3.connect(tmp_path / "vacuum.db", autocommit=True)
    try:
        with pytest.raises(MigrationError, match="cannot VACUUM from within a transaction"):
            _apply_migrations(connection, _load_scripts(scripts))
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        connection.close()


def test_vacuum_with_the_directive_runs_and_stamps_its_version(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "0001_table.sql").write_text("CREATE TABLE t (x INTEGER);", encoding="utf-8")
    (scripts / "0002_vacuum.sql").write_text(_NO_TRANSACTION + "VACUUM;", encoding="utf-8")
    connection = sqlite3.connect(tmp_path / "vacuum.db", autocommit=True)
    try:
        _apply_migrations(connection, _load_scripts(scripts))
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert not connection.in_transaction
    finally:
        connection.close()


def test_failing_script_with_the_directive_leaves_the_version_unchanged(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "0001_table.sql").write_text("CREATE TABLE t (x INTEGER);", encoding="utf-8")
    (scripts / "0002_broken.sql").write_text(
        _NO_TRANSACTION + "INSERT INTO inexistante VALUES (1);", encoding="utf-8"
    )
    connection = sqlite3.connect(tmp_path / "broken.db", autocommit=True)
    try:
        with pytest.raises(MigrationError, match="migration 2"):
            _apply_migrations(connection, _load_scripts(scripts))
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        connection.close()


_LARGE_REWRITE_PRAGMAS = "PRAGMA secure_delete = OFF;\nPRAGMA cache_size = -262144;\n"


def _secure_delete_and_cache_size(connection: sqlite3.Connection) -> tuple[int, int]:
    secure_delete = connection.execute("PRAGMA secure_delete").fetchone()[0]
    cache_size = connection.execute("PRAGMA cache_size").fetchone()[0]
    return secure_delete, cache_size


@pytest.mark.parametrize("transactional", [True, False])
def test_a_script_pragmas_are_restored_after_it(tmp_path: Path, transactional: bool) -> None:
    """``secure_delete = 1`` stands for the image's build, which compiles ``SECURE_DELETE`` in."""
    connection = sqlite3.connect(tmp_path / "pragmas.db", autocommit=True)
    try:
        connection.execute("PRAGMA secure_delete = ON")
        before = _secure_delete_and_cache_size(connection)
        script = _LARGE_REWRITE_PRAGMAS + "CREATE TABLE t (x INTEGER);"
        _apply_migrations(connection, (Migration(1, script, transactional),))
        assert _secure_delete_and_cache_size(connection) == before == (1, -2000)
    finally:
        connection.close()


def test_a_script_pragmas_are_restored_even_when_it_fails(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "pragmas_fail.db", autocommit=True)
    try:
        connection.execute("PRAGMA secure_delete = ON")
        before = _secure_delete_and_cache_size(connection)
        script = _LARGE_REWRITE_PRAGMAS + "INSERT INTO inexistante VALUES (1);"
        with pytest.raises(MigrationError):
            _apply_migrations(connection, (Migration(1, script),))
        assert _secure_delete_and_cache_size(connection) == before
    finally:
        connection.close()


def test_a_fast_secure_delete_is_restored_as_fast(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "fast.db", autocommit=True)
    try:
        connection.execute("PRAGMA secure_delete = FAST")
        _apply_migrations(connection, (Migration(1, _LARGE_REWRITE_PRAGMAS),))
        assert connection.execute("PRAGMA secure_delete").fetchone()[0] == 2
    finally:
        connection.close()
