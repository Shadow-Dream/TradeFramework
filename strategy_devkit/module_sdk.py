#!/usr/bin/env python3
"""Class-based SDK host for one explicit TradeEngine Module.

The Engine owns DataKey resolution.  A module only receives the named values
declared by its input ports and returns values for its declared output ports.
"""

from __future__ import annotations

import inspect
import json
import math
import sys
import time
from collections.abc import Mapping
from contextvars import ContextVar
from copy import deepcopy
from typing import Any

from .module_contract import require_exact_fields


PROTOCOL_VERSION = "pipeline-data-v5"


MODULE_EXTENSION_POINTS = frozenset({
    "update",
    "on_initialize",
    "on_finalize",
    "on_snapshot",
    "on_restore",
    "on_close",
})
MODULE_ENGINE_OWNED_ATTRIBUTES = frozenset({
    "config",
    "configuration",
    "instance_id",
    "module_id",
    "version",
    "archive",
    "state",
    "_input_ports",
    "_output_ports",
    "_initialized",
    "_finalized",
    "_closed",
    "_runtime_input_validation_seconds",
    "_runtime_compute_seconds",
    "_runtime_output_validation_seconds",
    "_runtime_validated_input_issuer",
    "_runtime_reusable_output_epoch",
    "_runtime_reusable_output_generations",
    "_runtime_reusable_output_handles",
})
MODULE_ENGINE_OWNED_OBJECT_PROTOCOL = frozenset({
    "__new__",
    "__getattribute__",
    "__setattr__",
    "__delattr__",
    "__getattr__",
})


def _validate_protocol_json(value: Any, path: str = "value") -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain only finite JSON numbers.")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _validate_protocol_json(item, f"{path}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} object keys must be strings.")
            _validate_protocol_json(item, f"{path}.{key}")
        return
    raise ValueError(f"{path} contains a non-JSON value.")


def _protocol_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_protocol_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def _finite_protocol_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"JSON number is outside the finite range: {value}")
    return result


def _encode_protocol(value):
    _validate_protocol_json(value)
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


def _decode_protocol(value):
    return json.loads(
        value,
        parse_constant=_reject_protocol_constant,
        parse_float=_finite_protocol_float,
        object_pairs_hook=_protocol_pairs,
    )


_PROTOCOL_ENCODER = _encode_protocol
_PROTOCOL_DECODER = _decode_protocol


_REUSABLE_MODULE_OUTPUTS_TOKEN = object()
_VALIDATED_ENGINE_INPUTS_TOKEN = object()
_ENGINE_REUSABLE_OUTPUT_SCOPE = ContextVar(
    "trade_engine_reusable_output_scope",
    default=(),
)


class _ValidatedEngineInputs:
    """One-shot nominal proof issued for an already isolated Engine input."""

    __slots__ = ("_consumed", "_inputs", "_module")

    def __init__(self, module, inputs, *, _token):
        if _token is not _VALIDATED_ENGINE_INPUTS_TOKEN:
            raise TypeError("Validated Engine inputs are SDK-owned.")
        self._module = module
        self._inputs = inputs
        self._consumed = False

    def _consume(self, module, inputs) -> None:
        if self._consumed:
            raise RuntimeError("Validated Engine inputs have already been consumed.")
        self._consumed = True
        if self._module is not module or self._inputs is not inputs:
            raise TypeError(
                "Validated Engine inputs do not match this Module invocation."
            )


class _ReusableModuleOutputs:
    """SDK-local candidate; only an Engine adapter can turn it into proof."""

    __slots__ = (
        "_candidate",
        "_epoch",
        "_generation",
        "_module",
        "_registered",
        "_slot",
    )

    def __init__(
        self,
        module,
        slot,
        epoch,
        generation,
        candidate,
        *,
        _token,
    ):
        if _token is not _REUSABLE_MODULE_OUTPUTS_TOKEN:
            raise TypeError("Reusable Module outputs are SDK-owned.")
        self._module = module
        self._slot = slot
        self._epoch = epoch
        self._generation = generation
        self._candidate = candidate
        self._registered = False


