#!/usr/bin/env python3
"""DataKey paths, contract composition, and runtime validation specializations."""

import math
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

import engine.contracts.contract_expansion as contract_expansion
import engine.contracts.data_model as data_model_contract
import engine.contracts.data_path as data_path_contract


_IMMUTABLE_JSON_SCALARS = (str, int, float, bool, type(None))
_FAST_VALIDATION_MISSING = object()
_DECLARED_DATA_MISSING = object()


def isolate_json_value(value: Any, memo: dict[int, Any] | None = None) -> Any:
    """Clone common finite-JSON values with ``deepcopy``-compatible ownership."""
    if isinstance(value, _IMMUTABLE_JSON_SCALARS):
        return value
    if memo is None:
        memo = {}
    identity = id(value)
    if identity in memo:
        return memo[identity]
    if type(value) is dict:
        isolated = {}
        memo[identity] = isolated
        for key, child in value.items():
            isolated[key] = isolate_json_value(child, memo)
        return isolated
    if type(value) is list:
        isolated = []
        memo[identity] = isolated
        isolated.extend(isolate_json_value(child, memo) for child in value)
        return isolated
    return deepcopy(value, memo)


def _compile_fast_normalized_json_validator(plan: data_model_contract.CompiledValidationPlan):
    """Compile an allocation-free success path for ordinary JSON schemas.

    This helper is deliberately not an error authority.  It returns ``True``
    only after proving the complete value against the supported plan, and
    returns ``False`` for every mismatch.  The public validator then repeats a
    mismatch with ``data_model_contract.compiled_validation_failure`` so error order and text stay
    owned by the complete implementation.  Composition, enum, and trusted
    transport plans remain entirely on that implementation.
    """
    json_values_equal = data_model_contract.json_values_equal
    if (
        plan.trusted_wildcard
        or plan.all_of
        or plan.any_of
        or plan.one_of
        or plan.enum is not None
    ):
        return None
    if plan.schema_false:
        return lambda _value: False

    if plan.wildcard:
        def validate_wildcard(value):
            if value is None or isinstance(value, (str, bool, int)):
                return True
            if isinstance(value, float):
                return math.isfinite(value)
            if type(value) is dict:
                for name, child in value.items():
                    if not isinstance(name, str) or not validate_wildcard(child):
                        return False
                return True
            if type(value) is list:
                for child in value:
                    if not validate_wildcard(child):
                        return False
                return True
            return False

        return validate_wildcard

    allowed_types = plan.allowed_types
    if "object" in allowed_types and allowed_types <= frozenset({"object", "null"}):
        properties = {}
        for name, child_plan in plan.properties.items():
            child_validator = _compile_fast_normalized_json_validator(child_plan)
            if child_validator is None:
                return None
            properties[name] = child_validator
        if plan.additional is False:
            additional = False
        else:
            additional = _compile_fast_normalized_json_validator(plan.additional)
            if additional is None:
                return None
        required = plan.required
        accepts_null = "null" in allowed_types
        has_const = plan.has_const
        const = plan.const

        def validate_object(value):
            if value is None:
                return accepts_null and (
                    not has_const or json_values_equal(value, const)
                )
            if type(value) is not dict:
                return False
            if has_const and not json_values_equal(value, const):
                return False
            for name in required:
                if name not in value:
                    return False
            for name, child in value.items():
                if not isinstance(name, str):
                    return False
                child_validator = properties.get(name, _FAST_VALIDATION_MISSING)
                if child_validator is _FAST_VALIDATION_MISSING:
                    if additional is False:
                        return False
                    child_validator = additional
                if not child_validator(child):
                    return False
            return True

        return validate_object

    if "array" in allowed_types and allowed_types <= frozenset({"array", "null"}):
        item_validator = _compile_fast_normalized_json_validator(plan.items)
        if item_validator is None:
            return None
        accepts_null = "null" in allowed_types
        has_const = plan.has_const
        const = plan.const

        def validate_array(value):
            if value is None:
                return accepts_null and (
                    not has_const or json_values_equal(value, const)
                )
            if type(value) is not list:
                return False
            if has_const and not json_values_equal(value, const):
                return False
            for child in value:
                if not item_validator(child):
                    return False
            return True

        return validate_array

    # Container/scalar unions use the complete implementation.  The common
    # scalar unions below need no runtime type-label construction.
    if "object" in allowed_types or "array" in allowed_types:
        return None
    accepts_null = "null" in allowed_types
    accepts_boolean = "boolean" in allowed_types
    accepts_integer = "integer" in allowed_types or "number" in allowed_types
    accepts_number = "number" in allowed_types
    accepts_string = "string" in allowed_types
    has_const = plan.has_const
    const = plan.const

    def validate_scalar(value):
        if value is None:
            accepted = accepts_null
        elif isinstance(value, bool):
            accepted = accepts_boolean
        elif isinstance(value, int):
            accepted = accepts_integer
        elif isinstance(value, float):
            accepted = accepts_number and math.isfinite(value)
        elif isinstance(value, str):
            accepted = accepts_string
        else:
            accepted = False
        return accepted and (
            not has_const or json_values_equal(value, const)
        )

    return validate_scalar


