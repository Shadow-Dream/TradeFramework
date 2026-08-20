"""Expansion and structural queries for normalized DataKey contracts."""

from collections import OrderedDict
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from typing import Any, Dict

import engine.contracts.data_compatibility as data_compatibility_contract
import engine.contracts.data_model as data_model_contract
import engine.contracts.data_path as data_path_contract
from engine.contracts import strict_json


__all__ = (
    "contract_path_required", "contract_root_paths",
    "contract_expansion_cache_scope", "expand_contracts",
    "expanded_contract_path_required", "expanded_contract_root_paths",
    "literal_data_key_schema", "merge_object_schemas", "prefixed_contracts",
    "resolve_contract_path", "resolve_expanded_contract_path",
)


_MISSING = object()
_EXPAND_CONTRACT_CACHE_MAX_ENTRIES = 32
_EXPAND_CONTRACT_CACHE = ContextVar(
    "engine_contract_expansion_cache",
    default=None,
)


def _has_uncacheable_json_topology(value: Any) -> bool:
    """Return whether JSON round-tripping would change Python-level semantics."""
    seen = set()
    pending = [value]
    while pending:
        current = pending.pop()
        if type(current) is dict:
            identity = id(current)
            if identity in seen:
                return True
            seen.add(identity)
            pending.extend(current.keys())
            pending.extend(current.values())
        elif type(current) is list:
            identity = id(current)
            if identity in seen:
                return True
            seen.add(identity)
            pending.extend(current)
        elif type(current) is str and not current.isascii():
            if any("\ud800" <= character <= "\udfff" for character in current):
                return True
    return False


def prefixed_contracts(prefix, contracts):
    """Return normalized contracts rooted beneath one DataKey prefix."""
    if not isinstance(prefix, str):
        raise ValueError("DataKey prefix must be a string.")
    if not isinstance(contracts, Mapping):
        raise ValueError("DataKey contracts must be an object.")
    prefix = prefix.strip(".")
    return expand_contracts({
        f"{prefix}.{data_key}" if prefix else data_key:
            data_model_contract.normalize_data_key_schema(
                schema,
                path=f"{prefix}.{data_key}" if prefix else data_key,
            )
        for data_key, schema in contracts.items()
    })


def merge_object_schemas(left: Any, right: Any, path: str) -> Dict[str, Any]:
    left = data_model_contract.normalize_schema(left)
    right = data_model_contract.normalize_schema(right)
    if left is False or right is False:
        raise ValueError(f"DataKey '{path}' has conflicting schemas.")
    if left and "object" not in data_model_contract.normalized_schema_types(left):
        raise ValueError(f"DataKey parent '{path}' is not an object.")
    if right and "object" not in data_model_contract.normalized_schema_types(right):
        raise ValueError(f"DataKey parent '{path}' is not an object.")
    structural = {"type", "properties", "required", "additionalProperties"}
    result = {}
    for keyword in sorted((set(left) | set(right)) - structural):
        if (
            keyword in left
            and keyword in right
            and not data_model_contract.json_values_equal(left[keyword], right[keyword])
        ):
            raise ValueError(
                f"DataKey '{path}' has conflicting object keyword '{keyword}'."
            )
        result[keyword] = deepcopy(left.get(keyword, right.get(keyword)))

    merged_types = (
        data_model_contract.normalized_schema_types(left)
        | data_model_contract.normalized_schema_types(right)
    )
    result["type"] = (
        next(iter(merged_types)) if len(merged_types) == 1 else sorted(merged_types)
    )
    left_properties = left.get("properties", {})
    right_properties = right.get("properties", {})
    left_extra = left.get("additionalProperties", True)
    right_extra = right.get("additionalProperties", True)
    properties = dict(left_properties)
    for name, child in right_properties.items():
        if (
            name in properties
            and not data_model_contract.json_values_equal(properties[name], child)
        ):
            if (
                "object" in data_model_contract.normalized_schema_types(properties[name])
                and "object" in data_model_contract.normalized_schema_types(child)
            ):
                properties[name] = merge_object_schemas(properties[name], child, f"{path}.{name}")
            elif not (
                data_compatibility_contract.normalized_schemas_compatible(properties[name], child)
                and data_compatibility_contract.normalized_schemas_compatible(child, properties[name])
            ):
                raise ValueError(f"DataKey '{path}.{name}' has conflicting schemas.")
        else:
            properties[name] = child

    # A typed map can also emit any explicitly named property owned by the
    # other component.  Conflicting value contracts cannot be merged without
    # losing one producer's valid output.
    for explicit_properties, other_extra in (
        (left_properties, right_extra),
        (right_properties, left_extra),
    ):
        if not isinstance(other_extra, Mapping):
            continue
        for name, child in explicit_properties.items():
            if not (
                data_compatibility_contract.normalized_schemas_compatible(child, other_extra)
                and data_compatibility_contract.normalized_schemas_compatible(other_extra, child)
            ):
                raise ValueError(f"DataKey '{path}.{name}' has conflicting schemas.")

    if left_extra is True or right_extra is True:
        merged_extra = True
    elif left_extra is False:
        merged_extra = deepcopy(right_extra)
    elif right_extra is False:
        merged_extra = deepcopy(left_extra)
    elif data_model_contract.json_values_equal(left_extra, right_extra):
        merged_extra = deepcopy(left_extra)
    elif (
        "object" in data_model_contract.normalized_schema_types(left_extra)
        and "object" in data_model_contract.normalized_schema_types(right_extra)
    ):
        merged_extra = merge_object_schemas(
            left_extra, right_extra, f"{path}.*"
        )
    else:
        raise ValueError(f"DataKey '{path}.*' has conflicting schemas.")

    result["properties"] = properties
    result["required"] = sorted(
        set(left.get("required", [])) | set(right.get("required", []))
    )
    result["additionalProperties"] = merged_extra
    return data_model_contract.normalize_schema(result)


