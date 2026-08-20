#!/usr/bin/env python3
"""Authority-bound lifecycle and value boundary for one Module instance."""

from __future__ import annotations

import copy
import tempfile
import time
from collections.abc import Mapping

from engine.authority.module_invocation import (
    module_invocation_material,
    require_module_invocation_authority,
)
from engine.contracts import strict_json
from engine.contracts.data import (
    compile_normalized_json_isolator,
    compile_normalized_json_validator,
)
from engine.contracts.data_model import port_schema, validate_normalized_json_value
from engine.contracts.module import (
    require_exact_fields,
)
from engine.runtime.module_adapter import (
    module_adapter_material,
    prepare_module_adapter_authority,
)
from engine.runtime.module_implementation import materialize_verified_module_definition
from engine.runtime.process_module_adapter import create_process_module_adapter
from engine.runtime.python_module_adapter import create_python_module_adapter
from engine.runtime.reusable_output import (
    ReusableOutputOwner,
    is_reusable_output_receipt,
)

try:
    import orjson as _orjson
except ImportError:  # The recursive exact-JSON clone remains the portable path.
    _orjson = None


__all__ = ("ModuleInvoker",)


_VALIDATED_MODULE_INPUTS_TOKEN = object()


class _ValidatedModuleInputs:
    """One-shot proof that a parent Runtime validated this invocation's wires."""

    __slots__ = ("_authority", "_consumed", "_inputs")

    def __init__(self, authority, inputs, *, _token):
        if _token is not _VALIDATED_MODULE_INPUTS_TOKEN:
            raise TypeError("Validated Module inputs are Engine-owned.")
        self._authority = authority
        self._inputs = dict(inputs)
        self._consumed = False

    def _consume(self, authority):
        if self._authority is not authority:
            raise TypeError(
                "Validated Module inputs do not match this invocation authority."
            )
        if self._consumed:
            raise RuntimeError("Validated Module inputs have already been consumed.")
        self._consumed = True
        inputs = self._inputs
        self._inputs = None
        return inputs


def seal_runtime_validated_module_inputs(authority, inputs):
    """Internal bridge from an authority-bound parent Runtime.

    The bridge deliberately performs no value validation.  Its production
    callers are mechanically restricted to Runtimes which first prove every
    source wire against the compiled Graph plan.  The resulting proof is bound
    to one invocation authority and can be consumed only once.
    """

    require_module_invocation_authority(authority)
    if type(inputs) is not dict:
        raise TypeError("Validated Module inputs must be an exact object.")
    return _ValidatedModuleInputs(
        authority,
        inputs,
        _token=_VALIDATED_MODULE_INPUTS_TOKEN,
    )


def _isolate_validated_json_tree_recursive(value):
    """Copy a proven JSON tree with the same alias semantics as strict invoke.

    The compiled Python input isolator intentionally creates a new container
    for every JSON path, even when two paths in the caller refer to the same
    Python object.  The validated Graph path must preserve that observable
    Module contract while avoiding a second schema traversal.
    """
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if type(value) is dict:
        return {
            name: _isolate_validated_json_tree_recursive(child)
            for name, child in value.items()
        }
    if type(value) is list:
        return [_isolate_validated_json_tree_recursive(child) for child in value]
    raise TypeError("Validated Module inputs contain a non-JSON value.")


def _isolate_validated_json_tree(value):
    """Detach one Graph-proven JSON tree without repeating schema validation.

    The one-shot Graph authority has already proved that every value is exact
    finite JSON.  An orjson round trip is therefore only an ownership transfer,
    not a validation shortcut.  It also deliberately splits shared source
    containers at every JSON path, matching the established Python-Module
    input contract.  Values outside orjson's portable domain (notably integers
    wider than 64 bits) retain the exact recursive implementation.
    """

    if _orjson is not None:
        try:
            isolated = _orjson.loads(_orjson.dumps(value))
            if type(isolated) is dict:
                return isolated
        except (TypeError, ValueError, OverflowError, RecursionError):
            pass
    return _isolate_validated_json_tree_recursive(value)


