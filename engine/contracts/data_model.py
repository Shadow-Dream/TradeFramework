#!/usr/bin/env python3
"""Recursive JSON Schema contracts shared by every TradeEngine data boundary.

The contract language is deliberately a strict, transportable subset of JSON
Schema.  There are no TradeEngine-specific type names.  ``{}`` is the wildcard
schema; all other schemas are composed from JSON's native types.
"""

import math
from collections import OrderedDict
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from typing import Any, Dict

from engine.contracts import strict_json

try:
    import orjson as _orjson
except ImportError:  # The uncached validator remains the portable authority.
    _orjson = None


__all__ = (
    "ANNOTATION_KEYS",
    "CompiledValidationPlan",
    "JSON_TYPES",
    "compile_validation_plan",
    "compiled_validation_failure",
    "declared_types",
    "infer_schema",
    "json_type",
    "json_value_in",
    "json_values_equal",
    "normalize_data_key_schema",
    "normalize_schema",
    "normalized_schema_label",
    "normalized_schema_types",
    "port_schema",
    "possible_runtime_types",
    "raise_compiled_validation_failure",
    "schema_label",
    "schema_types",
    "validate_json_value",
    "validate_normalized_json_value",
)


JSON_TYPES = {"null", "boolean", "object", "array", "number", "integer", "string"}
_SCHEMA_KEYS = {
    "type", "properties", "required", "additionalProperties", "items",
    "enum", "const", "anyOf", "oneOf", "allOf", "title", "description",
    "default",
}
ANNOTATION_KEYS = {"title", "description", "default"}
_MISSING = object()
_NORMALIZED_DATA_KEY_SCHEMA_CACHE_MAX_ENTRIES = 256
_NORMALIZED_DATA_KEY_SCHEMA_CACHE = ContextVar(
    "engine_normalized_data_key_schema_cache",
    default=None,
)


def _has_aliased_json_containers(value: Any) -> bool:
    """Reject cache material whose JSON round-trip would lose alias topology."""

    seen = set()
    pending = [value]
    while pending:
        current = pending.pop()
        if type(current) is dict:
            identity = id(current)
            if identity in seen:
                return True
            seen.add(identity)
            pending.extend(current.values())
        elif type(current) is list:
            identity = id(current)
            if identity in seen:
                return True
            seen.add(identity)
            pending.extend(current)
    return False


@contextmanager
def normalized_data_key_schema_cache_scope():
    """Provide the contracts package's bounded schema-normalization scope."""

    existing = _NORMALIZED_DATA_KEY_SCHEMA_CACHE.get()
    if existing is not None:
        yield
        return
    token = _NORMALIZED_DATA_KEY_SCHEMA_CACHE.set(OrderedDict())
    try:
        yield
    finally:
        _NORMALIZED_DATA_KEY_SCHEMA_CACHE.reset(token)


def json_values_equal(left: Any, right: Any) -> bool:
    """Compare JSON literals using Engine runtime types, never Python coercion."""
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return (
            left.keys() == right.keys()
            and all(json_values_equal(value, right[name]) for name, value in left.items())
        )
    if type(left) is list:
        return len(left) == len(right) and all(
            json_values_equal(left_value, right_value)
            for left_value, right_value in zip(left, right)
        )
    return left == right


def json_value_in(value: Any, candidates) -> bool:
    return any(json_values_equal(value, candidate) for candidate in candidates)