def _schema_explicit_child_names(schema: Any) -> set[str]:
    """Return statically named object fields, including composed/literal ones."""
    if not isinstance(schema, Mapping):
        return set()
    names = set(schema.get("properties", {}))
    literals = []
    if "const" in schema:
        literals.append(schema["const"])
    literals.extend(schema.get("enum", []))
    for value in literals:
        if type(value) is dict:
            names.update(value)
    for keyword in ("anyOf", "oneOf", "allOf"):
        for branch in schema.get(keyword, []):
            names.update(_schema_explicit_child_names(branch))
    return names


def _schema_union(options, path):
    unique = []
    for option in options:
        if not any(data_model_contract.json_values_equal(option, existing) for existing in unique):
            unique.append(option)
    if not unique:
        return False
    if len(unique) == 1:
        return deepcopy(unique[0])
    return data_model_contract.normalize_data_key_schema(
        {"anyOf": [deepcopy(option) for option in unique]}, path=path
    )


def _schema_intersection(options, path):
    material = [option for option in options if option not in ({}, True)]
    if any(option is False for option in material):
        return False
    if not material:
        return {}
    unique = []
    for option in material:
        if not any(data_model_contract.json_values_equal(option, existing) for existing in unique):
            unique.append(option)
    if len(unique) == 1:
        return deepcopy(unique[0])
    return data_model_contract.normalize_data_key_schema(
        {"allOf": [deepcopy(option) for option in unique]}, path=path
    )


def _schema_child_schema(schema: Any, name: str, *, path: str = "DataKey") -> Any:
    """Resolve the value contract for a named child whenever that child exists."""
    if schema is False or not isinstance(schema, Mapping):
        return False
    constraints = []
    has_direct_object_shape = bool(
        set(schema) & {"type", "properties", "required", "additionalProperties"}
    )
    if has_direct_object_shape:
        if "object" not in data_model_contract.normalized_schema_types(schema):
            return False
        properties = schema.get("properties", {})
        if name in properties:
            constraints.append(properties[name])
        else:
            additional = schema.get("additionalProperties", True)
            if additional is False:
                return False
            if isinstance(additional, Mapping):
                constraints.append(additional)

    if "const" in schema:
        value = schema["const"]
        if type(value) is not dict or name not in value:
            return False
        constraints.append(literal_data_key_schema(value[name]))
    if "enum" in schema:
        literal_children = [
            literal_data_key_schema(value[name])
            for value in schema["enum"]
            if type(value) is dict and name in value
        ]
        if not literal_children:
            return False
        constraints.append(_schema_union(literal_children, f"{path}.{name}"))

    for keyword in ("anyOf", "oneOf"):
        if keyword not in schema:
            continue
        branch_children = [
            child
            for branch in schema[keyword]
            if (child := _schema_child_schema(
                branch, name, path=f"{path}.{keyword}"
            )) is not False
        ]
        if not branch_children:
            return False
        constraints.append(
            _schema_union(branch_children, f"{path}.{name}")
        )
    if "allOf" in schema:
        branch_children = [
            _schema_child_schema(branch, name, path=f"{path}.allOf")
            for branch in schema["allOf"]
        ]
        if any(child is False for child in branch_children):
            return False
        constraints.append(
            _schema_intersection(branch_children, f"{path}.{name}")
        )
    return _schema_intersection(constraints, f"{path}.{name}")


