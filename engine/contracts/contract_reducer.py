"""Ordered contract writes and required-root state reduction."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Dict

import engine.contracts.contract_expansion as contract_expansion
import engine.contracts.data_compatibility as data_compatibility_contract
import engine.contracts.data_model as data_model_contract
import engine.contracts.data_path as data_path_contract


__all__ = (
    "ExpandedContractWriteReducer",
    "apply_expanded_contract_writes",
    "write_contract_path",
    "write_contract_state",
    "write_required_roots",
)


def write_required_roots(
    required_roots: Any,
    path: Any,
    *,
    required: bool,
) -> frozenset[str]:
    """Apply one ordered write to the Data Dict root-presence state."""
    if not isinstance(required, bool):
        raise ValueError("DataKey root presence requiredness must be a boolean.")
    roots = set(required_roots or ())
    root = data_path_contract.split_data_path(path)[0]
    if required:
        roots.add(root)
    return frozenset(roots)


def _optional_schema_union(left: Any, right: Any, path: str) -> Any:
    if left is None or data_model_contract.json_values_equal(left, right):
        return deepcopy(right)
    left = data_model_contract.normalize_data_key_schema(left, path=path)
    right = data_model_contract.normalize_data_key_schema(right, path=path)
    if data_compatibility_contract.normalized_schemas_compatible(left, right):
        return deepcopy(right)
    if data_compatibility_contract.normalized_schemas_compatible(right, left):
        return deepcopy(left)
    # Optional output means either the complete old value survives or the
    # complete new value is written.  Keeping that semantic union avoids
    # dropping composition, literal, and closed-object constraints.
    return data_model_contract.normalize_data_key_schema(
        {"anyOf": [deepcopy(left), deepcopy(right)]}, path=path
    )


def _schema_structure_categories(schema):
    types = data_model_contract.possible_runtime_types(schema)
    return "object" in types, bool(types - {"object"})


def _require_write_structure_compatibility(old_schema, new_schema, path):
    old_object, old_non_object = _schema_structure_categories(old_schema)
    new_object, new_non_object = _schema_structure_categories(new_schema)
    if (old_object and new_non_object) or (old_non_object and new_object):
        raise ValueError(f"DataKey contract write '{path}' changes JSON structure.")


def _created_path_schema(parts, value_schema, *, path):
    if not parts:
        return deepcopy(value_schema)
    name = parts[0]
    return data_model_contract.normalize_data_key_schema({
        "type": "object",
        "properties": {
            name: _created_path_schema(
                parts[1:], value_schema, path=f"{path}.{name}"
            )
        },
        "required": [name],
        "additionalProperties": False,
    }, path=path)


def _write_nested_schema(schema, parts, value_schema, *, required, path):
    schema = data_model_contract.normalize_data_key_schema(schema, path=path)
    types = data_model_contract.possible_runtime_types(schema)
    if not types:
        schema = {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
        types = {"object"}
    if types != {"object"}:
        raise ValueError(
            f"DataKey contract write '{path}.{'.'.join(parts)}' collides with a non-object path."
        )
    literals = (
        [schema["const"]] if "const" in schema else schema.get("enum")
    )
    if literals is not None:
        literal_schemas = [
            contract_expansion.literal_data_key_schema(value)
            for value in literals
        ]
        literal_schema = (
            literal_schemas[0]
            if len(literal_schemas) == 1
            else {"anyOf": literal_schemas}
        )
        return _write_nested_schema(
            literal_schema,
            parts,
            value_schema,
            required=required,
            path=path,
        )
    result = deepcopy(schema)
    # Constraints on the overwritten child no longer describe the post-write
    # value.  Transform composition branches too; exact-one can become
    # overlapping after a property replacement, so widen it to anyOf.
    result.pop("const", None)
    result.pop("enum", None)
    for keyword in ("allOf", "anyOf"):
        if keyword in result:
            result[keyword] = [
                _write_nested_schema(
                    branch,
                    parts,
                    value_schema,
                    required=required,
                    path=path,
                )
                for branch in result[keyword]
            ]
    if "oneOf" in result:
        transformed = [
            _write_nested_schema(
                branch,
                parts,
                value_schema,
                required=required,
                path=path,
            )
            for branch in result.pop("oneOf")
        ]
        result.setdefault("anyOf", []).extend(transformed)

    base_keywords = set(result) - {
        "allOf", "anyOf", "oneOf", "title", "description", "default"
    }
    if not base_keywords:
        # A composition-only object has no independent sibling schema.  Adding
        # a closed top-level ``properties`` object would AND an artificial
        # restriction with every branch and reject the branches' real fields.
        return data_model_contract.normalize_data_key_schema(result, path=path)

    name = parts[0]
    properties = dict(result.get("properties", {}))
    required_names = set(result.get("required", []))
    old_child = properties.get(name)
    if old_child is None:
        additional = result.get("additionalProperties", True)
        if additional is not False:
            old_child = {} if additional is True else additional
    child_path = f"{path}.{name}"
    if len(parts) == 1:
        if old_child is not None:
            _require_write_structure_compatibility(
                old_child, value_schema, child_path
            )
        properties[name] = (
            deepcopy(value_schema)
            if required
            else _optional_schema_union(old_child, value_schema, child_path)
        )
    else:
        created_child = _created_path_schema(
            parts[1:], value_schema, path=child_path
        )
        if old_child is None:
            updated_child = created_child
        else:
            updated_child = _write_nested_schema(
                old_child,
                parts[1:],
                value_schema,
                required=required,
                path=child_path,
            )
            if name not in required_names:
                updated_child = _optional_schema_union(
                    updated_child, created_child, child_path
                )
        properties[name] = updated_child
    if required or name in required_names:
        required_names.add(name)
    else:
        required_names.discard(name)
    result["properties"] = properties
    result["required"] = sorted(required_names)
    if not data_model_contract.declared_types(result):
        result["type"] = "object"
    return data_model_contract.normalize_data_key_schema(result, path=path)


class ExpandedContractWriteReducer:
    """Apply ordered writes to one already-expanded contract compilation state."""

    def __init__(self, expanded_contracts: Mapping[str, Any], required_roots: Any):
        if not isinstance(expanded_contracts, Mapping):
            raise ValueError("Expanded DataKey contracts must be an object mapping.")
        root_schemas = {
            path: deepcopy(schema)
            for path, schema in expanded_contracts.items()
            if len(data_path_contract.split_data_path(path)) == 1
        }
        missing_roots = sorted({
            data_path_contract.split_data_path(path)[0]
            for path in expanded_contracts
            if data_path_contract.split_data_path(path)[0] not in root_schemas
        })
        if missing_roots:
            raise ValueError(
                "Expanded DataKey contracts are missing root declaration(s): "
                + ", ".join(missing_roots)
            )
        self._root_schemas = root_schemas
        self._required_roots = set(required_roots or ())

    def apply(self, path: Any, schema: Any, *, required: bool) -> None:
        """Apply exactly one write while preserving the caller's global order."""
        if not isinstance(required, bool):
            raise ValueError("DataKey contract write requiredness must be a boolean.")
        parts = data_path_contract.split_data_path(path)
        path_text = ".".join(parts)
        value_schema = data_model_contract.normalize_data_key_schema(
            schema, path=path_text
        )
        root = parts[0]
        root_schema = self._root_schemas.get(root)
        if len(parts) == 1:
            if root_schema is not None:
                _require_write_structure_compatibility(
                    root_schema, value_schema, root
                )
            updated_root = (
                value_schema
                if required or root_schema is None
                else _optional_schema_union(root_schema, value_schema, root)
            )
        else:
            if root_schema is None:
                updated_root = _created_path_schema(
                    parts[1:], value_schema, path=root
                )
            else:
                updated_root = _write_nested_schema(
                    root_schema,
                    parts[1:],
                    value_schema,
                    required=required,
                    path=root,
                )
                if root not in self._required_roots:
                    updated_root = _optional_schema_union(
                        updated_root,
                        _created_path_schema(parts[1:], value_schema, path=root),
                        root,
                    )
        # The previous implementation rebuilt ``remaining + root`` after each
        # write.  Preserve that observable canonical mapping order exactly.
        self._root_schemas.pop(root, None)
        self._root_schemas[root] = updated_root
        if required:
            self._required_roots.add(root)

    def apply_many(self, writes: Any) -> None:
        """Apply a batch transactionally without crossing an observation boundary."""
        root_schemas = self._root_schemas
        required_roots = self._required_roots
        self._root_schemas = deepcopy(root_schemas)
        self._required_roots = set(required_roots)
        try:
            for path, schema, required in writes:
                self.apply(path, schema, required=required)
        except BaseException:
            self._root_schemas = root_schemas
            self._required_roots = required_roots
            raise

    def materialize(self):
        """Return a detached expanded map and its root-presence state."""
        return (
            contract_expansion.expand_contracts(self._root_schemas),
            frozenset(self._required_roots),
        )


