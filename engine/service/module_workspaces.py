#!/usr/bin/env python3
"""Editable Module workspaces backed by the common immutable archive transaction."""

from __future__ import annotations

import base64
import hashlib
import re
import shutil
import stat
from pathlib import Path

from engine.repository import workspace_paths
from engine.core import clock as engine_clock
from engine.archive import version as version_archive
from engine.archive import version_evidence
from engine.contracts import strict_json
from engine.contracts.archive import require_resource_path_segment
from engine.contracts.module import MODULE_KINDS, definition_key
from engine.repository import control_state
from engine.repository import module_definitions
from engine.service import jupyter_workspaces
from engine.service import module_publication


REPOSITORY_LABELS = {
    "pipeline": "Pipeline Module",
    "analysis": "Analysis Module",
    "environment": "Environment Module",
}
WORKSPACE_CONTROL_FILES = {
    version_archive.MANIFEST_NAME,
    version_archive.RECORD_NAME,
    "module.json",
    "module-draft.json",
    ".module-workspace.json",
}


def require_module_kind(kind, repository=None):
    kind = str(kind or "").strip()
    if kind not in MODULE_KINDS:
        raise ValueError(f"Invalid Module kind: {kind}")
    actual_repository = module_definitions.module_repository_for_kind(kind)
    if repository is not None and actual_repository != repository:
        raise ValueError(
            f"Module kind '{kind}' belongs to the {actual_repository} repository, not {repository}."
        )
    return kind


def _repository_definitions(config, repository):
    if repository not in module_definitions.MODULE_REPOSITORIES:
        raise ValueError(f"Unknown Module repository: {repository}")
    return module_definitions.load_repository_evidence(config, repository)


def _replace_archive_root(value, archive_root):
    if isinstance(value, str):
        return value.replace(str(archive_root), "{{moduleRoot}}")
    if isinstance(value, list):
        return [_replace_archive_root(item, archive_root) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_archive_root(item, archive_root)
            for key, item in value.items()
        }
    return value


def _portable_definition(definition):
    archive_root = Path((definition.get("archive") or {}).get("root") or "").resolve()
    draft = {
        key: value
        for key, value in definition.items()
        if key not in {
            "version", "status", "archive", "contentDigest",
            "createdAt", "builtin",
        }
    }
    draft["parameters"] = _replace_archive_root(draft.get("parameters") or {}, archive_root)
    return draft


