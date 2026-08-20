"""Strict contracts for immutable Backtest Result data and evidence."""

from __future__ import annotations

import engine.contracts.result_execution as result_execution
from engine.contracts import backtest as backtest_contracts
from engine.contracts.contract_expansion import (
    expand_contracts,
    expanded_contract_path_required,
    expanded_contract_root_paths,
)
from engine.contracts.data import compile_data_json_validator
from engine.contracts.data_model import (
    normalize_data_key_schema,
    validate_normalized_json_value,
)
from engine.contracts.data_path import split_data_path
from engine.contracts.module import require_exact_fields
from engine.contracts.visualization import require_spec as require_visualization_spec


def result_data_key_declarations(contracts, required_roots):
    contracts = expand_contracts(contracts)
    required_roots = frozenset(required_roots)
    unknown_required = sorted(
        required_roots - expanded_contract_root_paths(contracts)
    )
    if unknown_required:
        raise ValueError(
            "Result required roots are not declared by its contracts: "
            + ", ".join(unknown_required)
        )
    declarations = {}
    for data_key, schema in contracts.items():
        declarations[data_key] = {
            "label": data_key,
            "schema": normalize_data_key_schema(schema, path=data_key),
            "required": expanded_contract_path_required(
                contracts, data_key, required_roots=required_roots
            ),
            "source": {"path": f"cycles.data.{data_key}"},
            "encoding": {
                "time": "decisionTime",
                "value": f"data.{data_key}",
            },
        }
    return declarations


def result_metadata_payload(result):
    require_exact_fields(
        result,
        allowed={
            "schemaVersion", "cycles", "dataKeys", "metrics",
            "executionChain", "sampleFrameContract",
        },
        required={
            "schemaVersion", "cycles", "dataKeys", "metrics",
            "executionChain", "sampleFrameContract",
        },
        label="Result metadata source",
    )
    if result["schemaVersion"] != 8 or not isinstance(result["cycles"], list):
        raise ValueError("Result metadata source uses an unsupported Result schema.")
    if not isinstance(result["dataKeys"], dict) or not isinstance(
        result["executionChain"], dict
    ):
        raise ValueError("Result metadata source has invalid indexed objects.")
    return {
        "schemaVersion": result["schemaVersion"],
        "hasCycles": True,
        "dataKeys": result["dataKeys"],
        "executionChain": result["executionChain"],
    }


def _nonnegative_integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer.")
    return value


def compile_cycle_validator(data_keys):
    if not isinstance(data_keys, dict):
        raise ValueError("Result dataKeys must be an object.")
    contracts = {}
    for data_key, declaration in data_keys.items():
        if not isinstance(data_key, str) or not data_key:
            raise ValueError("Result dataKeys contains an invalid DataKey path.")
        require_exact_fields(
            declaration,
            allowed={"label", "schema", "required", "source", "encoding"},
            required={"label", "schema", "required", "source", "encoding"},
            label=f"Result dataKeys.{data_key}",
        )
        if declaration["label"] != data_key:
            raise ValueError(f"Result DataKey '{data_key}' label does not match its path.")
        if declaration["source"] != {"path": f"cycles.data.{data_key}"}:
            raise ValueError(f"Result DataKey '{data_key}' has an invalid source.")
        if declaration["encoding"] != {
            "time": "decisionTime",
            "value": f"data.{data_key}",
        }:
            raise ValueError(f"Result DataKey '{data_key}' has an invalid encoding.")
        if not isinstance(declaration["required"], bool):
            raise ValueError(f"Result DataKey '{data_key}' required must be a boolean.")
        contracts[data_key] = normalize_data_key_schema(
            declaration["schema"], path=data_key
        )
    expanded_contracts = expand_contracts(contracts)
    required_roots = frozenset(
        data_key
        for data_key, declaration in data_keys.items()
        if len(split_data_path(data_key)) == 1 and declaration["required"]
    )
    for data_key, declaration in data_keys.items():
        if declaration["required"] != expanded_contract_path_required(
            expanded_contracts,
            data_key,
            required_roots=required_roots,
        ):
            raise ValueError(
                f"Result DataKey '{data_key}' required presence is inconsistent."
            )
    return compile_data_json_validator(
        expanded_contracts,
        required_paths=required_roots,
        contracts_expanded=True,
    )


