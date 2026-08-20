"""Pure compiler for Result Visualization data-read contracts."""

from __future__ import annotations

import copy

from engine.compiler import result_projection as result_projection_compiler
from engine.contracts import visualization as visualization_contracts
from engine.contracts.contract_expansion import (
    expand_contracts,
    expanded_contract_path_required,
    resolve_contract_path,
)
from engine.contracts.data_compatibility import schemas_compatible
from engine.contracts.data_model import (
    normalize_data_key_schema,
    normalize_schema,
    schema_label,
    schema_types,
)
from engine.contracts.data_path import split_data_path
from engine.contracts.json_schema import validate_config


def _visualizer_read_contract(schema):
    """Relax object width for the fields a read-only Visualizer consumes."""

    result = normalize_schema(schema)
    if result is False:
        return False
    result = copy.deepcopy(result)
    for keyword in ("allOf", "anyOf", "oneOf"):
        if keyword in result:
            result[keyword] = [
                _visualizer_read_contract(branch)
                for branch in result[keyword]
            ]
    if "properties" in result:
        result["properties"] = {
            name: _visualizer_read_contract(child)
            for name, child in result["properties"].items()
        }
    if "object" in schema_types(result):
        result["additionalProperties"] = True
    return result


def _visualizer_declared_contract(schema):
    """Treat every declared object field as structurally available to readers."""

    result = normalize_schema(schema)
    if result is False:
        return False
    result = copy.deepcopy(result)
    for keyword in ("allOf", "anyOf", "oneOf"):
        if keyword in result:
            result[keyword] = [
                _visualizer_declared_contract(branch)
                for branch in result[keyword]
            ]
    if "properties" in result:
        result["properties"] = {
            name: _visualizer_declared_contract(child)
            for name, child in result["properties"].items()
        }
        result["required"] = sorted(result["properties"])
    return result


def _result_declarations(contracts, required_roots):
    return {
        path: {
            "schema": schema,
            "required": expanded_contract_path_required(
                contracts,
                path,
                required_roots=required_roots,
            ),
        }
        for path, schema in contracts.items()
    }


def compile_visualization_contracts(
    data_keys,
    spec,
    module_definitions,
    visualizer_definitions,
):
    """Compile and prove a Visualization against verified Result declarations.

    The compiler accepts only detached contract material.  Repository/config
    lookup belongs to the service layer so this function is deterministic and
    cannot write durable state.
    """

    if not isinstance(data_keys, dict):
        raise ValueError("Result dataKeys must be an object.")
    if not isinstance(visualizer_definitions, dict):
        raise ValueError("Visualizer definitions must be an object.")
    visualization_contracts.require_spec(spec)
    base_contracts = expand_contracts({
        data_key: normalize_data_key_schema(
            declaration["schema"],
            path=data_key,
        )
        for data_key, declaration in data_keys.items()
    })
    base_required_roots = frozenset(
        data_key
        for data_key, declaration in data_keys.items()
        if len(split_data_path(data_key)) == 1 and declaration["required"]
    )

    _, root_contracts, root_required_roots = (
        result_projection_compiler.compile_temporary_module_plan(
            {
                "dataKeys": _result_declarations(
                    base_contracts,
                    base_required_roots,
                )
            },
            spec.get("temporaryModules") or [],
            module_definitions,
        )
    )
    missing_contract = object()
    for pane in spec["panes"]:
        _, contracts, _required_roots = (
            result_projection_compiler.compile_temporary_module_plan(
                {
                    "dataKeys": _result_declarations(
                        root_contracts,
                        root_required_roots,
                    )
                },
                pane["temporaryModules"],
                module_definitions,
            )
        )
        for visualizer in pane["visualizers"]:
            callback = visualizer["callback"]
            definition = visualizer_definitions.get(callback)
            if definition is None:
                raise ValueError(f"Unknown Visualizer contract: '{callback}'.")
            input_contracts = {
                name: normalize_data_key_schema(
                    port.get("schema"),
                    path=f"Visualizer {callback}.{name}",
                )
                for name, port in definition["inputPorts"].items()
            }
            params = visualizer["params"]
            validate_config(
                params,
                definition["paramsSchema"],
                path=f"visualizer.{visualizer['id']}.params",
            )
            for port_name, required_schema in input_contracts.items():
                data_key = params.get(port_name)
                if not data_key:
                    raise ValueError(
                        f"Visualizer '{callback}' requires input '{port_name}'."
                    )
                provided_schema = resolve_contract_path(
                    contracts,
                    data_key,
                    missing_contract,
                )
                if provided_schema is missing_contract:
                    raise ValueError(
                        f"Visualizer '{callback}' references unknown DataKey "
                        f"'{data_key}'."
                    )
                if not schemas_compatible(
                    _visualizer_declared_contract(provided_schema),
                    _visualizer_read_contract(required_schema),
                ):
                    raise ValueError(
                        f"Visualizer '{callback}.{port_name}' schema mismatch: "
                        f"DataKey '{data_key}' is {schema_label(provided_schema)}, "
                        f"requires {schema_label(required_schema)}."
                    )
    return root_contracts


__all__ = ("compile_visualization_contracts",)
