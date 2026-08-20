#!/usr/bin/env python3
"""Managed host paths shared by Dataset, Module, and Sampler Workspaces."""

from __future__ import annotations

from pathlib import Path

from engine.contracts.archive import require_resource_path_segment


_WORKSPACE_DIRECTORIES = {
    "dataset": "_dataset_workspaces",
    "module": "_module_workspaces",
    "sampler": "_sampler_workspaces",
}


def _require_no_symlink_components(path, *, label):
    """Reject every existing symlink hop before a managed-path mkdir."""

    path = Path(path).absolute()
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} may not traverse a symbolic link: {current}")
        if current.exists() and not current.is_dir():
            raise ValueError(f"{label} may traverse only directories: {current}")


def managed_workspace_root(config, workspace_kind):
    """Create and resolve one Engine-owned Workspace repository without symlinks."""

    directory_name = _WORKSPACE_DIRECTORIES.get(workspace_kind)
    if directory_name is None:
        raise ValueError(f"Unsupported Workspace kind: {workspace_kind}")
    release_root = Path(config["releaseRoot"]).expanduser().absolute()
    _require_no_symlink_components(release_root, label="Workspace release root")
    release_root.mkdir(parents=True, exist_ok=True)
    resolved_release_root = release_root.resolve()

    root = release_root / directory_name
    _require_no_symlink_components(
        root,
        label=f"{workspace_kind.title()} Workspace root",
    )
    root.mkdir(exist_ok=True)
    resolved_root = root.resolve()
    try:
        resolved_root.relative_to(resolved_release_root)
    except ValueError as exc:
        raise ValueError(
            f"{workspace_kind.title()} Workspace root is outside the release repository."
        ) from exc
    return resolved_root


def managed_workspace_path(config, workspace_kind, workspace_id):
    """Resolve one Workspace path and reject aliases outside its managed root."""

    workspace_id = require_resource_path_segment(
        workspace_id,
        label=f"{workspace_kind.title()} Workspace ID",
    )
    root = managed_workspace_root(config, workspace_kind)
    target = root / workspace_id
    if target.is_symlink():
        raise ValueError(
            f"{workspace_kind.title()} Workspace may not be a symbolic link."
        )
    resolved_target = target.resolve()
    try:
        resolved_target.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"{workspace_kind.title()} Workspace is outside its managed repository."
        ) from exc
    if target.exists() and not target.is_dir():
        raise ValueError(f"{workspace_kind.title()} Workspace must be a directory.")
    return resolved_target


def require_workspace_file(root, name, *, label, required=True):
    """Resolve one regular Workspace file without accepting a symlink alias."""

    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"{label} Workspace root must be a real directory.")
    name = require_resource_path_segment(name, label=f"{label} file name")
    path = root / name
    if path.is_symlink():
        raise ValueError(f"{label} may not be a symbolic link.")
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} is outside its managed Workspace.") from exc
    if required and (not path.exists() or not path.is_file()):
        raise ValueError(f"{label} is missing or is not a regular file.")
    if path.exists() and not path.is_file():
        raise ValueError(f"{label} must be a regular file.")
    return resolved


def dataset_workspace_root(config):
    return managed_workspace_root(config, "dataset")


def module_workspace_root(config):
    return managed_workspace_root(config, "module")


def sampler_workspace_root(config):
    return managed_workspace_root(config, "sampler")


__all__ = (
    "dataset_workspace_root",
    "managed_workspace_path",
    "managed_workspace_root",
    "module_workspace_root",
    "require_workspace_file",
    "sampler_workspace_root",
)
