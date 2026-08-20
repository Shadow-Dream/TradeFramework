"""Pipeline control-plane publication and lifecycle use cases."""

from __future__ import annotations

from pathlib import Path

from engine.archive import version_transaction
from engine.authority import module_definition as module_definition_authority
from engine.compiler import pipeline as pipeline_compiler
from engine.compiler import pipeline_manifest as pipeline_manifest_compiler
from engine.contracts.exact_fields import require_exact_fields
from engine.contracts.module import definition_key
from engine.contracts.pipeline import (
    PIPELINE_DRAFT_FIELDS,
    PIPELINE_VERSION_FIELDS,
    pipeline_manifest_digest,
)
from engine.core import clock as engine_clock
from engine.core import resource_ids
from engine.repository import control_state
from engine.repository import module_definitions
from engine.repository import pipelines as pipeline_repository


def compile_pipeline_manifest(config, definition):
    definitions, evidence = module_definitions.load_definition_versions(
        config,
        module_definitions.module_references(definition.get("instances")),
    )
    authorities = module_definition_authority.module_definition_authorities_from_record_location_evidence(
        config["releaseRoot"], evidence
    )
    return pipeline_manifest_compiler.compile_pipeline_manifest_from_definition_authorities(
        definition,
        definitions,
        authorities,
    )


def pipeline_control_snapshot(
    definition,
    manifest,
    definitions,
    definition_authorities,
):
    """Build the immutable publication snapshot from verified authorities."""

    instances = pipeline_manifest_compiler.normalize_pipeline_instances_from_definitions(
        definition["instances"], definitions
    )
    active_definitions = {}
    for instance_id in pipeline_manifest_compiler.active_instance_ids(definition):
        instance = instances.get(instance_id)
        if instance is None:
            raise ValueError(
                f"Pipeline control snapshot references missing instance '{instance_id}'."
            )
        key = definition_key(
            instance["kind"], instance["moduleId"], instance["version"]
        )
        module_definition = definitions.get(key)
        if module_definition is None:
            raise ValueError(
                "Pipeline control snapshot references missing Module Definition "
                f"'{key}'."
            )
        active_definitions[key] = module_definition

    active_authorities = {
        key: definition_authorities[key] for key in active_definitions
    }
    pipeline_compiler.pipeline_contract_template_from_verified_authorities(
        manifest,
        active_definitions,
        active_authorities,
    )
    rebuilt_manifest = (
        pipeline_manifest_compiler.compile_pipeline_manifest_from_definition_authorities(
            definition,
            active_definitions,
            active_authorities,
        )
    )
    if rebuilt_manifest != manifest:
        raise ValueError(
            "Pipeline control snapshot manifest does not match its Definition "
            "and Module Definitions."
        )
    return {
        "schemaVersion": 3,
        "createdAt": engine_clock.utc_now(),
        "definition": definition,
        "manifest": manifest,
        "manifestHash": pipeline_manifest_digest(manifest),
        "activeModuleDefinitions": active_definitions,
    }


