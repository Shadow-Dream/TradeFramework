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
from engine.contracts.data_path import get_data_path, set_data_path
from engine.runtime import lifecycle as runtime_lifecycle
from engine.runtime import module_invoker
from engine.runtime import result_stream


def _strict_json_equal(actual, expected):
    return strict_json.exact_equal(actual, expected)


class ResultCycleProcessor:
    """Validate and optionally transform one streamed Result cycle at a time."""

    def __init__(self, data_keys, temporary_plan=None):
        self.data_keys = data_keys
        self.temporary_plan = temporary_plan
        self.base_validator = result_contracts.compile_cycle_validator(data_keys)
        self.base_cycle_ids = result_stream.UniqueTextIndex(
            prefix="trade-result-reader-identities-"
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
        self.finalized = False

    def __enter__(self):
        if self.temporary_plan is None:
            return self
        try:
            plan, contracts, required_roots = self.temporary_plan
            self.final_contracts = contracts
            self.final_validator = result_contracts.compile_cycle_validator(
                result_contracts.result_data_key_declarations(
                    contracts, required_roots
                )
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
        except BaseException:
            self.close(suppress_errors=True)
            raise
        return self

    def require_projection_paths(self, paths):
        missing_contract = object()
        for path, parts in result_stream.normalize_projection_paths(paths):
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
        result_contracts.require_cycle(
            cycle, index, self.base_validator, self.base_cycle_ids
        )
        for node, invoker in self.invokers:
            binding = node["binding"]
            input_ports = node["ports"]["inputs"]
            missing = object()
            current_values = {}
            for port_name, data_key in node["inputPlan"]:
                value = get_data_path(cycle["data"], data_key, missing)
                if value is missing:
                    if input_ports[port_name]["required"]:
                        raise ValueError(
                            f"Temporary Module '{binding['instanceId']}' input "
                            f"'{port_name}' cannot read DataKey '{data_key}'."
                        )
                    continue
                current_values[port_name] = value
            outputs = invoker.invoke(current_values)
            for port_name, data_key in node["outputPlan"]:
                if port_name in outputs:
                    set_data_path(cycle["data"], data_key, outputs[port_name])
        # With no temporary Module plan the base Result contract is already
        # the final contract.  Re-running the same compiled validator here
        # traversed every DataKey twice while proving nothing new.  Temporary
        # plans install a distinct final validator in __enter__ and retain the
        # required post-transform proof.
        if self.final_validator is not self.base_validator:
            self.final_validator(cycle["data"])
        return cycle

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
):
    """Verify one sealed archive while writing its bounded projection."""
    with ResultCycleProcessor(
        evidence["dataKeys"], temporary_plan
    ) as processor:
        processor.require_projection_paths(paths)

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

        return result_stream.write_projection(
            evidence["path"],
            destination_path,
            paths=paths,
            data_keys=evidence["dataKeys"],
            expected_digest=evidence["contentDigest"],
            expected_size=evidence["resultSize"],
            prepare_cycle=processor.prepare_cycle,
            finalize_cycles=processor.finalize,
            validate_metadata=validate_metadata,
        )


__all__ = ("ResultCycleProcessor", "write_verified_result_projection")