def normalize_schema(value: Any) -> Any:
    """Return a canonical recursive schema or reject unsupported pseudo types."""
    if value is True:
        return {}
    if value is False:
        return False
    if not isinstance(value, Mapping):
        raise ValueError(
            "A data contract must be a JSON Schema object; "
            "string type aliases are forbidden."
        )
    unknown = set(value) - _SCHEMA_KEYS
    if unknown:
        raise ValueError("Unsupported JSON Schema keyword(s): " + ", ".join(sorted(unknown)))

    # Rebuild structural children below.  Deep-copying the complete remaining
    # subtree at every recursion level makes normalization super-linear for
    # large, deeply nested Pipeline contracts.
    schema = dict(value)
    for keyword in ("enum", "const", "title", "description", "default"):
        if keyword in schema:
            schema[keyword] = deepcopy(schema[keyword])
    raw_type = schema.get("type", _MISSING)
    if raw_type is not _MISSING:
        types = [raw_type] if isinstance(raw_type, str) else raw_type
        if (
            not isinstance(types, list)
            or not types
            or any(item not in JSON_TYPES for item in types)
        ):
            raise ValueError(
                "JSON Schema 'type' must contain only: " + ", ".join(sorted(JSON_TYPES))
            )
        if len(set(types)) != len(types):
            raise ValueError("JSON Schema 'type' contains duplicate entries.")
        schema["type"] = types[0] if len(types) == 1 else list(types)

    if "properties" in schema:
        if not isinstance(schema["properties"], Mapping):
            raise ValueError("JSON Schema 'properties' must be an object.")
        schema["properties"] = {
            str(name): normalize_schema(child) for name, child in schema["properties"].items()
        }
        if declared_types(schema) and "object" not in declared_types(schema):
            raise ValueError("A schema with 'properties' must allow the object type.")
    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, list) or any(not isinstance(name, str) for name in required):
            raise ValueError("JSON Schema 'required' must be an array of property names.")
        if len(set(required)) != len(required):
            raise ValueError("JSON Schema 'required' contains duplicate property names.")
        schema["required"] = list(required)
    if "additionalProperties" in schema:
        additional = schema["additionalProperties"]
        if not isinstance(additional, bool):
            schema["additionalProperties"] = normalize_schema(additional)
    if "items" in schema:
        schema["items"] = normalize_schema(schema["items"])
        if declared_types(schema) and "array" not in declared_types(schema):
            raise ValueError("A schema with 'items' must allow the array type.")
    for keyword in ("anyOf", "oneOf", "allOf"):
        if keyword in schema:
            branches = schema[keyword]
            if not isinstance(branches, list) or not branches:
                raise ValueError(f"JSON Schema '{keyword}' must be a non-empty array.")
            schema[keyword] = [normalize_schema(branch) for branch in branches]
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise ValueError("JSON Schema 'enum' must be a non-empty array.")
    if "enum" in schema and any(
        json_values_equal(schema["enum"][index], schema["enum"][prior])
        for index in range(len(schema["enum"]))
        for prior in range(index)
    ):
        raise ValueError("JSON Schema 'enum' values must be unique.")
    for keyword, values in (
        ("const", [schema["const"]] if "const" in schema else []),
        ("enum", schema.get("enum", [])),
    ):
        for index, literal in enumerate(values):
            failure = compiled_validation_failure(
                literal,
                compile_validation_plan(schema, trusted_json=False),
            )
            if failure is not None:
                raise ValueError(
                    f"JSON Schema '{keyword}' contains a value outside its declared schema."
                )
    return schema


def _normalize_data_key_schema_uncached(
    value: Any,
    *,
    path: str = "DataKey",
) -> Any:
    """Return one closed, recursively typed runtime DataKey contract.

    DataKey values deliberately exclude JSON arrays.  Collections are object
    maps with a concrete ``additionalProperties`` value schema.  Generic JSON
    Schema remains available to configuration documents; this stricter entry
    point is only for Module ports and Pipeline DataKeys.
    """
    schema = normalize_schema(value)
    _validate_data_key_schema(schema, path=path)
    return schema