def compile_normalized_json_validator(
    schema: Any,
    *,
    path: str = "value",
    trusted_json: bool = False,
):
    """Compile an equivalent validator without changing accepted JSON values."""
    plan = data_model_contract.compile_validation_plan(schema, trusted_json=trusted_json)
    fast_validator = (
        None
        if trusted_json
        else _compile_fast_normalized_json_validator(plan)
    )
    validation_failure = data_model_contract.compiled_validation_failure
    raise_validation_failure = data_model_contract.raise_compiled_validation_failure

    def validate(value: Any) -> None:
        if fast_validator is not None and fast_validator(value):
            return
        failure = validation_failure(value, plan)
        if failure is None:
            return
        raise_validation_failure(failure, path)

    return validate


def _compiled_isolation_failure(
    value: Any,
    plan: data_model_contract.CompiledValidationPlan,
    memo,
    *,
    json_type=data_model_contract.json_type,
    validation_failure=data_model_contract.compiled_validation_failure,
    json_values_equal=data_model_contract.json_values_equal,
    json_value_in=data_model_contract.json_value_in,
):
    """Return ``(isolated value, failure)`` while walking ordinary JSON once."""
    if plan.schema_false:
        return None, ((), "forbidden", None)
    if plan.trusted_wildcard:
        return isolate_json_value(value, memo), None

    actual = json_type(value)
    if actual == "non-json" or (isinstance(value, float) and not math.isfinite(value)):
        return None, ((), "non-json", None)

    if plan.wildcard:
        if type(value) is dict:
            isolated = {}
            memo[id(value)] = isolated
            for name, child in value.items():
                if not isinstance(name, str):
                    return None, ((), "non-string-key", None)
                child_isolated, failure = _compiled_isolation_failure(child, plan, memo)
                if failure:
                    suffix, kind, detail = failure
                    return None, ((name, *suffix), kind, detail)
                isolated[name] = child_isolated
            return isolated, None
        if type(value) is list:
            isolated = []
            memo[id(value)] = isolated
            for index, child in enumerate(value):
                child_isolated, failure = _compiled_isolation_failure(child, plan, memo)
                if failure:
                    suffix, kind, detail = failure
                    return None, ((index, *suffix), kind, detail)
                isolated.append(child_isolated)
            return isolated, None
        return value, None

    for branch in plan.all_of:
        failure = validation_failure(value, branch)
        if failure:
            return None, failure
    if plan.any_of and not any(
        validation_failure(value, branch) is None
        for branch in plan.any_of
    ):
        return None, ((), "combination", "anyOf")
    if plan.one_of and sum(
        validation_failure(value, branch) is None
        for branch in plan.one_of
    ) != 1:
        return None, ((), "combination", "oneOf")

    if not (
        actual in plan.allowed_types
        or (actual == "integer" and "number" in plan.allowed_types)
    ):
        return None, ((), "type", (actual, plan.type_label))
    if plan.has_const and not json_values_equal(value, plan.const):
        return None, ((), "const", None)
    if plan.enum is not None and not json_value_in(value, plan.enum):
        return None, ((), "enum", None)

    if type(value) is dict:
        isolated = {}
        memo[id(value)] = isolated
        for name in plan.required:
            if name not in value:
                return None, ((name,), "required", None)
        for name, child in value.items():
            if not isinstance(name, str):
                return None, ((), "non-string-key", None)
            child_plan = plan.properties.get(name)
            if child_plan is None:
                if plan.additional is False:
                    return None, ((name,), "additional", None)
                child_plan = plan.additional
            child_isolated, failure = _compiled_isolation_failure(child, child_plan, memo)
            if failure:
                suffix, kind, detail = failure
                return None, ((name, *suffix), kind, detail)
            isolated[name] = child_isolated
        return isolated, None
    if type(value) is list:
        isolated = []
        memo[id(value)] = isolated
        for index, child in enumerate(value):
            child_isolated, failure = _compiled_isolation_failure(child, plan.items, memo)
            if failure:
                suffix, kind, detail = failure
                return None, ((index, *suffix), kind, detail)
            isolated.append(child_isolated)
        return isolated, None
    return value, None


