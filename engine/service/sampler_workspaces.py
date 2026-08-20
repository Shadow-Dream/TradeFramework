#!/usr/bin/env python3
"""Sampler edit Workspace creation and immutable publication use cases."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat

from engine.contracts import sampler as sampler_contracts
from engine.contracts import strict_json
from engine.contracts.exact_fields import require_exact_fields
from engine.core import clock as engine_clock
from engine.repository import control_state
from engine.repository import samplers
from engine.repository import workspace_paths
from engine.service import jupyter_workspaces


_MARKER_NAME = ".sampler-workspace.json"
_MARKER_FIELDS = frozenset({
    "schemaVersion",
    "workspaceId",
    "sourceSamplerKey",
    "createdAt",
})


def workspace_id(sampler_id, version):
    raw = f"sampler-{sampler_id}-{version}".casefold()
    readable = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-")
    digest = hashlib.sha256(f"{sampler_id}\0{version}".encode("utf-8")).hexdigest()
    return f"{readable[:100] or 'sampler'}-{digest}"


def _read_workspace_text(root, name, *, label):
    workspace_paths.require_workspace_file(
        root,
        name,
        label=label,
        required=True,
    )
    root_descriptor = os.open(
        root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=root_descriptor,
        )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError(f"{label} must be a regular file.")
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = None
                return handle.read()
        finally:
            if descriptor is not None:
                os.close(descriptor)
    finally:
        os.close(root_descriptor)


def _load_marker(target, *, identifier, source_key):
    try:
        marker = strict_json.loads(
            _read_workspace_text(
                target,
                _MARKER_NAME,
                label="Sampler Workspace identity metadata",
            )
        )
    except (OSError, ValueError) as exc:
        raise ValueError("Sampler Workspace identity metadata is invalid.") from exc
    try:
        require_exact_fields(
            marker,
            allowed=_MARKER_FIELDS,
            required=_MARKER_FIELDS,
            label="Sampler Workspace identity metadata",
        )
    except ValueError as exc:
        raise ValueError("Sampler Workspace identity metadata is invalid.") from exc
    if (
        marker["schemaVersion"] != 1
        or marker["workspaceId"] != identifier
        or marker["sourceSamplerKey"] != source_key
        or not isinstance(marker["createdAt"], str)
        or not marker["createdAt"]
    ):
        raise ValueError("Sampler Workspace identity metadata is invalid.")
    return marker


def _validate_existing_workspace(target, definition, *, identifier, source_key):
    marker = _load_marker(target, identifier=identifier, source_key=source_key)
    workspace_paths.require_workspace_file(
        target,
        "sampler.json",
        label="Sampler Workspace sampler.json",
        required=True,
    )
    if definition["type"] == "python-script":
        workspace_paths.require_workspace_file(
            target,
            "sampler.py",
            label="Python Script Sampler Workspace sampler.py",
            required=True,
        )
    workspace_paths.require_workspace_file(
        target,
        "SAMPLER_VERSION.md",
        label="Sampler Workspace instructions",
        required=True,
    )
    return marker


def _create_workspace(target, definition, *, identifier, source_key):
    target.mkdir(parents=False, exist_ok=False)
    try:
        draft = {
            key: definition[key]
            for key in sampler_contracts.SAMPLER_DRAFT_FIELDS
            if key in definition
        }
        control_state.atomic_write_json(target / "sampler.json", draft)
        if definition["type"] == "python-script":
            (target / "sampler.py").write_text(
                str(definition.get("source") or ""),
                encoding="utf-8",
            )
        instructions = (
            "Edit `sampler.json` in this isolated Workspace. Sampler versions are "
            "immutable: use `Publish Workspace` in the Engine Data page; the Engine "
            "assigns the next version only when its content changed.\n"
        )
        if definition["type"] == "python-script":
            instructions += (
                "Edit the implementation in `sampler.py`; Workspace publishing reads "
                "that file as the authoritative source. The entry point receives "
                "`(dataset, parameters)` and yields objects containing `decisionTime` "
                "and `data`.\n"
            )
        (target / "SAMPLER_VERSION.md").write_text(instructions, encoding="utf-8")
        marker = {
            "schemaVersion": 1,
            "workspaceId": identifier,
            "sourceSamplerKey": source_key,
            "createdAt": engine_clock.utc_now(),
        }
        control_state.atomic_write_json(target / _MARKER_NAME, marker)
        return marker, draft
    except BaseException:
        if target.exists() and not target.is_symlink():
            shutil.rmtree(target)
        raise


def _open_edit_workspace_locked(config, sampler_id, version):
    definition = samplers.get_sampler(config, sampler_id, version)
    if definition.get("builtin"):
        raise ValueError("Built-in Samplers are read-only.")
    if definition.get("type") not in {"row-map", "python-script"}:
        raise ValueError(f"Unsupported Sampler editor type: {definition.get('type')}")

    identifier = workspace_id(definition["samplerId"], definition["version"])
    target = workspace_paths.managed_workspace_path(config, "sampler", identifier)
    source_key = f"{definition['samplerId']}::{definition['version']}"
    created = not target.exists()
    if created:
        marker, draft = _create_workspace(
            target,
            definition,
            identifier=identifier,
            source_key=source_key,
        )
    else:
        marker = _validate_existing_workspace(
            target,
            definition,
            identifier=identifier,
            source_key=source_key,
        )
        draft = {
            key: definition[key]
            for key in sampler_contracts.SAMPLER_DRAFT_FIELDS
            if key in definition
        }
    return {
        "accepted": True,
        "workspaceId": identifier,
        "workspacePath": str(target),
        "sourceSamplerKey": marker["sourceSamplerKey"],
        "definition": draft,
        "created": created,
    }


def open_edit_workspace(config, sampler_id, version):
    """Create or reopen an isolated copy of one immutable Sampler version."""

    with control_state.control_state_lock(config):
        return _open_edit_workspace_locked(config, sampler_id, version)


def publish_edit_workspace(config, sampler_id, version):
    """Publish a verified edit Workspace through the Sampler archive transaction."""

    identifier = workspace_id(sampler_id, version)
    with jupyter_workspaces.workspace_lifecycle_guard(identifier, "sampler"):
        jupyter_workspaces.stop_workspace_server(identifier, "sampler")
        with control_state.control_state_lock(config):
            workspace = _open_edit_workspace_locked(config, sampler_id, version)
            root = workspace_paths.managed_workspace_path(
                config,
                "sampler",
                workspace["workspaceId"],
            )
            _load_marker(
                root,
                identifier=workspace["workspaceId"],
                source_key=workspace["sourceSamplerKey"],
            )
            draft = strict_json.loads(
                _read_workspace_text(
                    root,
                    "sampler.json",
                    label="Sampler Workspace sampler.json",
                )
            )
            if not isinstance(draft, dict):
                raise ValueError("Sampler Workspace sampler.json must contain an object.")
            if draft.get("samplerId") != sampler_id:
                raise ValueError("Sampler Workspace cannot change its Sampler identity.")
            if draft.get("type") == "python-script":
                draft["source"] = _read_workspace_text(
                    root,
                    "sampler.py",
                    label="Python Script Sampler Workspace sampler.py",
                )
            latest_before = samplers.get_sampler(config, sampler_id)
            published = samplers.save_sampler(config, draft)
            return {
                "accepted": True,
                "unchanged": str(published["version"]) == str(latest_before["version"]),
                "sampler": published,
                "workspaceId": workspace["workspaceId"],
                "sourceSamplerKey": workspace["sourceSamplerKey"],
            }


__all__ = ("open_edit_workspace", "publish_edit_workspace", "workspace_id")
