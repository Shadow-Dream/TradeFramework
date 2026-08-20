"""Contract projection from an Environment Observation to a Pipeline Data Dict."""

from __future__ import annotations

from copy import deepcopy

from engine.contracts.contract_expansion import (
    expand_contracts,
    expanded_contract_path_required,
    expanded_contract_root_paths,
    resolve_contract_path,
    resolve_expanded_contract_path,
)
from engine.contracts.data_compatibility import normalized_schemas_disjoint
from engine.contracts.data_model import (
    ANNOTATION_KEYS,
    json_values_equal,
    normalize_data_key_schema,
    possible_runtime_types,
    validate_normalized_json_value,
)
from engine.contracts.data_path import split_data_path
from engine.contracts.digest import canonical_json_digest
from engine.contracts.observation_input import (
    compile_observation_projection_plan,
    normalize_pipeline_config,
)


_MISSING = object()
_SELECT_ALL = object()
_WITNESS_LIMIT = 64


def _literal_without_path(value, parts):
    value = deepcopy(value)
    node = value
    for segment in parts[:-1]:
        if type(node) is not dict or segment not in node:
            return value
        node = node[segment]
    if type(node) is dict:
        node.pop(parts[-1], None)
    return value


def _schema_has_path(schema, parts):
    return resolve_contract_path(
        {"root": schema},
        ".".join(("root", *parts)),
        _MISSING,
    ) is not _MISSING


def observation_contract_digest(observation_contracts, required_roots):
    """Digest one normalized Observation schema including root presence."""

    contracts = expand_contracts(observation_contracts)
    roots = sorted(frozenset(required_roots))
    unknown = sorted(set(roots) - set(expanded_contract_root_paths(contracts)))
    if unknown:
        raise ValueError(
            "Observation required roots are not declared by its contracts: "
            + ", ".join(unknown)
        )
    return "sha256:" + canonical_json_digest({
        "contracts": contracts,
        "requiredRoots": roots,
    })


def _unique_json_values(values):
    unique = []
    for value in values:
        if not any(json_values_equal(value, existing) for existing in unique):
            unique.append(value)
    return unique


def _project_literal_selection(value, selection):
    """Project one exact object literal and report whether any path emitted."""

    if type(value) is not dict:
        return {}, False
    projected = {}
    for name, child_selection in selection.items():
        if name not in value:
            continue
        child = value[name]
        if child_selection is _SELECT_ALL:
            projected[name] = deepcopy(child)
            continue
        nested, emitted = _project_literal_selection(child, child_selection)
        if emitted:
            projected[name] = nested
    return projected, bool(projected)


def _schema_provably_empty(schema):
    """Use the Engine's sound proof to recognize an impossible source set."""

    return schema is False or normalized_schemas_disjoint(schema, schema)


def _append_unique_json_value(values, value):
    if len(values) >= _WITNESS_LIMIT:
        return
    if not any(json_values_equal(value, existing) for existing in values):
        values.append(deepcopy(value))


def _direct_object_raw_candidates(schema, *, depth, forced_parts=None):
    """Construct bounded object candidates, optionally forcing one path."""

    if depth >= 64 or "object" not in possible_runtime_types(schema):
        return []
    direct_shape = bool(
        set(schema) & {"type", "properties", "required", "additionalProperties"}
    )
    if forced_parts and not direct_shape:
        return []
    properties = schema.get("properties", {})
    additional = schema.get("additionalProperties", False)
    options = {}
    for name in schema.get("required", ()):
        child = properties.get(name, additional)
        child_options = (
            _normalized_schema_witnesses(child, depth=depth + 1)
            if isinstance(child, dict)
            else []
        )
        if not child_options:
            return []
        options[name] = child_options

    if forced_parts:
        name = forced_parts[0]
        child = properties.get(name, additional)
        if not isinstance(child, dict):
            return []
        child_options = (
            _normalized_schema_path_witnesses(
                child,
                forced_parts[1:],
                depth=depth + 1,
            )
            if len(forced_parts) > 1
            else _normalized_schema_witnesses(child, depth=depth + 1)
        )
        if not child_options:
            return []
        options[name] = child_options

    raw = []
    base = {name: values[0] for name, values in options.items()}
    _append_unique_json_value(raw, base)
    for name, values in options.items():
        for value in values[1:]:
            variant = deepcopy(base)
            variant[name] = value
            _append_unique_json_value(raw, variant)
    if not forced_parts:
        for name, child in properties.items():
            if name in base or not isinstance(child, dict):
                continue
            child_options = _normalized_schema_witnesses(
                child,
                depth=depth + 1,
            )
            if child_options:
                variant = deepcopy(base)
                variant[name] = child_options[0]
                _append_unique_json_value(raw, variant)
    return raw


