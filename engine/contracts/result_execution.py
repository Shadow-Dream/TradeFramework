"""Strict Result execution-chain and frozen-snapshot contracts."""

from __future__ import annotations

import copy
import math

from engine.contracts import strict_json
from engine.contracts.backtest import (
    BACKTEST_EXECUTION_SNAPSHOT_FIELDS,
    BACKTEST_EXECUTION_SNAPSHOT_SCHEMA_VERSION,
    backtest_evidence_digest,
    backtest_execution_inputs,
)
from engine.contracts.backtest_composition import require_artifact
from engine.contracts.contract_expansion import contract_root_paths
from engine.contracts.data_model import (
    normalize_data_key_schema,
    validate_normalized_json_value,
)
from engine.contracts.digest import is_sha256_digest
from engine.contracts.module import PROTOCOL_VERSION, require_exact_fields
from engine.contracts.observation_input import normalize_pipeline_config


def _nonnegative_number(value, label):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{label} must be a finite non-negative number.")
    return value


def _require_snapshot_value_match(actual, expected, *, label, message):
    """Preserve snapshot diagnostics while enforcing strict-JSON equality."""

    if strict_json.dumps(actual, sort_keys=True) != strict_json.dumps(
        expected,
        sort_keys=True,
    ):
        raise ValueError(message) from ValueError(
            f"{label} is not strict-JSON equal to its frozen value."
        )


def _require_observation_input(value):
    normalized = normalize_pipeline_config({"observationInput": value})[
        "observationInput"
    ]
    if normalized != value:
        raise ValueError("Result Pipeline.observationInput is not normalized.")
    return value


def _nonnegative_integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer.")
    return value


def _require_string_list(value, label):
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(f"{label} must contain unique non-empty strings.")
    return value


def _require_contract_map(value, label):
    require_exact_fields(
        value,
        allowed={"inputs", "outputs", "inputSources"},
        required={"inputs", "outputs"},
        label=label,
    )
    for direction in ("inputs", "outputs"):
        if not isinstance(value[direction], dict):
            raise ValueError(f"{label}.{direction} must be an object.")
        for data_key, schema in value[direction].items():
            if not isinstance(data_key, str) or not data_key:
                raise ValueError(f"{label}.{direction} contains an invalid DataKey.")
            normalize_data_key_schema(schema, path=data_key)
    input_sources = value.get("inputSources", {})
    if not isinstance(input_sources, dict):
        raise ValueError(f"{label}.inputSources must be an object.")
    for source, state in input_sources.items():
        if not isinstance(source, str) or not source:
            raise ValueError(f"{label}.inputSources contains an invalid source name.")
        require_exact_fields(
            state,
            allowed={"contracts", "requiredRoots"},
            required={"contracts", "requiredRoots"},
            label=f"{label}.inputSources.{source}",
        )
        if not isinstance(state["contracts"], dict):
            raise ValueError(
                f"{label}.inputSources.{source}.contracts must be an object."
            )
        for data_key, schema in state["contracts"].items():
            if not isinstance(data_key, str) or not data_key:
                raise ValueError(
                    f"{label}.inputSources.{source}.contracts contains an invalid DataKey."
                )
            normalize_data_key_schema(
                schema,
                path=f"{label}.inputSources.{source}.contracts.{data_key}",
            )
        required_roots = state["requiredRoots"]
        _require_string_list(
            required_roots,
            f"{label}.inputSources.{source}.requiredRoots",
        )
        unknown = sorted(
            set(required_roots) - set(contract_root_paths(state["contracts"]))
        )
        if unknown:
            raise ValueError(
                f"{label}.inputSources.{source}.requiredRoots contains undeclared "
                "root(s): " + ", ".join(unknown)
            )
    return value


