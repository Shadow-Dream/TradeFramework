#!/usr/bin/env python3
"""Dataset Workspace repository and managed development files."""

from __future__ import annotations

import errno
import os
import sqlite3
import stat
from functools import wraps
from pathlib import Path

from engine.archive import version as version_archive
from engine.contracts import strict_json
from engine.contracts.dataset_workspace import (
    MAX_SUBMITTED_SCRIPT_BYTES,
    WORKSPACE_BINDING_FIELDS,
    require_request_fields,
    validate_alias,
    workspace_row,
)
from engine.control import database as engine_database
from engine.core import clock as engine_clock
from engine.core import resource_ids
from engine.repository import control_state
from engine.repository import datasets
from engine.repository import workspace_paths


def _control_transaction(function):
    @wraps(function)
    def invoke(config, *args, **kwargs):
        with control_state.control_state_lock(config):
            return function(config, *args, **kwargs)
    return invoke


def _make_tree_read_only(root):
    for path in sorted(Path(root).rglob("*"), reverse=True):
        if path.is_symlink():
            continue
        path.chmod(0o555 if path.is_dir() else 0o444)
    Path(root).chmod(0o555)


def workspace_root(config):
    return workspace_paths.dataset_workspace_root(config)


@_control_transaction
def reconcile_workspace_directories(config):
    """Remove unindexed create-crash orphans and verify every live index path."""
    root = workspace_root(config).resolve()
    with engine_database.connect_database(config) as conn:
        rows = conn.execute(
            "SELECT workspace_id, workspace_path, status FROM dataset_workspaces"
        ).fetchall()
    indexed = {}
    for row in rows:
        expected = root / row["workspace_id"]
        stored = Path(row["workspace_path"])
        if stored.resolve(strict=False) != expected:
            raise ValueError("Dataset Workspace index path is not its managed identity path.")
        indexed[row["workspace_id"]] = row
        if row["status"] != "deleting" and (
            expected.is_symlink() or not expected.is_dir()
        ):
            raise ValueError(
                f"Dataset Workspace '{row['workspace_id']}' directory is missing or invalid."
            )
    for entry in tuple(root.iterdir()):
        if entry.name in indexed:
            continue
        if entry.is_symlink():
            entry.unlink()
        elif entry.is_dir():
            version_archive.discard_archive(entry)
        else:
            raise ValueError(
                f"Dataset Workspace root contains an unexpected entry: {entry.name}"
            )
def version_storage_root(version):
    raw = version["storage"]["uri"]
    path = Path(raw).expanduser().resolve()
    if path.is_file():
        path = path.parent
    if not path.is_dir():
        raise ValueError(f"Dataset version storage does not exist: {path}")
    return path


def resolve_source_bindings(config, sources):
    if not isinstance(sources, list) or not sources:
        raise ValueError("Workspace requires at least one source Dataset binding.")
    bindings = []
    aliases = set()
    for index, source in enumerate(sources):
        require_request_fields(
            source,
            allowed={"datasetId", "datasetVersionId", "alias"},
            required={"datasetId"},
            label=f"Workspace source binding {index}",
        )
        if not isinstance(source["datasetId"], str) or not source["datasetId"].strip():
            raise ValueError("Each Workspace source requires datasetId.")
        dataset_id = source["datasetId"].strip()
        dataset = datasets.get_dataset(config, dataset_id)
        if dataset.get("status") != "active":
            raise ValueError(f"Source Dataset '{dataset_id}' is archived and cannot enter a Workspace.")
        version_id = source.get("datasetVersionId", "")
        if "datasetVersionId" in source and (
            not isinstance(version_id, str) or not version_id.strip()
        ):
            raise ValueError("Workspace source datasetVersionId must be a non-empty string.")
        version = datasets.ensure_dataset_version(config, dataset_id, version_id)
        if "alias" in source:
            if not isinstance(source["alias"], str) or not source["alias"].strip():
                raise ValueError("Workspace source alias must be a non-empty string.")
            alias = validate_alias(source["alias"])
        else:
            alias = f"dataset{index + 1}"
        if alias in aliases:
            raise ValueError(f"Duplicate Workspace Dataset alias: {alias}")
        aliases.add(alias)
        bindings.append({
            "alias": alias,
            "datasetId": dataset_id,
            "datasetVersionId": version["datasetVersionId"],
            "storageRoot": str(version_storage_root(version)),
            "contentHash": version["contentHash"],
        })
    return bindings