def normalize_data_key_schema(value: Any, *, path: str = "DataKey") -> Any:
    """Return one normalized DataKey contract with operation-scoped reuse.

    Only successful, exact built-in JSON objects are memoized.  Every input
    whose Python semantics cannot survive an optional JSON codec round-trip
    follows the authoritative uncached path, as does every call outside the
    Engine compiler scope.  Invalid schemas are never cached, so their
    path-specific diagnostics retain their original order and wording.
    """

    cache = _NORMALIZED_DATA_KEY_SCHEMA_CACHE.get()
    if (
        cache is None
        or _orjson is None
        or type(value) is not dict
        or not strict_json.is_exact_json(value)
        or _has_aliased_json_containers(value)
    ):
        return _normalize_data_key_schema_uncached(value, path=path)
    try:
        # Mapping order is part of the current normalized Python material.
        # Do not merge differently ordered inputs merely because JSON object
        # member order is transport-semantically insignificant.
        cache_key = _orjson.dumps(value)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return _normalize_data_key_schema_uncached(value, path=path)
    cached = cache.get(cache_key)
    if cached is not None:
        cache.move_to_end(cache_key)
        return _orjson.loads(cached)

    normalized = _normalize_data_key_schema_uncached(value, path=path)
    try:
        encoded = _orjson.dumps(normalized)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return normalized
    cache[cache_key] = encoded
    cache.move_to_end(cache_key)
    while len(cache) > _NORMALIZED_DATA_KEY_SCHEMA_CACHE_MAX_ENTRIES:
        cache.popitem(last=False)
    return normalized


def _validate_data_key_schema(schema: Any, *, path: str) -> None:
    if schema is False:
        return
    if not schema:
        raise ValueError(
            f"DataKey contract '{path}' may not use the wildcard schema {{}}."
        )

    for keyword in ("anyOf", "oneOf", "allOf"):
        for index, branch in enumerate(schema.get(keyword, [])):
            _validate_data_key_schema(
                branch,
                path=f"{path}.{keyword}[{index}]",
            )

    declared = declared_types(schema)
    if "array" in declared or "items" in schema:
        raise ValueError(
            f"DataKey contract '{path}' may not contain the array runtime type; "
            "use a typed object map."
        )
    if not declared and not any(
        keyword in schema
        for keyword in ("anyOf", "oneOf", "allOf", "const", "enum")
    ):
        raise ValueError(f"DataKey contract '{path}' must declare a concrete runtime type.")

    for keyword in ("const", "enum"):
        if keyword not in schema:
            continue
        values = [schema[keyword]] if keyword == "const" else schema[keyword]
        if any(_contains_runtime_array(item) for item in values):
            raise ValueError(
                f"DataKey contract '{path}.{keyword}' may not contain array values."
            )

    if "object" not in declared and "properties" not in schema:
        return

    additional = schema.get("additionalProperties", _MISSING)
    if additional is _MISSING or additional is True:
        raise ValueError(
            f"DataKey object contract '{path}' must close additionalProperties "
            "or declare a concrete value schema."
        )
    for name, child in schema.get("properties", {}).items():
        _validate_data_key_schema(child, path=f"{path}.{name}")
    if isinstance(additional, Mapping):
        _validate_data_key_schema(additional, path=f"{path}.*")


def _contains_runtime_array(value: Any) -> bool:
    if isinstance(value, list):
        return True
    if isinstance(value, Mapping):
        return any(_contains_runtime_array(child) for child in value.values())
    return False


def declared_types(schema: Mapping[str, Any]) -> set[str]:
    raw = schema.get("type")
    if raw is None:
        return set()
    return {raw} if isinstance(raw, str) else set(raw)


def schema_types(schema: Any) -> set[str]:
    schema = normalize_schema(schema)
    return normalized_schema_types(schema)


def normalized_schema_types(schema: Any) -> set[str]:
    if schema is False:
        return set()
    types = declared_types(schema)
    if types:
        return types
    if "properties" in schema or "required" in schema:
        return {"object"}
    if "items" in schema:
        return {"array"}
    return set(JSON_TYPES)


