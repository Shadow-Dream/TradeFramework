"""Engine control-database schema and connection authority.

This module owns the physical SQLite schema and the only production connection
factory.  Repositories may depend on it; compilation and runtime layers must
not.
"""

import fcntl
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from engine.archive import version as version_archive
from engine.contracts import strict_json
from engine.control.owner import assert_control_access
from engine.core import clock as engine_clock


DATABASE_SCHEMA_VERSION = 19
_EXPECTED_DATABASE_SCHEMA_FINGERPRINT = None
_DATABASE_PREPARE_THREAD_LOCK = threading.Lock()
_DATABASE_PREPARE_LOCK_NAME = ".engine-database.lock"
_BACKTEST_IDENTITY_INDEX_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS "
    "backtest_jobs_backtest_identity ON backtest_jobs (backtest_id)"
)
_BACKTEST_IDENTITY_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS backtest_job_backtest_identity_required
BEFORE INSERT ON backtest_jobs
WHEN typeof(NEW.backtest_id) != 'text' OR length(trim(NEW.backtest_id)) = 0
BEGIN
    SELECT RAISE(ABORT, 'Backtest Job backtest identity is required');
END
""".strip()


def _database_path(config):
    return str(Path(config["controlRoot"]) / "engine-data.db")


@contextmanager
def _database_prepare_lock(config):
    """Serialize physical database preparation across threads and processes."""

    root = Path(config["controlRoot"])
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / _DATABASE_PREPARE_LOCK_NAME
    with _DATABASE_PREPARE_THREAD_LOCK, lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            # Close the check/acquire race with a service claiming ownership
            # after the caller's first access check.
            assert_control_access(config)
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _database_schema_fingerprint(connection):
    return tuple(
        tuple(row)
        for row in connection.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL
            ORDER BY type, name
            """
        ).fetchall()
    )


def _expected_database_schema_fingerprint():
    global _EXPECTED_DATABASE_SCHEMA_FINGERPRINT
    if _EXPECTED_DATABASE_SCHEMA_FINGERPRINT is None:
        with sqlite3.connect(":memory:") as reference:
            _initialize_database(reference)
            _EXPECTED_DATABASE_SCHEMA_FINGERPRINT = _database_schema_fingerprint(reference)
    return _EXPECTED_DATABASE_SCHEMA_FINGERPRINT


def _require_database_schema(connection):
    schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if schema_version != DATABASE_SCHEMA_VERSION:
        raise RuntimeError(
            f"Engine database schema {schema_version} is not prepared for "
            f"{DATABASE_SCHEMA_VERSION}."
        )
    if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
        raise RuntimeError("Engine database foreign-key enforcement is disabled.")
    if str(connection.execute("PRAGMA journal_mode").fetchone()[0]).casefold() != "wal":
        raise RuntimeError("Engine database WAL journal mode is disabled.")
    if (
        _database_schema_fingerprint(connection)
        != _expected_database_schema_fingerprint()
    ):
        raise RuntimeError("Engine database schema objects do not match the current contract.")


def prepare_database(config):
    """Archive an incompatible database as one verified directory before replacement."""
    assert_control_access(config)
    with _database_prepare_lock(config):
        _prepare_database_locked(config)