def _normalized_schema_witnesses(schema, *, depth=0):
    """Return only validator-proven inhabitants; an empty result is unknown."""

    if schema is False or depth >= 64:
        return []
    raw = []
    if "const" in schema:
        _append_unique_json_value(raw, schema["const"])
    for value in schema.get("enum", ()):
        _append_unique_json_value(raw, value)
    for keyword in ("anyOf", "oneOf", "allOf"):
        for branch in schema.get(keyword, ()):
            for value in _normalized_schema_witnesses(branch, depth=depth + 1):
                _append_unique_json_value(raw, value)

    types = possible_runtime_types(schema)
    for value_type, candidates in (
        ("null", (None,)),
        ("boolean", (False, True)),
        ("integer", (0, 1, -1)),
        ("number", (0.0, 1.0, -1.0, 0, 1, -1)),
        ("string", ("", "x")),
    ):
        if value_type in types:
            for candidate in candidates:
                _append_unique_json_value(raw, candidate)

    if "object" in types:
        for candidate in _direct_object_raw_candidates(schema, depth=depth):
            _append_unique_json_value(raw, candidate)

    witnesses = []
    for candidate in raw:
        try:
            validate_normalized_json_value(
                candidate,
                schema,
                path="Observation projection witness",
            )
        except ValueError:
            continue
        _append_unique_json_value(witnesses, candidate)
    return witnesses


def _value_has_segments(value, parts):
    node = value
    for segment in parts:
        if type(node) is not dict or segment not in node:
            return False
        node = node[segment]
    return True


def _normalized_schema_path_witnesses(schema, parts, *, depth=0):
    """Return validator-proven inhabitants in which one named path exists."""

    if schema is False or depth >= 64 or not parts:
        return _normalized_schema_witnesses(schema, depth=depth)
    raw = [
        value
        for value in _normalized_schema_witnesses(schema, depth=depth)
        if _value_has_segments(value, parts)
    ]
    for keyword in ("anyOf", "oneOf", "allOf"):
        for branch in schema.get(keyword, ()):
            for value in _normalized_schema_path_witnesses(
                branch,
                parts,
                depth=depth + 1,
            ):
                _append_unique_json_value(raw, value)
    for value in _direct_object_raw_candidates(
        schema,
        depth=depth,
        forced_parts=parts,
    ):
        _append_unique_json_value(raw, value)

    witnesses = []
    for candidate in raw:
        if not _value_has_segments(candidate, parts):
            continue
        try:
            validate_normalized_json_value(
                candidate,
                schema,
                path="Observation projection path witness",
            )
        except ValueError:
            continue
        _append_unique_json_value(witnesses, candidate)
    return witnesses


def _require_partial_source_witness(schema, *, path):
    if not _normalized_schema_witnesses(schema):
        raise ValueError(
            "Observation projection cannot prove a non-empty source schema "
            f"for a partial image at '{path}'; select the complete parent path "
            "instead."
        )


def _selection_has_nonemitting_witness(schema, selection):
    return any(
        not _project_literal_selection(value, selection)[1]
        for value in _normalized_schema_witnesses(schema)
    )


def _selected_leaf_paths(selection, prefix=()):
    for name, child_selection in selection.items():
        path = (*prefix, name)
        if child_selection is _SELECT_ALL:
            yield path
        else:
            yield from _selected_leaf_paths(child_selection, path)


