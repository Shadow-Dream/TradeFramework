"""Repository-backed Backtest composition validation and freezing use cases."""

from __future__ import annotations

import copy

from engine.authority import module_definition as module_definition_authority
from engine.authority import pipeline as pipeline_authority
from engine.authority import sampler as sampler_authority
from engine.compiler import analysis as analysis_compiler
from engine.compiler import environment as environment_compiler
from engine.compiler import pipeline_manifest as pipeline_manifest_compiler
from engine.composition import backtest as backtest_composition
from engine.contracts import backtest as backtest_contracts
from engine.contracts import pipeline as pipeline_contracts
from engine.contracts import strict_json
from engine.contracts.contract_expansion import (
    contract_expansion_cache_scope,
    contract_root_paths,
)
from engine.contracts.module import definition_key
from engine.core import clock as engine_clock
from engine.core import runtime_identity
from engine.repository import datasets as dataset_repository
from engine.repository import graph_resources
from engine.repository import module_definitions as module_repository
from engine.repository import pipelines as pipeline_repository
from engine.repository import samplers as sampler_repository


def _pipeline_definitions_for_manifest(pipeline_definition, manifest):
    snapshot = pipeline_repository.load_pipeline_control_snapshot(
        pipeline_definition,
        manifest,
    )
    definitions = copy.deepcopy(snapshot["activeModuleDefinitions"])
    definition_authorities = {
        key: module_definition_authority.verify_module_definition_authority(
            definition
        )
        for key, definition in definitions.items()
    }
    rebuilt_manifest = (
        pipeline_manifest_compiler.compile_pipeline_manifest_from_definition_authorities(
            pipeline_definition,
            definitions,
            definition_authorities,
        )
    )
    if rebuilt_manifest != manifest:
        raise ValueError(
            "Pipeline manifest does not exactly match its frozen Definition "
            "and Module Definitions."
        )
    return definitions, definition_authorities


def _graph_module_definitions(
    definition,
    repository,
    repository_authorities,
    *,
    label,
):
    nodes = set(definition["graph"]["nodes"])
    required = {
        definition_key(
            instance["kind"],
            instance["moduleId"],
            instance["version"],
        )
        for instance_id, instance in definition["instances"].items()
        if instance_id in nodes
    }
    if not required.issubset(repository):
        missing = sorted(required - set(repository))
        raise ValueError(
            f"{label} references missing Module definition(s): "
            + ", ".join(missing)
        )
    definitions = {
        key: copy.deepcopy(repository[key]) for key in sorted(required)
    }
    if not isinstance(repository_authorities, dict) or not required.issubset(
        repository_authorities
    ):
        raise ValueError(
            f"{label} is missing verified Module Definition authority."
        )
    definition_authorities = {
        key: repository_authorities[key] for key in sorted(required)
    }
    return definitions, definition_authorities