def _port_maps(ports):
    inputs = ports["inputs"]
    outputs = ports["outputs"]
    if not isinstance(inputs, Mapping) or not isinstance(outputs, Mapping):
        raise ValueError("Module ports must contain input and output objects.")
    return dict(inputs), dict(outputs)


def _validate_and_isolate_inputs(
    inputs,
    ports,
    label,
    schemas=None,
    validators=None,
    isolator=None,
):
    if not isinstance(inputs, Mapping):
        raise ValueError(f"Module '{label}' inputs must be an object.")
    if isolator is not None:
        return isolator(dict(inputs))
    declared, _ = _port_maps(ports)
    unknown = sorted(set(inputs) - set(declared))
    if unknown:
        raise ValueError(
            f"Module '{label}' received undeclared input port(s): {', '.join(unknown)}."
        )
    missing = sorted(
        name
        for name, spec in declared.items()
        if spec.get("required", True) and name not in inputs
    )
    if missing:
        raise ValueError(
            f"Module '{label}' omitted required input port(s): {', '.join(missing)}."
        )
    isolated = dict(inputs)
    schemas = schemas or {name: port_schema(port) for name, port in declared.items()}
    for name, value in isolated.items():
        if validators is not None:
            validators[name](value)
        else:
            validate_normalized_json_value(
                value,
                schemas[name],
                path=f"{label}.inputs.{name}",
                trusted_json=True,
            )
    return isolated


def _validate_and_isolate_outputs(
    outputs,
    ports,
    label,
    schemas=None,
    validators=None,
    isolators=None,
):
    if not isinstance(outputs, Mapping):
        raise ValueError(f"Module '{label}' outputs must be an object.")
    _, declared = _port_maps(ports)
    unknown = sorted(set(outputs) - set(declared))
    if unknown:
        raise ValueError(
            f"Module '{label}' returned undeclared output port(s): {', '.join(unknown)}."
        )
    missing = sorted(
        name
        for name, spec in declared.items()
        if spec.get("required", True) and name not in outputs
    )
    if missing:
        raise ValueError(
            f"Module '{label}' omitted required output port(s): {', '.join(missing)}."
        )
    isolated = {}
    schemas = schemas or {name: port_schema(port) for name, port in declared.items()}
    for name, value in outputs.items():
        if isolators is not None:
            isolated[name] = isolators[name](value)
        elif validators is not None:
            validators[name](value)
            isolated[name] = value
        else:
            validate_normalized_json_value(
                value,
                schemas[name],
                path=f"{label}.outputs.{name}",
                trusted_json=True,
            )
            isolated[name] = value
    return isolated