def _selection_has_emitting_witness(schema, selection):
    candidates = _normalized_schema_witnesses(schema)
    for parts in _selected_leaf_paths(selection):
        for value in _normalized_schema_path_witnesses(schema, parts):
            _append_unique_json_value(candidates, value)
    return any(
        _project_literal_selection(value, selection)[1]
        for value in candidates
    )


def _schema_has_missing_child_witness(schema, name):
    return any(
        type(value) is not dict or name not in value
        for value in _normalized_schema_witnesses(schema)
    )


def _selection_always_emits(schema, selection):
    """Conservatively prove a selected descendant exists for every value."""

    if selection is _SELECT_ALL:
        return True
    normalized = normalize_data_key_schema(schema, path="Observation selection")
    if _schema_provably_empty(normalized):
        # Universal presence over an impossible source branch is vacuously true.
        return True
    if "const" in normalized:
        return _project_literal_selection(normalized["const"], selection)[1]
    if "enum" in normalized:
        return all(
            _project_literal_selection(value, selection)[1]
            for value in normalized["enum"]
        )

    direct_guarantee = False
    if possible_runtime_types(normalized) == {"object"}:
        for name, child_selection in selection.items():
            child = resolve_contract_path(
                {"root": normalized},
                f"root.{name}",
                _MISSING,
            )
            if child is _MISSING or child is False:
                continue
            if _schema_child_required(normalized, name) and (
                child_selection is _SELECT_ALL
                or _selection_always_emits(child, child_selection)
            ):
                direct_guarantee = True
                break

    union_guarantees = [
        bool(normalized[keyword])
        and all(
            _selection_always_emits(branch, selection)
            for branch in normalized[keyword]
        )
        for keyword in ("anyOf", "oneOf")
        if keyword in normalized
    ]
    intersection_guarantee = any(
        _selection_always_emits(branch, selection)
        for branch in normalized.get("allOf", [])
    )
    return direct_guarantee or any(union_guarantees) or intersection_guarantee


def _schema_child_required(schema, segment):
    """Return conditional child presence once its parent value exists."""

    root = "value"
    expanded = expand_contracts({root: schema})
    return expanded_contract_path_required(
        expanded,
        f"{root}.{segment}",
        required_roots={root},
    )


def _require_exact_partial_composition(schema, *, path):
    """Reject partial images the contract subset cannot express exactly."""

    if schema is False:
        return
    if "allOf" in schema:
        raise ValueError(
            "Observation projection cannot represent a partial allOf image "
            f"exactly at '{path}'; select the complete parent path instead."
        )
    unions = [keyword for keyword in ("anyOf", "oneOf") if keyword in schema]
    sibling_assertions = set(schema) - set(unions) - ANNOTATION_KEYS
    if len(unions) > 1 or (unions and sibling_assertions):
        raise ValueError(
            "Observation projection cannot represent a partial composed image "
            f"exactly at '{path}'; select the complete parent path instead."
        )
    if unions == ["oneOf"]:
        branches = schema["oneOf"]
        if any(
            not normalized_schemas_disjoint(branch, branches[prior])
            for index, branch in enumerate(branches)
            for prior in range(index)
        ):
            raise ValueError(
                "Observation projection cannot represent a partial overlapping "
                f"oneOf image exactly at '{path}'; select the complete parent "
                "path instead."
            )


