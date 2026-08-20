"""Canonical repository-location authorities for frozen execution records."""

from __future__ import annotations

from pathlib import Path

from engine.archive import version as version_archive
from engine.contracts.archive import require_resource_path_segment
from engine.contracts.module import (
    MODULE_RELEASE_DIRECTORIES,
)


_GRAPH_SPECS = {
    "analysis": ("analysisId", "_analyses"),
    "environment": ("environmentId", "_environments"),
}
def _canonical_version(value, *, label):
    version = require_resource_path_segment(value, label=label)
    if not version.isdigit() or version != str(int(version)) or int(version) < 1:
        raise ValueError(f"{label} must be a canonical positive integer.")
    return version


def _expected_record_root(
    release_root,
    record,
    *,
    identity_field,
    resource_type,
    release_dir,
    expected_identity=None,
    expected_version=None,
    expected_resource_id=None,
    path_prefix=(),
):
    if not isinstance(record, dict):
        raise ValueError("Frozen execution record must be an object.")
    identity = require_resource_path_segment(
        record.get(identity_field),
        label=identity_field,
    )
    version = _canonical_version(
        record.get("version"),
        label=f"{resource_type} version",
    )
    if expected_identity is not None and identity != expected_identity:
        raise ValueError(
            f"Frozen {resource_type} identity does not match its request."
        )
    if expected_version is not None and version != expected_version:
        raise ValueError(
            f"Frozen {resource_type} version does not match its request."
        )
    archive = record.get("archive")
    if not isinstance(archive, dict):
        raise ValueError(f"Frozen {resource_type} archive descriptor is invalid.")
    if (
        archive.get("resourceType") != resource_type
        or archive.get("resourceId") != (
            identity if expected_resource_id is None else expected_resource_id
        )
    ):
        raise ValueError(
            f"Frozen {resource_type} archive identity does not match its record."
        )
    expected_root = (
        Path(release_root)
        / release_dir
        / Path(*path_prefix)
        / identity
        / version
    )
    return expected_root


def _verify_record_location(
    release_root,
    record,
    **requirements,
):
    expected_root = _expected_record_root(
        release_root,
        record,
        **requirements,
    )
    version_archive.verify_record_location(
        record,
        managed_root=release_root,
        expected_root=expected_root,
    )
    return record


def verify_pipeline_record(release_root, record, *, pipeline_id, version):
    """Verify one Pipeline record at its exact immutable repository path."""
    return _verify_record_location(
        release_root,
        record,
        identity_field="pipelineId",
        resource_type="pipeline",
        release_dir="_pipelines",
        expected_identity=require_resource_path_segment(
            pipeline_id,
            label="pipelineId",
        ),
        expected_version=_canonical_version(version, label="Pipeline version"),
    )


def verify_cycle_graph_record(
    release_root,
    record,
    *,
    resource_type,
    expected_identity=None,
    expected_version=None,
):
    """Verify one Analysis or Environment record at its identity-bound path."""
    spec = _GRAPH_SPECS.get(resource_type)
    if spec is None:
        raise ValueError(f"Unsupported Cycle Graph resource type: {resource_type}")
    identity_field, release_dir = spec
    return _verify_record_location(
        release_root,
        record,
        identity_field=identity_field,
        resource_type=resource_type,
        release_dir=release_dir,
        expected_identity=(
            None
            if expected_identity is None
            else require_resource_path_segment(
                expected_identity,
                label=identity_field,
            )
        ),
        expected_version=(
            None
            if expected_version is None
            else _canonical_version(
                expected_version,
                label=f"{resource_type} version",
            )
        ),
    )


def verify_sampler_record(
    release_root,
    record,
    *,
    expected_identity=None,
    expected_version=None,
):
    """Verify one Sampler record at its exact immutable repository path."""
    return _verify_record_location(
        release_root,
        record,
        identity_field="samplerId",
        resource_type="sampler",
        release_dir="_samplers",
        expected_identity=(
            None
            if expected_identity is None
            else require_resource_path_segment(
                expected_identity,
                label="samplerId",
            )
        ),
        expected_version=(
            None
            if expected_version is None
            else _canonical_version(expected_version, label="sampler version")
        ),
    )


def verify_module_definition_record(release_root, record):
    """Verify one Module record in the repository selected by its exact kind."""
    if not isinstance(record, dict):
        raise ValueError("Frozen Module Definition must be an object.")
    kind = require_resource_path_segment(record.get("kind"), label="Module kind")
    module_id = require_resource_path_segment(
        record.get("moduleId"),
        label="moduleId",
    )
    release_dir = MODULE_RELEASE_DIRECTORIES.get(kind)
    if release_dir is None:
        raise ValueError(f"Unsupported frozen Module kind: {kind}")
    return _verify_record_location(
        release_root,
        record,
        identity_field="moduleId",
        resource_type="module",
        release_dir=release_dir,
        expected_identity=module_id,
        expected_resource_id=f"{kind}/{module_id}",
        path_prefix=(kind,),
    )


def module_definition_record_expected_root(release_root, record):
    """Validate Module identity metadata and return its canonical repository root."""
    if not isinstance(record, dict):
        raise ValueError("Frozen Module Definition must be an object.")
    kind = require_resource_path_segment(record.get("kind"), label="Module kind")
    module_id = require_resource_path_segment(
        record.get("moduleId"),
        label="moduleId",
    )
    release_dir = MODULE_RELEASE_DIRECTORIES.get(kind)
    if release_dir is None:
        raise ValueError(f"Unsupported frozen Module kind: {kind}")
    return _expected_record_root(
        release_root,
        record,
        identity_field="moduleId",
        resource_type="module",
        release_dir=release_dir,
        expected_identity=module_id,
        expected_resource_id=f"{kind}/{module_id}",
        path_prefix=(kind,),
    )


__all__ = (
    "module_definition_record_expected_root",
    "verify_cycle_graph_record",
    "verify_module_definition_record",
    "verify_pipeline_record",
    "verify_sampler_record",
)