def _workspace_bundle_files(root):
    root = Path(root).resolve()
    files = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise ValueError(f"Module Workspace may not contain symlinks: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if len(relative.parts) == 1 and relative.name in WORKSPACE_CONTROL_FILES:
            continue
        if any(part in {".ipynb_checkpoints", "__pycache__"} for part in relative.parts):
            continue
        files.append({
            "path": relative.as_posix(),
            "contentBase64": base64.b64encode(path.read_bytes()).decode("ascii"),
            "executable": bool(path.stat().st_mode & stat.S_IXUSR),
        })
    if not files:
        raise ValueError("Module Workspace contains no publishable files.")
    return files


def _make_writable(root):
    root = Path(root)
    for path in [root, *root.rglob("*")]:
        mode = path.stat().st_mode
        if path.is_dir():
            path.chmod(mode | stat.S_IWUSR | stat.S_IXUSR)
        else:
            path.chmod(mode | stat.S_IWUSR)


def workspace_id(kind, module_id, version):
    kind = require_resource_path_segment(kind, label="Module kind")
    module_id = require_resource_path_segment(module_id, label="moduleId")
    version = require_resource_path_segment(version, label="Module version")
    identity = "\0".join((kind, module_id, version)).encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()
    readable = re.sub(
        r"[^a-z0-9_-]+", "-", f"{kind}-{module_id}-{version}".casefold()
    ).strip("-")[:80]
    return f"module-{readable or 'resource'}-{digest}"


def _open_edit_workspace(
    config,
    kind,
    module_id,
    version,
    *,
    repository,
    resource_label,
):
    definitions, repository_evidence = _repository_definitions(
        config, repository
    )
    key = definition_key(kind, module_id, version)
    definition = definitions.get(key)
    if not definition:
        raise ValueError(f"{resource_label} definition does not exist: {key}")
    evidence_by_key = version_evidence.verified_record_index_material(
        repository_evidence
    )["locationEvidence"]
    verified = version_evidence.verified_record_location_material(
        evidence_by_key[key]
    )["record"]
    if strict_json.dumps(verified, sort_keys=True) != strict_json.dumps(
        definition, sort_keys=True
    ):
        raise ValueError(
            f"{resource_label} definition does not match its repository evidence: {key}"
        )
    if definition.get("builtin"):
        raise ValueError(f"Built-in {resource_label}s are read-only.")
    source = Path((definition.get("archive") or {}).get("root") or "")
    identifier = workspace_id(kind, module_id, version)
    target = workspace_paths.managed_workspace_path(config, "module", identifier)
    created = False
    if not target.exists():
        shutil.copytree(source, target)
        _make_writable(target)
        created = True

    marker_path = target / ".module-workspace.json"
    if not created:
        marker = control_state.load_json_file(marker_path, None)
        if (
            not isinstance(marker, dict)
            or marker.get("schemaVersion") != 1
            or marker.get("workspaceId") != identifier
            or marker.get("sourceModuleKey") != key
        ):
            raise ValueError("Module Workspace identity metadata is invalid.")

    draft = _portable_definition(definition)
    if created or not (target / "module-draft.json").is_file():
        control_state.atomic_write_json(target / "module-draft.json", draft)
    if created:
        control_state.atomic_write_json(marker_path, {
            "schemaVersion": 1,
            "workspaceId": identifier,
            "sourceModuleKey": key,
            "createdAt": engine_clock.utc_now(),
        })
    return {
        "accepted": True,
        "workspaceId": identifier,
        "workspacePath": str(target),
        "sourceModuleKey": key,
        "definition": draft,
    }


def open_edit_workspace(config, kind, module_id, version):
    kind = require_module_kind(kind, "pipeline")
    return _open_edit_workspace(
        config,
        kind,
        module_id,
        version,
        repository="pipeline",
        resource_label="Module",
    )


def open_repository_edit_workspace(config, repository, kind, module_id, version):
    kind = require_module_kind(kind, repository)
    return _open_edit_workspace(
        config,
        kind,
        module_id,
        version,
        repository=repository,
        resource_label=REPOSITORY_LABELS[repository],
    )


def publish_edit_workspace(config, repository, kind, module_id, version):
    """Publish one edited Workspace through the sole Module archive transaction."""
    kind = require_module_kind(kind, repository)
    identifier = workspace_id(kind, module_id, version)
    with jupyter_workspaces.workspace_lifecycle_guard(identifier, "module"):
        jupyter_workspaces.stop_workspace_server(identifier, "module")
        with control_state.control_state_lock(config):
            workspace = _open_edit_workspace(
                config,
                kind,
                module_id,
                version,
                repository=repository,
                resource_label=REPOSITORY_LABELS[repository],
            )
            root = Path(workspace["workspacePath"]).resolve()
            marker = control_state.load_json_file(
                root / ".module-workspace.json", None
            )
            if (
                not isinstance(marker, dict)
                or marker.get("sourceModuleKey") != workspace["sourceModuleKey"]
            ):
                raise ValueError("Module Workspace identity metadata is invalid.")
            draft = control_state.load_json_file(root / "module-draft.json", None)
            if not isinstance(draft, dict):
                raise ValueError(
                    "Module Workspace module-draft.json must contain an object."
                )
            if draft.get("kind") != kind or draft.get("moduleId") != module_id:
                raise ValueError("Module Workspace cannot change its Module identity.")
            result = module_publication.publish_module(
                config,
                {**draft, "files": _workspace_bundle_files(root)},
                repository=repository,
            )
            return {
                **result,
                "workspaceId": workspace["workspaceId"],
                "sourceModuleKey": workspace["sourceModuleKey"],
            }
