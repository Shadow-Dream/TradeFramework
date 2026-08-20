"""SQLite repository and lifecycle state machine for Backtest jobs."""

from __future__ import annotations

import copy
import json

from engine.contracts import strict_json
from engine.contracts import digest as digest_contracts
from engine.contracts.backtest import (
    backtest_evidence_digest,
    backtest_execution_inputs,
)
from engine.control import database as engine_database


ACTIVE_STATUSES = frozenset({"queued", "running"})
TERMINAL_STATUSES = frozenset({"completed", "failed"})
_PUBLIC_JOB_COLUMNS = (
    "job_id",
    "status",
    "phase",
    "pipeline_id",
    "dataset_id",
    "submitted_at",
    "started_at",
    "completed_at",
    "total_cycles",
    "completed_cycles",
    "backtest_id",
    "error_text",
    "snapshot_hash",
)
_PUBLIC_JOB_SELECT = ", ".join(_PUBLIC_JOB_COLUMNS)


def _canonical_json_equal(left, right):
    """Compare strict JSON values without Python's numeric type coercion."""
    return strict_json.exact_equal(left, right)


def _require_request_evidence(
    request,
    *,
    pipeline_id,
    dataset_id,
    snapshot_hash,
):
    if not isinstance(request, dict) or not isinstance(
        request.get("executionSnapshot"), dict
    ):
        raise ValueError("Backtest Job index request has no execution snapshot.")
    execution_snapshot = request["executionSnapshot"]
    actual_snapshot_hash = execution_snapshot.get("snapshotHash")
    unsigned_snapshot = {
        key: value
        for key, value in execution_snapshot.items()
        if key != "snapshotHash"
    }
    if (
        actual_snapshot_hash != snapshot_hash
        or actual_snapshot_hash != backtest_evidence_digest(unsigned_snapshot)
        or request.get("pipeline", {}).get("pipelineId") != pipeline_id
        or request.get("datasetId") != dataset_id
    ):
        raise ValueError(
            "Backtest Job index request does not match its execution evidence."
        )
    request_inputs = {
        key: copy.deepcopy(value)
        for key, value in request.items()
        if key != "executionSnapshot"
    }
    if not _canonical_json_equal(
        execution_snapshot.get("executionInputs"),
        backtest_execution_inputs(request_inputs),
    ):
        raise ValueError(
            "Backtest Job execution inputs do not match its snapshot."
        )
    return request


