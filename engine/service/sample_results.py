"""First-class Engine materialization of Dataset + Sampler DataKey timelines."""

from __future__ import annotations

import copy
import fcntl
import tempfile
from pathlib import Path

from engine.authority import dataset as dataset_authority
from engine.authority import sampler as sampler_authority
from engine.composition import backtest as backtest_composition
from engine.contracts import result as result_contracts
from engine.contracts import sample_result as sample_result_contracts
from engine.contracts import strict_json
from engine.contracts.contract_expansion import contract_root_paths, expand_contracts
from engine.contracts.digest import canonical_json_digest
from engine.contracts.sampler import canonical_sampler_parameters
from engine.core.runtime_identity import engine_runtime_identity
from engine.repository import datasets as dataset_repository
from engine.repository import sample_results as sample_result_repository
from engine.repository import samplers as sampler_repository
from engine.runtime import backtest_provider
from engine.runtime import dataset as dataset_runtime
from engine.runtime import lifecycle as runtime_lifecycle
from engine.runtime import sampler as sampler_runtime
from engine.worker.sample_result_writer import SampleResultWriter


def _require_request(value):
    if not isinstance(value, dict) or set(value) != {
        "datasetId",
        "datasetVersionId",
        "sampler",
    }:
        raise ValueError(
            "Sample Result request requires exactly datasetId, datasetVersionId and sampler."
        )
    for field in ("datasetId", "datasetVersionId"):
        if not isinstance(value[field], str) or not value[field]:
            raise ValueError(f"Sample Result request {field} is required.")
    sampler = value["sampler"]
    if not isinstance(sampler, dict) or set(sampler) != {
        "samplerId",
        "version",
        "parameters",
    }:
        raise ValueError(
            "Sample Result sampler requires samplerId, version and parameters."
        )
    for field in ("samplerId", "version"):
        if not isinstance(sampler[field], str) or not sampler[field]:
            raise ValueError(f"Sample Result sampler {field} is required.")
    if not isinstance(sampler["parameters"], dict):
        raise ValueError("Sample Result sampler parameters must be an object.")
    return copy.deepcopy(value)


def _resolve(config, request):
    request = _require_request(request)
    dataset_version = dataset_repository.ensure_dataset_version(
        config,
        request["datasetId"],
        request["datasetVersionId"],
    )
    sampler_definition = sampler_repository.get_sampler_execution_version(
        config,
        request["sampler"]["samplerId"],
        request["sampler"]["version"],
    )
    authority = sampler_authority.verify_managed_sampler_runtime_bundle_authority(
        config["releaseRoot"],
        sampler_definition,
        expected_identity=request["sampler"]["samplerId"],
        expected_version=request["sampler"]["version"],
    )
    required_capabilities = frozenset(
        sampler_authority.verified_sampler_required_capabilities(authority)
    )
    missing = sorted(required_capabilities - set(dataset_version["capabilities"]))
    if missing:
        raise ValueError(
            "Sampler requires Dataset capability/capabilities: " + ", ".join(missing)
        )
    dataset_version, storage_authority = (
        dataset_authority.verify_dataset_version_storage_authority(
            config["releaseRoot"],
            dataset_version,
            semantic_capabilities=required_capabilities,
        )
    )
    effective_parameters = canonical_sampler_parameters({
        **copy.deepcopy(sampler_definition["config"]),
        **copy.deepcopy(request["sampler"]["parameters"]),
    })
    contracts = sampler_authority.resolve_verified_sampler_output_contracts(
        authority,
        effective_parameters,
        backtest_composition.dataset_field_schema(dataset_version),
    )
    required_roots = frozenset(contract_root_paths(contracts))
    data_keys = result_contracts.result_data_key_declarations(
        contracts,
        required_roots,
    )
    unsigned_execution = {
        "schemaVersion": sample_result_contracts.SAMPLE_RESULT_SCHEMA_VERSION,
        "engineRuntime": engine_runtime_identity(),
        "dataset": {
            "datasetId": dataset_version["datasetId"],
            "datasetVersionId": dataset_version["datasetVersionId"],
            "contentHash": dataset_version["contentHash"],
        },
        "sampler": {
            "samplerId": sampler_definition["samplerId"],
            "version": sampler_definition["version"],
            "contentDigest": sampler_definition["contentDigest"],
            "parameters": effective_parameters,
        },
    }
    sample_result_id = "sha256:" + canonical_json_digest(unsigned_execution)
    execution = {
        "schemaVersion": sample_result_contracts.SAMPLE_RESULT_SCHEMA_VERSION,
        "sampleResultId": sample_result_id,
        **{key: value for key, value in unsigned_execution.items() if key != "schemaVersion"},
    }
    sample_result_contracts.require_execution(
        execution,
        sample_result_id=sample_result_id,
    )
    return {
        "sampleResultId": sample_result_id,
        "datasetVersion": dataset_version,
        "datasetStorageAuthority": storage_authority,
        "samplerDefinition": sampler_definition,
        "samplerAuthority": authority,
        "parameters": effective_parameters,
        "contracts": contracts,
        "requiredRoots": required_roots,
        "dataKeys": data_keys,
        "execution": execution,
    }


