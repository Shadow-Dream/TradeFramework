"""Read-only Backtest preparation which cannot activate dynamic Runtime code."""

from __future__ import annotations

import copy
from typing import NamedTuple

from engine.authority import dataset as dataset_authority
from engine.authority import module_definition as module_definition_authority
from engine.authority import pipeline as pipeline_authority
from engine.authority import sampler as sampler_authority
from engine.authority.graph_cycle import (
    verified_cycle_graph_execution_material,
    verify_managed_cycle_graph_definition_authority,
)
from engine.composition import backtest as backtest_composition
from engine.contracts import backtest as backtest_contracts
from engine.contracts import strict_json
from engine.contracts.exact_fields import require_exact_fields


class PreparedBacktest(NamedTuple):
    analysis_definition: dict
    dataset_name: str
    dataset_storage_authority: object
    dataset_version: dict
    environment_definition: dict
    sampler_definition: dict
    sampler_parameters: dict
    sampler_runtime_authority: object
    verified_composition: object
    verified_sampler_contracts: object
    verified_sampler_required_roots: object


def prepare_backtest_execution(
    config,
    request,
    *,
    dataset_id,
    execution_snapshot,
    pipeline_id,
    pipeline_version,
):
    """Verify immutable artifacts and construct no Runtime/spawn capability."""

    validated_composition_artifact = (
        backtest_composition.validate_backtest_composition_artifact(
            execution_snapshot["compositionArtifact"]
        )
    )
    dataset_name = execution_snapshot["datasetName"]
    if not isinstance(dataset_name, str) or not dataset_name.strip():
        raise ValueError("Backtest execution snapshot datasetName is required.")
    request_inputs = {
        key: copy.deepcopy(value)
        for key, value in request.items()
        if key != "executionSnapshot"
    }
    if strict_json.dumps(
        execution_snapshot["executionInputs"], sort_keys=True
    ) != strict_json.dumps(
        backtest_contracts.backtest_execution_inputs(request_inputs), sort_keys=True
    ):
        raise ValueError(
            "Backtest execution inputs do not match their frozen snapshot."
        )
    frozen_pipeline = execution_snapshot["pipeline"]
    if not isinstance(frozen_pipeline, dict):
        raise ValueError("Backtest execution snapshot Pipeline must be an object.")
    if (
        frozen_pipeline["pipelineId"] != pipeline_id
        or frozen_pipeline["version"] != pipeline_version
    ):
        raise ValueError("Backtest Pipeline version does not match its frozen snapshot.")
    (
        frozen_pipeline_definition_authority,
        frozen_pipeline_manifest,
        frozen_pipeline_modules,
        frozen_pipeline_module_authorities,
        _frozen_manifest_hash,
    ) = pipeline_authority.verify_frozen_pipeline_execution_snapshot(
        config,
        pipeline_id,
        frozen_pipeline,
        pipeline_version=pipeline_version,
    )
    dataset_version = copy.deepcopy(execution_snapshot["datasetVersion"])
    if not isinstance(dataset_version, dict) or not dataset_version:
        raise ValueError("Backtest execution snapshot is missing its Dataset version.")
    if dataset_version["datasetId"] != dataset_id:
        raise ValueError("Frozen Dataset version does not belong to the requested Dataset.")
    if request["datasetVersionId"] != dataset_version["datasetVersionId"]:
        raise ValueError("Backtest Dataset version does not match its frozen snapshot.")
    sampler_request = require_exact_fields(
        request["sampler"],
        allowed={"samplerId", "version", "parameters"},
        required={"samplerId", "version", "parameters"},
        label="Frozen Backtest sampler",
    )
    sampler_id = sampler_request["samplerId"]
    sampler_version = sampler_request["version"]
    if (
        not isinstance(sampler_id, str)
        or not sampler_id.strip()
        or not isinstance(sampler_version, str)
        or not sampler_version.strip()
    ):
        raise ValueError("sampler.samplerId and sampler.version must be non-empty strings.")
    sampler_parameters = sampler_request["parameters"]
    if not isinstance(sampler_parameters, dict):
        raise ValueError("sampler.parameters must be an object.")
    sampler_definition = copy.deepcopy(execution_snapshot["samplerDefinition"])
    if not isinstance(sampler_definition, dict) or not sampler_definition:
        raise ValueError("Backtest execution snapshot is missing its Sampler version.")
    sampler_runtime_authority = (
        sampler_authority.verify_managed_sampler_runtime_bundle_authority(
            config["releaseRoot"],
            sampler_definition,
            expected_identity=sampler_id,
            expected_version=sampler_version,
        )
    )
    sampler_required_capabilities = frozenset(
        sampler_authority.verified_sampler_required_capabilities(
            sampler_runtime_authority
        )
    )
    missing_capabilities = sorted(
        set(sampler_required_capabilities) - set(dataset_version["capabilities"])
    )
    if missing_capabilities:
        raise ValueError(
            "Sampler requires Dataset capability/capabilities: "
            + ", ".join(missing_capabilities)
        )
    (
        dataset_version,
        dataset_storage_authority,
    ) = dataset_authority.verify_dataset_version_storage_authority(
        config["releaseRoot"],
        dataset_version,
        semantic_capabilities=sampler_required_capabilities,
    )
    environment_request = require_exact_fields(
        request["environment"],
        allowed={"environmentId", "version"},
        required={"environmentId", "version"},
        label="Frozen Backtest environment",
    )
    environment_id = environment_request["environmentId"]
    environment_version = environment_request["version"]
    if (
        not isinstance(environment_id, str)
        or not environment_id.strip()
        or not isinstance(environment_version, str)
        or not environment_version.strip()
    ):
        raise ValueError(
            "Frozen Backtest Environment identity and version must be non-empty strings."
        )
    frozen_environment_modules = copy.deepcopy(
        execution_snapshot["environmentModuleDefinitions"]
    )
    if not isinstance(frozen_environment_modules, dict):
        raise ValueError("Frozen Environment Module definitions must be an object.")
    environment_definition = copy.deepcopy(execution_snapshot["environmentDefinition"])
    if not isinstance(environment_definition, dict) or not environment_definition:
        raise ValueError("Backtest execution snapshot is missing its Environment version.")
    environment_definition_authority = verify_managed_cycle_graph_definition_authority(
        config["releaseRoot"],
        environment_definition,
        resource_type="environment",
        expected_identity=environment_id,
        expected_version=environment_version,
    )
    authoritative_environment_definition = verified_cycle_graph_execution_material(
        environment_definition_authority,
        graph_label="Environment Graph",
    )
    environment_module_definition_authorities = {}
    for key, definition in frozen_environment_modules.items():
        environment_module_definition_authorities[key] = (
            module_definition_authority.verify_managed_module_definition_authority(
                config["releaseRoot"], definition
            )
        )
    analysis_request = require_exact_fields(
        request["analysis"],
        allowed={"analysisId", "version"},
        required={"analysisId", "version"},
        label="Frozen Backtest analysis",
    )
    analysis_id = analysis_request["analysisId"]
    analysis_version = analysis_request["version"]
    if (
        not isinstance(analysis_id, str)
        or not analysis_id.strip()
        or not isinstance(analysis_version, str)
        or not analysis_version.strip()
    ):
        raise ValueError(
            "Frozen Backtest Analysis identity and version must be non-empty strings."
        )
    frozen_analysis_modules = copy.deepcopy(
        execution_snapshot["analysisModuleDefinitions"]
    )
    if not isinstance(frozen_analysis_modules, dict):
        raise ValueError("Frozen Analysis Module definitions must be an object.")
    analysis_definition = copy.deepcopy(execution_snapshot["analysisDefinition"])
    if not isinstance(analysis_definition, dict) or not analysis_definition:
        raise ValueError("Backtest execution snapshot is missing its Analysis version.")
    analysis_definition_authority = verify_managed_cycle_graph_definition_authority(
        config["releaseRoot"],
        analysis_definition,
        resource_type="analysis",
        expected_identity=analysis_id,
        expected_version=analysis_version,
    )
    authoritative_analysis_definition = verified_cycle_graph_execution_material(
        analysis_definition_authority,
        graph_label="Analysis Graph",
    )
    analysis_module_definition_authorities = {}
    for key, definition in frozen_analysis_modules.items():
        analysis_module_definition_authorities[key] = (
            module_definition_authority.verify_managed_module_definition_authority(
                config["releaseRoot"], definition
            )
        )
    pipeline_contract_template = (
        pipeline_authority.pipeline_contract_template_from_validated_plan(
            frozen_pipeline_manifest,
            frozen_pipeline_modules,
            frozen_pipeline_module_authorities,
            backtest_composition.validated_backtest_artifact_pipeline_plan(
                validated_composition_artifact
            ),
            label="Backtest composition artifact Pipeline plan",
        )
    )
    verified_composition = backtest_composition.bind_frozen_backtest_composition(
        validated_composition_artifact,
        pipeline_definition_authority=frozen_pipeline_definition_authority,
        pipeline_contract_template=pipeline_contract_template,
        sampler_runtime_authority=sampler_runtime_authority,
        sampler_parameters=sampler_parameters,
        dataset_schema=backtest_composition.dataset_field_schema(dataset_version),
        environment_definition=authoritative_environment_definition,
        environment_definition_authority=environment_definition_authority,
        environment_module_definition_authorities=(
            environment_module_definition_authorities
        ),
        analysis_definition=authoritative_analysis_definition,
        analysis_definition_authority=analysis_definition_authority,
        analysis_module_definition_authorities=(
            analysis_module_definition_authorities
        ),
    )
    (
        verified_sampler_contracts,
        verified_sampler_required_roots,
        _verified_cycle_contracts,
        _verified_cycle_required_roots,
        _verified_result_contracts,
        _verified_result_required_roots,
    ) = backtest_composition.verified_backtest_contract_material(
        verified_composition
    )
    return PreparedBacktest(
        analysis_definition=analysis_definition,
        dataset_name=dataset_name,
        dataset_storage_authority=dataset_storage_authority,
        dataset_version=dataset_version,
        environment_definition=environment_definition,
        sampler_definition=sampler_definition,
        sampler_parameters=sampler_parameters,
        sampler_runtime_authority=sampler_runtime_authority,
        verified_composition=verified_composition,
        verified_sampler_contracts=verified_sampler_contracts,
        verified_sampler_required_roots=verified_sampler_required_roots,
    )


__all__ = ("PreparedBacktest", "prepare_backtest_execution")