def _project_selected_schema(schema, selection, *, path):
    """Compute the conditional image of one object schema under selection.

    The transform starts from the original parent once, so sibling correlation
    in literals and union branches remains authoritative.  A pairwise-disjoint
    source ``oneOf`` becomes ``anyOf`` because its distinct variants may become
    identical after projection.
    """

    if selection is _SELECT_ALL:
        return normalize_data_key_schema(schema, path=path)
    normalized = normalize_data_key_schema(schema, path=path)
    if _schema_provably_empty(normalized):
        return _MISSING
    _require_exact_partial_composition(normalized, path=path)
    _require_partial_source_witness(normalized, path=path)
    if not _selection_has_emitting_witness(normalized, selection):
        raise ValueError(
            "Observation projection cannot prove a source value emits any "
            f"selected path at '{path}'; select the complete parent path instead."
        )
    properties = {}
    required = []
    for name, child_selection in selection.items():
        child = resolve_contract_path(
            {"root": normalized},
            f"root.{name}",
            _MISSING,
        )
        if child is _MISSING or child is False:
            continue
        projected_child = (
            normalize_data_key_schema(child, path=f"{path}.{name}")
            if child_selection is _SELECT_ALL
            else _project_selected_schema(
                child,
                child_selection,
                path=f"{path}.{name}",
            )
        )
        if projected_child is _MISSING:
            continue
        properties[name] = projected_child
        # Object keywords are conditional on the source value being an object.
        # A nullable/scalar alternative may omit the projected root entirely,
        # but once any selected child emits, the source is in its object variant
        # and this layer's original ``required`` assertion still applies.
        if name in normalized.get("required", ()):
            if (
                child_selection is _SELECT_ALL
                or _selection_always_emits(child, child_selection)
            ):
                required.append(name)
            elif not _selection_has_nonemitting_witness(
                child,
                child_selection,
            ):
                raise ValueError(
                    "Observation projection cannot prove exact selected-field "
                    f"presence at '{path}.{name}'."
                )
        elif (
            not set(normalized) & {"anyOf", "oneOf", "const", "enum"}
            and not _schema_has_missing_child_witness(normalized, name)
        ):
            raise ValueError(
                "Observation projection cannot prove exact optional-field "
                f"presence at '{path}.{name}'."
            )
    if not properties:
        return _MISSING

    result = {
        "type": "object",
        "properties": properties,
        "required": sorted(required),
        "additionalProperties": False,
    }
    constraints = []
    if not required:
        constraints.append({
            "anyOf": [
                {
                    "type": "object",
                    "properties": deepcopy(properties),
                    "required": [name],
                    "additionalProperties": False,
                }
                for name in sorted(properties)
            ]
        })

    if "const" in normalized:
        literal, emitted = _project_literal_selection(
            normalized["const"], selection
        )
        if not emitted:
            return _MISSING
        result["const"] = literal
    if "enum" in normalized:
        literals = _unique_json_values([
            literal
            for value in normalized["enum"]
            for literal, emitted in [_project_literal_selection(value, selection)]
            if emitted
        ])
        if not literals:
            return _MISSING
        result["enum"] = literals

    for keyword in ("anyOf", "oneOf"):
        if keyword not in normalized:
            continue
        branches = [
            projected
            for index, branch in enumerate(normalized[keyword])
            for projected in [
                _project_selected_schema(
                    branch,
                    selection,
                    path=f"{path}.{keyword}[{index}]",
                )
            ]
            if projected is not _MISSING
        ]
        branches = _unique_json_values(branches)
        if not branches:
            return _MISSING
        constraints.append({"anyOf": branches})
    if constraints:
        result["allOf"] = constraints
    return normalize_data_key_schema(result, path=path)