def _prepare_database_locked(config):
    """Prepare the database while the physical replacement lock is held."""

    path = Path(_database_path(config))
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            _initialize_database(connection)
        return
    with sqlite3.connect(path) as probe:
        probe.execute("PRAGMA foreign_keys=ON")
        schema_version = int(probe.execute("PRAGMA user_version").fetchone()[0])
        journal_mode = str(probe.execute("PRAGMA journal_mode").fetchone()[0]).casefold()
        has_tables = bool(probe.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' LIMIT 1"
        ).fetchone())
        schema_matches = (
            has_tables
            and schema_version == DATABASE_SCHEMA_VERSION
            and journal_mode == "wal"
            and _database_schema_fingerprint(probe)
            == _expected_database_schema_fingerprint()
        )
    if not has_tables or schema_matches:
        if not has_tables:
            with sqlite3.connect(path) as connection:
                _initialize_database(connection)
        return
    archived_at = engine_clock.utc_now()
    stamp = datetime.fromisoformat(
        archived_at.removesuffix("Z") + "+00:00"
    ).strftime("%Y%m%dT%H%M%S%fZ")
    destination = Path(config["controlRoot"]) / "_incompatible_archives" / "database" / stamp
    staging = version_archive.staging_directory(destination.parent)
    source_files = [candidate for candidate in (
        path, Path(f"{path}-wal"), Path(f"{path}-shm")
    ) if candidate.exists()]
    try:
        for source in source_files:
            shutil.copy2(source, staging / source.name)
        (staging / "archive.json").write_text(strict_json.dumps({
            "reason": "Incompatible Engine database physical contract",
            "foundSchemaVersion": schema_version,
            "requiredSchemaVersion": DATABASE_SCHEMA_VERSION,
            "foundJournalMode": journal_mode,
            "requiredJournalMode": "wal",
            "archivedAt": archived_at,
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        digest = version_archive.content_digest(version_archive.file_manifest(staging))
        version_archive.seal_directory(
            staging,
            destination,
            managed_root=config["controlRoot"],
            resource_type="incompatible-database",
            resource_id="engine-data",
            version=stamp,
            digest=digest,
        )
        for source in source_files:
            source.unlink()
        with sqlite3.connect(path) as connection:
            _initialize_database(connection)
    except Exception:
        if staging.exists():
            version_archive.discard_archive(staging)
        raise


def connect_database(config):
    assert_control_access(config)
    path = Path(_database_path(config))
    if not path.is_file():
        raise RuntimeError("Engine database is not prepared.")
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        _require_database_schema(connection)
    except Exception:
        connection.close()
        raise
    return connection


def _initialize_database(connection):
    connection.executescript(
        """
        PRAGMA foreign_keys=ON;
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS datasets (
            dataset_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            source_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            archived_at TEXT NOT NULL DEFAULT '',
            archive_reason TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS dataset_versions (
            version_id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            capabilities_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            storage_type TEXT NOT NULL,
            storage_uri TEXT NOT NULL,
            manifest_json TEXT NOT NULL,
            manifest_digest TEXT NOT NULL,
            build_job_id TEXT NOT NULL DEFAULT '',
            UNIQUE (dataset_id, content_hash),
            UNIQUE (dataset_id, version_id),
            FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id)
        );
        CREATE TRIGGER IF NOT EXISTS dataset_evidence_no_update
        BEFORE UPDATE ON datasets
        WHEN NEW.dataset_id IS NOT OLD.dataset_id
          OR NEW.source_json IS NOT OLD.source_json
          OR NEW.created_at IS NOT OLD.created_at
          OR NEW.metadata_json IS NOT OLD.metadata_json
        BEGIN
            SELECT RAISE(ABORT, 'Dataset identity and provenance are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS dataset_no_delete
        BEFORE DELETE ON datasets
        BEGIN
            SELECT RAISE(ABORT, 'Dataset records may only be archived');
        END;
        CREATE INDEX IF NOT EXISTS dataset_versions_dataset_publication
            ON dataset_versions (dataset_id);
        CREATE TRIGGER IF NOT EXISTS sealed_dataset_version_no_update
        BEFORE UPDATE ON dataset_versions
        BEGIN
            SELECT RAISE(ABORT, 'Dataset versions are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS sealed_dataset_version_no_delete
        BEFORE DELETE ON dataset_versions
        BEGIN
            SELECT RAISE(ABORT, 'Dataset versions are immutable');
        END;
        CREATE TABLE IF NOT EXISTS dataset_publication_receipts (
            operation_nonce TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL,
            version_id TEXT NOT NULL,
            manifest_digest TEXT NOT NULL,
            build_job_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (dataset_id, version_id)
                REFERENCES dataset_versions(dataset_id, version_id)
        );
        CREATE TRIGGER IF NOT EXISTS dataset_publication_receipt_no_update
        BEFORE UPDATE ON dataset_publication_receipts
        BEGIN
            SELECT RAISE(ABORT, 'Dataset publication receipts are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS dataset_publication_receipt_no_delete
        BEFORE DELETE ON dataset_publication_receipts
        BEGIN
            SELECT RAISE(ABORT, 'Dataset publication receipts are immutable');
        END;
        CREATE TABLE IF NOT EXISTS sampler_definitions (
            sampler_id TEXT NOT NULL,
            version TEXT NOT NULL,
            name TEXT NOT NULL,
            sampler_type TEXT NOT NULL,
            config_json TEXT NOT NULL,
            parameter_schema_json TEXT NOT NULL DEFAULT '{}',
            output_schema_json TEXT NOT NULL,
            source_text TEXT NOT NULL,
            entry_point TEXT NOT NULL,
            status TEXT NOT NULL,
            content_digest TEXT NOT NULL,
            archive_root TEXT NOT NULL,
            archive_manifest_digest TEXT NOT NULL,
            builtin INTEGER NOT NULL CHECK (builtin IN (0, 1)),
            created_at TEXT NOT NULL,
            PRIMARY KEY (sampler_id, version)
        );
        CREATE TRIGGER IF NOT EXISTS sampler_definition_no_update
        BEFORE UPDATE ON sampler_definitions
        BEGIN
            SELECT RAISE(ABORT, 'Archived Sampler definitions are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS sampler_definition_no_delete
        BEFORE DELETE ON sampler_definitions
        BEGIN
            SELECT RAISE(ABORT, 'Archived Sampler definitions are immutable');
        END;
        CREATE TABLE IF NOT EXISTS dataset_lineage (
            alias TEXT NOT NULL,
            upstream_dataset_id TEXT NOT NULL,
            upstream_version_id TEXT NOT NULL,
            downstream_dataset_id TEXT NOT NULL,
            downstream_version_id TEXT NOT NULL,
            build_job_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (downstream_version_id, alias),
            FOREIGN KEY (upstream_dataset_id, upstream_version_id)
                REFERENCES dataset_versions(dataset_id, version_id),
            FOREIGN KEY (downstream_dataset_id, downstream_version_id)
                REFERENCES dataset_versions(dataset_id, version_id)
        );
        CREATE INDEX IF NOT EXISTS dataset_lineage_upstream
            ON dataset_lineage (upstream_dataset_id, created_at);
        CREATE INDEX IF NOT EXISTS dataset_lineage_downstream
            ON dataset_lineage (downstream_dataset_id, created_at);
        CREATE TRIGGER IF NOT EXISTS dataset_lineage_no_update
        BEFORE UPDATE ON dataset_lineage
        BEGIN
            SELECT RAISE(ABORT, 'Dataset lineage is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS dataset_lineage_no_delete
        BEFORE DELETE ON dataset_lineage
        BEGIN
            SELECT RAISE(ABORT, 'Dataset lineage is immutable');
        END;
        CREATE TABLE IF NOT EXISTS dataset_workspaces (
            workspace_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('draft', 'submitted', 'published', 'failed', 'deleting')),
            workspace_path TEXT NOT NULL,
            source_bindings_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            submitted_job_id TEXT NOT NULL DEFAULT '',
            internal INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS dataset_build_jobs (
            job_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            output_dataset_id TEXT NOT NULL,
            output_dataset_name TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('submitted', 'running', 'completed', 'failed')),
            execution_workspace_path TEXT NOT NULL DEFAULT '',
            recipe_id TEXT NOT NULL DEFAULT '',
            recipe_version TEXT NOT NULL DEFAULT '',
            script_path TEXT NOT NULL,
            script_digest TEXT NOT NULL,
            arguments_json TEXT NOT NULL DEFAULT '[]',
            source_bindings_json TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT '',
            completed_at TEXT NOT NULL DEFAULT '',
            output_version_id TEXT NOT NULL DEFAULT '',
            stdout_text TEXT NOT NULL DEFAULT '',
            stderr_text TEXT NOT NULL DEFAULT '',
            error_text TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS dataset_build_submission_receipts (
            operation_nonce TEXT PRIMARY KEY,
            job_id TEXT NOT NULL UNIQUE,
            submission_digest TEXT NOT NULL,
            FOREIGN KEY (job_id) REFERENCES dataset_build_jobs(job_id)
        );
        CREATE TRIGGER IF NOT EXISTS dataset_build_submission_receipt_no_update
        BEFORE UPDATE ON dataset_build_submission_receipts
        BEGIN
            SELECT RAISE(ABORT, 'Dataset Build submission receipts are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS dataset_build_submission_receipt_no_delete
        BEFORE DELETE ON dataset_build_submission_receipts
        BEGIN
            SELECT RAISE(ABORT, 'Dataset Build submission receipts are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS dataset_build_job_terminal_no_update
        BEFORE UPDATE ON dataset_build_jobs
        WHEN OLD.status IN ('completed', 'failed')
        BEGIN
            SELECT RAISE(ABORT, 'Terminal Dataset Build Jobs are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS dataset_build_job_no_delete
        BEFORE DELETE ON dataset_build_jobs
        BEGIN
            SELECT RAISE(ABORT, 'Dataset Build Job evidence is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS dataset_workspace_terminal_state_no_update
        BEFORE UPDATE ON dataset_workspaces
        WHEN OLD.status IN ('published', 'failed', 'deleting')
          AND NOT (
            OLD.status IN ('published', 'failed')
            AND NEW.status = 'deleting'
            AND NEW.submitted_job_id IS OLD.submitted_job_id
          )
          AND (NEW.status IS NOT OLD.status OR NEW.submitted_job_id IS NOT OLD.submitted_job_id)
        BEGIN
            SELECT RAISE(ABORT, 'Terminal Dataset Workspace lifecycle is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS dataset_workspace_submitted_job_no_replace
        BEFORE UPDATE ON dataset_workspaces
        WHEN OLD.submitted_job_id <> ''
          AND NEW.submitted_job_id IS NOT OLD.submitted_job_id
        BEGIN
            SELECT RAISE(ABORT, 'Dataset Workspace submitted Job is immutable');
        END;
        CREATE TABLE IF NOT EXISTS dataset_recipes (
            recipe_id TEXT NOT NULL,
            version TEXT NOT NULL,
            name TEXT NOT NULL,
            script_digest TEXT NOT NULL,
            content_digest TEXT NOT NULL,
            archive_root TEXT NOT NULL,
            archive_manifest_digest TEXT NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL,
            PRIMARY KEY (recipe_id, version)
        );
        CREATE TRIGGER IF NOT EXISTS dataset_recipe_no_update
        BEFORE UPDATE ON dataset_recipes
        BEGIN
            SELECT RAISE(ABORT, 'Archived Dataset Recipes are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS dataset_recipe_no_delete
        BEFORE DELETE ON dataset_recipes
        BEGIN
            SELECT RAISE(ABORT, 'Archived Dataset Recipes are immutable');
        END;
        CREATE TABLE IF NOT EXISTS backtests (
            backtest_id TEXT PRIMARY KEY,
            pipeline_id TEXT NOT NULL,
            dataset_id TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('completed', 'archived')),
            runner TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            request_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            visualization_json TEXT NOT NULL,
            archived_at TEXT NOT NULL DEFAULT '',
            archive_reason TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id)
        );
        CREATE TABLE IF NOT EXISTS backtest_jobs (
            job_id TEXT PRIMARY KEY,
            status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
            phase TEXT NOT NULL,
            pipeline_id TEXT NOT NULL,
            dataset_id TEXT NOT NULL,
            request_json TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT '',
            completed_at TEXT NOT NULL DEFAULT '',
            total_cycles INTEGER NOT NULL DEFAULT 0,
            completed_cycles INTEGER NOT NULL DEFAULT 0,
            backtest_id TEXT NOT NULL DEFAULT '',
            error_text TEXT NOT NULL DEFAULT '',
            snapshot_hash TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id)
        );
        CREATE INDEX IF NOT EXISTS backtest_jobs_submitted
            ON backtest_jobs (submitted_at DESC);
        CREATE TRIGGER IF NOT EXISTS backtest_job_evidence_no_update
        BEFORE UPDATE ON backtest_jobs
        WHEN NEW.job_id IS NOT OLD.job_id
          OR NEW.pipeline_id IS NOT OLD.pipeline_id
          OR NEW.dataset_id IS NOT OLD.dataset_id
          OR NEW.request_json IS NOT OLD.request_json
          OR NEW.submitted_at IS NOT OLD.submitted_at
          OR NEW.backtest_id IS NOT OLD.backtest_id
          OR NEW.snapshot_hash IS NOT OLD.snapshot_hash
        BEGIN
            SELECT RAISE(ABORT, 'Backtest Job execution evidence is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS backtest_job_no_delete
        BEFORE DELETE ON backtest_jobs
        BEGIN
            SELECT RAISE(ABORT, 'Backtest Job evidence is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS backtest_job_terminal_no_update
        BEFORE UPDATE ON backtest_jobs
        WHEN OLD.status IN ('completed', 'failed')
        BEGIN
            SELECT RAISE(ABORT, 'Terminal Backtest Jobs are immutable');
        END;
        CREATE TABLE IF NOT EXISTS backtest_result_metadata (
            backtest_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL,
            has_cycles INTEGER NOT NULL,
            data_keys_json TEXT NOT NULL,
            execution_chain_json TEXT NOT NULL,
            content_digest TEXT NOT NULL,
            result_size INTEGER NOT NULL,
            FOREIGN KEY (backtest_id) REFERENCES backtests(backtest_id)
        );
        CREATE TRIGGER IF NOT EXISTS backtest_result_metadata_no_update
        BEFORE UPDATE ON backtest_result_metadata
        BEGIN
            SELECT RAISE(ABORT, 'Backtest Result metadata is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS backtest_result_metadata_no_delete
        BEFORE DELETE ON backtest_result_metadata
        BEGIN
            SELECT RAISE(ABORT, 'Backtest Result metadata is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS backtest_execution_evidence_no_update
        BEFORE UPDATE ON backtests
        WHEN NEW.backtest_id IS NOT OLD.backtest_id
          OR NEW.pipeline_id IS NOT OLD.pipeline_id
          OR NEW.dataset_id IS NOT OLD.dataset_id
          OR NEW.runner IS NOT OLD.runner
          OR NEW.created_at IS NOT OLD.created_at
          OR NEW.completed_at IS NOT OLD.completed_at
          OR NEW.request_json IS NOT OLD.request_json
          OR NEW.metrics_json IS NOT OLD.metrics_json
        BEGIN
            SELECT RAISE(ABORT, 'Backtest execution evidence is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS backtest_no_delete
        BEFORE DELETE ON backtests
        BEGIN
            SELECT RAISE(ABORT, 'Backtest evidence may only be archived');
        END;
        CREATE TABLE IF NOT EXISTS visualizations (
            visualization_id TEXT PRIMARY KEY,
            backtest_id TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            spec_json TEXT NOT NULL,
            FOREIGN KEY (backtest_id) REFERENCES backtests(backtest_id)
        );
        """
    )
    connection.execute(_BACKTEST_IDENTITY_INDEX_SQL)
    connection.execute(_BACKTEST_IDENTITY_TRIGGER_SQL)
    connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
    connection.commit()
