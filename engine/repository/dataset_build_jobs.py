#!/usr/bin/env python3
"""Dataset Build Job index, terminal transitions, and scratch ownership."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from engine.archive import version as version_archive
from engine.contracts import strict_json
from engine.contracts.archive import require_resource_path_segment
from engine.contracts.dataset_workspace import build_job_row
from engine.control import database as engine_database
from engine.core import clock as engine_clock
from engine.repository import dataset_build_paths
from engine.repository import datasets


def job_root(config):
    return dataset_build_paths.dataset_build_job_root(config)


def require_build_execution_workspace(config, job_id, *, required=False):
    """Prove the DB path is exactly this Job's symlink-free managed Workspace."""

    with engine_database.connect_database(config) as conn:
        row = conn.execute(
            "SELECT execution_workspace_path FROM dataset_build_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"Unknown Dataset Build Job: {job_id}")
    canonical = dataset_build_paths.dataset_build_workspace(
        config, job_id, required=required
    )
    stored = row["execution_workspace_path"]
    if (
        not isinstance(stored, str)
        or not Path(stored).is_absolute()
        or Path(stored) != canonical
    ):
        raise ValueError(
            "Dataset Build Job execution Workspace path is not its managed identity path."
        )
    return canonical


def reconcile_build_job_directories(config):
    """Verify indexed scratch and remove only unindexed canonical Job roots."""

    root = dataset_build_paths.dataset_build_job_root(config)
    with engine_database.connect_database(config) as conn:
        rows = conn.execute(
            "SELECT job_id FROM dataset_build_jobs ORDER BY job_id"
        ).fetchall()
    indexed = {row["job_id"] for row in rows}
    for job_id in sorted(indexed):
        require_build_execution_workspace(config, job_id, required=False)
    for entry in tuple(root.iterdir()):
        try:
            job_id = require_resource_path_segment(
                entry.name, label="Dataset Build Job directory name"
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Dataset Build Job root contains an invalid entry: {entry.name}"
            ) from exc
        if entry.is_symlink():
            if job_id in indexed:
                raise ValueError(
                    f"Indexed Dataset Build Job directory is a symbolic link: {entry.name}"
                )
            entry.unlink()
            continue
        expected = dataset_build_paths.dataset_build_job_directory(config, job_id)
        if job_id in indexed:
            continue
        if entry.is_dir() and entry.resolve() == expected:
            version_archive.discard_archive(entry)
        else:
            raise ValueError(
                f"Dataset Build Job root contains an unexpected entry: {entry.name}"
            )


def submission_digest(submission):
    encoded = strict_json.dumps(
        submission, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def commit_build_submission(config, submission, operation_nonce):
    """Atomically claim one Workspace, create its Job, and seal a receipt."""

    digest = submission_digest(submission)
    with engine_database.connect_database(config) as conn:
        conn.execute("BEGIN IMMEDIATE")
        authoritative = conn.execute(
            "SELECT status, submitted_job_id FROM dataset_workspaces WHERE workspace_id = ?",
            (submission["workspaceId"],),
        ).fetchone()
        if (
            authoritative is None
            or authoritative["status"] != "draft"
            or authoritative["submitted_job_id"]
        ):
            current = authoritative["status"] if authoritative is not None else "missing"
            raise ValueError(
                f"Dataset Workspace '{submission['workspaceId']}' is already {current}."
            )
        if conn.execute(
            "SELECT 1 FROM datasets WHERE dataset_id = ?",
            (submission["outputDatasetId"],),
        ).fetchone():
            raise ValueError(
                f"Dataset '{submission['outputDatasetId']}' already exists and is immutable. "
                "A build must publish a new Dataset ID."
            )
        conn.execute(
            """
            INSERT INTO dataset_build_jobs
            (job_id, workspace_id, output_dataset_id, output_dataset_name, status,
             execution_workspace_path, recipe_id, recipe_version, script_path, script_digest,
             arguments_json, source_bindings_json, submitted_at)
            VALUES (?, ?, ?, ?, 'submitted', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                submission["jobId"], submission["workspaceId"],
                submission["outputDatasetId"], submission["outputDatasetName"],
                submission["executionWorkspacePath"], submission["recipeId"],
                submission["recipeVersion"], submission["scriptPath"],
                submission["scriptDigest"],
                strict_json.dumps(submission["arguments"]),
                strict_json.dumps(submission["sources"], sort_keys=True),
                submission["submittedAt"],
            ),
        )
        workspace_update = conn.execute(
            """
            UPDATE dataset_workspaces
            SET status = 'submitted', submitted_job_id = ?, updated_at = ?
            WHERE workspace_id = ? AND status = 'draft' AND submitted_job_id = ''
            """,
            (
                submission["jobId"], submission["submittedAt"],
                submission["workspaceId"],
            ),
        )
        if workspace_update.rowcount != 1:
            raise RuntimeError("Dataset Workspace was submitted concurrently.")
        conn.execute(
            """
            INSERT INTO dataset_build_submission_receipts
            (operation_nonce, job_id, submission_digest) VALUES (?, ?, ?)
            """,
            (operation_nonce, submission["jobId"], digest),
        )
        conn.commit()


def build_submission_commit_state(config, submission, operation_nonce):
    """Resolve a commit ACK failure using this transaction's exact receipt."""

    expected_digest = submission_digest(submission)
    try:
        with engine_database.connect_database(config) as conn:
            receipt = conn.execute(
                """
                SELECT job_id, submission_digest
                FROM dataset_build_submission_receipts WHERE operation_nonce = ?
                """,
                (operation_nonce,),
            ).fetchone()
            if receipt is None:
                return "absent"
            if tuple(receipt) != (submission["jobId"], expected_digest):
                return "conflict"
            job = conn.execute(
                "SELECT * FROM dataset_build_jobs WHERE job_id = ?",
                (submission["jobId"],),
            ).fetchone()
            workspace = conn.execute(
                "SELECT status, submitted_job_id, updated_at FROM dataset_workspaces WHERE workspace_id = ?",
                (submission["workspaceId"],),
            ).fetchone()
        if job is None or workspace is None:
            return "conflict"
        expected_job = {
            "jobId": submission["jobId"],
            "workspaceId": submission["workspaceId"],
            "outputDatasetId": submission["outputDatasetId"],
            "outputDatasetName": submission["outputDatasetName"],
            "status": "submitted",
            "recipeId": submission["recipeId"],
            "recipeVersion": submission["recipeVersion"],
            "scriptDigest": submission["scriptDigest"],
            "arguments": submission["arguments"],
            "sources": submission["sources"],
            "submittedAt": submission["submittedAt"],
            "startedAt": "", "completedAt": "", "outputVersionId": "",
            "stdout": "", "stderr": "", "error": "",
        }
        if (
            build_job_row(job) != expected_job
            or job["execution_workspace_path"] != submission["executionWorkspacePath"]
            or job["script_path"] != submission["scriptPath"]
            or tuple(workspace) != (
                "submitted", submission["jobId"], submission["submittedAt"]
            )
        ):
            return "conflict"
        return "committed"
    except (OSError, TypeError, ValueError, sqlite3.Error):
        return "unknown"


def mark_build_running(config, job_id):
    started_at = engine_clock.utc_now()
    with engine_database.connect_database(config) as conn:
        cursor = conn.execute(
            """
            UPDATE dataset_build_jobs SET status = 'running', started_at = ?
            WHERE job_id = ? AND status = 'submitted'
            """,
            (started_at, job_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Dataset Build Job did not start from submitted state.")
        conn.commit()
    return started_at

def get_build_job(config, job_id):
    with engine_database.connect_database(config) as conn:
        row = conn.execute("SELECT * FROM dataset_build_jobs WHERE job_id = ?", (job_id,)).fetchone()
    if not row:
        raise ValueError(f"Unknown Dataset Build Job: {job_id}")
    return build_job_row(row)


def list_build_jobs(config):
    with engine_database.connect_database(config) as conn:
        rows = conn.execute("SELECT * FROM dataset_build_jobs ORDER BY submitted_at DESC").fetchall()
    return [build_job_row(row) for row in rows]


def completed_build_evidence(config, job_id):
    """Return one verified immutable Dataset Version committed by this Job."""
    with engine_database.connect_database(config) as conn:
        rows = conn.execute(
            """
            SELECT version_id, dataset_id, created_at
            FROM dataset_versions
            WHERE build_job_id = ?
            ORDER BY version_id
            """,
            (job_id,),
        ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError(f"Dataset Build Job '{job_id}' has multiple output versions.")
    row = rows[0]
    version = datasets.verify_dataset_version_id(config, row["version_id"])
    if (
        version["datasetId"] != row["dataset_id"]
        or version["buildJobId"] != job_id
    ):
        raise ValueError(f"Dataset Build Job '{job_id}' output evidence is inconsistent.")
    return {
        "datasetVersionId": row["version_id"],
        "datasetId": row["dataset_id"],
        "completedAt": row["created_at"],
    }


def discard_execution_workspace(config, job_id, execution_workspace):
    """Best-effort removal of non-authoritative terminal Build scratch data."""
    try:
        expected_root = dataset_build_paths.dataset_build_job_directory(
            config, job_id
        )
        expected_workspace = dataset_build_paths.dataset_build_workspace(
            config, job_id
        )
    except (TypeError, ValueError):
        return False
    supplied = Path(execution_workspace)
    if (
        not supplied.is_absolute()
        or supplied != expected_workspace
        or supplied.is_symlink()
    ):
        return False
    try:
        if expected_root.exists():
            version_archive.discard_archive(expected_root)
    except OSError:
        return False
    return True


def terminal_build_workspaces(config):
    """Return terminal Job scratch identities for service-level reconciliation."""
    with engine_database.connect_database(config) as conn:
        rows = conn.execute(
            """
            SELECT job_id, execution_workspace_path FROM dataset_build_jobs
            WHERE status IN ('completed', 'failed') ORDER BY job_id
            """
        ).fetchall()
    return [
        {
            "jobId": row["job_id"],
            "executionWorkspacePath": row["execution_workspace_path"],
        }
        for row in rows
    ]


def mark_build_completed(config, job_id, workspace_id, evidence):
    with engine_database.connect_database(config) as conn:
        conn.execute("BEGIN IMMEDIATE")
        job_update = conn.execute(
            """
            UPDATE dataset_build_jobs
            SET status = 'completed', completed_at = ?, output_version_id = ?, error_text = ''
            WHERE job_id = ? AND workspace_id = ? AND output_dataset_id = ?
              AND status = 'running'
            """,
            (
                evidence["completedAt"], evidence["datasetVersionId"], job_id,
                workspace_id, evidence["datasetId"],
            ),
        )
        workspace_update = conn.execute(
            """
            UPDATE dataset_workspaces
            SET status = 'published', updated_at = ?
            WHERE workspace_id = ? AND status = 'submitted'
              AND submitted_job_id = ?
            """,
            (evidence["completedAt"], workspace_id, job_id),
        )
        if job_update.rowcount != 1 or workspace_update.rowcount != 1:
            raise RuntimeError("Dataset Build completion state changed concurrently.")
        conn.commit()


def mark_build_failed(config, job_id, workspace_id, error):
    """Atomically close one active Build after a deterministic failure."""
    message = str(error).strip() or "Dataset Build failed without an error message."
    now = engine_clock.utc_now()
    with engine_database.connect_database(config) as conn:
        conn.execute("BEGIN IMMEDIATE")
        job_update = conn.execute(
            """
            UPDATE dataset_build_jobs
            SET status = 'failed', completed_at = ?, error_text = ?
            WHERE job_id = ? AND status IN ('submitted', 'running')
            """,
            (now, message, job_id),
        )
        workspace_update = conn.execute(
            """
            UPDATE dataset_workspaces SET status = 'failed', updated_at = ?
            WHERE workspace_id = ? AND status = 'submitted'
              AND submitted_job_id = ?
            """,
            (now, workspace_id, job_id),
        )
        if job_update.rowcount != 1 or workspace_update.rowcount != 1:
            raise RuntimeError(
                "Dataset Build failure state changed concurrently."
            ) from error
        conn.commit()
def assert_output_dataset_available(config, dataset_id):
    with engine_database.connect_database(config) as conn:
        if conn.execute("SELECT 1 FROM datasets WHERE dataset_id = ?", (dataset_id,)).fetchone():
            raise ValueError(
                f"Dataset '{dataset_id}' already exists and is immutable. A build must publish a new Dataset ID."
            )
