#!/usr/bin/env python3
"""Pure validation and record contracts for Dataset Workspaces and Builds."""

from __future__ import annotations

import hashlib
import re
import shlex

from engine.contracts import strict_json


ALIAS_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
ENGINE_WORKSPACE_DIRECTORY = ".trade-engine"
ENGINE_SCRIPT_NAME = "submitted.py"
BUILD_COMPLETION_EVIDENCE_NAME = "build-completion.json"
RESERVED_WORKSPACE_NAMES = {
    ".tmp", "_build", "_dataset.json", ENGINE_WORKSPACE_DIRECTORY,
}
MAX_LOG_CHARS = 200_000
MAX_LOG_TAIL_BYTES = MAX_LOG_CHARS * 4 + 4
MAX_SUBMITTED_SCRIPT_BYTES = 2 * 1024 * 1024
MAX_EXECUTION_WORKSPACE_BYTES = 10 * 1024 ** 3
MAX_EXECUTION_WORKSPACE_ENTRIES = 100_000
MIN_HOST_FREE_BYTES = 1024 ** 3
WORKSPACE_SCAN_INTERVAL_SECONDS = 0.25
PROCESS_TERMINATION_GRACE_SECONDS = 0.5
BUILD_REQUEST_FIELDS = frozenset({
    "workspaceId", "outputDatasetId", "outputDatasetName",
    "recipeId", "recipeVersion", "arguments", "timeoutSeconds", "jobId",
})
PROCESS_REQUEST_FIELDS = frozenset(
    (BUILD_REQUEST_FIELDS - {"workspaceId"}) | {"datasetIds"}
)
WORKSPACE_BINDING_FIELDS = frozenset({
    "alias", "datasetId", "datasetVersionId", "storageRoot", "contentHash",
})


def require_request_fields(request, *, allowed, required, label):
    if not isinstance(request, dict):
        raise ValueError(f"{label} must be an object.")
    unknown = sorted(set(request) - set(allowed))
    if unknown:
        raise ValueError(f"{label} contains unsupported field(s): " + ", ".join(unknown))
    missing = sorted(field for field in required if field not in request)
    if missing:
        raise ValueError(f"{label} requires field(s): " + ", ".join(missing))
    return request


def validate_alias(value):
    alias = str(value or "").strip()
    if not ALIAS_PATTERN.fullmatch(alias) or alias in RESERVED_WORKSPACE_NAMES:
        raise ValueError(
            "Dataset workspace aliases must start with a letter and contain only letters, numbers, '_' or '-'."
        )
    return alias


def workspace_row(row):
    try:
        sources = strict_json.loads(row["source_bindings_json"])
    except ValueError as exc:
        raise ValueError("Dataset Workspace contains invalid stored source JSON.") from exc
    if not isinstance(sources, list):
        raise ValueError("Dataset Workspace stored sources must be an array.")
    return {
        "workspaceId": row["workspace_id"],
        "name": row["name"],
        "status": row["status"],
        "workspacePath": row["workspace_path"],
        "sources": sources,
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "submittedJobId": row["submitted_job_id"],
        "internal": bool(row["internal"]),
    }


def recipe_row(row):
    return {
        "recipeId": row["recipe_id"],
        "version": row["version"],
        "name": row["name"],
        "scriptDigest": row["script_digest"],
        "createdAt": row["created_at"],
        "status": row["status"],
        "contentDigest": row["content_digest"],
        "archive": {
            "resourceType": "dataset-recipe",
            "resourceId": row["recipe_id"],
            "root": row["archive_root"],
            "manifestDigest": row["archive_manifest_digest"],
        },
    }


def script_digest(script_text):
    return "sha256:" + hashlib.sha256(script_text.encode("utf-8")).hexdigest()


def normalize_script_arguments(value):
    """Tokenize call-time argv without interpreting Script semantics."""
    if value in (None, ""):
        return []
    if isinstance(value, str):
        try:
            arguments = shlex.split(value, posix=True)
        except ValueError as exc:
            raise ValueError(f"Dataset Script arguments are invalid: {exc}") from exc
    elif isinstance(value, list):
        arguments = list(value)
    else:
        raise ValueError("Dataset Script arguments must be a command-line string or an argv array.")
    if not all(isinstance(argument, str) for argument in arguments):
        raise ValueError("Every Dataset Script argument must be a string.")
    if any("\0" in argument for argument in arguments):
        raise ValueError("Dataset Script arguments cannot contain NUL bytes.")
    return arguments


def build_job_row(row):
    try:
        arguments = strict_json.loads(row["arguments_json"])
        sources = strict_json.loads(row["source_bindings_json"])
    except ValueError as exc:
        raise ValueError("Dataset Build Job contains invalid stored JSON.") from exc
    if not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
        raise ValueError("Dataset Build Job stored arguments must be a string array.")
    if not isinstance(sources, list):
        raise ValueError("Dataset Build Job stored sources must be an array.")
    status = row["status"]
    if status not in {"submitted", "running", "completed", "failed"}:
        raise ValueError("Dataset Build Job status is invalid.")
    text_fields = (
        "job_id", "workspace_id", "output_dataset_id", "output_dataset_name",
        "execution_workspace_path", "recipe_id", "recipe_version", "script_digest",
        "submitted_at", "started_at", "completed_at", "output_version_id",
        "stdout_text", "stderr_text", "error_text",
    )
    if any(not isinstance(row[field], str) for field in text_fields):
        raise ValueError("Dataset Build Job contains an invalid text field.")
    required = (
        "job_id", "workspace_id", "output_dataset_id", "output_dataset_name",
        "execution_workspace_path", "recipe_id", "recipe_version", "script_digest",
        "submitted_at",
    )
    if any(not row[field] for field in required):
        raise ValueError("Dataset Build Job identity and submission fields are required.")
    if status == "submitted" and (
        row["started_at"] or row["completed_at"] or row["output_version_id"] or row["error_text"]
    ):
        raise ValueError("Submitted Dataset Build Job lifecycle is invalid.")
    if status == "running" and (
        not row["started_at"] or row["completed_at"] or row["output_version_id"] or row["error_text"]
    ):
        raise ValueError("Running Dataset Build Job lifecycle is invalid.")
    if status == "completed" and (
        not row["started_at"] or not row["completed_at"] or not row["output_version_id"] or row["error_text"]
    ):
        raise ValueError("Completed Dataset Build Job lifecycle is invalid.")
    if status == "failed" and (
        not row["completed_at"] or not row["error_text"] or row["output_version_id"]
    ):
        raise ValueError("Failed Dataset Build Job lifecycle is invalid.")
    return {
        "jobId": row["job_id"],
        "workspaceId": row["workspace_id"],
        "outputDatasetId": row["output_dataset_id"],
        "outputDatasetName": row["output_dataset_name"],
        "status": status,
        "recipeId": row["recipe_id"],
        "recipeVersion": row["recipe_version"],
        "scriptDigest": row["script_digest"],
        "arguments": arguments,
        "sources": sources,
        "submittedAt": row["submitted_at"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
        "outputVersionId": row["output_version_id"],
        "stdout": row["stdout_text"],
        "stderr": row["stderr_text"],
        "error": row["error_text"],
    }