def _public_job(row):
    status = row["status"]
    if status not in ACTIVE_STATUSES | TERMINAL_STATUSES:
        raise ValueError("Backtest Job index contains an invalid status.")
    total = row["total_cycles"]
    completed = row["completed_cycles"]
    if (
        isinstance(total, bool)
        or not isinstance(total, int)
        or total < 0
        or isinstance(completed, bool)
        or not isinstance(completed, int)
        or completed < 0
        or (total > 0 and completed > total)
    ):
        raise ValueError("Backtest Job index contains invalid cycle counts.")
    for field in (
        "job_id",
        "phase",
        "pipeline_id",
        "dataset_id",
        "submitted_at",
        "started_at",
        "completed_at",
        "backtest_id",
        "error_text",
        "snapshot_hash",
    ):
        if not isinstance(row[field], str):
            raise ValueError(f"Backtest Job index field '{field}' is invalid.")
    if (
        not row["job_id"]
        or not row["pipeline_id"]
        or not row["dataset_id"]
        or not row["submitted_at"]
    ):
        raise ValueError(
            "Backtest Job index identity and submission time are required."
        )
    if not digest_contracts.is_sha256_digest(row["snapshot_hash"]):
        raise ValueError("Backtest Job index snapshot hash is invalid.")
    if status == "queued" and (
        row["phase"] != "queued"
        or row["started_at"]
        or row["completed_at"]
        or row["error_text"]
        or total
        or completed
    ):
        raise ValueError("Queued Backtest Job index lifecycle state is invalid.")
    if status == "running" and (
        not row["started_at"] or row["completed_at"] or row["error_text"]
    ):
        raise ValueError("Running Backtest Job index lifecycle state is invalid.")
    if status == "completed" and (
        row["phase"] != "completed"
        or not row["started_at"]
        or not row["completed_at"]
        or row["error_text"]
        or completed != total
    ):
        raise ValueError("Completed Backtest Job index lifecycle state is invalid.")
    if status == "failed" and (
        row["phase"] not in {"failed", "interrupted"}
        or not row["completed_at"]
        or not row["error_text"]
    ):
        raise ValueError("Failed Backtest Job index lifecycle state is invalid.")
    progress = 1.0 if status == "completed" else (
        completed / total if total else 0.0
    )
    return {
        "jobId": row["job_id"],
        "status": status,
        "phase": row["phase"],
        "pipelineId": row["pipeline_id"],
        "datasetId": row["dataset_id"],
        "submittedAt": row["submitted_at"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
        "totalCycles": total,
        "completedCycles": completed,
        "progress": progress,
        "backtestId": row["backtest_id"],
        "error": row["error_text"],
        "snapshotHash": row["snapshot_hash"],
    }


class BacktestJobRepository:
    """The sole SQL owner for persistent Backtest job and evidence state."""

    def __init__(self, config):
        self.config = copy.deepcopy(config)

    def prepare(self):
        engine_database.prepare_database(self.config)

    def get(self, job_id):
        with engine_database.connect_database(self.config) as connection:
            row = connection.execute(
                f"SELECT {_PUBLIC_JOB_SELECT} FROM backtest_jobs "
                "WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Unknown Backtest job: {job_id}")
        return _public_job(row)

    def list(self, limit=50):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError(
                "Backtest Job limit must be a non-negative integer."
            )
        with engine_database.connect_database(self.config) as connection:
            rows = connection.execute(
                f"""
                SELECT {_PUBLIC_JOB_SELECT} FROM backtest_jobs
                ORDER BY CASE status
                    WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                    submitted_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            queued_ids = [
                row["job_id"]
                for row in connection.execute(
                    """
                    SELECT job_id FROM backtest_jobs
                    WHERE status = 'queued' ORDER BY submitted_at
                    """
                ).fetchall()
            ]
        jobs = [_public_job(row) for row in rows]
        positions = {
            job_id: index + 1 for index, job_id in enumerate(queued_ids)
        }
        for job in jobs:
            job["queuePosition"] = (
                positions[job["jobId"]] if job["status"] == "queued" else 0
            )
        return jobs

    def insert_queued(
        self,
        *,
        job_id,
        backtest_id,
        pipeline_id,
        dataset_id,
        request,
        submitted_at,
        snapshot_hash,
    ):
        if not isinstance(backtest_id, str) or not backtest_id.strip():
            raise ValueError(
                "Backtest Job backtest_id must be a non-empty string."
            )
        try:
            request_json = strict_json.dumps(request, sort_keys=True)
            durable_request = strict_json.loads(request_json)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError("Backtest Job index request JSON is invalid.") from exc
        _require_request_evidence(
            durable_request,
            pipeline_id=pipeline_id,
            dataset_id=dataset_id,
            snapshot_hash=snapshot_hash,
        )
        with engine_database.connect_database(self.config) as connection:
            connection.execute(
                """
                INSERT INTO backtest_jobs
                (job_id, status, phase, pipeline_id, dataset_id, request_json,
                 submitted_at, started_at, completed_at, total_cycles,
                 completed_cycles, backtest_id, error_text, snapshot_hash)
                VALUES (?, 'queued', 'queued', ?, ?, ?, ?, '', '', 0, 0, ?, '', ?)
                """,
                (
                    job_id,
                    pipeline_id,
                    dataset_id,
                    request_json,
                    submitted_at,
                    backtest_id,
                    snapshot_hash,
                ),
            )
            connection.commit()

    def mark_running(self, job_id, started_at):
        with engine_database.connect_database(self.config) as connection:
            cursor = connection.execute(
                """
                UPDATE backtest_jobs
                SET status = 'running', phase = 'preparing', started_at = ?,
                    error_text = ''
                WHERE job_id = ? AND status = 'queued'
                """,
                (started_at, job_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    "Backtest Job did not start from queued state."
                )
            connection.commit()

    def record_progress(self, job_id, *, phase, total_cycles, completed_cycles):
        with engine_database.connect_database(self.config) as connection:
            cursor = connection.execute(
                """
                UPDATE backtest_jobs
                SET phase = ?, total_cycles = ?, completed_cycles = ?
                WHERE job_id = ? AND status = 'running'
                """,
                (phase, total_cycles, completed_cycles, job_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    "Backtest Job progress state changed concurrently."
                )
            connection.commit()

    def mark_completed(self, job_id, completed_at, cycle_count):
        with engine_database.connect_database(self.config) as connection:
            row = connection.execute(
                "SELECT status FROM backtest_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown Backtest Job: {job_id}")
            if row["status"] == "completed":
                return False
            if row["status"] != "running":
                raise ValueError(
                    f"Backtest Job '{job_id}' cannot complete from status "
                    f"'{row['status']}'."
                )
            cursor = connection.execute(
                """
                UPDATE backtest_jobs
                SET status = 'completed', phase = 'completed', completed_at = ?,
                    total_cycles = ?, completed_cycles = ?, error_text = ''
                WHERE job_id = ? AND status = 'running'
                """,
                (completed_at, cycle_count, cycle_count, job_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    "Backtest Job completion state changed concurrently."
                )
            connection.commit()
        return True

    def fail_active(self, job_id, *, phase, completed_at, error):
        with engine_database.connect_database(self.config) as connection:
            cursor = connection.execute(
                """
                UPDATE backtest_jobs
                SET status = 'failed', phase = ?, completed_at = ?,
                    error_text = ?
                WHERE job_id = ? AND status IN ('queued', 'running')
                """,
                (phase, completed_at, str(error)[:4000], job_id),
            )
            connection.commit()
        return cursor.rowcount == 1

    def active_references(self):
        with engine_database.connect_database(self.config) as connection:
            rows = connection.execute(
                """
                SELECT job_id, backtest_id, status
                FROM backtest_jobs
                WHERE status IN ('queued', 'running')
                ORDER BY submitted_at, job_id
                """
            ).fetchall()
        return [
            (row["job_id"], row["backtest_id"], row["status"])
            for row in rows
        ]

    def interrupt_active(self, completed_at):
        with engine_database.connect_database(self.config) as connection:
            connection.execute(
                """
                UPDATE backtest_jobs
                SET status = 'failed', phase = 'interrupted', completed_at = ?,
                    error_text =
                        'Engine stopped before this Backtest job completed.'
                WHERE status IN ('queued', 'running')
                """,
                (completed_at,),
            )
            connection.commit()

    def interrupt_queued(self, completed_at):
        with engine_database.connect_database(self.config) as connection:
            connection.execute(
                """
                UPDATE backtest_jobs
                SET status = 'failed', phase = 'interrupted', completed_at = ?,
                    error_text =
                        'Engine stopped before this Backtest job started.'
                WHERE status = 'queued'
                """,
                (completed_at,),
            )
            connection.commit()

    def result_catalog_row(self, backtest_id):
        with engine_database.connect_database(self.config) as connection:
            return connection.execute(
                """
                SELECT status, completed_at, metrics_json
                FROM backtests WHERE backtest_id = ?
                """,
                (backtest_id,),
            ).fetchone()

    def active_request_for_job(self, job_id, backtest_id):
        """Return the exact active Job request bound to one Backtest identity."""

        with engine_database.connect_database(self.config) as connection:
            row = connection.execute(
                f"""
                SELECT {_PUBLIC_JOB_SELECT}, request_json FROM backtest_jobs
                WHERE job_id = ? AND backtest_id = ?
                  AND status IN ('queued', 'running')
                """,
                (job_id, backtest_id),
            ).fetchone()
        if row is None:
            return None
        try:
            request = strict_json.loads(row["request_json"])
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(
                "Backtest Job recovery request is invalid."
            ) from exc
        return _require_request_evidence(
            request,
            pipeline_id=row["pipeline_id"],
            dataset_id=row["dataset_id"],
            snapshot_hash=row["snapshot_hash"],
        )