def verify_workspace_bindings(config, bindings):
    if not isinstance(bindings, list) or not bindings:
        raise ValueError("Dataset Workspace stored sources must be a non-empty array.")
    verified = []
    aliases = set()
    for index, binding in enumerate(bindings):
        require_request_fields(
            binding,
            allowed=WORKSPACE_BINDING_FIELDS,
            required=WORKSPACE_BINDING_FIELDS,
            label=f"Dataset Workspace stored source {index}",
        )
        for field in ("datasetId", "datasetVersionId", "storageRoot", "contentHash"):
            if not isinstance(binding[field], str) or not binding[field]:
                raise ValueError(
                    f"Dataset Workspace stored source {index}.{field} is invalid."
                )
        alias = validate_alias(binding["alias"])
        if alias in aliases:
            raise ValueError(f"Duplicate Workspace Dataset alias: {alias}")
        aliases.add(alias)
        dataset = datasets.get_dataset(config, binding["datasetId"])
        if dataset["status"] != "active":
            raise ValueError(
                f"Source Dataset '{binding['datasetId']}' is archived and cannot be built."
            )
        version = datasets.ensure_dataset_version(
            config, binding["datasetId"], binding["datasetVersionId"]
        )
        expected = {
            "alias": alias,
            "datasetId": binding["datasetId"],
            "datasetVersionId": version["datasetVersionId"],
            "storageRoot": str(version_storage_root(version)),
            "contentHash": version["contentHash"],
        }
        if binding != expected:
            raise ValueError(
                f"Dataset Workspace stored source '{alias}' no longer matches its sealed Version."
            )
        verified.append(expected)
    return verified


def materialize_source_links(path, bindings):
    path.mkdir(parents=True, exist_ok=False)
    for binding in bindings:
        _make_tree_read_only(Path(binding["storageRoot"]))
        os.symlink(binding["storageRoot"], path / binding["alias"], target_is_directory=True)


def verify_source_links(path, bindings):
    for binding in bindings:
        link = path / binding["alias"]
        if not link.is_symlink():
            raise ValueError(f"Submitted script altered source Dataset link '{binding['alias']}'.")
        if link.resolve() != Path(binding["storageRoot"]).resolve():
            raise ValueError(f"Submitted script redirected source Dataset link '{binding['alias']}'.")
def _workspace_creation_commit_state(config, expected):
    """Resolve a possibly late SQLite commit without touching indexed files."""
    try:
        with engine_database.connect_database(config) as conn:
            row = conn.execute(
                "SELECT * FROM dataset_workspaces WHERE workspace_id = ?",
                (expected["workspaceId"],),
            ).fetchone()
        if row is None:
            return "absent"
        return "committed" if workspace_row(row) == expected else "conflict"
    except (OSError, TypeError, ValueError, sqlite3.Error):
        return "unknown"


