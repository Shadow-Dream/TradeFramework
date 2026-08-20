"""Nominal binding between one verified Module Definition and one instance."""

from __future__ import annotations

import copy
from collections.abc import Mapping

from engine.authority.module_definition import (
    require_verified_module_definition_authority,
    verified_module_definition_material,
)
from engine.contracts.json_schema import validate_config
from engine.contracts.module import (
    MODULE_INSTANCE_FIELDS,
    definition_key,
    normalize_ports,
    require_exact_fields,
)


__all__ = (
    "bind_module_invocation_authorities",
    "bind_module_invocation_authority",
    "module_invocation_material",
    "require_module_invocation_authority",
)


_MODULE_INVOCATION_AUTHORITY_TOKEN = object()


class _ModuleInvocationAuthority:
    """Engine-owned proof that an instance identity matches its Definition."""

    __slots__ = ("_binding", "_definition_authority")

    def __init__(self, binding, definition_authority, *, _token):
        if _token is not _MODULE_INVOCATION_AUTHORITY_TOKEN:
            raise TypeError("Module Invocation authority is Engine-owned.")
        self._binding = copy.deepcopy(binding)
        self._definition_authority = definition_authority

    def _material(self):
        return copy.deepcopy(self._binding), self._definition_authority


def bind_module_invocation_authority(binding, definition_authority):
    """Prove that one exact Runtime binding belongs to a verified Definition."""

    require_verified_module_definition_authority(definition_authority)
    require_exact_fields(
        binding,
        allowed=MODULE_INSTANCE_FIELDS,
        required=MODULE_INSTANCE_FIELDS,
        label="Runtime Module binding",
    )
    for field in ("instanceId", "kind", "moduleId", "version"):
        if not isinstance(binding[field], str) or not binding[field]:
            raise ValueError(f"Runtime Module binding.{field} is invalid.")
    for field in ("config", "inputs", "outputs"):
        if not isinstance(binding[field], dict):
            raise ValueError(f"Runtime Module binding.{field} must be an object.")
    definition = verified_module_definition_material(definition_authority)
    if any(
        binding[field] != definition[field]
        for field in ("kind", "moduleId", "version")
    ):
        raise ValueError(
            "Runtime Module binding identity does not match its Definition."
        )
    validate_config(
        binding["config"],
        definition["configSchema"],
        path=f"Runtime Module binding '{binding['instanceId']}'.config",
    )
    ports = normalize_ports(
        definition["ports"],
        label=f"Runtime Module binding '{binding['instanceId']}' ports",
    )
    for direction in ("inputs", "outputs"):
        wired = binding[direction]
        unknown = sorted(set(wired) - set(ports[direction]))
        if unknown:
            raise ValueError(
                f"Runtime Module binding '{binding['instanceId']}' binds undeclared "
                f"{direction} port(s): " + ", ".join(unknown)
            )
        missing = sorted(
            name for name, spec in ports[direction].items()
            if spec["required"] and name not in wired
        )
        if missing:
            raise ValueError(
                f"Runtime Module binding '{binding['instanceId']}' omits required "
                f"{direction} port(s): " + ", ".join(missing)
            )
        if any(
            not isinstance(wire_id, str)
            or not wire_id.strip()
            or wire_id.strip() == "null"
            for wire_id in wired.values()
        ):
            raise ValueError(
                f"Runtime Module binding '{binding['instanceId']}' has an invalid "
                f"{direction} wire ID."
            )
    return _ModuleInvocationAuthority(
        binding,
        definition_authority,
        _token=_MODULE_INVOCATION_AUTHORITY_TOKEN,
    )


def bind_module_invocation_authorities(
    node_ids,
    bindings,
    definition_authorities,
):
    """Bind an exact ordered parent node set to verified Module Definitions."""

    node_ids = tuple(node_ids)
    if (
        any(not isinstance(node_id, str) or not node_id for node_id in node_ids)
        or len(node_ids) != len(set(node_ids))
    ):
        raise ValueError("Module invocation node IDs must be unique strings.")
    if not isinstance(bindings, Mapping) or set(bindings) != set(node_ids):
        raise ValueError("Module invocation bindings must exactly match its node IDs.")
    if not isinstance(definition_authorities, Mapping):
        raise ValueError("Module Definition authorities must be an object.")
    result = {}
    for node_id in node_ids:
        binding = bindings[node_id]
        key = definition_key(
            binding["kind"],
            binding["moduleId"],
            binding["version"],
        )
        if key not in definition_authorities:
            raise ValueError(f"Module invocation is missing Definition authority: {key}")
        result[node_id] = bind_module_invocation_authority(
            binding,
            definition_authorities[key],
        )
    return result


def module_invocation_material(authority):
    """Return the detached binding and Definition proof from a nominal authority."""

    require_module_invocation_authority(authority)
    return authority._material()


def require_module_invocation_authority(authority):
    """Validate the nominal type without projecting or copying its material."""

    if type(authority) is not _ModuleInvocationAuthority:
        raise TypeError("Module Invocation authority is Engine-owned.")
