"""Canonical public Backtest request and execution-evidence contracts."""

from __future__ import annotations

import copy

from engine.contracts.digest import canonical_json_digest
from engine.contracts.config_override import (
    normalize_module_config_overrides,
    normalize_resource_config_override,
)
from engine.contracts.module import require_exact_fields
from engine.contracts.protocol import common_protocol_id


BACKTEST_REQUEST_FIELDS = frozenset({
    "pipeline",
    "datasetId",
    "datasetVersionId",
    "sampler",
    "environment",
    "analysis",
    "limit",
})
BACKTEST_EXECUTION_SNAPSHOT_SCHEMA_VERSION = 13
BACKTEST_EXECUTION_SNAPSHOT_FIELDS = frozenset({
    "schemaVersion",
    "createdAt",
    "engineRuntime",
    "datasetName",
    "datasetVersion",
    "executionInputs",
    "samplerDefinition",
    "environmentDefinition",
    "environmentModuleDefinitions",
    "analysisDefinition",
    "analysisModuleDefinitions",
    "pipeline",
    "compositionArtifact",
    "snapshotHash",
})
BACKTEST_RUNNER = "engine.backtest"


def normalize_backtest_request(request):
    """Validate and isolate the complete public Backtest composition request."""
    request = copy.deepcopy(require_exact_fields(
        request,
        allowed=BACKTEST_REQUEST_FIELDS,
        required={
            "pipeline",
            "datasetId",
            "datasetVersionId",
            "sampler",
            "environment",
            "analysis",
        },
        label="Backtest request",
    ))
    references = (
        ("pipeline", "pipelineId", {"configOverride", "moduleConfigOverrides"}),
        ("environment", "environmentId", {"moduleConfigOverrides"}),
        ("analysis", "analysisId", {"moduleConfigOverrides"}),
    )
    for field, identity_field, override_fields in references:
        fields = {identity_field, "version", *override_fields}
        require_exact_fields(
            request[field],
            allowed=fields,
            required={identity_field, "version"},
            label=f"Backtest {field}",
        )
        for name in (identity_field, "version"):
            if not isinstance(request[field][name], str):
                raise ValueError(f"Backtest {field}.{name} must be a string.")
        if field == "pipeline" and "configOverride" in request[field]:
            request[field]["configOverride"] = normalize_resource_config_override(
                request[field]["configOverride"],
                label=f"Backtest {field}.configOverride",
            )
        if "moduleConfigOverrides" in request[field]:
            request[field]["moduleConfigOverrides"] = (
                normalize_module_config_overrides(
                    request[field]["moduleConfigOverrides"],
                    label=f"Backtest {field}.moduleConfigOverrides",
                )
            )
    require_exact_fields(
        request["sampler"],
        allowed={"samplerId", "version", "parameters"},
        required={"samplerId", "version", "parameters"},
        label="Backtest sampler",
    )
    if not isinstance(request["sampler"]["parameters"], dict):
        raise ValueError("Backtest sampler.parameters must be an object.")
    for name in ("samplerId", "version"):
        if not isinstance(request["sampler"][name], str):
            raise ValueError(f"Backtest sampler.{name} must be a string.")
    if "limit" in request and (
        isinstance(request["limit"], bool)
        or not isinstance(request["limit"], int)
        or request["limit"] < 1
    ):
        raise ValueError("Backtest limit must be a positive integer.")
    required_values = (
        ("datasetId", request["datasetId"]),
        ("datasetVersionId", request["datasetVersionId"]),
        ("pipeline.pipelineId", request["pipeline"]["pipelineId"]),
        ("sampler.samplerId", request["sampler"]["samplerId"]),
        ("sampler.version", request["sampler"]["version"]),
        ("environment.environmentId", request["environment"]["environmentId"]),
        ("analysis.analysisId", request["analysis"]["analysisId"]),
    )
    for label, value in required_values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Backtest {label} is required.")
    for resource in ("pipeline", "environment", "analysis"):
        if not request[resource]["version"].strip():
            raise ValueError(f"Backtest {resource}.version is required.")
    return request


def backtest_execution_inputs(request):
    """Return the unique normalized request representation signed by a snapshot."""
    normalized = normalize_backtest_request(request)
    execution_inputs = copy.deepcopy(normalized)
    execution_inputs["limit"] = normalized.get("limit")
    return execution_inputs


def backtest_evidence_digest(payload):
    """Return the historical canonical digest used by Backtest snapshots."""
    return "sha256:" + canonical_json_digest(payload)


def execution_snapshot_protocol_id(snapshot):
    """Project passive ownership from the exact top-level frozen resources."""

    if not isinstance(snapshot, dict):
        return None
    try:
        resources = (
            snapshot["datasetVersion"],
            snapshot["samplerDefinition"],
            snapshot["pipeline"]["definition"],
            snapshot["environmentDefinition"],
            snapshot["analysisDefinition"],
        )
    except (KeyError, TypeError):
        return None
    return common_protocol_id(resources)


__all__ = (
    "BACKTEST_EXECUTION_SNAPSHOT_FIELDS",
    "BACKTEST_EXECUTION_SNAPSHOT_SCHEMA_VERSION",
    "BACKTEST_REQUEST_FIELDS",
    "BACKTEST_RUNNER",
    "backtest_evidence_digest",
    "backtest_execution_inputs",
    "execution_snapshot_protocol_id",
    "normalize_backtest_request",
)