def create_workspace(config, request, *, internal=False):
    require_request_fields(
        request,
        allowed={"workspaceId", "name", "sources"},
        required={"sources"},
        label="Dataset Workspace create request",
    )
    if not isinstance(internal, bool):
        raise ValueError("Dataset Workspace internal ownership must be a boolean.")
    if "workspaceId" in request and (
        not isinstance(request["workspaceId"], str)
        or not request["workspaceId"].strip()
    ):
        raise ValueError("Workspace ID must be a non-empty string.")
    requested_id = request.get("workspaceId", "").strip()
    if "name" in request and (
        not isinstance(request["name"], str) or not request["name"].strip()
    ):
        raise ValueError("Workspace name must be a non-empty string.")
    name = request.get("name", "").strip()
    if len(name) > 160:
        raise ValueError("Workspace name must be 160 characters or fewer.")
    if not requested_id and not name:
        raise ValueError("Workspace name is required.")
    workspace_id = (
        resource_ids.normalize_resource_id(requested_id)
        if requested_id
        else resource_ids.new_resource_id("workspace")
    )
    bindings = resolve_source_bindings(config, request["sources"])
    path = workspace_root(config) / workspace_id
    with engine_database.connect_database(config) as conn:
        if conn.execute("SELECT 1 FROM dataset_workspaces WHERE workspace_id = ?", (workspace_id,)).fetchone():
            raise ValueError(f"Dataset Workspace already exists: {workspace_id}")
    if path.exists() or path.is_symlink():
        raise ValueError(f"Dataset Workspace path already exists: {path}")
    created_at = engine_clock.utc_now()
    expected = {
        "workspaceId": workspace_id,
        "name": name or workspace_id,
        "status": "draft",
        "workspacePath": str(path),
        "sources": bindings,
        "createdAt": created_at,
        "updatedAt": created_at,
        "submittedJobId": "",
        "internal": bool(internal),
    }
    try:
        materialize_source_links(path, bindings)
        with engine_database.connect_database(config) as conn:
            conn.execute(
                """
                INSERT INTO dataset_workspaces
                (workspace_id, name, status, workspace_path, source_bindings_json, created_at, updated_at,
                 submitted_job_id, internal)
                VALUES (?, ?, 'draft', ?, ?, ?, ?, '', ?)
                """,
                (
                    workspace_id,
                    name or workspace_id,
                    str(path),
                    strict_json.dumps(bindings, sort_keys=True),
                    created_at,
                    created_at,
                    1 if internal else 0,
                ),
            )
            conn.commit()
    except BaseException:
        commit_state = _workspace_creation_commit_state(config, expected)
        if commit_state == "committed":
            return expected
        if commit_state == "absent" and (path.exists() or path.is_symlink()):
            version_archive.discard_archive(path)
        raise
    return expected


def get_workspace(config, workspace_id):
    with engine_database.connect_database(config) as conn:
        row = conn.execute("SELECT * FROM dataset_workspaces WHERE workspace_id = ?", (workspace_id,)).fetchone()
    if not row:
        raise ValueError(f"Unknown Dataset Workspace: {workspace_id}")
    return workspace_row(row)


def list_workspaces(config):
    with engine_database.connect_database(config) as conn:
        rows = conn.execute(
            "SELECT * FROM dataset_workspaces WHERE internal = 0 ORDER BY created_at DESC"
        ).fetchall()
    return [workspace_row(row) for row in rows]


def rename_workspace(config, workspace_id, name):
    name = str(name or "").strip()
    if not name:
        raise ValueError("Workspace name is required.")
    if len(name) > 160:
        raise ValueError("Workspace name must be 160 characters or fewer.")
    get_workspace(config, workspace_id)
    now = engine_clock.utc_now()
    with engine_database.connect_database(config) as conn:
        conn.execute(
            "UPDATE dataset_workspaces SET name = ?, updated_at = ? WHERE workspace_id = ?",
            (name, now, workspace_id),
        )
        conn.commit()
    return get_workspace(config, workspace_id)