def resolve_backtest_composition(config, request):
    """Resolve exact resources and compile contracts without side effects."""

    request = backtest_contracts.normalize_backtest_request(request)
    pipeline_ref = request["pipeline"]
    environment_ref = request["environment"]
    analysis_ref = request["analysis"]
    sampler_ref = request["sampler"]
    pipeline_id = pipeline_ref["pipelineId"]
    _pipeline_metadata, pipeline_definition = (
        pipeline_repository.load_pipeline_execution_version(
            config,
            pipeline_id,
            pipeline_ref["version"],
        )
    )
    manifest = pipeline_repository.load_pipeline_manifest(pipeline_definition)
    pipeline_modules, pipeline_module_authorities = (
        _pipeline_definitions_for_manifest(pipeline_definition, manifest)
    )
    pipeline_contract_template = (
        pipeline_authority.pipeline_contract_template_from_verified_authorities(
            manifest,
            pipeline_modules,
            pipeline_module_authorities,
        )
    )

    dataset = dataset_repository.get_dataset(config, request["datasetId"])
    dataset_version = dataset_repository.ensure_dataset_version(
        config,
        request["datasetId"],
        request["datasetVersionId"],
    )
    sampler_definition = sampler_repository.get_sampler_execution_version(
        config,
        sampler_ref["samplerId"],
        sampler_ref["version"],
    )
    sampler_runtime_authority = (
        sampler_authority.verify_sampler_runtime_bundle_authority(
            sampler_definition
        )
    )
    missing_capabilities = sorted(
        set(
            sampler_authority.verified_sampler_required_capabilities(
                sampler_runtime_authority
            )
        )
        - set(dataset_version["capabilities"])
    )
    if missing_capabilities:
        raise ValueError(
            "Sampler requires Dataset capability/capabilities: "
            + ", ".join(missing_capabilities)
        )
    sampler_contracts = (
        sampler_authority.resolve_verified_sampler_output_contracts(
            sampler_runtime_authority,
            sampler_ref["parameters"],
            backtest_composition.dataset_field_schema(dataset_version),
        )
    )
    sampler_required_roots = frozenset(contract_root_paths(sampler_contracts))

    environment_definition = graph_resources.load_version(
        config,
        "environment",
        environment_ref["environmentId"],
        environment_ref["version"],
    )
    analysis_definition = graph_resources.load_version(
        config,
        "analysis",
        analysis_ref["analysisId"],
        analysis_ref["version"],
    )
    environment_repository, environment_repository_evidence = (
        module_repository.load_definition_versions(
            config,
            (
                (
                    instance["kind"],
                    instance["moduleId"],
                    instance["version"],
                )
                for instance in environment_definition["instances"].values()
            ),
        )
    )
    analysis_repository, analysis_repository_evidence = (
        module_repository.load_definition_versions(
            config,
            (
                (
                    instance["kind"],
                    instance["moduleId"],
                    instance["version"],
                )
                for instance in analysis_definition["instances"].values()
            ),
        )
    )
    environment_repository_authorities = (
        module_definition_authority.module_definition_authorities_from_record_location_evidence(
            config["releaseRoot"],
            environment_repository_evidence,
        )
    )
    analysis_repository_authorities = (
        module_definition_authority.module_definition_authorities_from_record_location_evidence(
            config["releaseRoot"],
            analysis_repository_evidence,
        )
    )
    environment_modules, environment_module_authorities = (
        _graph_module_definitions(
            environment_definition,
            environment_repository,
            environment_repository_authorities,
            label="Environment Graph",
        )
    )
    analysis_modules, analysis_module_authorities = _graph_module_definitions(
        analysis_definition,
        analysis_repository,
        analysis_repository_authorities,
        label="Analysis Graph",
    )
    environment_definition = (
        environment_compiler.validate_environment_definition_authority(
            environment_definition,
            environment_module_authorities,
        )
    )
    analysis_definition = (
        analysis_compiler.validate_analysis_definition_authority(
            analysis_definition,
            analysis_module_authorities,
        )
    )
    plans = backtest_composition.compose_backtest_plans(
        pipeline_contract_template=pipeline_contract_template,
        sampler_contracts=sampler_contracts,
        sampler_required_roots=sampler_required_roots,
        environment_definition=environment_definition,
        environment_module_authorities=environment_module_authorities,
        analysis_definition=analysis_definition,
        analysis_module_authorities=analysis_module_authorities,
    )
    return {
        "request": request,
        "pipelineDefinition": pipeline_definition,
        "pipelineManifest": manifest,
        "pipelineModuleDefinitions": pipeline_modules,
        "datasetName": dataset["name"],
        "datasetVersion": dataset_version,
        "samplerDefinition": sampler_definition,
        "environmentDefinition": environment_definition,
        "environmentModuleDefinitions": environment_modules,
        "analysisDefinition": analysis_definition,
        "analysisModuleDefinitions": analysis_modules,
        **plans,
    }


def _validate_backtest_composition(config, request):
    resolved = resolve_backtest_composition(config, request)
    return {
        "valid": True,
        "pipelineTopology": resolved["pipelinePlan"]["topology"],
        "environmentTopology": resolved["environmentPlan"]["topology"],
        "analysisTopology": resolved["analysisPlan"]["topology"],
        "resultContracts": resolved["resultContracts"],
    }


