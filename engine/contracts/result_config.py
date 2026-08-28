"""Frozen Result configuration evidence and legacy request contracts."""

from __future__ import annotations

import copy

from engine.contracts import config_override as config_override_contracts
from engine.contracts.backtest import backtest_evidence_digest
from engine.contracts.data_model import validate_normalized_json_value
from engine.contracts.module import require_exact_fields


LEGACY_SNAPSHOT_SCHEMA_VERSION = 12


def require_configuration(value):
    require_exact_fields(
        value,
        allowed={"sampler", "pipeline", "environment", "analysis", "digest"},
        required={"sampler", "pipeline", "environment", "analysis", "digest"},
        label="Result configuration",
    )
    current_resource_fields = {
        "sampler": {"override", "effective"},
        "pipeline": {
            "configOverride",
            "moduleConfigOverrides",
            "effectiveConfig",
            "effectiveModuleConfigs",
        },
        "environment": {"moduleConfigOverrides", "effectiveModuleConfigs"},
        "analysis": {"moduleConfigOverrides", "effectiveModuleConfigs"},
    }
    legacy_resource_fields = {
        resource: {"override", "effective"}
        for resource in ("sampler", "pipeline", "environment", "analysis")
    }
    pipeline_configuration = value["pipeline"]
    legacy_shape = (
        type(pipeline_configuration) is dict
        and bool(set(pipeline_configuration) & {"override", "effective"})
    )
    resource_fields = (
        legacy_resource_fields if legacy_shape else current_resource_fields
    )
    for resource, fields in resource_fields.items():
        require_exact_fields(
            value[resource],
            allowed=fields,
            required=fields,
            label=f"Result configuration.{resource}",
        )
        for field in fields:
            item = value[resource][field]
            if type(item) is not dict:
                raise ValueError(
                    f"Result configuration.{resource}.{field} must be an object."
                )
            validate_normalized_json_value(
                item,
                {},
                path=f"Result.configuration.{resource}.{field}",
            )
    unsigned = {key: item for key, item in value.items() if key != "digest"}
    if value["digest"] != backtest_evidence_digest(unsigned):
        raise ValueError("Result configuration.digest is invalid.")
    return value


def snapshot_configuration_v13(execution_snapshot, execution_inputs):
    sampler_parameters = copy.deepcopy(execution_inputs["sampler"]["parameters"])
    configuration = {
        "sampler": {
            "override": sampler_parameters,
            "effective": {
                **copy.deepcopy(execution_snapshot["samplerDefinition"]["config"]),
                **copy.deepcopy(sampler_parameters),
            },
        },
    }
    pipeline_config_override = copy.deepcopy(
        execution_inputs["pipeline"].get("configOverride", {})
    )
    pipeline_module_config_overrides = copy.deepcopy(
        execution_inputs["pipeline"].get("moduleConfigOverrides", {})
    )
    effective_pipeline = config_override_contracts.apply_pipeline_config_override(
        execution_snapshot["pipeline"]["definition"],
        pipeline_config_override,
        label="Pipeline",
    )
    effective_pipeline = config_override_contracts.apply_module_config_overrides(
        effective_pipeline,
        pipeline_module_config_overrides,
        label="Pipeline",
    )
    configuration["pipeline"] = {
        "configOverride": pipeline_config_override,
        "moduleConfigOverrides": pipeline_module_config_overrides,
        "effectiveConfig": copy.deepcopy(effective_pipeline["config"]),
        "effectiveModuleConfigs": (
            config_override_contracts.effective_module_configs(
                effective_pipeline,
                label="Pipeline",
            )
        ),
    }
    resources = (
        ("environment", execution_snapshot["environmentDefinition"], "Environment"),
        ("analysis", execution_snapshot["analysisDefinition"], "Analysis"),
    )
    for resource, definition, label in resources:
        module_config_overrides = copy.deepcopy(
            execution_inputs[resource].get("moduleConfigOverrides", {})
        )
        effective = config_override_contracts.apply_module_config_overrides(
            definition,
            module_config_overrides,
            label=label,
        )
        configuration[resource] = {
            "moduleConfigOverrides": module_config_overrides,
            "effectiveModuleConfigs": (
                config_override_contracts.effective_module_configs(
                    effective,
                    label=label,
                )
            ),
        }
    configuration["digest"] = backtest_evidence_digest(configuration)
    return configuration