def require_cycle(cycle, index, validate_cycle_data, cycle_ids):
    require_exact_fields(
        cycle,
        allowed={"schemaVersion", "cycleId", "decisionTime", "data"},
        required={"schemaVersion", "cycleId", "decisionTime", "data"},
        label=f"Result cycles[{index}]",
    )
    if cycle["schemaVersion"] != 3:
        raise ValueError(f"Result cycles[{index}] schemaVersion 3 is required.")
    cycle_id = cycle["cycleId"]
    if not isinstance(cycle_id, str) or not cycle_id:
        raise ValueError("Result cycleId values must be unique non-empty strings.")
    if hasattr(cycle_ids, "claim"):
        unique = cycle_ids.claim(cycle_id)
    else:
        unique = cycle_id not in cycle_ids
        if unique:
            cycle_ids.add(cycle_id)
    if not unique:
        raise ValueError("Result cycleId values must be unique non-empty strings.")
    if not isinstance(cycle["decisionTime"], str) or not cycle["decisionTime"]:
        raise ValueError(f"Result cycles[{index}].decisionTime is required.")
    validate_cycle_data(cycle["data"])
    return cycle


def require_metadata(
    metadata,
    *,
    cycle_count,
    first_cycle_id,
    last_cycle_id,
    execution_snapshot=None,
    verified_cycle_validator=None,
):
    require_exact_fields(
        metadata,
        allowed={
            "schemaVersion", "dataKeys", "metrics",
            "executionChain", "sampleFrameContract",
        },
        required={
            "schemaVersion", "dataKeys", "metrics",
            "executionChain", "sampleFrameContract",
        },
        label="Result metadata",
    )
    if metadata["schemaVersion"] != 8:
        raise ValueError(
            "This Result contract is archived and cannot be loaded by the current runtime."
        )
    if verified_cycle_validator is None:
        validate_cycle_data = compile_cycle_validator(metadata["dataKeys"])
    elif not callable(verified_cycle_validator):
        raise TypeError("Verified Result cycle validator must be callable.")
    else:
        # Internal streamed readers may reuse a validator compiled from an
        # exactly-equal immutable dataKeys index.  The caller owns that equality
        # proof; ordinary materialized Result validation always compiles here.
        validate_cycle_data = verified_cycle_validator
    require_exact_fields(
        metadata["metrics"],
        allowed={"cycleCount", "analysis"},
        required={"cycleCount"},
        label="Result metrics",
    )
    _nonnegative_integer(
        metadata["metrics"]["cycleCount"], "Result metrics.cycleCount"
    )
    if metadata["metrics"]["cycleCount"] != cycle_count:
        raise ValueError("Result metrics.cycleCount does not match cycles.")
    if "analysis" in metadata["metrics"]:
        if not isinstance(metadata["metrics"]["analysis"], dict):
            raise ValueError("Result metrics.analysis must be an object.")
        validate_normalized_json_value(
            metadata["metrics"]["analysis"], {}, path="Result.metrics.analysis"
        )
    execution_chain = result_execution.require_execution_chain(
        metadata["executionChain"]
    )
    sample_contract = metadata["sampleFrameContract"]
    require_exact_fields(
        sample_contract,
        allowed={
            "schemaVersion", "frameCount", "firstCycleId", "lastCycleId",
            "causalityRule",
        },
        required={
            "schemaVersion", "frameCount", "firstCycleId", "lastCycleId",
            "causalityRule",
        },
        label="Result sampleFrameContract",
    )
    if sample_contract["schemaVersion"] != 1:
        raise ValueError("Result sampleFrameContract schemaVersion 1 is required.")
    if (
        isinstance(sample_contract["frameCount"], bool)
        or not isinstance(sample_contract["frameCount"], int)
        or sample_contract["frameCount"] < 0
        or sample_contract["frameCount"] != cycle_count
    ):
        raise ValueError("Result sampleFrameContract.frameCount does not match cycles.")
    if sample_contract["causalityRule"] != (
        "Sampler owns decisionTime and as-of visibility"
    ):
        raise ValueError("Result sampleFrameContract.causalityRule is invalid.")
    for name in ("firstCycleId", "lastCycleId"):
        if sample_contract[name] is not None and (
            not isinstance(sample_contract[name], str) or not sample_contract[name]
        ):
            raise ValueError(f"Result sampleFrameContract.{name} is invalid.")
    if (
        sample_contract["firstCycleId"] != first_cycle_id
        or sample_contract["lastCycleId"] != last_cycle_id
    ):
        raise ValueError(
            "Result sampleFrameContract cycle boundaries do not match cycles."
        )
    if execution_snapshot is not None:
        result_execution.require_snapshot_match(
            execution_chain, execution_snapshot
        )
    return validate_cycle_data