def validate_backtest_composition(config, request):
    """Validate one composition in an operation-scoped compiler context."""

    with contract_expansion_cache_scope():
        return _validate_backtest_composition(config, request)


def _freeze_backtest_request(config, request):
    """Validate then freeze one composition of exact immutable versions."""

    resolved = resolve_backtest_composition(config, request)
    request = resolved["request"]
    pipeline_id = request["pipeline"]["pipelineId"]
    pipeline_definition = resolved["pipelineDefinition"]
    manifest = resolved["pipelineManifest"]
    manifest_hash = pipeline_contracts.pipeline_manifest_digest(manifest)
    dataset_version = resolved["datasetVersion"]
    sampler_definition = resolved["samplerDefinition"]
    environment_definition = resolved["environmentDefinition"]
    environment_module_definitions = resolved["environmentModuleDefinitions"]
    analysis_definition = resolved["analysisDefinition"]
    frozen_analysis_modules = resolved["analysisModuleDefinitions"]
    pipeline_module_definitions = resolved["pipelineModuleDefinitions"]
    composition_artifact = (
        backtest_composition.build_backtest_composition_artifact(resolved)
    )
    snapshot = {
        "schemaVersion": backtest_contracts.BACKTEST_EXECUTION_SNAPSHOT_SCHEMA_VERSION,
        "createdAt": engine_clock.utc_now(),
        "engineRuntime": runtime_identity.engine_runtime_identity(),
        "executionInputs": backtest_contracts.backtest_execution_inputs(request),
        "datasetName": resolved["datasetName"],
        "datasetVersion": copy.deepcopy(dataset_version),
        "samplerDefinition": copy.deepcopy(sampler_definition),
        "environmentDefinition": copy.deepcopy(environment_definition),
        "environmentModuleDefinitions": environment_module_definitions,
        "analysisDefinition": copy.deepcopy(analysis_definition),
        "analysisModuleDefinitions": frozen_analysis_modules,
        "compositionArtifact": composition_artifact,
        "pipeline": {
            "pipelineId": pipeline_id,
            "version": pipeline_definition["version"],
            "manifestHash": manifest_hash,
            "definition": copy.deepcopy(pipeline_definition),
            "manifest": copy.deepcopy(manifest),
            "moduleDefinitions": pipeline_module_definitions,
        },
    }
    snapshot["snapshotHash"] = backtest_contracts.backtest_evidence_digest(
        snapshot
    )
    frozen = copy.deepcopy(request)
    frozen["datasetVersionId"] = dataset_version["datasetVersionId"]
    frozen["executionSnapshot"] = snapshot
    return frozen


def require_frozen_backtest_admission(config, request):
    """Recheck mutable admission state without recompiling frozen resources."""

    if not isinstance(request, dict) or not isinstance(
        request.get("executionSnapshot"), dict
    ):
        raise ValueError(
            "Prepared Backtest admission requires a frozen executionSnapshot."
        )
    normalized = backtest_contracts.normalize_backtest_request(
        {
            key: copy.deepcopy(value)
            for key, value in request.items()
            if key != "executionSnapshot"
        }
    )
    snapshot = request["executionSnapshot"]
    if not strict_json.exact_equal(
        snapshot.get("executionInputs"),
        backtest_contracts.backtest_execution_inputs(normalized),
    ):
        raise ValueError(
            "Prepared Backtest execution inputs do not match its request."
        )
    pipeline_repository.load_pipeline_execution_version(
        config,
        normalized["pipeline"]["pipelineId"],
        normalized["pipeline"]["version"],
    )
    dataset = dataset_repository.get_dataset(config, normalized["datasetId"])
    if dataset["status"] != "active":
        raise ValueError(
            f"Dataset '{normalized['datasetId']}' is archived and cannot be used."
        )
    return request


def freeze_backtest_request(config, request):
    """Validate and freeze once with bounded pure compiler reuse."""

    with contract_expansion_cache_scope():
        return _freeze_backtest_request(config, request)


__all__ = (
    "freeze_backtest_request",
    "require_frozen_backtest_admission",
    "resolve_backtest_composition",
    "validate_backtest_composition",
)
