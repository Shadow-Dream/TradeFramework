#!/usr/bin/env python3
"""Symlink-safe host path authority for Dataset Build scratch data."""

from __future__ import annotations

import os
from pathlib import Path

from engine.contracts.archive import require_resource_path_segment


JOBS_DIRECTORY_NAME = "dataset-build-jobs"


def _require_real_directory_chain(path, *, label):
    path = Path(path).expanduser().absolute()
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        if current.is_symlink():
            raise ValueError(f"{label} may not traverse a symbolic link: {current}")
        if current.exists() and not current.is_dir():
            raise ValueError(f"{label} may traverse only directories: {current}")
    return path


def dataset_build_job_root(config):
    """Return the real Engine-owned job root without following a symlink hop."""

    control_root = _require_real_directory_chain(
        config["controlRoot"], label="Dataset Build control root"
    )
    control_root.mkdir(parents=True, exist_ok=True)
    resolved_control = control_root.resolve()
    root = control_root / JOBS_DIRECTORY_NAME
    _require_real_directory_chain(root, label="Dataset Build Job root")
    root.mkdir(exist_ok=True)
    resolved_root = root.resolve()
    try:
        resolved_root.relative_to(resolved_control)
    except ValueError as exc:
        raise ValueError("Dataset Build Job root is outside the control repository.") from exc
    return resolved_root


def dataset_build_job_directory(config, job_id, *, create=False):
    job_id = require_resource_path_segment(job_id, label="Dataset Build Job ID")
    root = dataset_build_job_root(config)
    target = root / job_id
    if target.is_symlink():
        raise ValueError("Dataset Build Job directory may not be a symbolic link.")
    resolved = target.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("Dataset Build Job directory is outside its managed root.") from exc
    if create:
        target.mkdir(exist_ok=False)
        if target.is_symlink() or target.resolve() != resolved:
            raise RuntimeError("Dataset Build Job directory identity changed during creation.")
    elif target.exists() and not target.is_dir():
        raise ValueError("Dataset Build Job path must be a directory.")
    return resolved


def dataset_build_workspace(config, job_id, *, required=False):
    root = dataset_build_job_directory(config, job_id)
    workspace = root / "workspace"
    if workspace.is_symlink():
        raise ValueError("Dataset Build execution Workspace may not be a symbolic link.")
    resolved = workspace.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("Dataset Build execution Workspace escaped its Job directory.") from exc
    if workspace.exists() and not workspace.is_dir():
        raise ValueError("Dataset Build execution Workspace must be a directory.")
    if required and not workspace.exists():
        raise ValueError("Dataset Build execution Workspace is missing.")
    return resolved


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