_FAST_ISOLATION_FAILURE = object()
_FAST_ISOLATION_MISSING = object()


def _compile_fast_normalized_json_isolator(plan: data_model_contract.CompiledValidationPlan):
    """Compile the ordinary valid path without becoming an error authority.

    The specialised functions below only return an isolated value when every
    check succeeds.  Any mismatch returns a private sentinel so the complete
    compiled implementation can reproduce the authoritative path and error.
    Schemas whose composition or enum semantics are less common stay entirely
    on that complete implementation.

    This path is used only for untrusted Python values.  It deliberately does
    not memoize containers: the complete untrusted isolator also creates a new
    container at every schema path, even when the source Python tree contains
    aliases.  Trusted transport values retain their existing memo semantics by
    never entering this compiler.
    """
    json_values_equal = data_model_contract.json_values_equal
    if (
        plan.trusted_wildcard
        or plan.all_of
        or plan.any_of
        or plan.one_of
        or plan.enum is not None
    ):
        return None
    if plan.schema_false:
        return lambda _value: _FAST_ISOLATION_FAILURE

    if plan.wildcard:
        def isolate_wildcard(value):
            if value is None or isinstance(value, (str, bool, int)):
                return value
            if isinstance(value, float):
                return value if math.isfinite(value) else _FAST_ISOLATION_FAILURE
            if type(value) is dict:
                isolated = {}
                for name, child in value.items():
                    if not isinstance(name, str):
                        return _FAST_ISOLATION_FAILURE
                    isolated_child = isolate_wildcard(child)
                    if isolated_child is _FAST_ISOLATION_FAILURE:
                        return _FAST_ISOLATION_FAILURE
                    isolated[name] = isolated_child
                return isolated
            if type(value) is list:
                isolated = []
                for child in value:
                    isolated_child = isolate_wildcard(child)
                    if isolated_child is _FAST_ISOLATION_FAILURE:
                        return _FAST_ISOLATION_FAILURE
                    isolated.append(isolated_child)
                return isolated
            return _FAST_ISOLATION_FAILURE

        return isolate_wildcard

    allowed_types = plan.allowed_types
    if "object" in allowed_types and allowed_types <= frozenset({"object", "null"}):
        properties = {}
        for name, child_plan in plan.properties.items():
            child_isolator = _compile_fast_normalized_json_isolator(child_plan)
            if child_isolator is None:
                return None
            properties[name] = child_isolator
        if plan.additional is False:
            additional = False
        else:
            additional = _compile_fast_normalized_json_isolator(plan.additional)
            if additional is None:
                return None
        required = plan.required
        accepts_null = "null" in allowed_types
        has_const = plan.has_const
        const = plan.const

        def isolate_object(value):
            if value is None:
                if not accepts_null:
                    return _FAST_ISOLATION_FAILURE
                if has_const and not json_values_equal(value, const):
                    return _FAST_ISOLATION_FAILURE
                return value
            if type(value) is not dict:
                return _FAST_ISOLATION_FAILURE
            if has_const and not json_values_equal(value, const):
                return _FAST_ISOLATION_FAILURE
            for name in required:
                if name not in value:
                    return _FAST_ISOLATION_FAILURE
            isolated = {}
            for name, child in value.items():
                if not isinstance(name, str):
                    return _FAST_ISOLATION_FAILURE
                child_isolator = properties.get(name, _FAST_ISOLATION_MISSING)
                if child_isolator is _FAST_ISOLATION_MISSING:
                    if additional is False:
                        return _FAST_ISOLATION_FAILURE
                    child_isolator = additional
                isolated_child = child_isolator(child)
                if isolated_child is _FAST_ISOLATION_FAILURE:
                    return _FAST_ISOLATION_FAILURE
                isolated[name] = isolated_child
            return isolated

        return isolate_object

    if "array" in allowed_types and allowed_types <= frozenset({"array", "null"}):
        item_isolator = _compile_fast_normalized_json_isolator(plan.items)
        if item_isolator is None:
            return None
        accepts_null = "null" in allowed_types
        has_const = plan.has_const
        const = plan.const

        def isolate_array(value):
            if value is None:
                if not accepts_null:
                    return _FAST_ISOLATION_FAILURE
                if has_const and not json_values_equal(value, const):
                    return _FAST_ISOLATION_FAILURE
                return value
            if type(value) is not list:
                return _FAST_ISOLATION_FAILURE
            if has_const and not json_values_equal(value, const):
                return _FAST_ISOLATION_FAILURE
            isolated = []
            for child in value:
                isolated_child = item_isolator(child)
                if isolated_child is _FAST_ISOLATION_FAILURE:
                    return _FAST_ISOLATION_FAILURE
                isolated.append(isolated_child)
            return isolated

        return isolate_array

    # A schema which mixes a container with another runtime type needs the
    # complete branch logic.  Scalar unions can use the compact checks below.
    if "object" in allowed_types or "array" in allowed_types:
        return None
    accepts_null = "null" in allowed_types
    accepts_boolean = "boolean" in allowed_types
    accepts_integer = "integer" in allowed_types or "number" in allowed_types
    accepts_number = "number" in allowed_types
    accepts_string = "string" in allowed_types
    has_const = plan.has_const
    const = plan.const

    def isolate_scalar(value):
        if value is None:
            accepted = accepts_null
        elif isinstance(value, bool):
            accepted = accepts_boolean
        elif isinstance(value, int):
            accepted = accepts_integer
        elif isinstance(value, float):
            accepted = accepts_number and math.isfinite(value)
        elif isinstance(value, str):
            accepted = accepts_string
        else:
            accepted = False
        if not accepted:
            return _FAST_ISOLATION_FAILURE
        if has_const and not json_values_equal(value, const):
            return _FAST_ISOLATION_FAILURE
        return value

    return isolate_scalar