def possible_runtime_types(schema: Any) -> set[str]:
    """Return concrete JSON runtime types allowed by every schema clause.

    Unlike ``normalized_schema_types`` this helper accounts for composition
    siblings and literal constraints.  It is used by ordered DataKey writes,
    where accepting a possible scalar ancestor would make the runtime write
    fail even if another branch happens to be an object.
    """
    if schema is False:
        return set()
    possible = normalized_schema_types(schema)
    if "number" in possible:
        possible = set(possible) | {"integer"}
    if "const" in schema:
        possible &= {json_type(schema["const"])}
    if "enum" in schema:
        possible &= {json_type(value) for value in schema["enum"]}
    for branch in schema.get("allOf", ()):
        possible &= possible_runtime_types(branch)
    for keyword in ("anyOf", "oneOf"):
        if keyword in schema:
            branch_types = set()
            for branch in schema[keyword]:
                branch_types |= possible_runtime_types(branch)
            possible &= branch_types
    return possible


def schema_label(schema: Any) -> str:
    schema = normalize_schema(schema)
    return normalized_schema_label(schema)


def normalized_schema_label(schema: Any) -> str:
    if schema is False:
        return "never"
    if not schema:
        return "any JSON"
    types = normalized_schema_types(schema)
    if types == {"object"} and schema.get("properties"):
        fields = ", ".join(schema["properties"])
        return "object{" + fields + "}"
    return " | ".join(sorted(types))


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if type(value) is dict:
        return "object"
    if type(value) is list:
        return "array"
    return "non-json"


def validate_json_value(
    value: Any,
    schema: Any = None,
    *,
    path: str = "value",
    trusted_json: bool = False,
) -> None:
    """Validate a JSON value with the same recursive schema used by topology."""
    if schema is None:
        raise ValueError(
            "Runtime validation requires an explicit JSON Schema; "
            "use {} for a wildcard."
        )
    schema = normalize_schema(schema)
    _validate_normalized_json_value(value, schema, path=path, trusted_json=trusted_json)


def validate_normalized_json_value(
    value: Any,
    schema: Any,
    *,
    path: str = "value",
    trusted_json: bool = False,
) -> None:
    """Validate with a schema already normalized at compile/load time."""
    _validate_normalized_json_value(
        value,
        schema,
        path=path,
        trusted_json=trusted_json,
    )


class CompiledValidationPlan:
    """Immutable executable shape for one already-normalized JSON Schema."""

    __slots__ = (
        "schema_false", "trusted_wildcard", "wildcard", "allowed_types",
        "type_label", "has_const", "const", "enum", "all_of", "any_of",
        "one_of", "properties", "required", "additional", "items",
    )

    def __init__(
        self,
        *,
        schema_false=False,
        trusted_wildcard=False,
        wildcard=False,
        allowed_types=(),
        type_label="",
        has_const=False,
        const=None,
        enum=None,
        all_of=(),
        any_of=(),
        one_of=(),
        properties=None,
        required=(),
        additional=True,
        items=None,
    ):
        self.schema_false = schema_false
        self.trusted_wildcard = trusted_wildcard
        self.wildcard = wildcard
        self.allowed_types = frozenset(allowed_types)
        self.type_label = type_label
        self.has_const = has_const
        self.const = const
        self.enum = enum
        self.all_of = tuple(all_of)
        self.any_of = tuple(any_of)
        self.one_of = tuple(one_of)
        self.properties = properties or {}
        self.required = tuple(required)
        self.additional = additional
        self.items = items


def compile_validation_plan(schema: Any, *, trusted_json: bool) -> CompiledValidationPlan:
    if schema is False:
        return CompiledValidationPlan(schema_false=True)
    if not schema:
        return CompiledValidationPlan(
            trusted_wildcard=trusted_json,
            wildcard=True,
            allowed_types=JSON_TYPES,
            type_label=" | ".join(sorted(JSON_TYPES)),
        )
    additional_schema = schema.get("additionalProperties", True)
    additional = (
        False
        if additional_schema is False
        else compile_validation_plan(
            additional_schema if isinstance(additional_schema, Mapping) else {},
            trusted_json=trusted_json,
        )
    )
    return CompiledValidationPlan(
        allowed_types=normalized_schema_types(schema),
        type_label=normalized_schema_label(schema),
        has_const="const" in schema,
        const=schema.get("const"),
        enum=tuple(schema["enum"]) if "enum" in schema else None,
        all_of=tuple(
            compile_validation_plan(branch, trusted_json=trusted_json)
            for branch in schema.get("allOf", ())
        ),
        any_of=tuple(
            compile_validation_plan(branch, trusted_json=trusted_json)
            for branch in schema.get("anyOf", ())
        ),
        one_of=tuple(
            compile_validation_plan(branch, trusted_json=trusted_json)
            for branch in schema.get("oneOf", ())
        ),
        properties={
            name: compile_validation_plan(child, trusted_json=trusted_json)
            for name, child in schema.get("properties", {}).items()
        },
        required=schema.get("required", ()),
        additional=additional,
        items=compile_validation_plan(
            schema.get("items", {}),
            trusted_json=trusted_json,
        ),
    )