class Module:
    """The one implementation base for every independently registered Module.

    Repository-specific classes only provide a ``kind`` marker.  Identity,
    version, configuration, ports, state, validation, and lifecycle state all
    live here so BuiltIn and process-hosted Modules cannot invent role-specific
    contracts.
    """

    kind = "Module"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        validate_module_implementation_class(cls)

    def __init__(self) -> None:
        self.config: dict[str, Any] = {}
        self.configuration: dict[str, Any] = {}
        self.instance_id = ""
        self.module_id = ""
        self.version = ""
        self.archive: dict[str, Any] = {}
        self.state: dict[str, Any] = {}
        self._input_ports: dict[str, Any] = {}
        self._output_ports: dict[str, Any] = {}
        self._initialized = False
        self._finalized = False
        self._closed = False
        self._runtime_input_validation_seconds = 0.0
        self._runtime_compute_seconds = 0.0
        self._runtime_output_validation_seconds = 0.0
        self._runtime_validated_input_issuer = None
        self._runtime_reusable_output_epoch = 0
        self._runtime_reusable_output_generations: dict[str, int] = {}
        self._runtime_reusable_output_handles: dict[
            str, _ReusableModuleOutputs
        ] = {}

    def update(self, /, **inputs: Any) -> dict[str, Any]:
        """Compute declared outputs from this Module's named input mapping."""
        raise NotImplementedError

    def on_initialize(self) -> None:
        """Optional hook called after config and port contracts are available."""

    def on_finalize(self) -> dict[str, Any]:
        """Optional hook called after the last invocation."""
        return {}

    def on_snapshot(self) -> dict[str, Any]:
        """Return serializable private state for an Engine checkpoint."""
        return deepcopy(self.state)

    def on_restore(self, snapshot: dict[str, Any]) -> None:
        """Restore a snapshot produced by on_snapshot()."""
        self.state = deepcopy(dict(snapshot))

    def on_close(self) -> None:
        """Release resources before the Module worker exits."""

    @property
    def version_key(self) -> str:
        if not self._initialized:
            raise RuntimeError("Module version is unavailable before initialize().")
        return f"{self.kind}/{self.module_id}/{self.version}"

    def initialize(self, configuration: Mapping[str, Any]) -> dict[str, Any]:
        if type(configuration) is not dict:
            raise ValueError("Module initialization requires a configuration object.")
        if self._initialized:
            raise RuntimeError("Module initialize() may only be called once.")
        if self._closed:
            raise RuntimeError("Cannot initialize a closed Module.")
        require_exact_fields(
            dict(configuration),
            allowed={"key", "kind", "moduleId", "version", "config", "inputs", "outputs", "archive"},
            required={"key", "kind", "moduleId", "version", "config", "inputs", "outputs", "archive"},
            label="Module initialization configuration",
        )
        self.configuration = dict(configuration)
        for field in ("key", "kind", "moduleId", "version"):
            if not isinstance(configuration[field], str) or not configuration[field].strip():
                raise ValueError(
                    f"Module initialization {field} must be a non-empty string."
                )
        self.instance_id = configuration["key"].strip()
        configured_kind = configuration["kind"].strip()
        self.module_id = configuration["moduleId"].strip()
        self.version = configuration["version"].strip()
        archive = configuration["archive"]
        if type(archive) is not dict:
            raise ValueError("Module initialization archive proof must be an object.")
        self.archive = dict(archive)
        if self.archive.get("status") != "archived":
            raise ValueError("Module initialization requires an Archived version.")
        content_digest = self.archive.get("contentDigest")
        if (
            not isinstance(content_digest, str)
            or not content_digest.startswith("sha256:")
            or len(content_digest) != 71
            or any(character not in "0123456789abcdef" for character in content_digest[7:])
        ):
            raise ValueError("Module initialization requires a verified archive content digest.")
        if self.kind == "Module":
            raise TypeError("Module implementations must inherit their repository kind class.")
        if configured_kind != self.kind:
            raise ValueError(
                f"Module kind '{configured_kind}' does not match implementation kind '{self.kind}'."
            )
        self._input_ports = _port_map(configuration["inputs"], "inputs")
        self._output_ports = _port_map(configuration["outputs"], "outputs")
        self.config = _parse_config(configuration["config"])
        Module._validate_implementation(self)
        self.on_initialize()
        validate_module_implementation_class(type(self))
        self._initialized = True
        return {
            "status": "initialized",
            "versionKey": Module.version_key.__get__(self, type(self)),
        }

    def _validate_implementation(self) -> None:
        _validate_update_signature(self.update, self._input_ports)

    def _compute(self, inputs: Mapping[str, Any]) -> Mapping[str, Any]:
        arguments = {
            name: inputs.get(name)
            for name in self._input_ports
            if name in inputs or self._input_ports[name].get("required", True)
        }
        return self.update(**arguments)

    def register_reusable_outputs(
        self,
        outputs: dict[str, Any],
        *,
        slot: str = "default",
    ):
        """Create a candidate whose value may be reused after Engine validation.

        The returned handle is deliberately not a validation receipt.  A
        PythonModule adapter must present it to its owning ModuleInvoker, which
        performs the normal output validation before acknowledging this
        generation.  ProcessRunner and direct SDK invocation materialize the
        candidate as ordinary output JSON on every call.
        """

        Module._require_running(self, "register reusable outputs")
        if not Module.reusable_output_registration_available(self):
            raise RuntimeError(
                "Reusable Module outputs require an active Engine invocation."
            )
        if (
            not isinstance(slot, str)
            or not slot
            or slot != slot.strip()
        ):
            raise ValueError(
                "Reusable Module output slot must be a non-empty trimmed string."
            )
        generation = self._runtime_reusable_output_generations.get(slot, 0) + 1
        self._runtime_reusable_output_generations[slot] = generation
        handle = _ReusableModuleOutputs(
            self,
            slot,
            self._runtime_reusable_output_epoch,
            generation,
            outputs,
            _token=_REUSABLE_MODULE_OUTPUTS_TOKEN,
        )
        self._runtime_reusable_output_handles[slot] = handle
        return handle

    def reusable_output_registration_available(self) -> bool:
        """Whether this exact Module is inside its Engine adapter invocation."""

        scope = _ENGINE_REUSABLE_OUTPUT_SCOPE.get()
        return bool(scope and scope[-1] is self)

    def _reusable_output_material(self, outputs):
        if type(outputs) is not _ReusableModuleOutputs:
            return None
        if (
            outputs._module is not self
            or outputs._epoch != self._runtime_reusable_output_epoch
            or self._runtime_reusable_output_handles.get(outputs._slot)
            is not outputs
        ):
            raise RuntimeError(
                "Reusable Module outputs do not belong to the active generation."
            )
        return (
            outputs._slot,
            outputs._epoch,
            outputs._generation,
            outputs._candidate,
            outputs._registered,
        )

    def _confirm_reusable_outputs(self, outputs) -> None:
        material = Module._reusable_output_material(self, outputs)
        if material is None:
            raise TypeError("Reusable Module output confirmation requires a handle.")
        outputs._registered = True

    def _invalidate_reusable_outputs(self) -> None:
        self._runtime_reusable_output_epoch += 1
        self._runtime_reusable_output_generations.clear()
        self._runtime_reusable_output_handles.clear()

    def _bind_validated_input_issuer(self, issuer) -> None:
        """Bind the one private adapter allowed to issue input proofs."""

        Module._require_running(self, "bind validated input issuer")
        if issuer is None:
            raise TypeError("Validated Engine input issuer is invalid.")
        if self._runtime_validated_input_issuer is not None:
            raise RuntimeError("Validated Engine input issuer is already bound.")
        self._runtime_validated_input_issuer = issuer

    def _issue_validated_engine_inputs(
        self,
        inputs: dict[str, Any],
        *,
        _issuer=None,
    ):
        """Bind an adapter-proven exact input owner to one SDK invocation."""

        Module._require_running(self, "issue validated inputs")
        if (
            _issuer is None
            or _issuer is not self._runtime_validated_input_issuer
        ):
            raise TypeError(
                "Validated Engine inputs require the bound adapter issuer."
            )
        if type(inputs) is not dict:
            raise TypeError("Validated Engine inputs must be an exact object.")
        return _ValidatedEngineInputs(
            self,
            inputs,
            _token=_VALIDATED_ENGINE_INPUTS_TOKEN,
        )

    def _invoke(
        self,
        inputs: dict[str, Any],
        *,
        preserve_reusable: bool,
        validated_inputs=None,
    ):
        Module._require_running(self, "invoke")
        phase_started = time.perf_counter()
        if validated_inputs is None:
            if type(inputs) is not dict:
                raise ValueError(f"{self.kind} invocation requires an inputs object.")
            unknown = sorted(set(inputs) - set(self._input_ports))
            if unknown:
                raise ValueError(
                    "Engine forwarded undeclared input port(s): "
                    + ", ".join(unknown)
                )
            missing = sorted(
                name
                for name, port in self._input_ports.items()
                if port.get("required", True) and name not in inputs
            )
            if missing:
                raise ValueError(
                    "Module invocation is missing required input port(s): "
                    + ", ".join(missing)
                )
        elif type(validated_inputs) is not _ValidatedEngineInputs:
            raise TypeError("Module invocation requires validated Engine inputs.")
        else:
            validated_inputs._consume(self, inputs)

        self._runtime_input_validation_seconds += time.perf_counter() - phase_started
        phase_started = time.perf_counter()
        raw_outputs = Module._compute(self, inputs)
        self._runtime_compute_seconds += time.perf_counter() - phase_started
        phase_started = time.perf_counter()
        reusable = Module._reusable_output_material(self, raw_outputs)
        if reusable is not None and preserve_reusable and reusable[4]:
            # The active Engine adapter already proved this exact generation.
            self._runtime_output_validation_seconds += (
                time.perf_counter() - phase_started
            )
            return raw_outputs
        outputs = reusable[3] if reusable is not None else raw_outputs
        if type(outputs) is not dict:
            raise ValueError("Module update() must return a JSON object.")
        unknown_outputs = sorted(set(outputs) - set(self._output_ports))
        if unknown_outputs:
            raise ValueError(f"{self.kind} returned undeclared output port(s): " + ", ".join(unknown_outputs))
        missing_outputs = sorted(
            name
            for name, port in self._output_ports.items()
            if port.get("required", True) and name not in outputs
        )
        if missing_outputs:
            raise ValueError(f"{self.kind} omitted required output port(s): " + ", ".join(missing_outputs))
        self._runtime_output_validation_seconds += time.perf_counter() - phase_started
        return raw_outputs if preserve_reusable and reusable is not None else dict(outputs)

    def invoke(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return Module._invoke(self, inputs, preserve_reusable=False)

    def _invoke_for_engine(self, inputs: dict[str, Any], *, _validated_inputs=None):
        scope = _ENGINE_REUSABLE_OUTPUT_SCOPE.get()
        scope_token = _ENGINE_REUSABLE_OUTPUT_SCOPE.set((*scope, self))
        try:
            return Module._invoke(
                self,
                inputs,
                preserve_reusable=True,
                validated_inputs=_validated_inputs,
            )
        finally:
            _ENGINE_REUSABLE_OUTPUT_SCOPE.reset(scope_token)

    def runtime_metrics(self) -> dict[str, float]:
        """Return Engine-owned SDK phase timings through the formal host boundary."""
        return {
            "inputValidationSeconds": self._runtime_input_validation_seconds,
            "computeSeconds": self._runtime_compute_seconds,
            "outputValidationSeconds": self._runtime_output_validation_seconds,
        }

    def finalize(self) -> dict[str, Any]:
        Module._require_running(self, "finalize")
        try:
            result = self.on_finalize()
            if type(result) is not dict:
                raise ValueError("Module on_finalize() must return an object.")
            _validate_protocol_json(result, "Module finalize result")
            result = _PROTOCOL_DECODER(_PROTOCOL_ENCODER(result))
            self._finalized = True
            return result
        finally:
            Module._invalidate_reusable_outputs(self)

    def snapshot(self) -> dict[str, Any]:
        Module._require_running(self, "snapshot")
        snapshot = self.on_snapshot()
        if type(snapshot) is not dict:
            raise ValueError("Module on_snapshot() must return an object.")
        try:
            value = dict(snapshot)
            _validate_protocol_json(value, "Module snapshot")
            return _PROTOCOL_DECODER(_PROTOCOL_ENCODER(value))
        except (TypeError, ValueError) as exc:
            raise ValueError("Module snapshot must be finite JSON data.") from exc

    def restore(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        Module._require_running(self, "restore")
        if type(snapshot) is not dict:
            raise ValueError("Module restore snapshot must be an object.")
        try:
            _validate_protocol_json(snapshot, "Module restore snapshot")
            isolated = _PROTOCOL_DECODER(_PROTOCOL_ENCODER(snapshot))
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("Module restore snapshot must be finite JSON data.") from exc
        Module._invalidate_reusable_outputs(self)
        self.on_restore(isolated)
        return {"status": "restored"}

    def close(self) -> dict[str, Any]:
        if self._closed:
            return {"status": "closed"}
        try:
            self.on_close()
            self._closed = True
            return {"status": "closed"}
        finally:
            Module._invalidate_reusable_outputs(self)

    def _require_active(self, operation: str) -> None:
        if not self._initialized:
            raise RuntimeError(f"Cannot {operation} a Module before initialize().")
        if self._closed:
            raise RuntimeError(f"Cannot {operation} a closed Module.")

    def _require_running(self, operation: str) -> None:
        Module._require_active(self, operation)
        if self._finalized:
            raise RuntimeError(f"Cannot {operation} a finalized Module.")


def _engine_owned_module_descriptors() -> tuple[tuple[str, Any], ...]:
    ignored = {
        "__module__",
        "__dict__",
        "__weakref__",
        "__doc__",
        "kind",
        *MODULE_EXTENSION_POINTS,
    }
    descriptors = [
        (name, descriptor)
        for name, descriptor in Module.__dict__.items()
        if name not in ignored
        and (
            inspect.isfunction(descriptor)
            or isinstance(descriptor, (classmethod, staticmethod, property))
        )
    ]
    for name in sorted(MODULE_ENGINE_OWNED_OBJECT_PROTOCOL - {"__getattr__"}):
        descriptors.append((name, inspect.getattr_static(Module, name)))
    return tuple(descriptors)


_ENGINE_OWNED_MODULE_DESCRIPTORS = _engine_owned_module_descriptors()


def validate_module_implementation_class(implementation: type[Module]) -> None:
    """Reject overrides of lifecycle behavior owned by the Engine SDK.

    This validation deliberately runs both while a subclass is created and
    after an archived ``module.py`` has finished executing.  The second pass
    catches class or inherited-mixin monkeypatches performed after class
    creation.  Only ``update`` and the documented ``on_*`` hooks are virtual.
    """

    if not isinstance(implementation, type) or not issubclass(implementation, Module):
        raise TypeError("Module implementation must inherit strategy_devkit.Module.")
    overridden = []
    for name, engine_descriptor in _ENGINE_OWNED_MODULE_DESCRIPTORS:
        try:
            resolved = inspect.getattr_static(implementation, name)
        except AttributeError:
            overridden.append(name)
            continue
        if resolved is not engine_descriptor:
            overridden.append(name)
    for ancestor in implementation.__mro__:
        if ancestor is Module:
            break
        overridden.extend(
            name
            for name in MODULE_ENGINE_OWNED_ATTRIBUTES | {"__getattr__"}
            if name in ancestor.__dict__
        )
    if overridden:
        raise TypeError(
            "Module implementations may override only update() and the documented "
            "on_* hooks; Engine-owned member(s) cannot be overridden: "
            + ", ".join(sorted(set(overridden)))
        )


class PipelineModule(Module):
    """Repository marker for Pipeline Modules; runtime invocation uses Module."""


class UniverseModule(PipelineModule):
    kind = "Universe"


class SignalModule(PipelineModule):
    kind = "Signal"


class TargetModule(PipelineModule):
    kind = "Target"


class ConstraintModule(PipelineModule):
    kind = "Constraint"


def _port_map(value: Any, label: str) -> dict[str, dict[str, Any]]:
    if type(value) is not dict:
        raise ValueError(f"Module configuration {label} must be an object.")
    result: dict[str, dict[str, Any]] = {}
    for name, port in value.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"Module configuration {label} contains an invalid port name.")
        if type(port) is not dict:
            raise ValueError(f"Module port '{name}' must be an object.")
        require_exact_fields(
            dict(port),
            allowed={"schema", "required"},
            required={"schema", "required"},
            label=f"Module {label}.{name}",
        )
        result[name] = dict(port)
    return result


def _parse_config(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if type(value) is not dict:
        raise ValueError("Module configuration 'config' must be an object.")
    return dict(value)


def _validate_update_signature(update: Any, input_ports: Mapping[str, Any]) -> None:
    if getattr(update, "__func__", None) is Module.update:
        raise ValueError("Module implementation must implement update().")
    signature = inspect.signature(update)
    parameters = list(signature.parameters.values())
    if (
        len(parameters) == 1
        and parameters[0].kind is inspect.Parameter.VAR_KEYWORD
    ):
        implementation = getattr(update, "__func__", None)
        implementation_parameters = (
            list(inspect.signature(implementation).parameters.values())
            if implementation is not None
            else ()
        )
        if (
            not implementation_parameters
            or implementation_parameters[0].kind
            is not inspect.Parameter.POSITIONAL_ONLY
        ):
            raise ValueError(
                "Module update(self, /, **inputs) must declare its receiver "
                "positional-only so every public port name, including 'self', "
                "is representable."
            )
        return
    invalid = [
        parameter.name
        for parameter in parameters
        if parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        )
    ]
    if invalid:
        raise ValueError(
            "Module update() must either declare named input ports explicitly or "
            "accept only **inputs; invalid parameter(s): "
            + ", ".join(invalid)
        )
    declared = {parameter.name for parameter in parameters}
    expected = set(input_ports)
    if declared != expected:
        missing = sorted(expected - declared)
        extra = sorted(declared - expected)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("undeclared " + ", ".join(extra))
        raise ValueError(
            "Module update() parameters do not match input ports: "
            + "; ".join(details)
        )
    optional_without_default = sorted(
        parameter.name
        for parameter in parameters
        if not input_ports[parameter.name].get("required", True)
        and parameter.default is inspect.Parameter.empty
    )
    if optional_without_default:
        raise ValueError(
            "Optional input port parameter(s) require Python defaults: "
            + ", ".join(optional_without_default)
        )


def handle_module_command(module: Module, command: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(module, Module):
        raise TypeError("Module command dispatch requires one Module instance.")
    validate_module_implementation_class(type(module))
    if type(payload) is not dict:
        raise ValueError("Transport request payload must be an object.")
    payload_fields = {
        "initialize": {"configuration"},
        "invoke": {"inputs"},
        "restore": {"snapshot"},
        "snapshot": set(),
        "finalize": set(),
        "close": set(),
    }
    if command not in payload_fields:
        raise ValueError(f"Unsupported command: {command}")
    require_exact_fields(
        dict(payload),
        allowed=payload_fields[command],
        required=payload_fields[command],
        label=f"Module {command} payload",
    )
    if command == "initialize":
        return Module.initialize(module, payload["configuration"])
    if command == "snapshot":
        return {"snapshot": Module.snapshot(module)}
    if command == "restore":
        return Module.restore(module, payload["snapshot"])
    if command == "finalize":
        return Module.finalize(module)
    if command == "close":
        return Module.close(module)
    if command == "invoke":
        return {"outputs": Module.invoke(module, payload["inputs"])}
    raise AssertionError("Validated Module command was not dispatched.")


def _response(request: Mapping[str, Any], success: bool, payload: Any = None, error: str = "") -> dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": request.get("requestId", ""),
        "success": success,
        "payload": payload or {},
        "error": error,
    }


def serve_module(module: Module, stdin: Any = None, stdout: Any = None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        if not line.strip():
            continue
        request: dict[str, Any] = {}
        try:
            request = _PROTOCOL_DECODER(line)
            require_exact_fields(
                request,
                allowed={"protocolVersion", "requestId", "command", "payload"},
                required={"protocolVersion", "requestId", "command", "payload"},
                label="Module transport request",
            )
            if request.get("protocolVersion") != PROTOCOL_VERSION:
                raise ValueError(f"Unsupported protocolVersion: {request.get('protocolVersion')}")
            result = handle_module_command(module, request["command"], request["payload"])
            response = _response(request, True, result)
        except Exception as exc:
            response = _response(request, False, error=str(exc))
        stdout.write(_PROTOCOL_ENCODER(response) + "\n")
        stdout.flush()
        if request.get("command") == "close" and response.get("success"):
            break


def run_module(module: Module) -> None:
    if not isinstance(module, Module):
        raise TypeError("run_module() requires one Module instance.")
    serve_module(module)