def compile_normalized_json_isolator(
    schema: Any,
    *,
    path: str = "value",
    trusted_json: bool = False,
):
    """Compile validation plus ownership isolation into one equivalent traversal."""
    plan = data_model_contract.compile_validation_plan(schema, trusted_json=trusted_json)
    fast_isolator = (
        None
        if trusted_json
        else _compile_fast_normalized_json_isolator(plan)
    )
    raise_validation_failure = data_model_contract.raise_compiled_validation_failure

    def isolate(value: Any) -> Any:
        if fast_isolator is not None:
            isolated = fast_isolator(value)
            if isolated is not _FAST_ISOLATION_FAILURE:
                return isolated
        isolated, failure = _compiled_isolation_failure(value, plan, {})
        if failure is not None:
            raise_validation_failure(failure, path)
        return isolated

    return isolate


def compile_data_json_validator(
    contracts: Mapping[str, Any],
    *,
    required_paths: Any = (),
    contracts_expanded: bool = False,
    trusted_json: bool = False,
):
    """Compile a complete Data Dict contract without changing its validation rules."""
    expanded = contracts if contracts_expanded else contract_expansion.expand_contracts(contracts)
    split_path = data_path_contract.split_data_path
    root_plans = {}
    for path, schema in expanded.items():
        if len(split_path(path)) != 1:
            continue
        plan = data_model_contract.compile_validation_plan(
            schema,
            trusted_json=trusted_json,
        )
        root_plans[path] = (
            plan,
            None
            if trusted_json
            else _compile_fast_normalized_json_validator(plan),
        )
    required_plan = tuple(
        (str(path), split_path(path))
        for path in required_paths
    )
    validation_failure = data_model_contract.compiled_validation_failure
    raise_validation_failure = data_model_contract.raise_compiled_validation_failure
    require_segments = data_path_contract.require_data_segments

    def validate(data: Mapping[str, Any]) -> None:
        if not isinstance(data, dict):
            raise ValueError("Pipeline data must be a JSON object.")
        for path, value in data.items():
            if not isinstance(path, str):
                raise ValueError("Pipeline data contains a non-string root DataKey.")
            compiled = root_plans.get(path)
            if compiled is None:
                raise ValueError(f"DataKey '{path}' is not declared by its contract.")
            plan, fast_validator = compiled
            if fast_validator is not None and fast_validator(value):
                continue
            failure = validation_failure(
                value,
                plan,
            )
            if failure is not None:
                raise_validation_failure(failure, str(path))
        for path, segments in required_plan:
            require_segments(data, segments, path=path)

    return validate