def compiled_validation_failure(value: Any, plan: CompiledValidationPlan):
    """Return ``(path suffix, error kind, detail)`` or ``None`` on success."""
    if plan.schema_false:
        return (), "forbidden", None
    if plan.trusted_wildcard:
        return None
    actual = json_type(value)
    if actual == "non-json" or (isinstance(value, float) and not math.isfinite(value)):
        return (), "non-json", None

    if plan.wildcard:
        if isinstance(value, dict):
            for name, child in value.items():
                if not isinstance(name, str):
                    return (), "non-string-key", None
                failure = compiled_validation_failure(child, plan)
                if failure:
                    suffix, kind, detail = failure
                    return (name, *suffix), kind, detail
        elif isinstance(value, list):
            for index, child in enumerate(value):
                failure = compiled_validation_failure(child, plan)
                if failure:
                    suffix, kind, detail = failure
                    return (index, *suffix), kind, detail
        return None

    for branch in plan.all_of:
        failure = compiled_validation_failure(value, branch)
        if failure:
            return failure
    if plan.any_of and not any(
        compiled_validation_failure(value, branch) is None
        for branch in plan.any_of
    ):
        return (), "combination", "anyOf"
    if plan.one_of and sum(
        compiled_validation_failure(value, branch) is None
        for branch in plan.one_of
    ) != 1:
        return (), "combination", "oneOf"

    if not (
        actual in plan.allowed_types
        or (actual == "integer" and "number" in plan.allowed_types)
    ):
        return (), "type", (actual, plan.type_label)
    if plan.has_const and not json_values_equal(value, plan.const):
        return (), "const", None
    if plan.enum is not None and not json_value_in(value, plan.enum):
        return (), "enum", None

    if isinstance(value, dict):
        for name in plan.required:
            if name not in value:
                return (name,), "required", None
        for name, child in value.items():
            if not isinstance(name, str):
                return (), "non-string-key", None
            child_plan = plan.properties.get(name)
            if child_plan is None:
                if plan.additional is False:
                    return (name,), "additional", None
                child_plan = plan.additional
            failure = compiled_validation_failure(child, child_plan)
            if failure:
                suffix, kind, detail = failure
                return (name, *suffix), kind, detail
    elif isinstance(value, list):
        for index, child in enumerate(value):
            failure = compiled_validation_failure(child, plan.items)
            if failure:
                suffix, kind, detail = failure
                return (index, *suffix), kind, detail
    return None


def _validation_path(root: str, suffix: tuple[Any, ...]) -> str:
    result = root
    for segment in suffix:
        result += f"[{segment}]" if isinstance(segment, int) else f".{segment}"
    return result
def raise_compiled_validation_failure(failure, path: str) -> None:
    suffix, kind, detail = failure
    label = _validation_path(path, suffix)
    if kind == "required":
        raise ValueError(f"Required field '{label}' is missing.")
    if kind == "additional":
        raise ValueError(f"Field '{label}' is not allowed by its schema.")
    if kind == "non-string-key":
        raise ValueError(f"JSON object '{label}' contains a non-string key.")
    if kind == "forbidden":
        raise ValueError(f"DataKey '{label}' is forbidden by its schema.")
    if kind == "non-json":
        raise ValueError(f"DataKey '{label}' contains a non-JSON value.")
    if kind == "type":
        actual, expected = detail
        raise ValueError(f"DataKey '{label}' is {actual}, expected {expected}.")
    if kind == "const":
        raise ValueError(f"DataKey '{label}' does not equal its schema const.")
    if kind == "enum":
        raise ValueError(f"DataKey '{label}' is not one of its schema enum values.")
    if kind == "combination":
        raise ValueError(f"DataKey '{label}' does not satisfy '{detail}'.")
    raise RuntimeError(f"Unknown compiled validation failure: {kind}")


