"""Strict shape contracts for frozen Backtest composition artifacts."""

from __future__ import annotations

import copy

from engine.contracts.backtest import backtest_evidence_digest
from engine.contracts.contract_expansion import (
    expand_contracts,
    expanded_contract_root_paths,
)
from engine.contracts.data_model import normalize_data_key_schema
from engine.contracts.digest import is_sha256_digest
from engine.contracts.graph import validate_compiled_graph
from engine.contracts.module import require_exact_fields
from engine.contracts.pipeline import require_pipeline_plan


BACKTEST_COMPOSITION_ARTIFACT_SCHEMA_VERSION = 3
BACKTEST_COMPOSITION_ARTIFACT_FIELDS = frozenset({
    "schemaVersion", "pipelinePlan", "environmentPlan", "analysisPlan",
    "samplerContracts", "samplerRequiredRoots", "cycleContracts",
    "cycleRequiredRoots", "resultContracts", "resultRequiredRoots",
    "artifactHash",
})
def _require_contract_state(container, contracts_field, roots_field, *, label):
    contracts = container[contracts_field]
    if not isinstance(contracts, dict):
        raise ValueError(f"{label}.{contracts_field} must be an object.")
    normalized = expand_contracts({
        data_key: normalize_data_key_schema(
            schema, path=f"{label}.{contracts_field}.{data_key}"
        )
        for data_key, schema in contracts.items()
        if isinstance(data_key, str) and data_key
    })
    if len(normalized) != len(contracts) or normalized != contracts:
        raise ValueError(
            f"{label}.{contracts_field} must contain normalized expanded "
            "DataKey contracts."
        )
    required_roots = container[roots_field]
    if (
        not isinstance(required_roots, list)
        or any(not isinstance(root, str) or not root for root in required_roots)
        or required_roots != sorted(set(required_roots))
        or not set(required_roots) <= set(expanded_contract_root_paths(normalized))
    ):
        raise ValueError(
            f"{label}.{roots_field} must be sorted declared root DataKeys."
        )
    return copy.deepcopy(normalized), frozenset(required_roots)


def require_artifact(artifact):
    """Validate a frozen artifact without creating executable modules."""

    if not isinstance(artifact, dict):
        raise ValueError("Backtest composition artifact must be an object.")
    require_exact_fields(
        artifact,
        allowed=BACKTEST_COMPOSITION_ARTIFACT_FIELDS,
        required=BACKTEST_COMPOSITION_ARTIFACT_FIELDS,
        label="Backtest composition artifact",
    )
    if (
        type(artifact["schemaVersion"]) is not int
        or artifact["schemaVersion"] != BACKTEST_COMPOSITION_ARTIFACT_SCHEMA_VERSION
    ):
        raise ValueError(
            "Backtest composition artifact schemaVersion "
            f"{BACKTEST_COMPOSITION_ARTIFACT_SCHEMA_VERSION} is required."
        )
    expected_hash = artifact["artifactHash"]
    unsigned = {
        key: value for key, value in artifact.items() if key != "artifactHash"
    }
    actual_hash = backtest_evidence_digest(unsigned)
    if (
        not is_sha256_digest(expected_hash)
        or expected_hash != actual_hash
    ):
        raise ValueError("Backtest composition artifact hash is invalid.")
    require_pipeline_plan(
        artifact["pipelinePlan"],
        label="Backtest composition artifact pipelinePlan",
    )
    validate_compiled_graph(
        artifact["environmentPlan"],
        label="Backtest composition artifact Environment Graph",
    )
    validate_compiled_graph(
        artifact["analysisPlan"],
        label="Backtest composition artifact Analysis Graph",
    )
    for contracts_field, roots_field in (
        ("samplerContracts", "samplerRequiredRoots"),
        ("cycleContracts", "cycleRequiredRoots"),
        ("resultContracts", "resultRequiredRoots"),
    ):
        _require_contract_state(
            artifact,
            contracts_field,
            roots_field,
            label="Backtest composition artifact",
        )
    return copy.deepcopy(artifact)


__all__ = (
    "BACKTEST_COMPOSITION_ARTIFACT_FIELDS",
    "BACKTEST_COMPOSITION_ARTIFACT_SCHEMA_VERSION",
    "require_artifact",
)
