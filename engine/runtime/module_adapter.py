#!/usr/bin/env python3
"""Common invocation adapter boundary for Engine Module runtimes."""

from __future__ import annotations

from abc import ABC, abstractmethod
import copy

from engine.authority.module_invocation import (
    module_invocation_material,
)
from engine.contracts.module import (
    definition_key,
    normalize_module_parameters,
    normalize_ports,
)
from engine.contracts import strict_json
from engine.runtime.module_implementation import (
    materialized_module_definition_material,
)


__all__ = (
    "InvocationAdapter",
    "module_adapter_material",
    "module_configuration",
    "prepare_module_adapter_authority",
    "require_initialized_module_configuration",
)


_MODULE_ADAPTER_AUTHORITY_TOKEN = object()


class _ModuleAdapterAuthority:
    """Nominal Runtime proof binding one verified Module to its isolated material."""

    __slots__ = ("_binding", "_definition", "_ports", "_activation_mode")

    def __init__(self, binding, definition, ports, activation_mode, *, _token):
        if _token is not _MODULE_ADAPTER_AUTHORITY_TOKEN:
            raise TypeError("Module Adapter authority is Engine-owned.")
        self._binding = copy.deepcopy(binding)
        self._definition = copy.deepcopy(definition)
        self._ports = copy.deepcopy(ports)
        self._activation_mode = activation_mode

    def _material(self, expected_activation_mode=None):
        if (
            expected_activation_mode is not None
            and self._activation_mode != expected_activation_mode
        ):
            raise TypeError(
                "Module Adapter authority has the wrong activation mode."
            )
        return (
            copy.deepcopy(self._binding),
            copy.deepcopy(self._definition),
            copy.deepcopy(self._ports),
            self._activation_mode,
        )


def prepare_module_adapter_authority(
    invocation_authority,
    materialized_definition,
):
    """Bind one verified Definition and materialization to its exact invocation."""

    binding, definition_authority = module_invocation_material(invocation_authority)
    definition = materialized_module_definition_material(
        materialized_definition,
        definition_authority,
    )
    ports = normalize_ports(
        definition["ports"],
        label=f"Module '{binding['instanceId']}' ports",
    )
    if ports != definition["ports"]:
        raise ValueError(
            f"Module '{binding['instanceId']}' ports are not normalized."
        )
    activation_mode = definition["activationMode"]
    if activation_mode not in {"PythonModule", "ProcessRunner"}:
        raise ValueError(
            "TradeEngine runtime does not support activation mode "
            f"'{activation_mode}' for module '{binding['instanceId']}'. "
            "Supported modes: ProcessRunner, PythonModule."
        )
    normalize_module_parameters(
        definition["parameters"],
        activation_mode=activation_mode,
        label=f"Module '{binding['instanceId']}' parameters",
    )
    return _ModuleAdapterAuthority(
        binding,
        definition,
        ports,
        activation_mode,
        _token=_MODULE_ADAPTER_AUTHORITY_TOKEN,
    )


def module_adapter_material(authority, *, expected_activation_mode=None):
    """Return detached adapter material from the exact nominal authority."""

    if type(authority) is not _ModuleAdapterAuthority:
        raise TypeError("Module Adapter authority is Engine-owned.")
    return authority._material(expected_activation_mode)


class InvocationAdapter(ABC):
    """Host transport for a Module; Module behavior lives in the shared SDK base."""

    @abstractmethod
    def invoke(self, inputs):
        raise NotImplementedError

    @abstractmethod
    def finalize(self):
        raise NotImplementedError

    @abstractmethod
    def snapshot(self):
        raise NotImplementedError

    @abstractmethod
    def restore(self, snapshot):
        raise NotImplementedError

    @abstractmethod
    def transport_metrics(self):
        raise NotImplementedError

    @abstractmethod
    def close(self):
        raise NotImplementedError


def module_configuration(authority):
    """Build the exact frozen configuration passed to one Module adapter."""
    binding, definition, ports, _activation_mode = module_adapter_material(authority)
    return {
        "key": binding["instanceId"],
        "kind": binding["kind"],
        "moduleId": binding["moduleId"],
        "version": binding["version"],
        "config": copy.deepcopy(binding["config"]),
        "inputs": copy.deepcopy(ports["inputs"]),
        "outputs": copy.deepcopy(ports["outputs"]),
        "archive": {
            "status": definition["status"],
            "contentDigest": definition["contentDigest"],
            **copy.deepcopy(definition["archive"]),
        },
    }


def require_initialized_module_configuration(
    authority,
    actual_configuration,
    *,
    actual_version_key,
    actual_identity=None,
    actual_config=None,
    actual_ports=None,
    actual_archive=None,
):
    """Prove initialization retained the exact Engine-owned Module contract."""

    binding, _definition, _ports, _activation_mode = module_adapter_material(authority)
    expected = module_configuration(authority)
    owned_fields = (
        "key",
        "kind",
        "moduleId",
        "version",
        "config",
        "inputs",
        "outputs",
        "archive",
    )
    try:
        changed = [
            field
            for field in owned_fields
            if field not in actual_configuration
            or not strict_json.exact_equal(
                actual_configuration[field],
                expected[field],
            )
        ] if type(actual_configuration) is dict else list(owned_fields)
        if type(actual_configuration) is dict:
            changed.extend(sorted(set(actual_configuration) - set(owned_fields)))
    except (AttributeError, TypeError, ValueError, RecursionError):
        changed = list(owned_fields)
    expected_version_key = definition_key(
        binding["kind"],
        binding["moduleId"],
        binding["version"],
    )
    if actual_version_key != expected_version_key:
        changed.append("versionKey")
    expected_identity = {
        "key": expected["key"],
        "kind": expected["kind"],
        "moduleId": expected["moduleId"],
        "version": expected["version"],
    }
    runtime_material = (
        ("identity", actual_identity, expected_identity),
        ("config", actual_config, expected["config"]),
        (
            "ports",
            actual_ports,
            {"inputs": expected["inputs"], "outputs": expected["outputs"]},
        ),
        ("archive", actual_archive, expected["archive"]),
    )
    for label, actual, expected_value in runtime_material:
        try:
            matches = strict_json.exact_equal(actual, expected_value)
        except (TypeError, ValueError, RecursionError):
            matches = False
        if not matches:
            changed.append(label)
    if changed:
        raise ValueError(
            f"Module '{binding['instanceId']}' changed Engine-owned "
            "initialization field(s): "
            + ", ".join(sorted(set(changed)))
            + "."
        )
    return expected_version_key