def compile_declared_data_json_proof(
    contracts: Mapping[str, Any],
    *,
    required_roots: Any = (),
    contracts_expanded: bool = False,
    path: str = "Data input",
    allow_declared_root_extensions: bool = False,
    boundary_paths: Any = None,
    required_paths: Any = (),
    conditional_required_paths: Any = (),
):
    """Compile presence and value phases for a declared Data Dict projection.

    Keeping the phases separate lets a composed boundary resolve every missing
    required path before inspecting any value schema.  Only declared roots are
    read; roots owned by another component are deliberately not traversed.
    """
    expanded = (
        contracts
        if contracts_expanded
        else contract_expansion.expand_contracts(contracts)
    )
    split_path = data_path_contract.split_data_path
    required = frozenset(required_roots)
    if boundary_paths is None:
        projected_contracts = tuple(
            (root, schema)
            for root, schema in sorted(expanded.items())
            if len(split_path(root)) == 1
        )
    else:
        missing_contract = object()
        projected_contracts = []
        for boundary_path in sorted(frozenset(boundary_paths)):
            schema = contract_expansion.resolve_expanded_contract_path(
                expanded,
                boundary_path,
                missing_contract,
            )
            if schema is not missing_contract:
                projected_contracts.append((boundary_path, schema))

    def projected_root_schema(schema):
        def allow_projection_extensions(value):
            if type(value) is not dict:
                return value
            projected_value = {
                name: (
                    {
                        child_name: allow_projection_extensions(child)
                        for child_name, child in child_value.items()
                    }
                    if name == "properties" and type(child_value) is dict
                    else allow_projection_extensions(child_value)
                    if name in {"additionalProperties", "items"}
                    and type(child_value) is dict
                    else [
                        allow_projection_extensions(child)
                        for child in child_value
                    ]
                    if name in {"allOf", "anyOf", "oneOf"}
                    and type(child_value) is list
                    else child_value
                )
                for name, child_value in value.items()
            }
            if (
                projected_value.get("type") == "object"
                and projected_value.get("additionalProperties") is False
            ):
                projected_value["additionalProperties"] = True
            return projected_value

        projected = (
            allow_projection_extensions(schema)
            if allow_declared_root_extensions
            else schema
        )
        return projected

    value_plan = tuple(
        (
            declared_path,
            split_path(declared_path),
            compile_normalized_json_validator(
                projected_root_schema(schema),
                path=f"{path}.{declared_path}",
                trusted_json=False,
            ),
        )
        for declared_path, schema in projected_contracts
    )
    required_plan = tuple(
        (str(required_path), split_path(required_path))
        for required_path in sorted(required | frozenset(required_paths))
    )
    conditional_required_plan = tuple(
        (str(required_path), split_path(required_path))
        for required_path in sorted(frozenset(conditional_required_paths))
    )

    def require_presence(data: Mapping[str, Any]) -> None:
        if not isinstance(data, Mapping):
            raise ValueError(f"{path} must be a Data Dict object.")
        missing = [
            required_path
            for required_path, segments in required_plan
            if data_path_contract.get_data_segments(
                data, segments, _DECLARED_DATA_MISSING
            ) is _DECLARED_DATA_MISSING
        ]
        missing.extend(
            required_path
            for required_path, segments in conditional_required_plan
            if segments[0] in data
            and data_path_contract.get_data_segments(
                data, segments, _DECLARED_DATA_MISSING
            ) is _DECLARED_DATA_MISSING
        )
        if missing:
            raise ValueError(
                f"{path} is missing required root DataKey(s): "
                + ", ".join(missing)
                + "."
            )

    def validate_values(data: Mapping[str, Any]) -> None:
        if not isinstance(data, Mapping):
            raise ValueError(f"{path} must be a Data Dict object.")
        for _declared_path, segments, validator in value_plan:
            value = data_path_contract.get_data_segments(
                data, segments, _DECLARED_DATA_MISSING
            )
            if value is not _DECLARED_DATA_MISSING:
                validator(value)

    return require_presence, validate_values