def _expand_contracts_uncached(contracts: Mapping[str, Any]) -> Dict[str, Any]:
    """Build parent object schemas and flatten every declared nested property."""
    if not isinstance(contracts, Mapping):
        raise ValueError("DataKey contracts must be an object mapping paths to JSON Schemas.")
    tree: Dict[str, Any] = {}
    explicit: Dict[tuple[str, ...], Any] = {}
    for path, schema in contracts.items():
        parts = data_path_contract.split_data_path(path)
        normalized = data_model_contract.normalize_data_key_schema(schema, path=str(path))
        previous = explicit.get(parts)
        if previous is not None and not data_model_contract.json_values_equal(previous, normalized):
            raise ValueError(f"DataKey '{path}' has duplicate conflicting schemas.")
        explicit[parts] = normalized
        node = tree
        for part in parts:
            node = node.setdefault(part, {})

    def build(node: Dict[str, Any], parts: tuple[str, ...]) -> Any:
        own = explicit.get(parts)
        children = {name: build(child, parts + (name,)) for name, child in node.items()}
        if children:
            path = ".".join(parts)
            existing_properties = own.get("properties", {}) if isinstance(own, Mapping) else {}
            # ``expand_contracts`` emits every parent property as a flattened
            # child.  On a later expansion those derived children are not new
            # producers and must not be merged against their parent's typed-map
            # default a second time.
            independent_children = {
                name: child
                for name, child in children.items()
                if not (
                    name in existing_properties
                    and data_model_contract.json_values_equal(existing_properties[name], child)
                )
                and not (
                    own is not None
                    and (
                        resolved := _schema_child_schema(
                            own, name, path=f"{path}.{name}"
                        )
                    ) is not False
                    and data_model_contract.json_values_equal(resolved, child)
                )
            }
            child_schema = {
                "type": "object",
                "properties": independent_children,
                "required": sorted(
                    set(independent_children) - set(existing_properties)
                ),
                "additionalProperties": False,
            }
            if independent_children:
                own = (
                    child_schema
                    if own is None
                    else merge_object_schemas(own, child_schema, path)
                )
        return data_model_contract.normalize_data_key_schema(
            own if own is not None else {},
            path=".".join(parts),
        )

    roots = {name: build(node, (name,)) for name, node in tree.items()}
    expanded: Dict[str, Any] = {}

    def emit(path: str, schema: Any) -> None:
        normalized = data_model_contract.normalize_data_key_schema(schema, path=path)
        existing = expanded.get(path)
        if existing is not None and not data_model_contract.json_values_equal(existing, normalized):
            if (
                "object" in data_model_contract.normalized_schema_types(existing)
                and "object" in data_model_contract.normalized_schema_types(normalized)
            ):
                normalized = merge_object_schemas(existing, normalized, path)
            else:
                raise ValueError(f"DataKey '{path}' has conflicting schemas.")
        expanded[path] = normalized
        if isinstance(normalized, Mapping):
            for name in sorted(_schema_explicit_child_names(normalized)):
                child = _schema_child_schema(normalized, name, path=path)
                if child is not False:
                    emit(f"{path}.{name}", child)

    for name, schema in roots.items():
        emit(name, schema)
    return expanded


@contextmanager
def contract_expansion_cache_scope():
    """Memoize pure contract compilation work for one bounded operation."""
    existing = _EXPAND_CONTRACT_CACHE.get()
    if existing is not None:
        with data_model_contract.normalized_data_key_schema_cache_scope():
            yield
        return
    token = _EXPAND_CONTRACT_CACHE.set(OrderedDict())
    try:
        with data_model_contract.normalized_data_key_schema_cache_scope():
            yield
    finally:
        _EXPAND_CONTRACT_CACHE.reset(token)


def expand_contracts(contracts: Mapping[str, Any]) -> Dict[str, Any]:
    """Build a detached expanded snapshot, memoized by exact JSON content.

    Contract expansion is a pure compilation step repeated across artifact,
    authority, and runtime boundaries.  The cache key is the complete strict
    JSON material rather than object identity, so caller mutation cannot make
    a stale entry authoritative.  Cached values are serialized immutable
    material and every hit is decoded into a new ownership tree.

    Non-JSON Mapping implementations and invalid schema inputs retain the
    original uncached validation path and its diagnostics.
    """
    if not isinstance(contracts, Mapping):
        raise ValueError(
            "DataKey contracts must be an object mapping paths to JSON Schemas."
        )
    cache = _EXPAND_CONTRACT_CACHE.get()
    if (
        cache is None
        or type(contracts) is not dict
        or not strict_json.is_exact_json(contracts)
        or _has_uncacheable_json_topology(contracts)
    ):
        return _expand_contracts_uncached(contracts)
    try:
        cache_key = strict_json.dumps(
            contracts,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError):
        return _expand_contracts_uncached(contracts)
    cached = cache.get(cache_key)
    if cached is not None:
        cache.move_to_end(cache_key)
        return strict_json.loads(cached)
    expanded = _expand_contracts_uncached(contracts)
    try:
        encoded = strict_json.dumps(
            expanded,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError):
        return expanded
    cache[cache_key] = encoded
    cache.move_to_end(cache_key)
    while len(cache) > _EXPAND_CONTRACT_CACHE_MAX_ENTRIES:
        cache.popitem(last=False)
    return expanded


