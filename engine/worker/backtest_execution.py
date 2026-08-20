"""Pure execution kernel for one Engine-frozen Backtest snapshot."""

import copy
import math
import sys
from time import perf_counter

from engine.archive import backtest_result as backtest_result_archive
from engine.archive import dataset as dataset_archive
from engine.composition import backtest as backtest_composition
from engine.contracts import backtest as backtest_contracts
from engine.contracts import digest as digest_contracts
from engine.contracts import result as result_contracts
from engine.contracts import visualization as visualization_contracts
from engine.contracts.contract_expansion import (
    contract_expansion_cache_scope,
    contract_root_paths,
    expand_contracts,
)
from engine.contracts.data_path import (
    compile_data_path_plan,
    project_compiled_data_paths,
)
from engine.contracts.exact_fields import require_exact_fields
from engine.contracts.graph_cycle import CURRENT_PIPELINE_SOURCE
from engine.core import clock as engine_clock
from engine.core import resource_ids
from engine.runtime import backtest_provider as _backtest_provider
from engine.runtime import dataset as _dataset_runtime
from engine.runtime import lifecycle as runtime_lifecycle
from engine.runtime import sampler as _sampler_runtime
from engine.worker import backtest_preparation
from engine.worker import result_writer as result_writer_worker


BACKTEST_EXECUTION_EVIDENCE_FIELDS = frozenset({
    "backtestId",
    "cycleCount",
    "contentDigest",
    "resultSize",
})

BacktestResultPublicationUncertain = (
    result_writer_worker.BacktestResultPublicationUncertain
)


def require_backtest_execution_evidence(evidence, *, backtest_id=None):
    """Validate the exact private receipt exchanged with an Engine parent."""

    require_exact_fields(
        evidence,
        allowed=BACKTEST_EXECUTION_EVIDENCE_FIELDS,
        required=BACKTEST_EXECUTION_EVIDENCE_FIELDS,
        label="Backtest worker execution evidence",
    )
    if (
        not isinstance(evidence["backtestId"], str)
        or not evidence["backtestId"].startswith("bt_")
        or not resource_ids.is_resource_id(evidence["backtestId"])
        or (
            backtest_id is not None
            and evidence["backtestId"] != backtest_id
        )
    ):
        raise ValueError("Backtest worker evidence identity is invalid.")
    if (
        isinstance(evidence["cycleCount"], bool)
        or not isinstance(evidence["cycleCount"], int)
        or evidence["cycleCount"] < 0
    ):
        raise ValueError("Backtest worker evidence cycleCount is invalid.")
    if not digest_contracts.is_sha256_digest(evidence["contentDigest"]):
        raise ValueError("Backtest worker evidence contentDigest is invalid.")
    if (
        isinstance(evidence["resultSize"], bool)
        or not isinstance(evidence["resultSize"], int)
        or evidence["resultSize"] < 1
    ):
        raise ValueError("Backtest worker evidence resultSize is invalid.")
    return evidence