class ModuleInvoker:
    """Validate and isolate both sides of one stateful Module instance."""

    __slots__ = (
        "_binding",
        "_lifecycle_state",
        "_invocation_count",
        "_owned_execution_root",
        "_definition",
        "_label",
        "_ports",
        "_port_schemas",
        "_port_validators",
        "_port_isolators",
        "_input_isolator",
        "_input_validation_seconds",
        "_output_validation_seconds",
        "_adapter",
        "_adapter_authority",
        "_invocation_authority",
        "_reusable_output_owner",
    )

    def __init__(self, *_args, **_kwargs):
        raise TypeError(
            "Module Runtime requires an Engine-owned verified Definition authority."
        )

    @classmethod
    def from_authority(
        cls,
        authority,
        *,
        execution_root=None,
        namespace="modules",
    ):
        """Create an invoker from a proof verified in this Engine call stack."""
        runtime = cls.__new__(cls)
        runtime._initialize_from_authority(
            authority,
            execution_root=execution_root,
            namespace=namespace,
        )
        return runtime

    def _initialize_from_authority(
        self,
        authority,
        *,
        execution_root,
        namespace,
    ):
        self._binding, definition_authority = module_invocation_material(authority)
        self._invocation_authority = authority
        self._lifecycle_state = "initializing"
        self._invocation_count = 0
        self._owned_execution_root = None
        if execution_root is None:
            self._owned_execution_root = tempfile.TemporaryDirectory(
                prefix="trade-module-execution-"
            )
            execution_root = self._owned_execution_root.name
        try:
            self._initialize_runtime(
                definition_authority,
                execution_root,
                namespace,
            )
        except BaseException:
            cleanup_errors = []
            if self._owned_execution_root is not None:
                try:
                    self._owned_execution_root.cleanup()
                except BaseException as cleanup_error:
                    cleanup_errors.append(cleanup_error)
                self._owned_execution_root = None
            # Runtime initialization remains the authoritative failure.
            raise

    def _initialize_runtime(self, definition_authority, execution_root, namespace):
        materialized_definition = materialize_verified_module_definition(
            definition_authority,
            execution_root,
            namespace,
        )
        self._adapter_authority = prepare_module_adapter_authority(
            self._invocation_authority,
            materialized_definition,
        )
        (
            _binding,
            self._definition,
            self._ports,
            mode,
        ) = module_adapter_material(self._adapter_authority)
        self._label = self._binding["instanceId"]
        trusted_outputs = mode == "ProcessRunner"
        self._port_schemas = {
            direction: {
                name: port_schema(port)
                for name, port in self._ports[direction].items()
            }
            for direction in ("inputs", "outputs")
        }
        self._port_validators = {
            direction: {
                name: compile_normalized_json_validator(
                    schema,
                    path=f"{self._label}.{direction}.{name}",
                    trusted_json=(
                        True if direction == "inputs" else trusted_outputs
                    ),
                )
                for name, schema in self._port_schemas[direction].items()
            }
            for direction in ("inputs", "outputs")
        }
        self._port_isolators = {
            name: compile_normalized_json_isolator(
                schema,
                path=f"{self._label}.outputs.{name}",
                trusted_json=trusted_outputs,
            )
            for name, schema in self._port_schemas["outputs"].items()
        }
        self._input_isolator = (
            compile_normalized_json_isolator(
                {
                    "type": "object",
                    "properties": copy.deepcopy(self._port_schemas["inputs"]),
                    "required": sorted(
                        name
                        for name, port in self._ports["inputs"].items()
                        if port.get("required", True)
                    ),
                    "additionalProperties": False,
                },
                path=f"{self._label}.inputs",
                trusted_json=False,
            )
            if mode == "PythonModule"
            else None
        )
        self._input_validation_seconds = 0.0
        self._output_validation_seconds = 0.0
        self._reusable_output_owner = None
        self._adapter = (
            create_python_module_adapter(
                self._adapter_authority,
            )
            if mode == "PythonModule"
            else create_process_module_adapter(
                self._adapter_authority,
            )
        )
        self._reusable_output_owner = ReusableOutputOwner(
            self._invocation_authority,
            self._adapter,
        )
        self._lifecycle_state = "running"

    @property
    def adapter_type(self):
        return type(self._adapter).__name__

    def _require_running(self, operation):
        if self._lifecycle_state == "closed":
            raise RuntimeError(f"Cannot {operation} a closed Module '{self._label}'.")
        if self._lifecycle_state == "finalized":
            raise RuntimeError(
                f"Cannot {operation} a finalized Module '{self._label}'."
            )
        if self._lifecycle_state != "running":
            raise RuntimeError(
                f"Cannot {operation} Module '{self._label}' before initialization."
            )

    def invoke(self, inputs):
        """Validate, invoke once, and return an isolated output object."""
        self._require_running("invoke")
        started = time.perf_counter()
        isolated_inputs = _validate_and_isolate_inputs(
            inputs,
            self._ports,
            self._label,
            self._port_schemas["inputs"],
            self._port_validators["inputs"],
            self._input_isolator,
        )
        self._input_validation_seconds += time.perf_counter() - started
        return self._invoke_isolated(isolated_inputs)

    def invoke_validated(self, inputs_authority):
        """Invoke from one parent-Runtime proof while preserving input ownership."""
        self._require_running("invoke")
        if type(inputs_authority) is not _ValidatedModuleInputs:
            raise TypeError("Module invocation requires validated input authority.")
        inputs = inputs_authority._consume(self._invocation_authority)
        started = time.perf_counter()
        isolated_inputs = (
            _isolate_validated_json_tree(inputs)
            if self._input_isolator is not None
            else dict(inputs)
        )
        self._input_validation_seconds += time.perf_counter() - started
        return self._invoke_isolated(isolated_inputs)

    def _invoke_isolated(self, isolated_inputs):
        self._invocation_count += 1
        outputs = self._adapter.invoke(isolated_inputs)
        started = time.perf_counter()
        receipt = outputs if is_reusable_output_receipt(outputs) else None
        if receipt is not None:
            material = self._reusable_output_owner.material(receipt)
            if material[5]:
                isolated_outputs = self._reusable_output_owner.resolve(receipt)
                self._output_validation_seconds += time.perf_counter() - started
                return isolated_outputs
            outputs = material[4]
        isolated_outputs = _validate_and_isolate_outputs(
            outputs,
            self._ports,
            self._label,
            self._port_schemas["outputs"],
            self._port_validators["outputs"],
            self._port_isolators,
        )
        if receipt is not None:
            self._reusable_output_owner.register_validated(
                receipt,
                isolated_outputs,
            )
            try:
                self._adapter.confirm_reusable_outputs(receipt)
            except BaseException:
                self._reusable_output_owner.discard(receipt)
                raise
        self._output_validation_seconds += time.perf_counter() - started
        return isolated_outputs

    def finalize(self):
        self._require_running("finalize")
        try:
            result = self._adapter.finalize()
            if type(result) is not dict:
                raise ValueError(
                    f"Module '{self._label}' finalize result must be an object."
                )
            try:
                return strict_json.loads(strict_json.dumps(result))
            except (TypeError, ValueError, RecursionError) as exc:
                raise ValueError(
                    f"Module '{self._label}' finalize result must be finite JSON data."
                ) from exc
        finally:
            if self._reusable_output_owner is not None:
                self._reusable_output_owner.invalidate()
            self._lifecycle_state = "finalized"

    def snapshot(self):
        self._require_running("snapshot")
        result = self._adapter.snapshot()
        if type(result) is not dict:
            raise ValueError(f"Module '{self._label}' snapshot must be an object.")
        try:
            return strict_json.loads(strict_json.dumps(result))
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError(
                f"Module '{self._label}' snapshot must be finite JSON data."
            ) from exc

    def restore(self, snapshot):
        self._require_running("restore")
        if type(snapshot) is not dict:
            raise ValueError(
                f"Module '{self._label}' restore snapshot must be an object."
            )
        try:
            isolated_snapshot = strict_json.loads(strict_json.dumps(snapshot))
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError(
                f"Module '{self._label}' restore snapshot must be finite JSON data."
            ) from exc
        if self._reusable_output_owner is not None:
            self._reusable_output_owner.invalidate()
        result = self._adapter.restore(isolated_snapshot)
        if type(result) is not dict:
            raise ValueError(
                f"Module '{self._label}' restore result must be an object."
            )
        result = dict(result)
        require_exact_fields(
            result,
            allowed={"status"},
            required={"status"},
            label=f"Module '{self._label}' restore result",
        )
        if result["status"] != "restored":
            raise ValueError(
                f"Module '{self._label}' restore did not confirm completion."
            )
        return copy.deepcopy(result)

    def close(self):
        if self._lifecycle_state == "closed":
            return {"status": "closed"}
        first_error = None
        result = None
        try:
            result = self._adapter.close()
            if type(result) is not dict:
                raise ValueError(f"Module '{self._label}' close result must be an object.")
            result = dict(result)
            require_exact_fields(
                result,
                allowed={"status"},
                required={"status"},
                label=f"Module '{self._label}' close result",
            )
            if result["status"] != "closed":
                raise ValueError(
                    f"Module '{self._label}' close did not confirm completion."
                )
            result = copy.deepcopy(result)
        except BaseException as exc:
            first_error = exc
        owner = getattr(self, "_reusable_output_owner", None)
        if owner is not None:
            owner.invalidate()
        self._lifecycle_state = "closed"
        if self._owned_execution_root is not None:
            try:
                self._owned_execution_root.cleanup()
            except BaseException as exc:
                first_error = first_error or exc
            finally:
                self._owned_execution_root = None
        if first_error is not None:
            raise first_error
        return result

    def transport_metrics(self):
        return {
            **self._adapter.transport_metrics(),
            "invocationCount": self._invocation_count,
            "inputValidationSeconds": self._input_validation_seconds,
            "outputValidationSeconds": self._output_validation_seconds,
        }
