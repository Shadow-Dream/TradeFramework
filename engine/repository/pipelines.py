"""Authoritative Pipeline indexes and immutable archive reads."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from engine.archive import version as version_archive
from engine.contracts.archive import require_resource_path_segment
from engine.contracts.exact_fields import require_exact_fields
from engine.contracts.module import definition_key
from engine.contracts.pipeline import (
    PIPELINE_VERSION_FIELDS,
    pipeline_manifest_digest,
    validate_pipeline_manifest,
)
from engine.repository import control_state


PIPELINE_METADATA_FIELDS = frozenset(
    {
        "schemaVersion",
        "pipelineId",
        "name",
        "status",
        "currentVersion",
        "manifestHash",
        "updatedAt",
    }
)
PIPELINE_CONTROL_SNAPSHOT_FIELDS = frozenset(
    {
        "schemaVersion",
        "createdAt",
        "definition",
        "manifest",
        "manifestHash",
        "activeModuleDefinitions",
    }
)
PIPELINE_STORE_FIELDS = frozenset({"schemaVersion", "pipelines", "versions"})


def validate_pipeline_store(config, store):
    """Verify the complete Pipeline identity/version index and archive locations."""

    if not isinstance(store, dict) or set(store) != PIPELINE_STORE_FIELDS:
        raise ValueError("Pipeline repository has an invalid schema.")
    if store.get("schemaVersion") != 1:
        raise ValueError("Pipeline repository schemaVersion must be 1.")
    if not isinstance(store.get("pipelines"), dict) or not isinstance(
        store.get("versions"), dict
    ):
        raise ValueError("Pipeline repository indexes must be objects.")
    versions_by_pipeline = {}
    for key, record in store["versions"].items():
        if not isinstance(record, dict):
            raise ValueError(f"Pipeline version index '{key}' must be an object.")
        require_exact_fields(
            record,
            allowed=PIPELINE_VERSION_FIELDS,
            required=PIPELINE_VERSION_FIELDS,
            label=f"Pipeline version '{key}'",
        )
        expected_key = f"{record.get('pipelineId')}/{record.get('version')}"
        if key != expected_key:
            raise ValueError(f"Pipeline version index key mismatch: {key}")
        version_archive.verify_record(record)
        version_archive.verify_record_location(
            record,
            managed_root=config["releaseRoot"],
            expected_root=(
                Path(config["releaseRoot"])
                / "_pipelines"
                / record["pipelineId"]
                / record["version"]
            ),
        )
        versions_by_pipeline.setdefault(record["pipelineId"], []).append(record)
    if set(versions_by_pipeline) != set(store["pipelines"]):
        raise ValueError(
            "Pipeline metadata and version indexes must have identical identities."
        )
    for pipeline_id, metadata in store["pipelines"].items():
        if not isinstance(metadata, dict) or set(metadata) != PIPELINE_METADATA_FIELDS:
            raise ValueError(
                f"Pipeline metadata has an invalid schema: {pipeline_id}"
            )
        if metadata.get("schemaVersion") != 1 or metadata.get(
            "pipelineId"
        ) != pipeline_id:
            raise ValueError(f"Pipeline metadata key mismatch: {pipeline_id}")
        if metadata.get("status") not in {"active", "inactive"}:
            raise ValueError(f"Pipeline '{pipeline_id}' has invalid status.")
        records = versions_by_pipeline[pipeline_id]
        version_archive.verify_record_collection(records, ("pipelineId",))
        latest = max(records, key=lambda record: int(record["version"]))
        if str(metadata.get("currentVersion") or "") != latest["version"]:
            raise ValueError(
                f"Pipeline '{pipeline_id}' currentVersion must be its latest version."
            )
        if metadata.get("manifestHash") != latest.get("manifestHash"):
            raise ValueError(
                f"Pipeline '{pipeline_id}' manifestHash does not match its current version."
            )
    return store


def load_pipeline_store(config):
    store = control_state.load_state(
        config,
        "pipelines.json",
        {"schemaVersion": 1, "pipelines": {}, "versions": {}},
    )
    return validate_pipeline_store(config, store)


def save_pipeline_store(config, store):
    validate_pipeline_store(config, store)
    control_state.save_state(config, "pipelines.json", store)


def load_pipelines(config):
    return deepcopy(load_pipeline_store(config)["pipelines"])


def load_pipeline_version(config, pipeline_id, version):
    pipeline_id = require_resource_path_segment(pipeline_id, label="pipelineId")
    version = str(version or "").strip()
    if not version:
        raise ValueError("Pipeline version is required.")
    store = load_pipeline_store(config)
    if not store["pipelines"].get(pipeline_id):
        raise ValueError(f"Unknown Pipeline: {pipeline_id}")
    record = store["versions"].get(f"{pipeline_id}/{version}")
    if not record:
        raise ValueError(f"Unknown Pipeline version: {pipeline_id}@{version}")
    version_archive.verify_record(record)
    return deepcopy(record)


def load_current_pipeline(config, pipeline_id):
    pipeline_id = require_resource_path_segment(pipeline_id, label="pipelineId")
    metadata = load_pipeline_store(config)["pipelines"].get(pipeline_id)
    if not metadata:
        raise ValueError(f"Unknown Pipeline: {pipeline_id}")
    return load_pipeline_version(config, pipeline_id, metadata["currentVersion"])


def pipeline_versions(config, pipeline_id):
    pipeline_id = require_resource_path_segment(pipeline_id, label="pipelineId")
    store = load_pipeline_store(config)
    metadata = store["pipelines"].get(pipeline_id)
    if not metadata:
        raise ValueError(f"Pipeline '{pipeline_id}' does not exist.")
    records = [
        record
        for record in store["versions"].values()
        if record.get("pipelineId") == pipeline_id
    ]
    records.sort(key=lambda record: int(record["version"]))
    versions = []
    for record in records:
        version_archive.verify_record(record)
        versions.append(
            {
                "schemaVersion": 1,
                "pipelineId": pipeline_id,
                "version": record["version"],
                "createdAt": record["createdAt"],
                "name": record.get("name") or pipeline_id,
                "contentDigest": record["contentDigest"],
                "current": record["version"] == metadata.get("currentVersion"),
            }
        )
    return versions


def load_pipeline_execution_version(config, pipeline_id, version):
    """Load one active exact Pipeline version without scanning its history."""

    pipeline_id = require_resource_path_segment(pipeline_id, label="pipelineId")
    version = require_resource_path_segment(
        str(version or "").strip(),
        label="Pipeline version",
    )
    store = control_state.load_state(
        config,
        "pipelines.json",
        {"schemaVersion": 1, "pipelines": {}, "versions": {}},
    )
    if (
        not isinstance(store, dict)
        or set(store) != {"schemaVersion", "pipelines", "versions"}
        or store.get("schemaVersion") != 1
        or not isinstance(store.get("pipelines"), dict)
        or not isinstance(store.get("versions"), dict)
    ):
        raise ValueError("Pipeline repository has an invalid schema.")
    metadata = store["pipelines"].get(pipeline_id)
    if not isinstance(metadata, dict) or set(metadata) != PIPELINE_METADATA_FIELDS:
        raise ValueError(f"Unknown Pipeline: {pipeline_id}")
    if (
        metadata.get("schemaVersion") != 1
        or metadata.get("pipelineId") != pipeline_id
        or metadata.get("status") != "active"
    ):
        raise ValueError(
            f"Pipeline '{pipeline_id}' is inactive or invalid and cannot run."
        )
    key = f"{pipeline_id}/{version}"
    record = store["versions"].get(key)
    if not isinstance(record, dict):
        raise ValueError(f"Unknown Pipeline version: {pipeline_id}@{version}")
    require_exact_fields(
        record,
        allowed=PIPELINE_VERSION_FIELDS,
        required=PIPELINE_VERSION_FIELDS,
        label=f"Pipeline version '{key}'",
    )
    if record.get("pipelineId") != pipeline_id or record.get("version") != version:
        raise ValueError("Pipeline version index key does not match its record.")
    version_archive.verify_record_location(
        record,
        managed_root=config["releaseRoot"],
        expected_root=(
            Path(config["releaseRoot"])
            / "_pipelines"
            / pipeline_id
            / version
        ),
    )
    return deepcopy(metadata), deepcopy(record)


def pipeline_manifest_path(pipeline_definition):
    return Path(pipeline_definition["archive"]["root"]) / "pipeline.json"


def load_pipeline_manifest(pipeline_definition):
    """Read and validate pipeline.json for one already verified version record."""

    pipeline_id = pipeline_definition.get("pipelineId")
    version = pipeline_definition.get("version")
    manifest = control_state.load_json_file(
        pipeline_manifest_path(pipeline_definition),
        None,
    )
    if not isinstance(manifest, dict):
        raise ValueError(
            f"Pipeline archive is missing pipeline.json: {pipeline_id}@{version}"
        )
    validate_pipeline_manifest(manifest)
    if pipeline_manifest_digest(manifest) != pipeline_definition.get("manifestHash"):
        raise ValueError(
            f"Pipeline manifest verification failed: {pipeline_id}@{version}"
        )
    return deepcopy(manifest)


def load_pipeline_control_snapshot(pipeline_definition, manifest):
    """Read the exact Module Definition snapshot bound to an archived manifest."""

    validate_pipeline_manifest(manifest)
    required = {
        definition_key(module["kind"], module["moduleId"], module["version"])
        for module in manifest["modules"]
    }
    version_archive.verify_record(pipeline_definition)
    root = Path(pipeline_definition["archive"]["root"])
    snapshot = control_state.load_json_file(root / "control-snapshot.json", None)
    if not isinstance(snapshot, dict):
        raise ValueError(
            "Pipeline archive is missing its immutable Module snapshot: "
            f"{pipeline_definition['pipelineId']}@{pipeline_definition['version']}"
        )
    require_exact_fields(
        snapshot,
        allowed=PIPELINE_CONTROL_SNAPSHOT_FIELDS,
        required=PIPELINE_CONTROL_SNAPSHOT_FIELDS,
        label="Pipeline control snapshot",
    )
    if snapshot["schemaVersion"] != 3:
        raise ValueError("Pipeline control snapshot schemaVersion 3 is required.")
    manifest_hash = pipeline_manifest_digest(manifest)
    archived_record = control_state.load_json_file(
        root / version_archive.RECORD_NAME,
        None,
    )
    indexed_record = deepcopy(pipeline_definition)
    indexed_record["archive"].pop("manifestDigest")
    if (
        not isinstance(archived_record, dict)
        or snapshot["definition"] != archived_record
        or indexed_record != archived_record
        or snapshot["manifest"] != manifest
        or snapshot["manifestHash"] != manifest_hash
        or pipeline_definition["manifestHash"] != manifest_hash
    ):
        raise ValueError(
            "Pipeline archive control snapshot does not match its frozen files."
        )
    definitions = snapshot["activeModuleDefinitions"]
    if not isinstance(definitions, dict) or set(definitions) != required:
        raise ValueError(
            "Pipeline Module definitions do not exactly match its manifest."
        )
    return deepcopy(snapshot)


__all__ = (
    "PIPELINE_CONTROL_SNAPSHOT_FIELDS",
    "PIPELINE_METADATA_FIELDS",
    "PIPELINE_STORE_FIELDS",
    "load_current_pipeline",
    "load_pipeline_control_snapshot",
    "load_pipeline_execution_version",
    "load_pipeline_manifest",
    "load_pipeline_store",
    "load_pipeline_version",
    "load_pipelines",
    "pipeline_manifest_path",
    "pipeline_versions",
    "save_pipeline_store",
    "validate_pipeline_store",
)