def _archive_pipeline_if_changed_locked(config, request):
    draft = pipeline_manifest_compiler.normalize_pipeline_draft(request)
    pipeline_id = draft["pipelineId"]
    store = pipeline_repository.load_pipeline_store(config)
    metadata = store["pipelines"].get(pipeline_id)
    if metadata and metadata.get("status") == "inactive":
        raise ValueError("Inactive Pipelines are read-only; clone it before editing.")
    records = [
        record
        for record in store["versions"].values()
        if record.get("pipelineId") == pipeline_id
    ]

    def destination_for_version(version):
        return Path(config["releaseRoot"]) / "_pipelines" / pipeline_id / version

    def prepare_staging(_staging, _version, _destination):
        definitions, evidence = module_definitions.load_definition_versions(
            config,
            module_definitions.module_references(draft["instances"]),
        )
        authorities = module_definition_authority.module_definition_authorities_from_record_location_evidence(
            config["releaseRoot"], evidence
        )
        manifest = pipeline_manifest_compiler.compile_pipeline_manifest_from_definition_authorities(
            draft,
            definitions,
            authorities,
        )
        normalized = dict(draft)
        return normalized, {
            "draft": normalized,
            "manifest": manifest,
            "moduleDefinitions": definitions,
            "moduleDefinitionAuthorities": authorities,
        }

    def create_record(_version, context):
        return {
            **context["draft"],
            "manifestHash": pipeline_manifest_digest(context["manifest"]),
        }

    def write_record(staging, record, context):
        control_state.atomic_write_json(staging / "pipeline.json", context["manifest"])
        control_state.atomic_write_json(staging / "definition.json", record)
        control_state.atomic_write_json(
            staging / "control-snapshot.json",
            pipeline_control_snapshot(
                record,
                context["manifest"],
                context["moduleDefinitions"],
                context["moduleDefinitionAuthorities"],
            ),
        )

    def commit_record(record, _context):
        version_key = f"{pipeline_id}/{record['version']}"
        store["versions"][version_key] = record
        store["pipelines"][pipeline_id] = {
            "schemaVersion": 1,
            "pipelineId": pipeline_id,
            "name": record.get("name") or pipeline_id,
            "status": "active",
            "currentVersion": record["version"],
            "manifestHash": record["manifestHash"],
            "updatedAt": record["createdAt"],
        }
        pipeline_repository.save_pipeline_store(config, store)

    def read_committed_record(record, _context):
        version_key = f"{pipeline_id}/{record['version']}"
        return pipeline_repository.load_pipeline_store(config)["versions"].get(
            version_key
        )

    result = version_transaction.archive_if_changed(
        records=records,
        identity_key="pipelineId",
        identity=pipeline_id,
        resource_type="pipeline",
        resource_id=pipeline_id,
        managed_root=config["releaseRoot"],
        destination_for_version=destination_for_version,
        prepare_staging=prepare_staging,
        create_record=create_record,
        record_fields=PIPELINE_VERSION_FIELDS,
        write_record=write_record,
        commit_record=commit_record,
        read_committed_record=read_committed_record,
        immutable_fields=(),
    )
    definition = result["record"]
    manifest = (result.get("context") or {}).get("manifest")
    if result["unchanged"]:
        manifest = pipeline_repository.load_pipeline_manifest(definition)
    control_state.append_history_event(
        config,
        "pipeline.archived-version",
        {
            "pipelineId": pipeline_id,
            "version": definition["version"],
            "unchanged": result["unchanged"],
        },
    )
    return {
        "accepted": True,
        "unchanged": result["unchanged"],
        "pipelineId": pipeline_id,
        "version": definition["version"],
        "pipeline": pipeline_repository.load_pipeline_store(config)["pipelines"][
            pipeline_id
        ],
        "definition": definition,
        "manifest": manifest,
    }


def archive_pipeline_if_changed(config, request):
    with control_state.control_state_lock(config):
        return _archive_pipeline_if_changed_locked(config, request)


def rename_pipeline(config, pipeline_id, name):
    pipeline_id = pipeline_manifest_compiler.normalize_pipeline_id(pipeline_id)
    name = str(name or "").strip()
    if not name:
        raise ValueError("Pipeline name is required.")
    if len(name) > 120:
        raise ValueError("Pipeline name must be 120 characters or fewer.")
    with control_state.control_state_lock(config):
        current = pipeline_repository.load_current_pipeline(config, pipeline_id)
        draft = {
            key: current[key] for key in PIPELINE_DRAFT_FIELDS if key in current
        }
        draft["name"] = name
        return _archive_pipeline_if_changed_locked(config, draft)


def create_pipeline(config, request):
    require_exact_fields(
        request,
        allowed={"name"},
        required={"name"},
        label="Create Pipeline request",
    )
    name = str(request["name"] or "").strip()
    if not name:
        raise ValueError("Pipeline name is required.")
    if len(name) > 120:
        raise ValueError("Pipeline name must be 120 characters or fewer.")
    with control_state.control_state_lock(config):
        pipelines = pipeline_repository.load_pipelines(config)
        pipeline_id = resource_ids.new_resource_id("pipeline")
        while pipeline_id in pipelines:
            pipeline_id = resource_ids.new_resource_id("pipeline")
        return _archive_pipeline_if_changed_locked(
            config,
            {
                "pipelineId": pipeline_id,
                "name": name,
                "config": {
                    "observationInput": {"whitelist": [], "blacklist": []}
                },
                "instances": {},
                "stages": {},
                "signalGraph": {"nodes": [], "inputs": {}, "outputs": {}},
            },
        )