def _validate_normalized_json_value(
    value: Any,
    schema: Any,
    *,
    path: str,
    trusted_json: bool = False,
) -> None:
    """Validate against a contract already canonicalized by normalize_schema."""
    if schema is False:
        raise ValueError(f"DataKey '{path}' is forbidden by its schema.")
    if trusted_json and not schema:
        return
    actual = json_type(value)
    if actual == "non-json" or (isinstance(value, float) and not math.isfinite(value)):
        raise ValueError(f"DataKey '{path}' contains a non-JSON value.")

    for keyword in ("allOf",):
        for branch in schema.get(keyword, []):
            _validate_normalized_json_value(
                value, branch, path=path, trusted_json=trusted_json
            )
    for keyword in ("anyOf", "oneOf"):
        if keyword in schema:
            matches = 0
            for branch in schema[keyword]:
                try:
                    _validate_normalized_json_value(
                        value, branch, path=path, trusted_json=trusted_json
                    )
                    matches += 1
                except ValueError:
                    pass
            expected = 1 if keyword == "oneOf" else None
            if (
                (expected is not None and matches != expected)
                or (expected is None and matches == 0)
            ):
                raise ValueError(f"DataKey '{path}' does not satisfy '{keyword}'.")

    allowed = normalized_schema_types(schema)
    compatible_actual = actual in allowed or (actual == "integer" and "number" in allowed)
    if not compatible_actual:
        raise ValueError(f"DataKey '{path}' is {actual}, expected {schema_label(schema)}.")
    if "const" in schema and not json_values_equal(value, schema["const"]):
        raise ValueError(f"DataKey '{path}' does not equal its schema const.")
    if "enum" in schema and not json_value_in(value, schema["enum"]):
        raise ValueError(f"DataKey '{path}' is not one of its schema enum values.")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                raise ValueError(f"Required field '{path}.{name}' is missing.")
        additional = schema.get("additionalProperties", True)
        if trusted_json and not properties and additional is True:
            return
        for name, child in value.items():
            if not isinstance(name, str):
                raise ValueError(f"JSON object '{path}' contains a non-string key.")
            if name in properties:
                child_schema = properties[name]
            elif additional is False:
                raise ValueError(f"Field '{path}.{name}' is not allowed by its schema.")
            elif isinstance(additional, Mapping):
                child_schema = additional
            else:
                child_schema = {}
            _validate_normalized_json_value(
                child,
                child_schema,
                path=f"{path}.{name}",
                trusted_json=trusted_json,
            )
    elif isinstance(value, list):
        item_schema = schema.get("items", {})
        for index, child in enumerate(value):
            _validate_normalized_json_value(
                child,
                item_schema,
                path=f"{path}[{index}]",
                trusted_json=trusted_json,
            )
def infer_schema(value: Any) -> Dict[str, Any]:
    actual = json_type(value)
    if actual == "non-json":
        raise ValueError(f"Cannot infer a JSON Schema for '{type(value).__name__}'.")
    if actual == "object":
        properties = {str(name): infer_schema(child) for name, child in value.items()}
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }
    if actual == "array":
        raise ValueError("Cannot infer a DataKey contract from an array; use a typed object map.")
    return {"type": actual}


def port_schema(port: Any) -> Any:
    if not isinstance(port, Mapping) or "schema" not in port:
        raise ValueError(
            "A module port must declare an explicit 'schema'; "
            "top-level 'type' is unsupported."
        )
    return normalize_data_key_schema(port["schema"], path="Module port")
