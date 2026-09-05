"""Bounded, authority-checked projection of immutable Result archives."""

from __future__ import annotations

import tempfile

from engine.contracts import result as result_contracts
from engine.contracts import strict_json
from engine.contracts.contract_expansion import (
    expand_contracts,
    resolve_contract_path,
)
from engine.contracts.data_model import normalize_data_key_schema
from engine.contracts.data_path import (
    get_data_segments,
    set_data_segments,
    split_data_path,
)
from engine.runtime import lifecycle as runtime_lifecycle
from engine.runtime import module_invoker
from engine.runtime import result_stream


def _strict_json_equal(actual, expected):
    return strict_json.exact_equal(actual, expected)


def _result_metadata_validator(evidence, processor):
    def validate_metadata(
        metadata, *, cycle_count, first_cycle_id, last_cycle_id
    ):
        if (
            not _strict_json_equal(metadata["dataKeys"], evidence["dataKeys"])
            or not _strict_json_equal(
                metadata["executionChain"], evidence["executionChain"]
            )
            or not _strict_json_equal(metadata["metrics"], evidence["metrics"])
        ):
            raise ValueError(
                "Result archive content does not match its immutable metadata index."
            )
        result_contracts.require_metadata(
            metadata,
            cycle_count=cycle_count,
            first_cycle_id=first_cycle_id,
            last_cycle_id=last_cycle_id,
            execution_snapshot=evidence["request"]["executionSnapshot"],
            verified_cycle_validator=processor.base_validator,
        )
        if not _strict_json_equal(
            metadata, evidence["manifest"]["resultMetadata"]
        ):
            raise ValueError(
                "Result archive metadata does not exactly match its sealed manifest."
            )
        if not _strict_json_equal(
            evidence["manifest"]["catalog"]["metrics"], metadata["metrics"]
        ):
            raise ValueError(
                "Result archive catalog metrics do not match its content."
            )

    return validate_metadata


class ResultCycleProcessor:
    """Validate and optionally transform one streamed Result cycle at a time."""

    def __init__(self, data_keys, temporary_plan=None, *, verified_base_frames=False):
        self.data_keys = data_keys
        self.temporary_plan = temporary_plan
        self.verified_base_frames = verified_base_frames
        self.base_validator = result_contracts.compile_cycle_validator(data_keys)
        self.base_cycle_ids = (
            None
            if verified_base_frames
            else result_stream.UniqueTextIndex(
                prefix="trade-result-reader-identities-"
            )
        )
        self.final_validator = self.base_validator
        self.final_contracts = expand_contracts({
            data_key: normalize_data_key_schema(
                declaration["schema"], path=data_key
            )
            for data_key, declaration in data_keys.items()
        })
        self.execution_root = None
        self.invokers = []
        self.cycle_plan = []
        self.mutation_paths = ()
        self.finalized = False

    def __enter__(self):
        if self.temporary_plan is None:
            return self
        try:
            plan, contracts, required_roots = self.temporary_plan
            self.final_contracts = contracts
            self.final_validator = (
                None
                if self.verified_base_frames
                else result_contracts.compile_cycle_validator(
                    result_contracts.result_data_key_declarations(
                        contracts, required_roots
                    )
                )
            )
            self.mutation_paths = tuple(
                split_data_path(data_key)
                for node in plan
                for _port_name, data_key in node["outputPlan"]
            )
            self.execution_root = tempfile.TemporaryDirectory(
                prefix="trade-result-modules-"
            )
            for node in plan:
                invoker = module_invoker.ModuleInvoker.from_authority(
                    node["invocationAuthority"],
                    execution_root=self.execution_root.name,
                    namespace="result-modules",
                )
                self.invokers.append((node, invoker))
                self.cycle_plan.append((
                    node,
                    invoker,
                    tuple(
                        (port_name, data_key, split_data_path(data_key))
                        for port_name, data_key in node["inputPlan"]
                    ),
                    tuple(
                        (port_name, split_data_path(data_key))
                        for port_name, data_key in node["outputPlan"]
                    ),
                ))
        except BaseException:
            self.close(suppress_errors=True)
            raise
        return self

    def require_projection_paths(self, paths, *, top_level_fields=None):
        missing_contract = object()
        for path, parts in result_stream.normalize_projection_paths(
            paths,
            top_level_fields=top_level_fields,
        ):
            if parts[0] != "cycles" or len(parts) == 1:
                continue
            if parts[1] in {"schemaVersion", "cycleId", "decisionTime"}:
                if len(parts) != 2:
                    raise ValueError(
                        f"Result slice references unknown cycle path '{path}'."
                    )
                continue
            if parts[1] != "data":
                raise ValueError(
                    f"Result slice references unknown cycle path '{path}'."
                )
            if len(parts) == 2:
                continue
            data_key = ".".join(parts[2:])
            if resolve_contract_path(
                self.final_contracts, data_key, missing_contract
            ) is missing_contract:
                raise ValueError(
                    f"Result slice references unknown DataKey '{data_key}'."
                )

    def prepare_cycle(self, index, cycle):
        if not self.verified_base_frames:
            result_contracts.require_cycle(
                cycle, index, self.base_validator, self.base_cycle_ids
            )
        for node, invoker, input_plan, output_plan in self.cycle_plan:
            binding = node["binding"]
            input_ports = node["ports"]["inputs"]
            missing = object()
            current_values = {}
            for port_name, data_key, segments in input_plan:
                value = get_data_segments(cycle["data"], segments, missing)
                if value is missing:
                    if input_ports[port_name]["required"]:
                        raise ValueError(
                            f"Temporary Module '{binding['instanceId']}' input "
                            f"'{port_name}' cannot read DataKey '{data_key}'."
                        )
                    continue
                current_values[port_name] = value
            inputs_authority = module_invoker.seal_runtime_validated_module_inputs(
                node["invocationAuthority"],
                current_values,
            )
            outputs = invoker.invoke_validated(inputs_authority)
            for port_name, segments in output_plan:
                if port_name in outputs:
                    set_data_segments(cycle["data"], segments, outputs[port_name])
        # Streamed archives retain a distinct post-transform validator.  A
        # cached frame already carries the Worker's immutable base proof;
        # static Graph compatibility plus each Module's isolated output proof
        # composes the final contract without traversing every base DataKey.
        if (
            self.final_validator is not None
            and self.final_validator is not self.base_validator
        ):
            self.final_validator(cycle["data"])
        return cycle

    def copy_cached_cycle(self, cycle):
        """Copy only containers a temporary output can mutate.

        Cached frames were already validated and never escape this Worker.
        Module inputs are independently isolated by ModuleInvoker, so untouched
        JSON branches can be shared read-only across requests.
        """

        copied = dict(cycle)
        base_data = cycle["data"]
        copied_data = dict(base_data)
        copied["data"] = copied_data
        for parts in self.mutation_paths:
            target = copied_data
            for segment in parts[:-1]:
                target_child = target.get(segment) if type(target) is dict else None
                if type(target_child) is not dict:
                    break
                cloned = dict(target_child)
                target[segment] = cloned
                target = cloned
        return copied

    def finalize(self):
        if self.finalized:
            raise RuntimeError("Result cycle processor is already finalized.")
        runtime_lifecycle.invoke_all(
            (invoker for _node, invoker in reversed(self.invokers)),
            "finalize",
        )
        self.finalized = True

    def close(self, *, suppress_errors):
        first_error = None
        try:
            runtime_lifecycle.invoke_all(
                (invoker for _node, invoker in reversed(self.invokers)),
                "close",
                suppress_errors=False,
            )
        except BaseException as exc:
            first_error = first_error or exc
        self.invokers = []
        self.cycle_plan = []
        if self.base_cycle_ids is not None:
            try:
                self.base_cycle_ids.close()
            except BaseException as exc:
                first_error = first_error or exc
        if self.execution_root is not None:
            try:
                self.execution_root.cleanup()
            except BaseException as exc:
                first_error = first_error or exc
            self.execution_root = None
        if first_error is not None and not suppress_errors:
            raise first_error
        return first_error

    def __exit__(self, kind, value, _traceback):
        cleanup_error = self.close(suppress_errors=kind is not None)
        if value is not None and cleanup_error is not None:
            cleanup_error.__context__ = None
            value.__context__ = cleanup_error


