"""Compile read-only Result transformations from archived Module Definitions."""

from __future__ import annotations

import copy

from engine.authority import module_invocation as module_invocation_authority
from engine.compiler.module_binding import resolve_verified_module_definition
from engine.contracts.contract_expansion import (
    contract_path_required,
    contract_root_paths,
    expand_contracts,
    resolve_contract_path,
)
from engine.contracts.contract_reducer import write_contract_state
from engine.contracts.data_compatibility import schemas_compatible
from engine.contracts.data_model import (
    normalize_data_key_schema,
    port_schema,
    schema_label,
)
from engine.contracts.data_path import canonical_data_key_order, split_data_path
from engine.contracts.json_schema import validate_config
from engine.contracts.module import (
    MODULE_INSTANCE_FIELDS,
    definition_key,
    normalize_ports,
    require_exact_fields,
    validate_instance_wiring,
)


def compile_temporary_module_plan(result, temporary_modules, module_definitions=None):
    """Resolve Result modules by declared DataKey dependencies, not UI order."""
    if module_definitions is None:
        raise ValueError(
            "Temporary visualization requires the Archived Module repository."
        )
    definitions = module_definitions
    initial_contracts = expand_contracts({
        data_key: normalize_data_key_schema(declaration["schema"], path=data_key)
        for data_key, declaration in result["dataKeys"].items()
    })
    initial_required_roots = frozenset(
        data_key
        for data_key, declaration in result["dataKeys"].items()
        if len(split_data_path(data_key)) == 1 and declaration["required"]
    )
    nodes = {}
    producers = {}
    source_order = []
    definition_authorities = {}
    definition_materials = {}
    if not isinstance(temporary_modules, list):
        raise ValueError("Temporary Modules must be an array.")
    for index, module in enumerate(temporary_modules):
        require_exact_fields(
            module,
            allowed=MODULE_INSTANCE_FIELDS,
            required=MODULE_INSTANCE_FIELDS,
            label=f"Temporary Module[{index}]",
        )
        module_id = module["moduleId"]
        instance_id = module["instanceId"]
        if (
            not isinstance(module_id, str)
            or not module_id
            or not isinstance(instance_id, str)
            or not instance_id
        ):
            raise ValueError("Temporary Module instanceId is required.")
        if instance_id in nodes:
            raise ValueError(
                f"Duplicate temporary module instanceId: '{instance_id}'."
            )
        key = definition_key(module["kind"], module_id, module["version"])
        if key not in definition_materials:
            actual_key, definition, definition_authority = (
                resolve_verified_module_definition(
                    definitions,
                    module["kind"],
                    module_id,
                    module["version"],
                    verified_definitions=definition_authorities,
                    label="Temporary Module",
                )
            )
            if actual_key != key:
                raise ValueError(
                    "Temporary Module Definition identity does not match its "
                    f"repository key: {key}"
                )
            ports = normalize_ports(definition["ports"])
            definition_materials[key] = (definition, ports)
        definition_authority = definition_authorities[key]
        definition, ports = definition_materials[key]
        binding = {
            "instanceId": instance_id,
            "kind": definition["kind"],
            "moduleId": definition["moduleId"],
            "version": definition["version"],
            "config": copy.deepcopy(module["config"]),
            "inputs": copy.deepcopy(module["inputs"]),
            "outputs": copy.deepcopy(module["outputs"]),
        }
        validate_config(
            binding["config"],
            definition["configSchema"],
            path=f"temporaryModule.{instance_id}.config",
        )
        validate_instance_wiring(binding, definition)
        if not binding["outputs"]:
            raise ValueError(
                f"Temporary Module '{instance_id}' must bind at least one output."
            )
        invocation_authority = (
            module_invocation_authority.bind_module_invocation_authority(
                binding,
                definition_authority,
            )
        )
        nodes[instance_id] = {
            "binding": binding,
            "invocationAuthority": invocation_authority,
            "ports": ports,
            "dependencies": set(),
            "inputPlan": tuple(sorted(binding["inputs"].items())),
            "outputPlan": tuple(sorted(
                binding["outputs"].items(),
                key=lambda item: canonical_data_key_order(item[1], item[0]),
            )),
        }
        source_order.append(instance_id)
        for _port_name, data_key in nodes[instance_id]["outputPlan"]:
            if data_key in producers:
                raise ValueError(
                    "Temporary Modules contain multiple producers for DataKey "
                    f"'{data_key}'."
                )
            producers[data_key] = instance_id

    output_contracts_by_node = {}
    for instance_id in source_order:
        node = nodes[instance_id]
        contracts = {}
        required_roots = frozenset()
        for port_name, data_key in node["outputPlan"]:
            required = node["ports"]["outputs"][port_name]["required"]
            contracts, required_roots = write_contract_state(
                contracts,
                required_roots,
                data_key,
                port_schema(node["ports"]["outputs"][port_name]),
                required=required,
            )
        output_contracts_by_node[instance_id] = contracts

    missing_contract = object()
    for instance_id in source_order:
        node = nodes[instance_id]
        for _port_name, data_key in node["inputPlan"]:
            for producer_id in source_order:
                if producer_id == instance_id:
                    continue
                producer_contracts = output_contracts_by_node[producer_id]
                input_parts = split_data_path(data_key)
                destructive = any(
                    len(output_parts := split_data_path(output_data_key))
                    < len(input_parts)
                    and input_parts[:len(output_parts)] == output_parts
                    for _output_port, output_data_key
                    in nodes[producer_id]["outputPlan"]
                ) and not contract_path_required(
                    producer_contracts,
                    data_key,
                    required_roots=contract_root_paths(producer_contracts),
                )
                available_elsewhere = (
                    resolve_contract_path(
                        initial_contracts, data_key, missing_contract
                    ) is not missing_contract
                ) or any(
                    resolve_contract_path(
                        output_contracts_by_node[other_id],
                        data_key,
                        missing_contract,
                    ) is not missing_contract
                    for other_id in source_order
                    if other_id not in {instance_id, producer_id}
                )
                if destructive and available_elsewhere:
                    nodes[producer_id]["dependencies"].add(instance_id)
                elif resolve_contract_path(
                    producer_contracts, data_key, missing_contract
                ) is not missing_contract:
                    node["dependencies"].add(producer_id)

    remaining = set(source_order)
    ordered = []
    while remaining:
        ready = [
            instance_id
            for instance_id in source_order
            if instance_id in remaining
            and not (nodes[instance_id]["dependencies"] & remaining)
        ]
        if not ready:
            raise ValueError(
                "Temporary module dependency graph contains a cycle: "
                + ", ".join(sorted(remaining))
            )
        for instance_id in ready:
            ordered.append(nodes[instance_id])
            remaining.remove(instance_id)

    contracts = dict(initial_contracts)
    required_roots = initial_required_roots
    for node in ordered:
        binding = node["binding"]
        for port_name, data_key in node["inputPlan"]:
            port = node["ports"]["inputs"][port_name]
            provided_schema = resolve_contract_path(
                contracts, data_key, missing_contract
            )
            if provided_schema is missing_contract:
                raise ValueError(
                    f"Temporary module input '{binding['instanceId']}.{port_name}' "
                    f"references unknown DataKey '{data_key}'."
                )
            if port["required"] and not contract_path_required(
                contracts, data_key, required_roots=required_roots
            ):
                raise ValueError(
                    f"Temporary module required input "
                    f"'{binding['instanceId']}.{port_name}' relies on optional "
                    f"DataKey '{data_key}'."
                )
            required_schema = port_schema(port)
            if not schemas_compatible(provided_schema, required_schema):
                raise ValueError(
                    f"Temporary module input '{binding['instanceId']}.{port_name}' "
                    f"schema mismatch: DataKey '{data_key}' is "
                    f"{schema_label(provided_schema)}, requires "
                    f"{schema_label(required_schema)}."
                )
        for port_name, data_key in node["outputPlan"]:
            required = node["ports"]["outputs"][port_name]["required"]
            contracts, required_roots = write_contract_state(
                contracts,
                required_roots,
                data_key,
                port_schema(node["ports"]["outputs"][port_name]),
                required=required,
            )
    return ordered, contracts, required_roots


__all__ = ("compile_temporary_module_plan",)
