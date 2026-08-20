#!/usr/bin/env python3
"""Dataset Workspace lifecycle orchestration across repository and Jupyter."""

from __future__ import annotations

import shutil

from engine.repository import control_state
from engine.repository import dataset_workspaces as workspace_repository
from engine.repository import folders as repository_folders
from engine.repository import workspace_files
from engine.service import jupyter_workspaces


def _cleanup_claimed_workspace(config, workspace):
    workspace_id = workspace["workspaceId"]
    repository_folders.remove_item_assignment(
        config,
        "data",
        repository_folders.shared_item_id("workspaces", workspace_id),
    )
    jupyter_workspaces.stop_workspace_server(workspace_id, "dataset")
    workspace_files.discard_workspace_runtime(config, workspace_id, "dataset")
    path = workspace_repository.require_managed_workspace_path(config, workspace)
    if path.is_symlink():
        raise ValueError("Dataset Workspace may not become a symbolic link during deletion.")
    if path.exists():
        shutil.rmtree(path)
    with control_state.control_state_lock(config):
        workspace_repository.finish_workspace_deletion(config, workspace_id)


def delete_workspace(config, workspace_id):
    """Delete one terminal Workspace with lifecycle-before-control lock order."""

    with jupyter_workspaces.workspace_lifecycle_guard(workspace_id, "dataset"):
        with control_state.control_state_lock(config):
            workspace = workspace_repository.claim_workspace_deletion(
                config, workspace_id
            )
        _cleanup_claimed_workspace(config, workspace)
    return {"workspaceId": workspace_id, "deleted": True}


def reconcile_deleting_workspaces(config):
    """Finish durable deletion claims left by an interrupted cleanup."""

    for workspace in workspace_repository.deleting_workspaces(config):
        workspace_id = workspace["workspaceId"]
        with jupyter_workspaces.workspace_lifecycle_guard(workspace_id, "dataset"):
            _cleanup_claimed_workspace(config, workspace)


def reconcile_internal_workspaces(config):
    """Remove hidden one-shot Process Workspaces left after Build recovery."""

    for workspace in workspace_repository.internal_workspaces(config):
        if workspace["status"] not in {"draft", "published", "failed"}:
            raise RuntimeError(
                "Internal Dataset Workspace remained active after Build recovery: "
                f"{workspace['workspaceId']} ({workspace['status']})."
            )
        delete_workspace(config, workspace["workspaceId"])