def write_verified_result_projection(
    evidence,
    paths,
    destination_path,
    *,
    temporary_plan=None,
    capture_cycle=None,
    capture_metadata=None,
    projection_format="rows",
    window=None,
):
    """Verify one sealed archive while writing its bounded projection."""
    with ResultCycleProcessor(
        evidence["dataKeys"], temporary_plan
    ) as processor:
        processor.require_projection_paths(paths)

        return result_stream.write_projection(
            evidence["path"],
            destination_path,
            paths=paths,
            data_keys=evidence["dataKeys"],
            expected_digest=evidence["contentDigest"],
            expected_size=evidence["resultSize"],
            prepare_cycle=processor.prepare_cycle,
            finalize_cycles=processor.finalize,
            validate_metadata=_result_metadata_validator(evidence, processor),
            capture_cycle=capture_cycle,
            capture_metadata=capture_metadata,
            projection_format=projection_format,
            window=window,
        )


def write_verified_result_projection_from_frames(
    evidence,
    frames,
    paths,
    destination_path,
    *,
    temporary_plan=None,
    projection_format="rows",
    window=None,
):
    """Project cached verified frames through fresh Result Module instances."""

    if not isinstance(frames, dict) or set(frames) != {"cycles", "metadata"}:
        raise ValueError("Cached Result frames are invalid.")
    with ResultCycleProcessor(
        evidence["dataKeys"],
        temporary_plan,
        verified_base_frames=True,
    ) as processor:
        processor.require_projection_paths(paths)
        return result_stream.write_projection_from_cycles(
            frames["cycles"],
            frames["metadata"],
            destination_path,
            paths=paths,
            data_keys=evidence["dataKeys"],
            prepare_cycle=processor.prepare_cycle,
            finalize_cycles=processor.finalize,
            validate_metadata=_result_metadata_validator(evidence, processor),
            copy_cycle=processor.copy_cached_cycle,
            projection_format=projection_format,
            window=window,
        )


__all__ = (
    "ResultCycleProcessor",
    "write_verified_result_projection",
    "write_verified_result_projection_from_frames",
)
