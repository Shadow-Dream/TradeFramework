"""Canonical public Backtest request and execution-evidence contracts."""

from __future__ import annotations

import copy

from engine.contracts.digest import canonical_json_digest
from engine.contracts.module import require_exact_fields


BACKTEST_REQUEST_FIELDS = frozenset({
    "pipeline",
    "datasetId",
    "datasetVersionId",
    "sampler",
    "environment",
    "analysis",
    "limit",
})
BACKTEST_EXECUTION_SNAPSHOT_SCHEMA_VERSION = 12
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
        ("pipeline", {"pipelineId", "version"}),
        ("environment", {"environmentId", "version"}),
        ("analysis", {"analysisId", "version"}),
    )
    for field, fields in references:
        require_exact_fields(
            request[field],
            allowed=fields,
            required=fields,
            label=f"Backtest {field}",
        )
        for name in fields:
            if not isinstance(request[field][name], str):
                raise ValueError(f"Backtest {field}.{name} must be a string.")
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


__all__ = (
    "BACKTEST_EXECUTION_SNAPSHOT_FIELDS",
    "BACKTEST_EXECUTION_SNAPSHOT_SCHEMA_VERSION",
    "BACKTEST_REQUEST_FIELDS",
    "BACKTEST_RUNNER",
    "backtest_evidence_digest",
    "backtest_execution_inputs",
    "normalize_backtest_request",
)
