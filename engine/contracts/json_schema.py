#!/usr/bin/env python3
"""Shared validation for user-facing configuration JSON Schemas.

DataKey contracts intentionally use TradeEngine's transportable JSON Schema
subset.  Module and application configuration, however, is ordinary JSON
Schema and may use validation keywords such as ``minimum``.  Keeping this
boundary explicit prevents UI-only schemas from becoming unenforced runtime
metadata.
"""

from __future__ import annotations

import math
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from engine.contracts import strict_json


_MODULE_CONFIG_SCHEMA_KEYS = frozenset({
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "const",
    "enum",
    "default",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "minProperties",
    "maxProperties",
    "title",
    "description",
})
_NO_WITNESS = object()
_MODULE_CONFIG_SCHEMA_BYTE_LIMIT = 1_000_000
_MODULE_CONFIG_WITNESS_UNIT_LIMIT = 100_000
_MODULE_CONFIG_SCHEMA_DEPTH_LIMIT = 64


def normalize_config_schema(schema: Any) -> dict[str, Any]:
    if schema is None:
        schema = {}
    if type(schema) is not dict:
        raise ValueError("configSchema must be a JSON Schema object.")
    if not strict_json.is_exact_json(schema, reject_aliases=True):
        raise ValueError("configSchema must be finite exact JSON data.")
    try:
        normalized = strict_json.loads(strict_json.dumps(dict(schema)))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("configSchema must be finite exact JSON data.") from exc
    try:
        Draft202012Validator.check_schema(normalized)
    except SchemaError as exc:
        raise ValueError(f"Invalid configSchema: {exc.message}") from exc
    return normalized


def validate_config(value: Any, schema: Any, *, path: str = "config") -> None:
    normalized = normalize_config_schema(schema)
    if not strict_json.is_exact_json(value, reject_aliases=True):
        raise ValueError(f"{path} must be finite exact JSON data.")
    try:
        # A strict encode/decode is the authoritative transport boundary.  It
        # rejects container/scalar subclasses, aliases outside JSON's tree
        # topology, non-finite values and non-string keys while retaining the
        # established detailed encoder errors for deep inputs.
        detached = strict_json.loads(strict_json.dumps(value))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"{path} must be finite exact JSON data.") from exc
    try:
        Draft202012Validator(normalized).validate(detached)
    except ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path)
        label = f"{path}.{location}" if location else path
        raise ValueError(f"Invalid {label}: {exc.message}") from exc


def normalize_module_config_schema(schema: Any) -> dict[str, Any]:
    """Require a Module configuration contract whose value domain is objects.

    A configuration crosses the same JSON boundary for PythonModule and
    ProcessRunner.  Constraining the root schema to an object makes that public
    domain explicit; instance validation remains the authority for every
    property and for satisfiability of the concrete configuration selected by
    the user.
    """

    if schema is None or (type(schema) is dict and not schema):
        schema = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    normalized = normalize_config_schema(schema)
    if normalized.get("type") != "object":
        raise ValueError("Module configSchema root type must be 'object'.")
    encoded_schema = strict_json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded_schema) > _MODULE_CONFIG_SCHEMA_BYTE_LIMIT:
        raise ValueError("Module configSchema exceeds the Engine size limit.")
    _validate_module_config_schema_subset(
        normalized,
        path="configSchema",
        depth=0,
        state={"nodes": 0},
    )
    witness = _module_config_witness(
        normalized,
        path="configSchema",
        budget={"remaining": _MODULE_CONFIG_WITNESS_UNIT_LIMIT},
    )
    if witness is _NO_WITNESS or not Draft202012Validator(normalized).is_valid(
        witness
    ):
        raise ValueError(
            "Module configSchema must describe at least one finite JSON value."
        )
    return normalized


def _validate_module_config_schema_subset(schema, *, path, depth, state):
    if depth >= _MODULE_CONFIG_SCHEMA_DEPTH_LIMIT:
        raise ValueError(f"{path} exceeds the Module configSchema depth limit.")
    state["nodes"] += 1
    if state["nodes"] > _MODULE_CONFIG_WITNESS_UNIT_LIMIT:
        raise ValueError("Module configSchema exceeds the Engine node limit.")
    if isinstance(schema, bool):
        return
    if type(schema) is not dict:
        raise ValueError(f"{path} must be a JSON Schema object or boolean.")
    unknown = sorted(set(schema) - _MODULE_CONFIG_SCHEMA_KEYS)
    if unknown:
        raise ValueError(
            f"{path} uses unsupported Module configSchema keyword(s): "
            + ", ".join(unknown)
        )
    if "default" in schema:
        default_contract = {
            key: value for key, value in schema.items() if key != "default"
        }
        if not Draft202012Validator(default_contract).is_valid(schema["default"]):
            raise ValueError(f"{path}.default does not satisfy its schema.")
    for name, child in schema.get("properties", {}).items():
        _validate_module_config_schema_subset(
            child,
            path=f"{path}.properties.{name}",
            depth=depth + 1,
            state=state,
        )
    for keyword in ("items", "additionalProperties"):
        child = schema.get(keyword)
        if isinstance(child, (bool, dict)):
            _validate_module_config_schema_subset(
                child,
                path=f"{path}.{keyword}",
                depth=depth + 1,
                state=state,
            )