def resolve_contract_path(
    contracts: Mapping[str, Any],
    path: Any,
    default: Any = None,
) -> Any:
    """Resolve the value schema for any valid DataKey path.

    Flattened declarations alone cannot enumerate keys admitted by a typed
    ``additionalProperties`` map.  Resolve from the root schema so explicit
    properties, typed maps, compositions and literal-object schemas all obey
    the same contract semantics.
    """
    expanded = expand_contracts(contracts)
    return resolve_expanded_contract_path(expanded, path, default)


def resolve_expanded_contract_path(
    expanded_contracts: Mapping[str, Any],
    path: Any,
    default: Any = None,
) -> Any:
    """Resolve a path from one already-expanded compilation snapshot."""
    if not isinstance(expanded_contracts, Mapping):
        raise ValueError("Expanded DataKey contracts must be an object mapping.")
    parts = data_path_contract.split_data_path(path)
    expanded = expanded_contracts
    schema = expanded.get(parts[0], _MISSING)
    if schema is _MISSING:
        return default
    for index, segment in enumerate(parts[1:], start=1):
        schema = _schema_child_schema(
            schema,
            segment,
            path=".".join(parts[:index]),
        )
        if schema is False:
            return default
    return deepcopy(schema)


def contract_root_paths(contracts: Mapping[str, Any]) -> frozenset[str]:
    """Return the declared root DataKeys in a contract map."""
    return expanded_contract_root_paths(expand_contracts(contracts))


def expanded_contract_root_paths(
    expanded_contracts: Mapping[str, Any],
) -> frozenset[str]:
    """Return root paths from one already-expanded compilation snapshot."""
    if not isinstance(expanded_contracts, Mapping):
        raise ValueError("Expanded DataKey contracts must be an object mapping.")
    return frozenset(
        path for path in expanded_contracts
        if len(data_path_contract.split_data_path(path)) == 1
    )


def _required_path_schema(parts: tuple[str, ...]) -> Any:
    schema: Any = {}
    for segment in reversed(parts):
        schema = {
            "type": "object",
            "properties": {segment: schema},
            "required": [segment],
            "additionalProperties": True,
        }
    return schema


def contract_path_required(
    contracts: Mapping[str, Any],
    path: Any,
    *,
    required_roots: Any = None,
) -> bool:
    """Return whether a complete DataKey path is guaranteed to exist.

    A contract describes the value *after* a root key exists; JSON Schema for
    that value cannot express whether the root key itself is present in the
    surrounding Data Dict.  Callers that propagate optional writes therefore
    pass their independently compiled ``required_roots`` set.  Contract-only
    callers retain the natural declaration semantics that every root exists.
    """
    expanded = expand_contracts(contracts)
    return expanded_contract_path_required(
        expanded,
        path,
        required_roots=required_roots,
    )


def expanded_contract_path_required(
    expanded_contracts: Mapping[str, Any],
    path: Any,
    *,
    required_roots: Any = None,
) -> bool:
    """Check presence in an already-expanded contract compilation snapshot.

    Contract compilers commonly check every declared path after one call to
    ``expand_contracts``.  Re-expanding the complete schema graph for each path
    is both redundant and quadratic in the number of declarations.
    """
    if not isinstance(expanded_contracts, Mapping):
        raise ValueError("Expanded DataKey contracts must be an object mapping.")
    parts = data_path_contract.split_data_path(path)
    expanded = expanded_contracts
    schema = expanded.get(parts[0])
    if schema is None:
        return False
    roots = (
        expanded_contract_root_paths(expanded)
        if required_roots is None
        else frozenset(required_roots)
    )
    if parts[0] not in roots:
        return False
    if len(parts) == 1:
        return True
    # ``expanded_contracts`` is produced by ``expand_contracts`` and the
    # required-path schema is constructed in normalized form above.  Calling
    # the public compatibility entry here normalized both trees again for
    # every declared Result path (hundreds of repeated full-root traversals).
    # Keep the exact same conservative subset proof over the already verified
    # normalized material.
    return data_compatibility_contract.normalized_schemas_compatible(
        schema, _required_path_schema(parts[1:])
    )


def literal_data_key_schema(value):
    if type(value) is dict:
        properties = {
            name: literal_data_key_schema(child)
            for name, child in value.items()
        }
        return {
            "type": "object",
            "properties": properties,
            "required": sorted(properties),
            "additionalProperties": False,
        }
    return {"const": deepcopy(value)}