def compile_declared_data_json_validator(
    contracts: Mapping[str, Any],
    *,
    required_roots: Any = (),
    contracts_expanded: bool = False,
    path: str = "Data input",
    allow_declared_root_extensions: bool = False,
    boundary_paths: Any = None,
    required_paths: Any = (),
    conditional_required_paths: Any = (),
):
    """Compile a complete validator for one declared Data Dict projection."""
    require_presence, validate_values = compile_declared_data_json_proof(
        contracts,
        required_roots=required_roots,
        contracts_expanded=contracts_expanded,
        path=path,
        allow_declared_root_extensions=allow_declared_root_extensions,
        boundary_paths=boundary_paths,
        required_paths=required_paths,
        conditional_required_paths=conditional_required_paths,
    )

    def validate(data: Mapping[str, Any]) -> None:
        require_presence(data)
        validate_values(data)

    return validate


def validate_data_json(
    data: Mapping[str, Any],
    contracts: Mapping[str, Any],
    *,
    required_paths: Any = (),
    contracts_expanded: bool = False,
    trusted_json: bool = False,
) -> None:
    if not isinstance(data, dict):
        raise ValueError("Pipeline data must be a JSON object.")
    expanded = contracts if contracts_expanded else contract_expansion.expand_contracts(contracts)
    split_path = data_path_contract.split_data_path
    require_path = data_path_contract.require_data_path
    validate_value = data_model_contract.validate_normalized_json_value
    # Parent contracts recursively contain their complete child structure.
    # Validating every flattened leaf again is redundant and made long
    # backtests repeatedly normalize the same schema hundreds of thousands of
    # times.
    root_contracts = {
        path: schema
        for path, schema in expanded.items()
        if len(split_path(path)) == 1
    }
    for path, value in data.items():
        if not isinstance(path, str):
            raise ValueError("Pipeline data contains a non-string root DataKey.")
        schema = root_contracts.get(path)
        if schema is None:
            raise ValueError(f"DataKey '{path}' is not declared by its contract.")
        validate_value(
            value,
            schema,
            path=path,
            trusted_json=trusted_json,
        )
    for path in required_paths:
        require_path(data, path)