def _module_config_witness(schema: Any, *, path: str, budget):
    if schema is True:
        return _reserve_module_config_witness(None, budget, path=path)
    if schema is False:
        return _NO_WITNESS
    if type(schema) is not dict:
        raise ValueError(f"{path} must be a JSON Schema object or boolean.")
    value_types = schema.get("type")
    if value_types is None:
        value_types = [
            "null",
            "boolean",
            "integer",
            "number",
            "string",
            "array",
            "object",
        ]
    elif isinstance(value_types, str):
        value_types = [value_types]
    if not isinstance(value_types, list) or not value_types:
        raise ValueError(f"{path}.type must declare at least one JSON type.")

    candidates = []
    if "const" in schema:
        candidates.append(schema["const"])
    candidates.extend(schema.get("enum", ()))
    if candidates:
        validator = Draft202012Validator(schema)
        for candidate in candidates:
            if (
                strict_json.is_exact_json(candidate, reject_aliases=True)
                and validator.is_valid(candidate)
            ):
                detached = strict_json.loads(strict_json.dumps(candidate))
                return _reserve_module_config_witness(
                    detached,
                    budget,
                    path=path,
                )
        return _NO_WITNESS

    for value_type in value_types:
        trial_budget = {"remaining": budget["remaining"]}
        try:
            candidate = _module_config_type_witness(
                value_type,
                schema,
                path=path,
                budget=trial_budget,
            )
        except (MemoryError, OverflowError, ValueError):
            candidate = _NO_WITNESS
        if (
            candidate is not _NO_WITNESS
            and Draft202012Validator(schema).is_valid(candidate)
        ):
            budget["remaining"] = trial_budget["remaining"]
            return candidate
    return _NO_WITNESS


def _module_config_type_witness(value_type, schema, *, path, budget):
    if value_type == "null":
        return _reserve_module_config_witness(None, budget, path=path)
    if value_type == "boolean":
        return _reserve_module_config_witness(False, budget, path=path)
    if value_type in {"integer", "number"}:
        candidates = _module_config_numeric_candidates(schema)
        if value_type == "integer":
            candidates = (value for value in candidates if type(value) is int)
        validator = Draft202012Validator(schema)
        for candidate in candidates:
            if validator.is_valid(candidate):
                return _reserve_module_config_witness(
                    candidate,
                    budget,
                    path=path,
                )
        return _NO_WITNESS
    if value_type == "string":
        minimum = schema.get("minLength", 0)
        maximum = schema.get("maxLength", math.inf)
        if minimum > maximum:
            return _NO_WITNESS
        if minimum > budget["remaining"]:
            return _NO_WITNESS
        return _reserve_module_config_witness(
            "x" * minimum,
            budget,
            path=path,
        )
    if value_type == "array":
        minimum = schema.get("minItems", 0)
        maximum = schema.get("maxItems", math.inf)
        if minimum > maximum:
            return _NO_WITNESS
        if minimum > budget["remaining"]:
            return _NO_WITNESS
        _consume_module_config_witness_budget(
            budget,
            1,
            path=path,
        )
        item_schema = schema.get("items", True)
        item = (
            _module_config_witness(
                item_schema,
                path=f"{path}.items",
                budget=budget,
            )
            if minimum
            else None
        )
        if item is _NO_WITNESS:
            return _NO_WITNESS
        item_units = _module_config_witness_units(item)
        if minimum > 1 and item_units > (
            budget["remaining"] // (minimum - 1)
        ):
            raise ValueError(
                f"{path} requires a configuration witness beyond the Engine limit."
            )
        if minimum > 1:
            _consume_module_config_witness_budget(
                budget,
                (minimum - 1) * item_units,
                path=path,
            )
        result = []
        for index in range(minimum):
            result.append(
                item if index == 0 else _clone_module_config_witness(item)
            )
        return result
    if value_type != "object":
        return _NO_WITNESS

    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if type(properties) is not dict or type(required) is not list:
        return _NO_WITNESS
    if any(type(name) is not str for name in required) or len(required) != len(set(required)):
        return _NO_WITNESS
    additional = schema.get("additionalProperties", True)
    if additional is False and any(name not in properties for name in required):
        return _NO_WITNESS
    _consume_module_config_witness_budget(budget, 1, path=path)
    candidate = {}
    for name in required:
        child_schema = properties.get(name, additional)
        child = _module_config_witness(
            child_schema,
            path=f"{path}.properties.{name}",
            budget=budget,
        )
        if child is _NO_WITNESS:
            return _NO_WITNESS
        candidate[name] = child
    minimum = schema.get("minProperties", 0)
    maximum = schema.get("maxProperties", math.inf)
    if len(candidate) > maximum or minimum > maximum:
        return _NO_WITNESS
    if minimum - len(candidate) > budget["remaining"]:
        return _NO_WITNESS
    for name, child_schema in properties.items():
        if len(candidate) >= minimum:
            break
        if name in candidate:
            continue
        child = _module_config_witness(
            child_schema,
            path=f"{path}.properties.{name}",
            budget=budget,
        )
        if child is not _NO_WITNESS:
            candidate[name] = child
    extra_index = 0
    while len(candidate) < minimum and additional is not False:
        child_schema = additional
        child = _module_config_witness(
            child_schema,
            path=f"{path}.additionalProperties",
            budget=budget,
        )
        if child is _NO_WITNESS:
            return _NO_WITNESS
        name = f"__extra{extra_index}"
        extra_index += 1
        if name not in candidate and name not in properties:
            candidate[name] = child
    return candidate if minimum <= len(candidate) <= maximum else _NO_WITNESS