def _require_transport(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    mode = value.get("runtimeMode")
    common = {
        "adapter", "runtimeMode", "invocationCount",
        "inputValidationSeconds", "outputValidationSeconds",
    }
    if mode == "in-process-python":
        fields = common | {
            "invokeSeconds", "inputCopySeconds", "sdkInvokeSeconds",
            "sdkInputValidationSeconds", "moduleComputeSeconds",
            "sdkOutputValidationSeconds",
        }
        time_fields = fields - {"adapter", "runtimeMode", "invocationCount"}
        integer_fields = {"invocationCount"}
    elif mode == "external-process":
        fields = common | {
            "protocolVersion", "requestCount", "requestBytes", "responseBytes",
            "commandCounts", "commandSeconds",
        }
        time_fields = {"inputValidationSeconds", "outputValidationSeconds"}
        integer_fields = {
            "invocationCount", "requestCount", "requestBytes", "responseBytes",
        }
    else:
        raise ValueError(f"{label}.runtimeMode is invalid.")
    require_exact_fields(value, allowed=fields, required=fields, label=label)
    if not isinstance(value["adapter"], str) or not value["adapter"]:
        raise ValueError(f"{label}.adapter is invalid.")
    for field in integer_fields:
        _nonnegative_integer(value[field], f"{label}.{field}")
    for field in time_fields:
        _nonnegative_number(value[field], f"{label}.{field}")
    if mode == "external-process":
        if value["protocolVersion"] != PROTOCOL_VERSION:
            raise ValueError(f"{label}.protocolVersion is invalid.")
        for field in ("commandCounts", "commandSeconds"):
            if not isinstance(value[field], dict):
                raise ValueError(f"{label}.{field} must be an object.")
        for command, count in value["commandCounts"].items():
            if not isinstance(command, str) or not command:
                raise ValueError(f"{label}.commandCounts contains an invalid command.")
            _nonnegative_integer(count, f"{label}.commandCounts.{command}")
        for command, seconds in value["commandSeconds"].items():
            if command not in value["commandCounts"]:
                raise ValueError(f"{label}.commandSeconds contains an unknown command.")
            _nonnegative_number(seconds, f"{label}.commandSeconds.{command}")
    return value


def _require_graph(value, *, label, identity_field, runtime_type):
    fields = {
        "type", "topology", "edges", "executionSeconds", "moduleDispatchSeconds",
        "graphOverheadSeconds", "moduleTransports", "dataKeyContract",
        identity_field, "version",
    }
    require_exact_fields(value, allowed=fields, required=fields, label=label)
    if value["type"] != runtime_type:
        raise ValueError(f"{label}.type is invalid.")
    for field in (identity_field, "version"):
        if not isinstance(value[field], str) or not value[field]:
            raise ValueError(f"{label}.{field} is invalid.")
    _require_string_list(value["topology"], f"{label}.topology")
    if not isinstance(value["edges"], list):
        raise ValueError(f"{label}.edges must be an array.")
    for index, edge in enumerate(value["edges"]):
        require_exact_fields(
            edge,
            allowed={"wire", "from", "to"},
            required={"wire", "from", "to"},
            label=f"{label}.edges[{index}]",
        )
        if not isinstance(edge["wire"], str) or not edge["wire"]:
            raise ValueError(f"{label}.edges[{index}].wire is invalid.")
        for endpoint in ("from", "to"):
            require_exact_fields(
                edge[endpoint],
                allowed={"node", "port", "schema"},
                required={"node", "port", "schema"},
                label=f"{label}.edges[{index}].{endpoint}",
            )
            if not isinstance(edge[endpoint]["node"], str) or not edge[endpoint]["node"]:
                raise ValueError(f"{label}.edges[{index}].{endpoint}.node is invalid.")
            if not isinstance(edge[endpoint]["port"], str) or not edge[endpoint]["port"]:
                raise ValueError(f"{label}.edges[{index}].{endpoint}.port is invalid.")
            normalize_data_key_schema(
                edge[endpoint]["schema"],
                path=f"{label}.edges[{index}].{endpoint}.schema",
            )
    for field in ("executionSeconds", "moduleDispatchSeconds", "graphOverheadSeconds"):
        _nonnegative_number(value[field], f"{label}.{field}")
    if not isinstance(value["moduleTransports"], dict) or set(
        value["moduleTransports"]
    ) != set(value["topology"]):
        raise ValueError(f"{label}.moduleTransports must exactly match topology.")
    for node_id, transport in value["moduleTransports"].items():
        _require_transport(transport, f"{label}.moduleTransports.{node_id}")
    _require_contract_map(value["dataKeyContract"], f"{label}.dataKeyContract")
    return value


def _require_pipeline(value):
    fields = {
        "pipelineId", "name", "version", "manifestHash", "nodes", "mode",
        "dataInterface", "dataKeyContract", "graphBoundaryTransfers",
        "executionSeconds", "moduleDispatchSeconds", "graphOverheadSeconds",
        "moduleTransports",
        "observationInput", "observationContractDigest",
    }
    require_exact_fields(value, allowed=fields, required=fields, label="Result Pipeline")
    for field in ("pipelineId", "name", "version", "manifestHash"):
        if not isinstance(value[field], str) or not value[field]:
            raise ValueError(f"Result Pipeline.{field} is invalid.")
    if value["mode"] != "per-cycle-data-dictionary":
        raise ValueError("Result Pipeline.mode is invalid.")
    if value["dataInterface"] != "declared-datakey-contracts":
        raise ValueError("Result Pipeline.dataInterface is invalid.")
    _require_string_list(value["nodes"], "Result Pipeline.nodes")
    _require_contract_map(value["dataKeyContract"], "Result Pipeline.dataKeyContract")
    _require_observation_input(value["observationInput"])
    if not is_sha256_digest(value["observationContractDigest"]):
        raise ValueError("Result Pipeline.observationContractDigest is invalid.")
    if not isinstance(value["graphBoundaryTransfers"], dict):
        raise ValueError("Result Pipeline.graphBoundaryTransfers must be an object.")
    for boundary, count in value["graphBoundaryTransfers"].items():
        if not isinstance(boundary, str) or not boundary:
            raise ValueError("Result Pipeline contains an invalid Graph boundary.")
        _nonnegative_integer(
            count, f"Result Pipeline.graphBoundaryTransfers.{boundary}"
        )
    for field in ("executionSeconds", "moduleDispatchSeconds", "graphOverheadSeconds"):
        _nonnegative_number(value[field], f"Result Pipeline.{field}")
    if not isinstance(value["moduleTransports"], dict) or set(
        value["moduleTransports"]
    ) != set(value["nodes"]):
        raise ValueError("Result Pipeline.moduleTransports must exactly match nodes.")
    for node_id, transport in value["moduleTransports"].items():
        _require_transport(transport, f"Result Pipeline.moduleTransports.{node_id}")
    return value


def _require_timings(value):
    require_exact_fields(
        value,
        allowed={
            "kernelPreparationSeconds", "graphBuildSeconds", "cyclePhasesSeconds",
            "cycleLoopSeconds", "resultWriter",
        },
        required={
            "kernelPreparationSeconds", "graphBuildSeconds", "cyclePhasesSeconds",
            "cycleLoopSeconds", "resultWriter",
        },
        label="Result timings",
    )
    for field in ("kernelPreparationSeconds", "graphBuildSeconds", "cycleLoopSeconds"):
        _nonnegative_number(value[field], f"Result timings.{field}")
    phase_fields = {
        "sampler", "environment", "pipeline", "previousDataProjection", "analysis",
        "resultProjection", "resultAppend", "finalize",
    }
    require_exact_fields(
        value["cyclePhasesSeconds"],
        allowed=phase_fields,
        required=phase_fields,
        label="Result timings.cyclePhasesSeconds",
    )
    for field in phase_fields:
        _nonnegative_number(
            value["cyclePhasesSeconds"][field],
            f"Result timings.cyclePhasesSeconds.{field}",
        )
    if value["cycleLoopSeconds"] != math.fsum(value["cyclePhasesSeconds"].values()):
        raise ValueError("Result timings.cycleLoopSeconds does not match its phases.")
    require_exact_fields(
        value["resultWriter"],
        allowed={"scope", "encodeSeconds", "writeSeconds", "encodedCycleCharacters"},
        required={"scope", "encodeSeconds", "writeSeconds", "encodedCycleCharacters"},
        label="Result timings.resultWriter",
    )
    if value["resultWriter"]["scope"] != "streamed-cycles":
        raise ValueError("Result timings.resultWriter.scope is invalid.")
    for field in ("encodeSeconds", "writeSeconds"):
        _nonnegative_number(
            value["resultWriter"][field], f"Result timings.resultWriter.{field}"
        )
    _nonnegative_integer(
        value["resultWriter"]["encodedCycleCharacters"],
        "Result timings.resultWriter.encodedCycleCharacters",
    )
    return value


def require_execution_chain(execution_chain):
    require_exact_fields(
        execution_chain,
        allowed={
            "snapshotHash", "dataset", "sampler", "environment",
            "pipeline", "analysis", "timings",
        },
        required={
            "snapshotHash", "dataset", "sampler", "environment",
            "pipeline", "analysis", "timings",
        },
        label="Result executionChain",
    )
    if not is_sha256_digest(execution_chain["snapshotHash"]):
        raise ValueError("Result executionChain.snapshotHash is invalid.")
    require_exact_fields(
        execution_chain["dataset"],
        allowed={"datasetId", "datasetVersionId", "contentHash"},
        required={"datasetId", "datasetVersionId", "contentHash"},
        label="Result Dataset",
    )
    for field in ("datasetId", "datasetVersionId"):
        if not isinstance(execution_chain["dataset"][field], str) or not execution_chain[
            "dataset"
        ][field]:
            raise ValueError(f"Result Dataset.{field} is invalid.")
    if not is_sha256_digest(execution_chain["dataset"]["contentHash"]):
        raise ValueError("Result Dataset.contentHash is invalid.")
    require_exact_fields(
        execution_chain["sampler"],
        allowed={"samplerId", "version", "name", "type", "parameters"},
        required={"samplerId", "version", "name", "type", "parameters"},
        label="Result Sampler",
    )
    for field in ("samplerId", "version", "name", "type"):
        if not isinstance(execution_chain["sampler"][field], str) or not execution_chain[
            "sampler"
        ][field]:
            raise ValueError(f"Result Sampler.{field} is invalid.")
    if not isinstance(execution_chain["sampler"]["parameters"], dict):
        raise ValueError("Result Sampler.parameters must be an object.")
    validate_normalized_json_value(
        execution_chain["sampler"]["parameters"],
        {},
        path="Result.Sampler.parameters",
    )
    _require_graph(
        execution_chain["environment"],
        label="Result Environment",
        identity_field="environmentId",
        runtime_type="EnvironmentGraph",
    )
    _require_pipeline(execution_chain["pipeline"])
    _require_graph(
        execution_chain["analysis"],
        label="Result Analysis",
        identity_field="analysisId",
        runtime_type="AnalysisGraph",
    )
    _require_timings(execution_chain["timings"])
    return execution_chain


def require_snapshot_match(execution_chain, execution_snapshot):
    require_exact_fields(
        execution_snapshot,
        allowed=BACKTEST_EXECUTION_SNAPSHOT_FIELDS,
        required=BACKTEST_EXECUTION_SNAPSHOT_FIELDS,
        label="Stored execution snapshot",
    )
    if (
        type(execution_snapshot["schemaVersion"]) is not int
        or execution_snapshot["schemaVersion"]
        != BACKTEST_EXECUTION_SNAPSHOT_SCHEMA_VERSION
    ):
        raise ValueError("Stored execution snapshot schemaVersion is invalid.")
    unsigned_snapshot = {
        key: value
        for key, value in execution_snapshot.items()
        if key != "snapshotHash"
    }
    if execution_snapshot["snapshotHash"] != backtest_evidence_digest(unsigned_snapshot):
        raise ValueError("Stored execution snapshot hash is invalid.")
    require_artifact(execution_snapshot["compositionArtifact"])
    execution_inputs = copy.deepcopy(execution_snapshot["executionInputs"])
    require_exact_fields(
        execution_inputs,
        allowed={
            "pipeline", "datasetId", "datasetVersionId", "sampler", "environment",
            "analysis", "limit",
        },
        required={
            "pipeline", "datasetId", "datasetVersionId", "sampler", "environment",
            "analysis", "limit",
        },
        label="Stored execution inputs",
    )
    normalized_inputs = copy.deepcopy(execution_inputs)
    if normalized_inputs["limit"] is None:
        normalized_inputs.pop("limit")
    _require_snapshot_value_match(
        execution_inputs,
        backtest_execution_inputs(normalized_inputs),
        label="Stored execution inputs",
        message="Stored execution inputs are invalid.",
    )
    expected_dataset = {
        "datasetId": execution_snapshot["datasetVersion"]["datasetId"],
        "datasetVersionId": execution_snapshot["datasetVersion"]["datasetVersionId"],
        "contentHash": execution_snapshot["datasetVersion"]["contentHash"],
    }
    if execution_chain["snapshotHash"] != execution_snapshot["snapshotHash"]:
        raise ValueError(
            "Result executionChain does not match its stored execution snapshot."
        )
    for actual, expected, label in (
        (execution_chain["dataset"], expected_dataset, "Result Dataset"),
        (
            execution_chain["sampler"]["parameters"],
            execution_inputs["sampler"]["parameters"],
            "Result Sampler parameters",
        ),
    ):
        _require_snapshot_value_match(
            actual,
            expected,
            label=label,
            message=(
                "Result executionChain does not match its stored execution "
                "snapshot."
            ),
        )
    identity_checks = (
        ("sampler", "samplerDefinition", "samplerId"),
        ("environment", "environmentDefinition", "environmentId"),
        ("analysis", "analysisDefinition", "analysisId"),
    )
    for chain_name, snapshot_name, identity_field in identity_checks:
        definition = execution_snapshot[snapshot_name]
        if (
            execution_chain[chain_name][identity_field] != definition[identity_field]
            or execution_chain[chain_name]["version"] != definition["version"]
        ):
            raise ValueError(
                f"Result {chain_name} identity does not match its snapshot."
            )
    frozen_pipeline = execution_snapshot["pipeline"]
    frozen_pipeline_plan = execution_snapshot["compositionArtifact"][
        "pipelinePlan"
    ]
    result_pipeline = execution_chain["pipeline"]
    if (
        execution_inputs["datasetId"] != expected_dataset["datasetId"]
        or execution_inputs["datasetVersionId"] != expected_dataset["datasetVersionId"]
        or execution_inputs["sampler"]["samplerId"]
        != execution_snapshot["samplerDefinition"]["samplerId"]
        or execution_inputs["sampler"]["version"]
        != execution_snapshot["samplerDefinition"]["version"]
        or execution_inputs["environment"]["environmentId"]
        != execution_snapshot["environmentDefinition"]["environmentId"]
        or execution_inputs["environment"]["version"]
        != execution_snapshot["environmentDefinition"]["version"]
        or execution_inputs["analysis"]["analysisId"]
        != execution_snapshot["analysisDefinition"]["analysisId"]
        or execution_inputs["analysis"]["version"]
        != execution_snapshot["analysisDefinition"]["version"]
        or execution_inputs["pipeline"]["pipelineId"] != frozen_pipeline["pipelineId"]
        or execution_inputs["pipeline"]["version"] != frozen_pipeline["version"]
        or result_pipeline["pipelineId"] != frozen_pipeline["pipelineId"]
        or result_pipeline["version"] != frozen_pipeline["version"]
        or result_pipeline["manifestHash"] != frozen_pipeline["manifestHash"]
    ):
        raise ValueError("Result Pipeline identity does not match its snapshot.")
    for actual, expected, label in (
        (result_pipeline["nodes"], frozen_pipeline_plan["topology"], "nodes"),
        (
            result_pipeline["observationInput"],
            frozen_pipeline_plan["observationInput"],
            "observationInput",
        ),
        (
            result_pipeline["observationContractDigest"],
            frozen_pipeline_plan["observationContractDigest"],
            "observationContractDigest",
        ),
        (
            result_pipeline["dataKeyContract"]["inputs"],
            frozen_pipeline_plan["inputContracts"],
            "input contracts",
        ),
        (
            result_pipeline["dataKeyContract"]["outputs"],
            frozen_pipeline_plan["outputContracts"],
            "output contracts",
        ),
    ):
        _require_snapshot_value_match(
            actual,
            expected,
            label=f"Result Pipeline {label}",
            message="Result Pipeline identity does not match its snapshot.",
        )
    return execution_chain


__all__ = (
    "require_execution_chain",
    "require_snapshot_match",
)
