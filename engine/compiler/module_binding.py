"""Shared authority lookup for compilers that bind archived Modules."""

from __future__ import annotations

from collections.abc import Mapping

from engine.authority import module_definition as module_definition_authority
from engine.contracts import strict_json
from engine.contracts.module import definition_key


def resolve_verified_module_definition(
    definitions,
    kind,
    module_id,
    version,
    *,
    verified_definitions=None,
    label="Module",
):
    """Resolve one exact repository identity and bind its verified authority."""
    if not isinstance(definitions, Mapping):
        raise ValueError(f"{label} Definition repository must be an object.")
    key = definition_key(kind, module_id, version)
    expected_identity = (kind, module_id, version)
    authority = None
    if verified_definitions is not None:
        authority = verified_definitions.get(key)
    if authority is not None:
        definition = module_definition_authority.verified_module_definition_material(
            authority
        )
    else:
        indexed_definition = definitions.get(key)
        if indexed_definition is None:
            raise ValueError(f"{label} definition does not exist: {key}")
        if not isinstance(indexed_definition, Mapping):
            raise ValueError(f"{label} definition must be an object: {key}")
        indexed_identity = tuple(
            indexed_definition.get(field)
            for field in ("kind", "moduleId", "version")
        )
        if indexed_identity != expected_identity:
            raise ValueError(
                f"{label} Definition identity does not match its repository key: {key}"
            )
        authority = module_definition_authority.verify_module_definition_authority(
            indexed_definition
        )
        definition = module_definition_authority.verified_module_definition_material(
            authority
        )
        if strict_json.dumps(definition, sort_keys=True) != strict_json.dumps(
            indexed_definition, sort_keys=True
        ):
            raise ValueError(
                f"{label} Definition '{key}' does not match its verified authority."
            )
        if verified_definitions is not None:
            verified_definitions[key] = authority
    actual_identity = tuple(
        definition.get(field) for field in ("kind", "moduleId", "version")
    )
    if actual_identity != expected_identity:
        raise ValueError(
            f"{label} Definition identity does not match its repository key: {key}"
        )
    return key, definition, authority


__all__ = ("resolve_verified_module_definition",)