def describe_sample_result(config, request):
    resolved = _resolve(config, request)
    sample_result_id = resolved["sampleResultId"]
    if not sample_result_repository.sample_result_exists(config, sample_result_id):
        return {
            "sampleResultId": sample_result_id,
            "ready": False,
            "view": None,
        }
    return {
        "sampleResultId": sample_result_id,
        "ready": True,
        "view": sample_result_repository.sample_result_view(
            config,
            sample_result_id,
        ),
    }


def _lock_path(config, sample_result_id):
    root = Path(config["releaseRoot"]) / "_sample_results" / ".locks"
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Sample Result lock root is invalid.")
    return root / f"{sample_result_id.removeprefix('sha256:')}.lock"


def materialize_sample_result(config, request):
    """Return one exact cached timeline, building it once when absent."""

    resolved = _resolve(config, request)
    sample_result_id = resolved["sampleResultId"]
    with _lock_path(config, sample_result_id).open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if sample_result_repository.sample_result_exists(config, sample_result_id):
            return {
                "cache": {"hit": True},
                "view": sample_result_repository.sample_result_view(
                    config,
                    sample_result_id,
                ),
            }
        dataset = dataset_runtime.create_dataset_handle(
            resolved["datasetStorageAuthority"]
        )
        with tempfile.TemporaryDirectory(prefix="trade-sample-runtime-") as execution_root:
            sampler = sampler_runtime.create_verified_sampler_runtime(
                resolved["samplerAuthority"],
                dataset,
                resolved["parameters"],
                source_schema=backtest_composition.dataset_field_schema(
                    resolved["datasetVersion"]
                ),
                execution_root=execution_root,
            )
            try:
                if strict_json.dumps(
                    expand_contracts(sampler.declared_output_contracts), sort_keys=True
                ) != strict_json.dumps(
                    expand_contracts(resolved["contracts"]), sort_keys=True
                ):
                    raise ValueError(
                        "Sample Result Sampler Runtime contracts changed after verification."
                    )
                provider = backtest_provider.BacktestSampleProvider(
                    dataset=dataset,
                    sampler=sampler,
                    required_data_keys=resolved["requiredRoots"],
                )
            except BaseException:
                runtime_lifecycle.invoke_all((sampler,), "close", suppress_errors=True)
                raise
            writer = SampleResultWriter(
                config["releaseRoot"],
                sample_result_id,
                resolved["dataKeys"],
            )
            try:
                for frame in provider:
                    writer.append({
                        "schemaVersion": 3,
                        "cycleId": frame.cycle_id,
                        "decisionTime": frame.decision_time,
                        "data": frame.data,
                    })
                metadata = {
                    "schemaVersion": sample_result_contracts.SAMPLE_RESULT_SCHEMA_VERSION,
                    "dataKeys": resolved["dataKeys"],
                    "execution": resolved["execution"],
                    "sampleFrameContract": {
                        "schemaVersion": 1,
                        "frameCount": writer.count,
                        "firstCycleId": writer.first_cycle_id,
                        "lastCycleId": writer.last_cycle_id,
                        "causalityRule": "Sampler owns decisionTime and as-of visibility",
                    },
                }
                writer.finish(metadata)
            except BaseException:
                writer.discard()
                runtime_lifecycle.invoke_all(
                    (provider,),
                    "close",
                    suppress_errors=True,
                )
                raise
            else:
                runtime_lifecycle.invoke_all(
                    (provider,),
                    "close",
                    suppress_errors=False,
                )
        return {
            "cache": {"hit": False},
            "view": sample_result_repository.sample_result_view(
                config,
                sample_result_id,
            ),
        }


def get_sample_result(config, sample_result_id):
    return sample_result_repository.sample_result_view(config, sample_result_id)


__all__ = (
    "describe_sample_result",
    "get_sample_result",
    "materialize_sample_result",
)