def _execute_backtest(
    config,
    request,
    *,
    backtest_id,
    execution_root,
    progress_callback=None,
):
    """Execute one frozen request and seal a Result without catalog access."""

    if not isinstance(request, dict) or "executionSnapshot" not in request:
        raise ValueError(
            "Backtest execution requires an explicitly frozen executionSnapshot."
        )
    if not isinstance(request["executionSnapshot"], dict) or not request["executionSnapshot"]:
        raise ValueError("Backtest executionSnapshot must be a non-empty Engine-owned object.")
    if execution_root is None:
        raise ValueError("Backtest execution_root must be explicit.")
    kernel_started = perf_counter()
    if (
        not isinstance(backtest_id, str)
        or not backtest_id.startswith("bt_")
        or not resource_ids.is_resource_id(backtest_id)
    ):
        raise ValueError("Backtest ID must be an Engine-issued Backtest resource ID.")
    require_exact_fields(
        request,
        allowed={
            "pipeline", "datasetId", "datasetVersionId", "sampler", "environment",
            "analysis", "limit", "executionSnapshot",
        },
        required={
            "pipeline", "datasetId", "datasetVersionId", "sampler", "environment",
            "analysis", "executionSnapshot",
        },
        label="Frozen Backtest request",
    )
    dataset_id = request["datasetId"]
    if not isinstance(dataset_id, str) or not dataset_id.strip():
        raise ValueError("Frozen Backtest datasetId must be a non-empty string.")
    pipeline_request = require_exact_fields(
        request["pipeline"],
        allowed={"pipelineId", "version"},
        required={"pipelineId", "version"},
        label="Frozen Backtest pipeline",
    )
    pipeline_id = pipeline_request["pipelineId"]
    pipeline_version = pipeline_request["version"]
    if (
        not isinstance(pipeline_id, str)
        or not pipeline_id.strip()
        or not isinstance(pipeline_version, str)
        or not pipeline_version.strip()
    ):
        raise ValueError("Frozen Backtest pipelineId and version must be non-empty strings.")
    execution_snapshot = request["executionSnapshot"]
    if not isinstance(execution_snapshot, dict) or not execution_snapshot:
        raise ValueError("Backtest execution requires a non-empty Engine-frozen snapshot.")
    require_exact_fields(
        execution_snapshot,
        allowed=backtest_contracts.BACKTEST_EXECUTION_SNAPSHOT_FIELDS,
        required=backtest_contracts.BACKTEST_EXECUTION_SNAPSHOT_FIELDS,
        label="Backtest execution snapshot",
    )
    if (
        type(execution_snapshot["schemaVersion"]) is not int
        or execution_snapshot["schemaVersion"]
        != backtest_contracts.BACKTEST_EXECUTION_SNAPSHOT_SCHEMA_VERSION
    ):
        raise ValueError(
            f"Backtest execution snapshot schemaVersion "
            f"{backtest_contracts.BACKTEST_EXECUTION_SNAPSHOT_SCHEMA_VERSION} is required."
        )
    if not isinstance(execution_snapshot["createdAt"], str) or not execution_snapshot["createdAt"]:
        raise ValueError("Backtest execution snapshot createdAt is required.")
    expected_snapshot_hash = execution_snapshot["snapshotHash"]
    unsigned_snapshot = {
        key: value for key, value in execution_snapshot.items() if key != "snapshotHash"
    }
    actual_snapshot_hash = backtest_contracts.backtest_evidence_digest(
        unsigned_snapshot
    )
    if (
        not isinstance(expected_snapshot_hash, str)
        or not digest_contracts.is_sha256_digest(expected_snapshot_hash)
        or expected_snapshot_hash != actual_snapshot_hash
    ):
        raise ValueError("Backtest execution snapshot hash is invalid.")
    expected_runtime_identity = execution_snapshot["engineRuntime"]
    # The process which will execute every Runtime performs the complete
    # identity check synchronously.  Keep this import after the snapshot hash
    # so malformed frozen evidence retains priority, but before preparation so
    # no authority or dynamic Runtime can be built under an unproved identity.
    from engine.core.runtime_identity import engine_runtime_identity

    if expected_runtime_identity != engine_runtime_identity():
        raise ValueError(
            "Backtest execution snapshot belongs to a different "
            "Engine/Python runtime."
        )

    prepared = backtest_preparation.prepare_backtest_execution(
        config,
        request,
        dataset_id=dataset_id,
        execution_snapshot=execution_snapshot,
        pipeline_id=pipeline_id,
        pipeline_version=pipeline_version,
    )

    analysis_definition = prepared.analysis_definition
    dataset_name = prepared.dataset_name
    dataset_version = prepared.dataset_version
    environment_definition = prepared.environment_definition
    sampler_definition = prepared.sampler_definition
    sampler_parameters = prepared.sampler_parameters
    sampler_runtime_authority = prepared.sampler_runtime_authority
    verified_composition = prepared.verified_composition
    verified_sampler_contracts = prepared.verified_sampler_contracts
    verified_sampler_required_roots = prepared.verified_sampler_required_roots
    dataset_handle = _dataset_runtime.create_dataset_handle(
        prepared.dataset_storage_authority
    )
    sampler = _sampler_runtime.create_verified_sampler_runtime(
        sampler_runtime_authority,
        dataset_handle,
        sampler_parameters,
        source_schema=backtest_composition.dataset_field_schema(dataset_version),
        execution_root=execution_root,
    )
    try:
        sampler_contracts = sampler.declared_output_contracts
        backtest_composition.require_canonical_value_match(
            expand_contracts(sampler_contracts),
            verified_sampler_contracts,
            label="Sampler Runtime declared contracts",
        )
        backtest_composition.require_canonical_value_match(
            sorted(contract_root_paths(sampler_contracts)),
            sorted(verified_sampler_required_roots),
            label="Sampler Runtime required contract roots",
        )
        provider = _backtest_provider.BacktestSampleProvider(
            dataset=dataset_handle,
            sampler=sampler,
            required_data_keys=contract_root_paths(sampler_contracts),
            max_frames=request["limit"] if "limit" in request else None,
        )
        if progress_callback:
            progress_callback(0, 0, "counting")
        total_cycles = len(provider)
        if progress_callback:
            progress_callback(0, total_cycles, "preparing")
    except BaseException:
        runtime_lifecycle.invoke_all((sampler,), "close", suppress_errors=True)
        raise
    request = dict(request)
    request["datasetVersionId"] = dataset_version["datasetVersionId"]
    request["sampler"] = {
        "samplerId": sampler_definition["samplerId"],
        "version": sampler_definition["version"],
        "parameters": sampler_parameters,
    }
    request["environment"] = {
        "environmentId": environment_definition["environmentId"],
        "version": environment_definition["version"],
    }
    request["analysis"] = {
        "analysisId": analysis_definition["analysisId"],
        "version": analysis_definition["version"],
    }
    runner = backtest_contracts.BACKTEST_RUNNER
    graph_build_started = perf_counter()
    try:
        (
            pipeline_runtime,
            environment,
            analysis_graph,
            data_contracts,
            data_required_roots,
        ) = (
            backtest_composition.create_backtest_graph_runtimes(
                execution_root=execution_root,
                verified_composition=verified_composition,
            )
        )
    except BaseException:
        runtime_lifecycle.invoke_all((provider,), "close", suppress_errors=True)
        raise
    graph_build_seconds = perf_counter() - graph_build_started
    result_writer = None
    try:
        pipeline_binding = pipeline_runtime.pipeline_binding
        name = f"{pipeline_binding.get('name') or 'Pipeline'} × {dataset_name}"
        result_directory = backtest_result_archive.archive_directory(
            config["releaseRoot"],
            backtest_id,
            label="Backtest Result directory",
        )
        result_path = result_directory / backtest_result_archive.RESULT_FILE_NAME
        result_writer = result_writer_worker.BacktestResultWriter(result_path)
        first_frame = None
        last_frame = None
        previous_data = {}
        previous_data_keys = tuple(dict.fromkeys((
            *environment.previous_data_keys,
            *analysis_graph.previous_data_keys,
        )))
        previous_data_plan = compile_data_path_plan(previous_data_keys)
        final_analysis = {}
        if progress_callback:
            progress_callback(0, total_cycles, "running")
    except BaseException:
        if result_writer is not None:
            result_writer.discard()
        runtime_lifecycle.invoke_all(
            (provider, analysis_graph, environment, pipeline_runtime),
            "close",
            suppress_errors=True,
        )
        raise
    phase_seconds = {
        "sampler": 0.0,
        "environment": 0.0,
        "pipeline": 0.0,
        "previousDataProjection": 0.0,
        "analysis": 0.0,
        "resultProjection": 0.0,
        "resultAppend": 0.0,
        "finalize": 0.0,
    }
    try:
        frames = iter(provider)
        while True:
            phase_started = perf_counter()
            try:
                frame = next(frames)
            except StopIteration:
                phase_seconds["sampler"] += perf_counter() - phase_started
                break
            phase_seconds["sampler"] += perf_counter() - phase_started
            sample = frame.data
            previous_cycle_data = previous_data
            phase_started = perf_counter()
            observation = environment.execute_observation(
                sample,
                previous_data,
                frame.decision_time,
            )
            phase_seconds["environment"] += perf_counter() - phase_started
            first_frame = first_frame or frame
            last_frame = frame
            phase_started = perf_counter()
            current_data = pipeline_runtime.execute_observation(observation)
            phase_seconds["pipeline"] += perf_counter() - phase_started
            phase_started = perf_counter()
            previous_data = project_compiled_data_paths(
                current_data,
                previous_data_plan,
                # The snapshot owns a fresh path tree while selected leaves
                # remain shared read-only.  Every later Graph/Analysis write
                # uses copy-on-write and every Module input is isolated, so a
                # second full value copy would add per-cycle work without
                # strengthening the ownership boundary.
                isolate_values=False,
            )
            phase_seconds["previousDataProjection"] += perf_counter() - phase_started
            phase_started = perf_counter()
            analysis_source_data = (
                {CURRENT_PIPELINE_SOURCE: current_data}
                if CURRENT_PIPELINE_SOURCE in analysis_graph.source_data_keys
                else {}
            )
            analysis_outputs = analysis_graph.execute_into(
                sample,
                previous_cycle_data,
                frame.decision_time,
                current_data,
                source_data=analysis_source_data,
            )
            phase_seconds["analysis"] += perf_counter() - phase_started
            final_analysis = analysis_outputs
            phase_started = perf_counter()
            cycle = {
                "schemaVersion": 3,
                "cycleId": frame.cycle_id,
                "decisionTime": frame.decision_time,
                "data": current_data,
            }
            phase_seconds["resultProjection"] += perf_counter() - phase_started
            phase_started = perf_counter()
            result_writer.append(cycle)
            phase_seconds["resultAppend"] += perf_counter() - phase_started
            if progress_callback:
                progress_callback(result_writer.count, total_cycles, "running")
        if result_writer.count != total_cycles:
            raise RuntimeError(
                "Sampler length contract mismatch: "
                f"declared {total_cycles} cycle(s), emitted "
                f"{result_writer.count}."
            )
        if progress_callback:
            progress_callback(result_writer.count, total_cycles, "finalizing")
        phase_started = perf_counter()
        runtime_lifecycle.invoke_all(
            (pipeline_runtime, environment, analysis_graph), "finalize"
        )
        phase_seconds["finalize"] += perf_counter() - phase_started
    except BaseException:
        result_writer.discard()
        raise
    finally:
        try:
            runtime_lifecycle.invoke_all(
                (provider, analysis_graph, environment, pipeline_runtime),
                "close",
                suppress_errors=sys.exc_info()[0] is not None,
            )
        except BaseException:
            result_writer.discard()
            raise
    try:
        # The timing snapshot below must include the last sub-threshold batch;
        # finish() may still flush defensively, but it should normally be empty.
        result_writer.flush_cycles()
    except BaseException:
        result_writer.discard()
        raise
    try:
        metrics = {"cycleCount": result_writer.count}
        if final_analysis:
            metrics["analysis"] = copy.deepcopy(final_analysis)
        result_fields = {
            "schemaVersion": 8,
            "dataKeys": result_contracts.result_data_key_declarations(
                data_contracts, data_required_roots
            ),
            "metrics": metrics,
        }
        result_fields["executionChain"] = {
            "snapshotHash": execution_snapshot["snapshotHash"],
            "dataset": {
                "datasetId": dataset_id,
                "datasetVersionId": dataset_version["datasetVersionId"],
                "contentHash": dataset_version["contentHash"],
            },
            "sampler": {
                "samplerId": sampler_definition["samplerId"],
                "version": sampler_definition["version"],
                "name": sampler_definition["name"],
                "type": sampler_definition["type"],
                "parameters": sampler_parameters,
            },
            "environment": environment.metadata(),
            "pipeline": {**pipeline_binding, **pipeline_runtime.metadata()},
            "analysis": analysis_graph.metadata(),
            "timings": {
                "kernelPreparationSeconds": graph_build_started - kernel_started,
                "graphBuildSeconds": graph_build_seconds,
                "cyclePhasesSeconds": phase_seconds,
                "cycleLoopSeconds": math.fsum(phase_seconds.values()),
                "resultWriter": {
                    "scope": "streamed-cycles",
                    "encodeSeconds": result_writer.encode_seconds,
                    "writeSeconds": result_writer.write_seconds,
                    "encodedCycleCharacters": result_writer.encoded_characters,
                },
            },
        }
        result_fields["sampleFrameContract"] = {
            "schemaVersion": 1,
            "frameCount": result_writer.count,
            "firstCycleId": first_frame.cycle_id if first_frame else None,
            "lastCycleId": last_frame.cycle_id if last_frame else None,
            "causalityRule": "Sampler owns decisionTime and as-of visibility",
        }
        visualization = visualization_contracts.default_spec(
            dataset_version["datasetId"],
            dataset_archive.visualization_time_zone(
                dataset_version["capabilities"]
            ),
        )
        now = engine_clock.utc_now()
    except BaseException:
        result_writer.discard()
        raise
    try:
        catalog = {
            "backtestId": backtest_id,
            "pipelineId": pipeline_id,
            "datasetId": dataset_id,
            "name": name,
            "runner": runner,
            "createdAt": now,
            "completedAt": now,
            # ResultWriter.finish() synchronously encodes these local frozen
            # values and retains no references after it returns.
            "request": request,
            "metrics": metrics,
            "visualization": visualization,
        }
        result_writer.finish(result_fields, catalog)
    except BaseException:
        if not result_writer.finished and not result_writer.published:
            result_writer.discard()
        raise
    return require_backtest_execution_evidence({
        "backtestId": backtest_id,
        "cycleCount": result_writer.count,
        "contentDigest": result_writer.content_digest,
        "resultSize": result_writer.result_size,
    }, backtest_id=backtest_id)


def execute_backtest(
    config,
    request,
    *,
    backtest_id,
    progress_callback=None,
    execution_root,
):
    """Execute one frozen Backtest with request-scoped compiler memoization."""
    with contract_expansion_cache_scope():
        return _execute_backtest(
            config,
            request,
            backtest_id=backtest_id,
            progress_callback=progress_callback,
            execution_root=execution_root,
        )


__all__ = (
    "BACKTEST_EXECUTION_EVIDENCE_FIELDS",
    "BacktestResultPublicationUncertain",
    "execute_backtest",
    "require_backtest_execution_evidence",
)