def _module_config_numeric_candidates(schema):
    """Return finite Engine JSON numbers around every declared boundary."""

    lower = []
    upper = []
    if "minimum" in schema:
        lower.append((schema["minimum"], False))
    if "exclusiveMinimum" in schema:
        lower.append((schema["exclusiveMinimum"], True))
    if "maximum" in schema:
        upper.append((schema["maximum"], False))
    if "exclusiveMaximum" in schema:
        upper.append((schema["exclusiveMaximum"], True))

    integer_minimum = max(
        (
            math.floor(value) + 1 if exclusive else math.ceil(value)
            for value, exclusive in lower
        ),
        default=None,
    )
    integer_maximum = min(
        (
            math.ceil(value) - 1 if exclusive else math.floor(value)
            for value, exclusive in upper
        ),
        default=None,
    )
    integers = []
    if integer_minimum is None and integer_maximum is None:
        integers.append(0)
    elif integer_minimum is None:
        integers.extend((min(0, integer_maximum), integer_maximum))
    elif integer_maximum is None:
        integers.extend((max(0, integer_minimum), integer_minimum))
    elif integer_minimum <= integer_maximum:
        integers.extend((
            min(max(0, integer_minimum), integer_maximum),
            integer_minimum,
            integer_maximum,
        ))

    floats = [0.0]
    finite_bounds = []
    for value, _exclusive in (*lower, *upper):
        try:
            converted = float(value)
        except (OverflowError, TypeError, ValueError):
            continue
        if math.isfinite(converted):
            finite_bounds.append(converted)
            floats.extend((
                math.nextafter(converted, -math.inf),
                converted,
                math.nextafter(converted, math.inf),
            ))
    if lower and upper:
        for left in finite_bounds:
            for right in finite_bounds:
                if left <= right:
                    midpoint = left + (right - left) / 2.0
                    if math.isfinite(midpoint):
                        floats.append(midpoint)
    return tuple(dict.fromkeys((*integers, *floats)))


def _clone_module_config_witness(value):
    return strict_json.loads(strict_json.dumps(value))


def _reserve_module_config_witness(value, budget, *, path):
    _consume_module_config_witness_budget(
        budget,
        _module_config_witness_units(value),
        path=path,
    )
    return value


def _module_config_witness_units(value):
    if type(value) is str:
        return max(1, len(value))
    if type(value) is list:
        return 1 + sum(_module_config_witness_units(item) for item in value)
    if type(value) is dict:
        return 1 + sum(
            max(1, len(key)) + _module_config_witness_units(item)
            for key, item in value.items()
        )
    return 1


def _consume_module_config_witness_budget(budget, units, *, path):
    if units > budget["remaining"]:
        raise ValueError(
            f"{path} requires a configuration witness beyond the Engine limit."
        )
    budget["remaining"] -= units


__all__ = (
    "normalize_config_schema",
    "normalize_module_config_schema",
    "validate_config",
)