def claim_workspace_deletion(config, workspace_id):
    """Durably claim a deletable Workspace; external cleanup happens in service."""

    with engine_database.connect_database(config) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM dataset_workspaces WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown Dataset Workspace: {workspace_id}")
        workspace = workspace_row(row)
        if workspace["status"] == "draft" and not workspace["submittedJobId"]:
            expected_status = "draft"
        elif workspace["status"] in {"published", "failed"}:
            expected_status = workspace["status"]
        elif workspace["status"] == "deleting":
            conn.rollback()
            return workspace
        else:
            raise ValueError("A submitted or running Dataset Workspace cannot be deleted.")
        cursor = conn.execute(
            """
            UPDATE dataset_workspaces SET status = 'deleting', updated_at = ?
            WHERE workspace_id = ? AND status = ? AND submitted_job_id = ?
            """,
            (
                engine_clock.utc_now(), workspace_id, expected_status,
                workspace["submittedJobId"],
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Dataset Workspace deletion claim changed concurrently.")
        conn.commit()
    workspace["status"] = "deleting"
    return workspace


def deleting_workspaces(config):
    with engine_database.connect_database(config) as conn:
        rows = conn.execute(
            """
            SELECT * FROM dataset_workspaces
            WHERE status = 'deleting' ORDER BY workspace_id
            """
        ).fetchall()
    return [workspace_row(row) for row in rows]


def internal_workspaces(config):
    with engine_database.connect_database(config) as conn:
        rows = conn.execute(
            "SELECT * FROM dataset_workspaces WHERE internal = 1 ORDER BY workspace_id"
        ).fetchall()
    return [workspace_row(row) for row in rows]


def require_managed_workspace_path(config, workspace):
    expected = workspace_paths.managed_workspace_path(
        config, "dataset", workspace["workspaceId"]
    )
    stored = Path(workspace["workspacePath"])
    if stored.is_symlink() or stored.resolve(strict=False) != expected:
        raise ValueError("Dataset Workspace path is not its managed identity path.")
    return expected


def finish_workspace_deletion(config, workspace_id):
    with engine_database.connect_database(config) as conn:
        cursor = conn.execute(
            "DELETE FROM dataset_workspaces WHERE workspace_id = ? AND status = 'deleting'",
            (workspace_id,),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Dataset Workspace deletion state changed concurrently.")
        conn.commit()
def list_workspace_scripts(config, workspace_id):
    workspace = get_workspace(config, workspace_id)
    root = Path(workspace["workspacePath"]).resolve()
    source_aliases = {item["alias"] for item in workspace["sources"]}
    scripts = []
    for current_root, directories, files in os.walk(root, followlinks=False):
        current = Path(current_root)
        directories[:] = sorted(
            name
            for name in directories
            if not (current / name).is_symlink() and (current != root or name not in source_aliases)
        )
        for name in sorted(files):
            path = current / name
            if path.suffix.lower() != ".py" or path.is_symlink() or not path.is_file():
                continue
            stat_result = path.stat()
            scripts.append({
                "path": path.relative_to(root).as_posix(),
                "size": stat_result.st_size,
                "modifiedAt": stat_result.st_mtime,
            })
    return scripts


def read_workspace_script(workspace, relative_path):
    value = str(relative_path or "").strip()
    if not value:
        raise ValueError("Workspace scriptPath is required.")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".py":
        raise ValueError("Workspace scriptPath must be a relative .py file inside the Workspace.")
    root = Path(workspace["workspacePath"]).resolve()
    source_aliases = {item["alias"] for item in workspace["sources"]}
    if relative.parts and relative.parts[0] in source_aliases:
        raise ValueError("Source Dataset files cannot be submitted as Workspace scripts.")
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | no_follow
    directory_descriptor = os.open(root, directory_flags)
    try:
        for component in relative.parts[:-1]:
            next_descriptor = os.open(component, directory_flags, dir_fd=directory_descriptor)
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor
        try:
            descriptor = os.open(relative.parts[-1], os.O_RDONLY | no_follow, dir_fd=directory_descriptor)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise ValueError("Workspace scriptPath cannot contain symbolic links.") from exc
            raise
        try:
            stat_result = os.fstat(descriptor)
            if not stat.S_ISREG(stat_result.st_mode):
                raise ValueError("Workspace scriptPath must identify a regular file.")
            if stat_result.st_size > MAX_SUBMITTED_SCRIPT_BYTES:
                raise ValueError(f"Workspace script is larger than {MAX_SUBMITTED_SCRIPT_BYTES} bytes.")
            chunks = []
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory_descriptor)
    try:
        script_text = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Workspace script must be UTF-8 encoded.") from exc
    if not script_text.strip():
        raise ValueError("Workspace script is empty.")
    return script_text