def require_result(result, *, execution_snapshot=None):
    if not isinstance(result, dict) or result.get("schemaVersion") != 8:
        raise ValueError(
            "This Result contract is archived and cannot be loaded by the current runtime."
        )
    require_exact_fields(
        result,
        allowed={
            "schemaVersion", "cycles", "dataKeys", "metrics",
            "executionChain", "sampleFrameContract",
        },
        required={
            "schemaVersion", "cycles", "dataKeys", "metrics",
            "executionChain", "sampleFrameContract",
        },
        label="Result",
    )
    if not isinstance(result["cycles"], list):
        raise ValueError(
            "This Result contract is archived and cannot be loaded by the current runtime."
        )
    first_cycle_id = (
        result["cycles"][0].get("cycleId")
        if result["cycles"] and isinstance(result["cycles"][0], dict)
        else None
    )
    last_cycle_id = (
        result["cycles"][-1].get("cycleId")
        if result["cycles"] and isinstance(result["cycles"][-1], dict)
        else None
    )
    metadata = {key: value for key, value in result.items() if key != "cycles"}
    validate_cycle_data = require_metadata(
        metadata,
        cycle_count=len(result["cycles"]),
        first_cycle_id=first_cycle_id,
        last_cycle_id=last_cycle_id,
        execution_snapshot=execution_snapshot,
    )
    cycle_ids = set()
    for index, cycle in enumerate(result["cycles"]):
        require_cycle(cycle, index, validate_cycle_data, cycle_ids)
    return result


def require_catalog(catalog, *, backtest_id, result=None):
    require_exact_fields(
        catalog,
        allowed={
            "backtestId", "pipelineId", "datasetId", "name", "runner",
            "createdAt", "completedAt", "request", "metrics", "visualization",
        },
        required={
            "backtestId", "pipelineId", "datasetId", "name", "runner",
            "createdAt", "completedAt", "request", "metrics", "visualization",
        },
        label="Backtest Result catalog evidence",
    )
    for field in (
        "backtestId", "pipelineId", "datasetId", "name", "runner",
        "createdAt", "completedAt",
    ):
        if not isinstance(catalog[field], str) or not catalog[field]:
            raise ValueError(f"Backtest Result catalog {field} is required.")
    if catalog["backtestId"] != backtest_id:
        raise ValueError("Backtest Result catalog identity is invalid.")
    if catalog["runner"] != backtest_contracts.BACKTEST_RUNNER:
        raise ValueError("Backtest Result catalog runner is invalid.")
    if not isinstance(catalog["request"], dict) or not isinstance(
        catalog["metrics"], dict
    ):
        raise ValueError("Backtest Result catalog request and metrics must be objects.")
    require_visualization_spec(catalog["visualization"])
    request = catalog["request"]
    if (
        request.get("datasetId") != catalog["datasetId"]
        or request.get("pipeline", {}).get("pipelineId") != catalog["pipelineId"]
        or not isinstance(request.get("executionSnapshot"), dict)
    ):
        raise ValueError("Backtest Result catalog request identity is invalid.")
    if result is not None:
        if catalog["metrics"] != result["metrics"]:
            raise ValueError(
                "Backtest Result catalog metrics do not match its Result."
            )
        result_execution.require_snapshot_match(
            result["executionChain"], request["executionSnapshot"]
        )
    return catalog


__all__ = (
    "compile_cycle_validator",
    "require_catalog",
    "require_cycle",
    "require_metadata",
    "require_result",
    "result_data_key_declarations",
    "result_metadata_payload",
)