def snapshot_configuration_v12(execution_snapshot, execution_inputs):
    """Rebuild the uniform configuration evidence written by snapshot v12."""

    sampler_parameters = copy.deepcopy(execution_inputs["sampler"]["parameters"])
    configuration = {
        "sampler": {
            "override": sampler_parameters,
            "effective": {
                **copy.deepcopy(execution_snapshot["samplerDefinition"]["config"]),
                **copy.deepcopy(sampler_parameters),
            },
        },
    }
    resources = (
        (
            "pipeline",
            execution_snapshot["pipeline"]["definition"],
            "Pipeline",
        ),
        (
            "environment",
            execution_snapshot["environmentDefinition"],
            "Environment",
        ),
        (
            "analysis",
            execution_snapshot["analysisDefinition"],
            "Analysis",
        ),
    )
    for resource, definition, label in resources:
        override = copy.deepcopy(
            execution_inputs[resource].get("configOverride", {})
        )
        effective = config_override_contracts.apply_module_config_overrides(
            definition,
            override,
            label=label,
        )
        configuration[resource] = {
            "override": override,
            "effective": config_override_contracts.effective_module_configs(
                effective,
                label=label,
            ),
        }
    configuration["digest"] = backtest_evidence_digest(configuration)
    return configuration


def legacy_backtest_execution_inputs(value):
    """Validate the exact public request representation frozen by snapshot v12."""

    value = copy.deepcopy(require_exact_fields(
        value,
        allowed={
            "pipeline", "datasetId", "datasetVersionId", "sampler",
            "environment", "analysis", "limit",
        },
        required={
            "pipeline", "datasetId", "datasetVersionId", "sampler",
            "environment", "analysis", "limit",
        },
        label="Stored execution inputs",
    ))
    references = (
        ("pipeline", "pipelineId"),
        ("environment", "environmentId"),
        ("analysis", "analysisId"),
    )
    for resource, identity_field in references:
        require_exact_fields(
            value[resource],
            allowed={identity_field, "version", "configOverride"},
            required={identity_field, "version"},
            label=f"Stored execution inputs.{resource}",
        )
        for field in (identity_field, "version"):
            if (
                not isinstance(value[resource][field], str)
                or not value[resource][field].strip()
            ):
                raise ValueError(
                    f"Stored execution inputs.{resource}.{field} is invalid."
                )
        if "configOverride" in value[resource]:
            value[resource]["configOverride"] = (
                config_override_contracts.normalize_module_config_overrides(
                    value[resource]["configOverride"],
                    label=(
                        f"Stored execution inputs.{resource}.configOverride"
                    ),
                )
            )
    require_exact_fields(
        value["sampler"],
        allowed={"samplerId", "version", "parameters"},
        required={"samplerId", "version", "parameters"},
        label="Stored execution inputs.sampler",
    )
    for field in ("samplerId", "version"):
        if (
            not isinstance(value["sampler"][field], str)
            or not value["sampler"][field].strip()
        ):
            raise ValueError(f"Stored execution inputs.sampler.{field} is invalid.")
    parameters = value["sampler"]["parameters"]
    if type(parameters) is not dict:
        raise ValueError("Stored execution inputs.sampler.parameters must be an object.")
    validate_normalized_json_value(
        parameters,
        {},
        path="Stored.executionInputs.sampler.parameters",
    )
    limit = value["limit"]
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
    ):
        raise ValueError("Stored execution inputs.limit is invalid.")
    for field in ("datasetId", "datasetVersionId"):
        if not isinstance(value[field], str) or not value[field].strip():
            raise ValueError(f"Stored execution inputs.{field} is invalid.")
    return value


__all__ = (
    "LEGACY_SNAPSHOT_SCHEMA_VERSION",
    "legacy_backtest_execution_inputs",
    "require_configuration",
    "snapshot_configuration_v12",
    "snapshot_configuration_v13",
)