def _schema_without_path(schema, parts, *, path):
    """Describe the value produced after deleting one nested object path."""

    normalized = normalize_data_key_schema(schema, path=path)
    if _schema_provably_empty(normalized):
        return False
    if not _schema_has_path(normalized, parts):
        return normalized
    _require_exact_partial_composition(normalized, path=path)
    _require_partial_source_witness(normalized, path=path)
    if not _normalized_schema_path_witnesses(normalized, parts):
        raise ValueError(
            "Observation projection cannot prove a source value contains the "
            f"deleted path at '{path}.{'.'.join(parts)}'; select the complete "
            "parent path instead."
        )
    result = {
        keyword: deepcopy(value)
        for keyword, value in normalized.items()
        if keyword not in {"anyOf", "oneOf", "allOf"}
    }
    for keyword in ("anyOf", "oneOf"):
        if keyword not in normalized:
            continue
        branches = _unique_json_values([
            _schema_without_path(
                branch,
                parts,
                path=f"{path}.{keyword}[{index}]",
            )
            if _schema_has_path(branch, parts)
            else deepcopy(branch)
            for index, branch in enumerate(normalized[keyword])
        ])
        result["anyOf"] = branches

    if "const" in result:
        result["const"] = _literal_without_path(result["const"], parts)
    if "enum" in result:
        transformed = [
            _literal_without_path(value, parts) for value in result["enum"]
        ]
        unique = []
        for value in transformed:
            if not any(json_values_equal(value, existing) for existing in unique):
                unique.append(value)
        result["enum"] = unique

    direct_shape = bool(
        set(normalized) & {"type", "properties", "required", "additionalProperties"}
    )
    if direct_shape:
        segment = parts[0]
        properties = dict(result.get("properties", {}))
        additional = normalized.get("additionalProperties", False)
        child = properties.get(segment, _MISSING)
        if child is _MISSING and isinstance(additional, dict):
            child = additional
        if child is not _MISSING and child is not False:
            if len(parts) == 1:
                properties[segment] = False
                result["required"] = [
                    name
                    for name in result.get("required", [])
                    if name != segment
                ]
            else:
                properties[segment] = _schema_without_path(
                    child,
                    parts[1:],
                    path=f"{path}.{segment}",
                )
            result["properties"] = properties
    return normalize_data_key_schema(result, path=path)


def project_observation_contract_state(
    observation_contracts,
    observation_required_roots,
    pipeline_config,
):
    """Compile the exact initial Data Dict contract selected by one Pipeline."""

    observation = expand_contracts(observation_contracts)
    required_roots = frozenset(observation_required_roots)
    unknown_required = sorted(
        required_roots - expanded_contract_root_paths(observation)
    )
    if unknown_required:
        raise ValueError(
            "Observation required roots are not declared by its contracts: "
            + ", ".join(unknown_required)
        )
    normalized_config = normalize_pipeline_config(pipeline_config)
    for field in ("whitelist", "blacklist"):
        for data_key in normalized_config["observationInput"][field]:
            resolved = resolve_expanded_contract_path(
                observation,
                data_key,
                _MISSING,
            )
            if resolved is _MISSING or resolved is False:
                raise ValueError(
                    f"Pipeline config.observationInput.{field} selects "
                    f"unavailable Observation DataKey '{data_key}'."
                )
    projection_plan = compile_observation_projection_plan(normalized_config)

    selections = {}
    for entry in projection_plan["whitelist"]:
        parts = tuple(entry["segments"])
        root = parts[0]
        if len(parts) == 1:
            selections[root] = _SELECT_ALL
            continue
        node = selections.setdefault(root, {})
        for segment in parts[1:-1]:
            node = node.setdefault(segment, {})
        node[parts[-1]] = _SELECT_ALL

    denied_by_root = {}
    for entry in projection_plan["blacklist"]:
        denied_parts = tuple(entry["segments"])
        denied_by_root.setdefault(denied_parts[0], []).append(denied_parts[1:])

    selected = {}
    selected_required_roots = set()
    for root, selection in selections.items():
        source_schema = observation[root]
        for denied_parts in denied_by_root.get(root, ()):
            source_schema = _schema_without_path(
                source_schema,
                denied_parts,
                path=root,
            )
        schema = (
            deepcopy(source_schema)
            if selection is _SELECT_ALL
            else _project_selected_schema(source_schema, selection, path=root)
        )
        if schema is _MISSING:
            raise ValueError(
                "Pipeline config.observationInput.whitelist selects unavailable "
                f"Observation content beneath '{root}'."
            )
        selected[root] = schema
        if root in required_roots:
            if (
                selection is _SELECT_ALL
                or _selection_always_emits(source_schema, selection)
            ):
                selected_required_roots.add(root)
            elif not _selection_has_nonemitting_witness(
                source_schema,
                selection,
            ):
                raise ValueError(
                    "Observation projection cannot prove exact root presence "
                    f"beneath '{root}'."
                )

    projected = expand_contracts(selected)
    selected_required_roots &= {
        split_data_path(path)[0] for path in projected
    }
    return projected, frozenset(selected_required_roots)


__all__ = (
    "observation_contract_digest",
    "project_observation_contract_state",
)
