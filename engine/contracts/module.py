#!/usr/bin/env python3
"""Authoritative JSON shape contract shared by every Module repository and Graph."""

from __future__ import annotations

from copy import deepcopy
import math

from engine.contracts.archive import (
    require_resource_path_segment as _require_resource_path_segment,
)
from engine.contracts.data_model import normalize_data_key_schema
from engine.contracts.json_schema import normalize_module_config_schema


PROTOCOL_VERSION = "pipeline-data-v5"

ENGINE_MODULE_KINDS = frozenset({
    "Universe",
    "Signal",
    "Target",
    "Constraint",
})
ANALYSIS_MODULE_KINDS = frozenset({"Analyzer"})
ENVIRONMENT_MODULE_KINDS = frozenset({"Environment"})
MODULE_KINDS = (
    ENGINE_MODULE_KINDS | ANALYSIS_MODULE_KINDS | ENVIRONMENT_MODULE_KINDS
)
MODULE_RELEASE_DIRECTORIES = {
    **{kind: "_modules" for kind in ENGINE_MODULE_KINDS},
    **{kind: "_analysis_modules" for kind in ANALYSIS_MODULE_KINDS},
    **{kind: "_environment_modules" for kind in ENVIRONMENT_MODULE_KINDS},
}
ACTIVATION_MODES = frozenset({"PythonModule", "ProcessRunner"})


MODULE_DRAFT_FIELDS = frozenset({
    "kind", "moduleId", "name", "activationMode", "parameters",
    "configSchema", "ports", "description", "files",
})
MODULE_DEFINITION_FIELDS = (MODULE_DRAFT_FIELDS - {"files"}) | frozenset({
    "version", "builtin", "status", "contentDigest", "createdAt", "archive",
})
PROCESS_MODULE_PARAMETER_FIELDS = frozenset({
    "command", "arguments", "workingDirectory", "requestTimeoutSeconds", "maxResponseBytes",
})
MODULE_INSTANCE_FIELDS = frozenset({
    "instanceId", "kind", "moduleId", "version", "config", "inputs", "outputs",
})
COMPILED_MODULE_FIELDS = MODULE_INSTANCE_FIELDS | frozenset({"key"})
GRAPH_FIELDS = frozenset({"nodes", "inputs", "outputs"})
GRAPH_BOUNDARY_FIELDS = frozenset({"dataKey", "wire"})
GRAPH_INPUT_BOUNDARY_FIELDS = GRAPH_BOUNDARY_FIELDS | frozenset({"source"})


def definition_key(kind, module_id, version):
    """Return the canonical repository key for one Module Definition."""
    return f"{kind}/{module_id}/{version}"


def require_exact_fields(value, *, allowed, required=(), label="Object"):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ValueError(f"{label} contains unsupported field(s): " + ", ".join(unknown))
    missing = sorted(set(required) - set(value))
    if missing:
        raise ValueError(f"{label} is missing required field(s): " + ", ".join(missing))
    return value


def normalize_module_parameters(
    parameters,
    *,
    activation_mode,
    label="Module parameters",
):
    if activation_mode == "PythonModule":
        require_exact_fields(parameters, allowed=set(), label=label)
        return {}
    if activation_mode != "ProcessRunner":
        raise ValueError(
            f"{label} cannot be normalized for activation mode '{activation_mode}'."
        )
    require_exact_fields(
        parameters,
        allowed=PROCESS_MODULE_PARAMETER_FIELDS,
        required={"command", "arguments"},
        label=label,
    )
    command = parameters["command"]
    if not isinstance(command, str) or not command.strip():
        raise ValueError(f"{label}.command must be a non-empty string.")
    arguments = parameters["arguments"]
    if not isinstance(arguments, list):
        raise ValueError(f"{label}.arguments must be an array.")
    if not all(
        isinstance(argument, (str, int, float)) and not isinstance(argument, bool)
        and (not isinstance(argument, float) or math.isfinite(argument))
        for argument in arguments
    ):
        raise ValueError(f"{label}.arguments must contain strings or numbers.")
    working_directory = parameters.get("workingDirectory")
    if working_directory is not None and not isinstance(working_directory, str):
        raise ValueError(f"{label}.workingDirectory must be a string.")
    if "requestTimeoutSeconds" in parameters and (
        isinstance(parameters["requestTimeoutSeconds"], bool)
        or not isinstance(parameters["requestTimeoutSeconds"], (int, float))
        or not math.isfinite(parameters["requestTimeoutSeconds"])
        or parameters["requestTimeoutSeconds"] <= 0
    ):
        raise ValueError(f"{label}.requestTimeoutSeconds must be a positive number.")
    if "maxResponseBytes" in parameters and (
        isinstance(parameters["maxResponseBytes"], bool)
        or not isinstance(parameters["maxResponseBytes"], int)
        or parameters["maxResponseBytes"] < 1
    ):
        raise ValueError(f"{label}.maxResponseBytes must be a positive integer.")
    return deepcopy(parameters)