def apply_expanded_contract_writes(
    expanded_contracts: Mapping[str, Any],
    required_roots: Any,
    writes: Any,
):
    """Apply consecutive writes to an explicit expanded compilation snapshot."""
    reducer = ExpandedContractWriteReducer(
        expanded_contracts,
        required_roots,
    )
    reducer.apply_many(writes)
    return reducer.materialize()


def write_contract_path(
    contracts: Mapping[str, Any],
    path: Any,
    schema: Any,
    *,
    required: bool = True,
    required_roots: Any = None,
) -> Dict[str, Any]:
    """Apply one ordered runtime DataKey write to its static contract map."""
    if not isinstance(required, bool):
        raise ValueError("DataKey contract write requiredness must be a boolean.")
    expanded = contract_expansion.expand_contracts(contracts)
    roots_before = (
        contract_expansion.expanded_contract_root_paths(expanded)
        if required_roots is None
        else frozenset(required_roots)
    )
    updated, _updated_roots = apply_expanded_contract_writes(
        expanded,
        roots_before,
        ((path, schema, required),),
    )
    return updated


def write_contract_state(
    contracts: Mapping[str, Any],
    required_roots: Any,
    path: Any,
    schema: Any,
    *,
    required: bool,
):
    """Apply one ordered write to value schemas and root presence together."""
    if not isinstance(required, bool):
        raise ValueError("DataKey contract write requiredness must be a boolean.")
    expanded = contract_expansion.expand_contracts(contracts)
    roots_before = (
        contract_expansion.expanded_contract_root_paths(expanded)
        if required_roots is None
        else frozenset(required_roots)
    )
    updated, _updated_roots = apply_expanded_contract_writes(
        expanded,
        roots_before,
        ((path, schema, required),),
    )
    return updated, write_required_roots(
        required_roots, path, required=required
    )