def load_pipeline_version_details(config, pipeline_id, version):
    record = pipeline_repository.load_pipeline_version(config, pipeline_id, version)
    manifest = pipeline_repository.load_pipeline_manifest(record)
    version_summary = next(
        item
        for item in pipeline_repository.pipeline_versions(config, pipeline_id)
        if item["version"] == record["version"]
    )
    return {
        "versionSummary": version_summary,
        "definition": record,
        "manifest": manifest,
    }


def get_pipeline_details(config, pipeline_id, version=""):
    definition = (
        pipeline_repository.load_pipeline_version(config, pipeline_id, version)
        if version
        else pipeline_repository.load_current_pipeline(config, pipeline_id)
    )
    manifest = pipeline_repository.load_pipeline_manifest(definition)
    metadata = pipeline_repository.load_pipeline_store(config)["pipelines"][
        definition["pipelineId"]
    ]
    return {
        "pipelineId": definition["pipelineId"],
        "pipeline": metadata,
        "definition": definition,
        "manifestPath": str(
            pipeline_repository.pipeline_manifest_path(definition)
        ),
        "manifest": manifest,
        "versions": pipeline_repository.pipeline_versions(
            config, definition["pipelineId"]
        ),
    }


def clone_pipeline(config, source_pipeline_id, request):
    require_exact_fields(
        request,
        allowed={"name"},
        required={"name"},
        label="Clone Pipeline request",
    )
    source_pipeline_id = pipeline_manifest_compiler.normalize_pipeline_id(
        source_pipeline_id
    )
    name = str(request["name"] or "").strip()
    if not name:
        raise ValueError("Pipeline name is required.")
    if len(name) > 120:
        raise ValueError("Pipeline name must be 120 characters or fewer.")
    with control_state.control_state_lock(config):
        source = pipeline_repository.load_current_pipeline(
            config, source_pipeline_id
        )
        pipelines = pipeline_repository.load_pipelines(config)
        pipeline_id = resource_ids.new_resource_id("pipeline")
        while pipeline_id in pipelines:
            pipeline_id = resource_ids.new_resource_id("pipeline")
        draft = {
            key: source[key] for key in PIPELINE_DRAFT_FIELDS if key in source
        }
        draft.update({"pipelineId": pipeline_id, "name": name})
        result = _archive_pipeline_if_changed_locked(config, draft)
        result["sourcePipelineId"] = source_pipeline_id
        return result


def disable_pipeline(config, pipeline_id, request):
    require_exact_fields(
        request,
        allowed={"reason"},
        required={"reason"},
        label="Disable Pipeline request",
    )
    pipeline_id = pipeline_manifest_compiler.normalize_pipeline_id(pipeline_id)
    with control_state.control_state_lock(config):
        store = pipeline_repository.load_pipeline_store(config)
        pipeline = store["pipelines"].get(pipeline_id)
        if not pipeline:
            raise ValueError(f"Pipeline '{pipeline_id}' does not exist.")
        if pipeline.get("status") == "inactive":
            return {"accepted": True, "pipeline": pipeline}
        disabled_at = engine_clock.utc_now()
        disabled_reason = str(request["reason"] or "").strip()
        pipeline = {
            **pipeline,
            "status": "inactive",
            "updatedAt": disabled_at,
        }
        store["pipelines"][pipeline_id] = pipeline
        pipeline_repository.save_pipeline_store(config, store)
        control_state.append_history_event(
            config,
            "pipeline.disabled",
            {
                "pipelineId": pipeline_id,
                "reason": disabled_reason,
                "disabledAt": disabled_at,
            },
        )
        return {
            "accepted": True,
            "pipeline": pipeline,
            "reason": disabled_reason,
            "disabledAt": disabled_at,
        }


__all__ = (
    "archive_pipeline_if_changed",
    "clone_pipeline",
    "compile_pipeline_manifest",
    "create_pipeline",
    "disable_pipeline",
    "get_pipeline_details",
    "load_pipeline_version_details",
    "pipeline_control_snapshot",
    "rename_pipeline",
)