def normalize_ports(ports, *, label="Module ports"):
    require_exact_fields(
        ports,
        allowed={"inputs", "outputs"},
        required={"inputs", "outputs"},
        label=label,
    )
    result = {"inputs": {}, "outputs": {}}
    for direction in ("inputs", "outputs"):
        values = ports[direction]
        if not isinstance(values, dict):
            raise ValueError(f"ports.{direction} must be an object.")
        for name, spec in values.items():
            if not isinstance(name, str) or not name:
                raise ValueError(f"ports.{direction} contains an invalid port name.")
            require_exact_fields(
                spec,
                allowed={"schema", "required"},
                required={"schema"},
                label=f"{label}.{direction}.{name}",
            )
            required = spec.get("required", True)
            if not isinstance(required, bool):
                raise ValueError(f"ports.{direction}.{name}.required must be a boolean.")
            result[direction][name] = {
                "schema": normalize_data_key_schema(
                    spec["schema"],
                    path=f"{label}.{direction}.{name}",
                ),
                "required": required,
            }
    return result


def validate_instance_wiring(instance, definition):
    """Require one Module instance to bind only declared, usable ports."""
    ports = normalize_ports(definition["ports"])
    inputs = instance["inputs"]
    outputs = instance["outputs"]
    if not isinstance(inputs, dict):
        raise ValueError("instance inputs must be an object.")
    if not isinstance(outputs, dict):
        raise ValueError("instance outputs must be an object.")

    for name in inputs:
        if name not in ports["inputs"]:
            raise ValueError(
                f"Instance '{instance['instanceId']}' binds unknown input port '{name}'."
            )
    for name in outputs:
        if name not in ports["outputs"]:
            raise ValueError(
                f"Instance '{instance['instanceId']}' binds unknown output port '{name}'."
            )

    for name, spec in ports["inputs"].items():
        if spec.get("required", True) and (
            name not in inputs
            or not isinstance(inputs[name], str)
            or not inputs[name].strip()
            or inputs[name].strip() == "null"
        ):
            raise ValueError(
                f"Instance '{instance['instanceId']}' requires input port '{name}'."
            )
    for name, spec in ports["outputs"].items():
        if spec.get("required", False) and (
            name not in outputs
            or not isinstance(outputs[name], str)
            or not outputs[name]
        ):
            raise ValueError(
                f"Instance '{instance['instanceId']}' requires output port '{name}'."
            )

    for direction, values in (("inputs", inputs), ("outputs", outputs)):
        for name, wire_id in values.items():
            if (
                not isinstance(wire_id, str)
                or not wire_id.strip()
                or wire_id.strip() == "null"
            ):
                raise ValueError(
                    f"Instance '{instance['instanceId']}' {direction}.{name} "
                    "must be a non-empty wire id."
                )
    return ports


def validate_module_definition(definition):
    """Validate one normalized archived Module Definition contract."""

    require_exact_fields(
        definition,
        allowed=MODULE_DEFINITION_FIELDS,
        required={
            "kind",
            "moduleId",
            "name",
            "activationMode",
            "parameters",
            "configSchema",
            "ports",
            "description",
            "version",
            "builtin",
        },
        label="Module definition",
    )
    kind = definition["kind"]
    module_id = definition["moduleId"]
    version = definition["version"]
    if kind not in MODULE_KINDS:
        raise ValueError(f"Invalid module kind: {kind}")
    _require_resource_path_segment(kind, label="Module kind")
    _require_resource_path_segment(module_id, label="moduleId")
    _require_resource_path_segment(version, label="Module version")

    for field in ("name", "description"):
        if not isinstance(definition[field], str):
            raise ValueError(f"Module '{module_id}' {field} must be a string.")
    if not definition["name"]:
        raise ValueError(f"Module '{module_id}' name is required.")
    if not isinstance(definition["builtin"], bool):
        raise ValueError(f"Module '{module_id}' builtin must be a boolean.")
    activation = definition["activationMode"]
    if activation not in ACTIVATION_MODES:
        raise ValueError(
            f"Module '{module_id}' has invalid activationMode '{activation}'."
        )
    normalized_parameters = normalize_module_parameters(
        definition["parameters"],
        activation_mode=activation,
        label=f"Module '{module_id}' parameters",
    )
    if normalized_parameters != definition["parameters"]:
        raise ValueError(f"Module '{module_id}' parameters are not normalized.")
    normalized_config_schema = normalize_module_config_schema(
        definition["configSchema"]
    )
    if normalized_config_schema != definition["configSchema"]:
        raise ValueError(f"Module '{module_id}' configSchema is not normalized.")
    normalized_definition_ports = normalize_ports(
        definition["ports"],
        label=(
            f"Module '{definition.get('kind')}/{definition.get('moduleId')}' ports"
        ),
    )
    if normalized_definition_ports != definition["ports"]:
        raise ValueError(f"Module '{module_id}' ports are not normalized.")
    return definition
