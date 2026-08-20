"""Typed Graph compiler for raw and verified Module authorities."""

from __future__ import annotations

import copy
from collections.abc import Mapping

from engine.authority import module_definition as _module_definition_authority
from engine.compiler.module_binding import resolve_verified_module_definition
from engine.authority.graph import (
    seal_compiled_graph_authority as _seal_compiled_graph_authority,
)
from engine.contracts.contract_expansion import (
    expanded_contract_path_required,
    expanded_contract_root_paths,
    expand_contracts,
    resolve_expanded_contract_path,
)
from engine.contracts.contract_reducer import ExpandedContractWriteReducer
from engine.contracts.data_compatibility import schemas_compatible
from engine.contracts.data_model import (
    normalize_data_key_schema,
    port_schema,
    schema_label,
)
from engine.contracts.data_path import canonical_data_key_order, split_data_path
from engine.contracts.graph import (
    normalize_graph,
)
from engine.contracts.json_schema import validate_config
from engine.contracts.module import (
    MODULE_INSTANCE_FIELDS,
    normalize_ports,
    require_exact_fields,
)


__all__ = (
    "compile_module_graph",
    "compile_module_graph_authority",
    "compile_verified_module_graph",
    "compile_verified_module_graph_authority",
)


def _ports(definition):
    return normalize_ports(
        definition["ports"],
        label=(
            f"Module '{definition.get('kind')}/{definition.get('moduleId')}/"
            f"{definition.get('version')}' ports"
        ),
    )


def _definition(definitions, instance, label, *, verified_definitions=None):
    if not isinstance(instance, Mapping):
        raise ValueError(f"{label} instance must be an object.")
    require_exact_fields(
        dict(instance),
        allowed=MODULE_INSTANCE_FIELDS,
        required=MODULE_INSTANCE_FIELDS,
        label=f"{label} instance",
    )
    for field in ("instanceId", "kind", "moduleId", "version"):
        if not isinstance(instance[field], str) or not instance[field].strip():
            raise ValueError(f"{label} instance {field} must be a non-empty string.")
    _key, definition, _authority = resolve_verified_module_definition(
        definitions,
        instance["kind"],
        instance["moduleId"],
        instance["version"],
        verified_definitions=verified_definitions,
        label=f"{label} Module",
    )
    return definition


def _validate_instance(instance_id, instance, definition, label):
    if not isinstance(instance, Mapping):
        raise ValueError(f"{label} instance '{instance_id}' must be an object.")
    require_exact_fields(
        dict(instance),
        allowed=MODULE_INSTANCE_FIELDS,
        required=MODULE_INSTANCE_FIELDS,
        label=f"{label} instance '{instance_id}'",
    )
    if instance["instanceId"] != instance_id:
        raise ValueError(f"{label} instance key '{instance_id}' must match instanceId.")
    config = instance["config"]
    if not isinstance(config, dict):
        raise ValueError(f"{label} instance '{instance_id}' config must be an object.")
    validate_config(config, definition["configSchema"], path=f"{label}.{instance_id}.config")
    ports = _ports(definition)
    for direction in ("inputs", "outputs"):
        bindings = instance[direction]
        if not isinstance(bindings, dict):
            raise ValueError(f"{label} instance '{instance_id}' {direction} must be an object.")
        unknown = sorted(set(bindings) - set(ports[direction]))
        if unknown:
            raise ValueError(
                f"{label} instance '{instance_id}' binds undeclared {direction} port(s): "
                + ", ".join(unknown)
            )
        missing = sorted(
            name for name, spec in ports[direction].items()
            if spec.get("required", True) and name not in bindings
        )
        if missing:
            raise ValueError(
                f"{label} instance '{instance_id}' omits required {direction} port(s): "
                + ", ".join(missing)
            )
        if not all(isinstance(wire, str) and wire for wire in bindings.values()):
            raise ValueError(f"{label} instance '{instance_id}' has an empty {direction} wire ID.")
    return ports


def _compile_module_graph_with_authorities(
    graph,
    instances,
    definitions,
    definition_authorities,
    initial_contracts,
    *,
    allowed_kinds=None,
    label="Graph",
    strict_sources=True,
    required_roots=None,
    source_contracts=None,
    source_required_roots=None,
):
    """Compile one editor Graph into a stable, fully typed execution plan."""
    if source_contracts is None:
        source_contracts = {}
    if not isinstance(source_contracts, Mapping):
        raise ValueError(f"{label} source contracts must be an object.")
    if source_required_roots is None:
        source_required_roots = {}
    if not isinstance(source_required_roots, Mapping):
        raise ValueError(f"{label} source required roots must be an object.")
    unknown_required_sources = sorted(set(source_required_roots) - set(source_contracts))
    if unknown_required_sources:
        raise ValueError(
            f"{label} required roots reference unknown input source(s): "
            + ", ".join(unknown_required_sources)
        )
    graph = normalize_graph(
        graph,
        label=label,
        input_sources=source_contracts,
    )
    if not isinstance(instances, Mapping):
        raise ValueError(f"{label} instances must be an object.")
    if initial_contracts is None:
        initial_contracts = {}
    if not isinstance(initial_contracts, Mapping):
        raise ValueError(f"{label} initial contracts must be an object.")
    initial = expand_contracts({
        data_key: normalize_data_key_schema(schema, path=data_key)
        for data_key, schema in initial_contracts.items()
    })
    initial_roots = expanded_contract_root_paths(initial)
    initial_required_roots = (
        initial_roots
        if required_roots is None
        else frozenset(required_roots)
    )
    unknown_required_roots = sorted(
        set(initial_required_roots) - set(initial_roots)
    )
    if unknown_required_roots:
        raise ValueError(
            f"{label} required root(s) are not declared: "
            + ", ".join(unknown_required_roots)
        )
    normalized_source_contracts = {}
    normalized_source_required_roots = {}
    for source in sorted({
        boundary["source"]
        for boundary in graph["inputs"].values()
        if "source" in boundary
    }):
        raw_contracts = source_contracts[source]
        if not isinstance(raw_contracts, Mapping):
            raise ValueError(f"{label} input source '{source}' contracts must be an object.")
        contracts = expand_contracts({
            data_key: normalize_data_key_schema(schema, path=data_key)
            for data_key, schema in raw_contracts.items()
        })
        contract_roots = expanded_contract_root_paths(contracts)
        required = (
            contract_roots
            if source not in source_required_roots
            else frozenset(source_required_roots[source])
        )
        unknown = sorted(set(required) - set(contract_roots))
        if unknown:
            raise ValueError(
                f"{label} input source '{source}' required root(s) are not declared: "
                + ", ".join(unknown)
            )
        normalized_source_contracts[source] = contracts
        normalized_source_required_roots[source] = frozenset(required)
    nodes = graph["nodes"]
    duplicate_nodes = sorted({node for node in nodes if nodes.count(node) > 1})
    if duplicate_nodes:
        raise ValueError(f"{label} contains duplicate node(s): {', '.join(duplicate_nodes)}")
    invalid_instance_ids = [
        instance_id
        for instance_id in instances
        if not isinstance(instance_id, str) or not instance_id
    ]
    if invalid_instance_ids:
        raise ValueError(f"{label} instances contain an invalid instance ID.")
    node_ids = set(nodes)
    missing_instances = sorted(node_id for node_id in nodes if node_id not in instances)
    orphan_instances = sorted(
        instance_id for instance_id in instances if instance_id not in node_ids
    )
    if missing_instances or orphan_instances:
        details = []
        if missing_instances:
            details.append("missing instance(s): " + ", ".join(missing_instances))
        if orphan_instances:
            details.append(
                "instance(s) outside graph.nodes: " + ", ".join(orphan_instances)
            )
        raise ValueError(f"{label} instances must exactly match graph.nodes: " + "; ".join(details))

    bindings = {}
    if not isinstance(definition_authorities, Mapping):
        raise TypeError("Verified Module Definition authorities must be an object.")
    verified_definitions = dict(definition_authorities)
    for authority in verified_definitions.values():
        _module_definition_authority.verified_module_definition_material(authority)
    ports_by_node = {}
    producers = {}
    producer_schemas = {}
    producer_presence = {}
    consumed_input_contracts = {}
    consumed_source_contracts = {
        source: {} for source in normalized_source_contracts
    }
    unknown_source_wires = {}
    consumed_source_wires = set()
    dependencies = {node_id: set() for node_id in nodes}
    edges = []
    missing_contract = object()

    ordered_input_boundaries = sorted(
        graph["inputs"].items(),
        key=lambda item: canonical_data_key_order(
            item[1]["dataKey"], item[0]
        ),
    )
    for boundary_id, boundary in ordered_input_boundaries:
        data_key = boundary["dataKey"]
        wire = boundary["wire"]
        source = boundary.get("source")
        boundary_contracts = (
            initial if source is None else normalized_source_contracts[source]
        )
        boundary_required_roots = (
            initial_required_roots
            if source is None
            else normalized_source_required_roots[source]
        )
        if wire in producers:
            raise ValueError(f"{label} wire '{wire}' has multiple producers.")
        schema = resolve_expanded_contract_path(
            boundary_contracts, data_key, missing_contract
        )
        if schema is not missing_contract:
            root = split_data_path(data_key)[0]
            consumed_contracts = (
                consumed_input_contracts
                if source is None
                else consumed_source_contracts[source]
            )
            consumed_contracts[root] = copy.deepcopy(boundary_contracts[root])
        else:
            schema = {}
            unknown_source_wires[wire] = (boundary_id, data_key)
        producers[wire] = (boundary_id, "value")
        producer_schemas[wire] = copy.deepcopy(schema)
        producer_presence[wire] = (
            expanded_contract_path_required(
                boundary_contracts,
                data_key,
                required_roots=boundary_required_roots,
            )
            if schema is not missing_contract
            else False
        )

    for node_id in nodes:
        instance = instances.get(node_id)
        if instance is None:
            raise ValueError(f"{label} references unknown instance '{node_id}'.")
        if allowed_kinds is not None and instance.get("kind") not in set(allowed_kinds):
            raise ValueError(
                f"{label} instance '{node_id}' has kind '{instance.get('kind')}', "
                f"expected one of {', '.join(sorted(allowed_kinds))}."
            )
        definition = _definition(
            definitions,
            instance,
            label,
            verified_definitions=verified_definitions,
        )
        ports = _validate_instance(node_id, instance, definition, label)
        ports_by_node[node_id] = ports
        binding = {
            "instanceId": node_id,
            "kind": instance["kind"],
            "moduleId": instance["moduleId"],
            "version": instance["version"],
            "config": copy.deepcopy(instance["config"]),
            "inputs": copy.deepcopy(instance["inputs"]),
            "outputs": copy.deepcopy(instance["outputs"]),
        }
        bindings[node_id] = binding
        for port_name, wire_id in sorted(binding["outputs"].items()):
            if wire_id in producers:
                raise ValueError(
                    f"{label} wire '{wire_id}' has multiple producers: "
                    f"'{producers[wire_id][0]}.{producers[wire_id][1]}' and '{node_id}.{port_name}'."
                )
            schema = port_schema(ports["outputs"][port_name])
            producers[wire_id] = (node_id, port_name)
            producer_schemas[wire_id] = schema
            producer_presence[wire_id] = ports["outputs"][port_name]["required"]

    for node_id in nodes:
        binding = bindings[node_id]
        for port_name, wire_id in sorted(binding["inputs"].items()):
            producer = producers.get(wire_id)
            port = ports_by_node[node_id]["inputs"][port_name]
            if producer is None:
                if port.get("required", True):
                    raise ValueError(
                        f"{label} input '{node_id}.{port_name}' references unknown wire '{wire_id}'."
                    )
                continue
            if (
                port.get("required", True)
                and not producer_presence[wire_id]
                and (strict_sources or wire_id not in unknown_source_wires)
            ):
                raise ValueError(
                    f"{label} required input '{node_id}.{port_name}' relies on "
                    f"optional wire '{wire_id}'."
                )
            if producer[0] == node_id:
                raise ValueError(f"{label} Module '{node_id}' reads its own output wire '{wire_id}'.")
            required_schema = port_schema(port)
            if wire_id in unknown_source_wires:
                consumed_source_wires.add(wire_id)
                if strict_sources and port.get("required", True):
                    boundary_id, data_key = unknown_source_wires[wire_id]
                    raise ValueError(
                        f"{label} Data Input '{boundary_id}' references unknown DataKey "
                        f"'{data_key}' required by '{node_id}.{port_name}'."
                    )
                # An unbound optional source has no runtime value whose type
                # can be inferred.  Consumer constraints are checked together
                # only after composition supplies a concrete source contract;
                # choosing the first consumer here made archival depend on
                # node ordering.
                edges.append({
                    "wire": wire_id,
                    "from": {
                        "node": producer[0], "port": producer[1],
                        "schema": copy.deepcopy(required_schema),
                    },
                    "to": {
                        "node": node_id, "port": port_name,
                        "schema": required_schema,
                    },
                })
                continue
            provided_schema = producer_schemas[wire_id]
            if not schemas_compatible(provided_schema, required_schema):
                raise ValueError(
                    f"{label} wire '{wire_id}' schema mismatch: "
                    f"{producer[0]}.{producer[1]} outputs {schema_label(provided_schema)}, "
                    f"but {node_id}.{port_name} requires {schema_label(required_schema)}."
                )
            if producer[0] in dependencies:
                dependencies[node_id].add(producer[0])
            edges.append({
                "wire": wire_id,
                "from": {"node": producer[0], "port": producer[1], "schema": provided_schema},
                "to": {"node": node_id, "port": port_name, "schema": required_schema},
            })

    remaining = set(nodes)
    topology = []
    while remaining:
        ready = [
            node_id for node_id in nodes
            if node_id in remaining and not (dependencies[node_id] & remaining)
        ]
        if not ready:
            raise ValueError(
                f"{label} dependency graph contains a cycle: " + ", ".join(sorted(remaining))
            )
        for node_id in ready:
            topology.append(node_id)
            remaining.remove(node_id)
    node_rank = {node_id: index for index, node_id in enumerate(topology)}

    def output_write_rank(item):
        boundary_id, boundary = item
        producer = producers.get(boundary["wire"], ("", ""))
        return (
            *canonical_data_key_order(boundary["dataKey"]),
            node_rank.get(producer[0], -1),
            producer[0],
            producer[1],
            boundary_id,
        )

    ordered_output_boundaries = sorted(
        graph["outputs"].items(), key=output_write_rank
    )
    output_contracts = ExpandedContractWriteReducer({}, frozenset())
    required_outputs = []
    for boundary_id, boundary in ordered_output_boundaries:
        data_key = boundary["dataKey"]
        wire = boundary["wire"]
        producer = producers.get(wire)
        if producer is None:
            raise ValueError(
                f"{label} Data Output '{boundary_id}' references unknown wire '{wire}'."
            )
        if wire in unknown_source_wires:
            # An optional input absent from this composition produces no
            # boundary write.  It remains an explicit never-present edge and
            # is recompiled with a concrete schema if a later composition
            # supplies the DataKey.
            consumed_source_wires.add(wire)
        schema = producer_schemas[wire]
        if producer_presence[wire]:
            required_outputs.append(boundary_id)
        if schema:
            output_contracts.apply(
                data_key,
                schema,
                required=producer_presence[wire],
            )
        edges.append({
            "wire": wire,
            "from": {
                "node": producer[0], "port": producer[1],
                "schema": schema if schema else False,
            },
            "to": {
                "node": boundary_id, "port": "value",
                "schema": schema if schema else False,
            },
        })

    unused_unknown_sources = sorted(set(unknown_source_wires) - consumed_source_wires)
    if unused_unknown_sources:
        boundary_id, data_key = unknown_source_wires[unused_unknown_sources[0]]
        raise ValueError(
            f"{label} Data Input '{boundary_id}' references unused unknown DataKey '{data_key}'."
        )

    # Non-strict compilation is used only while archiving a Graph before a
    # Backtest selects its concrete upstream resources.  Unknown boundary
    # contracts must not become wildcard DataKey contracts; omit them from
    # this preliminary plan and require strict recompilation at composition.
    # Keep each referenced root contract intact.  Reconstructing a root from a
    # flattened leaf would turn optional children into required children and
    # cannot represent dynamically named keys admitted by typed maps.
    compiled_input_contracts = consumed_input_contracts
    compiled_output_contracts, output_required_roots = output_contracts.materialize()
    input_port_rank = {
        node_id: {
            port_name: index
            for index, port_name in enumerate(sorted(bindings[node_id]["inputs"]))
        }
        for node_id in topology
    }
    output_boundary_rank = {
        boundary_id: index
        for index, (boundary_id, _boundary) in enumerate(ordered_output_boundaries)
    }

    def edge_rank(edge):
        target_node = edge["to"]["node"]
        if target_node in node_rank:
            return (
                0,
                node_rank[target_node],
                input_port_rank[target_node][edge["to"]["port"]],
                edge["wire"],
                edge["from"]["node"],
                edge["from"]["port"],
            )
        return (
            1,
            output_boundary_rank[target_node],
            0,
            edge["wire"],
            edge["from"]["node"],
            edge["from"]["port"],
        )

    edges.sort(key=edge_rank)
    required_output_set = set(required_outputs)
    required_outputs = [
        edge["to"]["node"]
        for edge in edges
        if edge["to"]["node"] in graph["outputs"]
        and edge["to"]["node"] in required_output_set
    ]
    compiled_input_contracts = expand_contracts(compiled_input_contracts)
    result = {
        "nodes": topology,
        "topology": topology,
        "inputs": copy.deepcopy(dict(ordered_input_boundaries)),
        "outputs": copy.deepcopy(dict(ordered_output_boundaries)),
        "bindings": bindings,
        "edges": edges,
        "inputContracts": compiled_input_contracts,
        "inputRequiredRoots": sorted(
            set(initial_required_roots)
            & set(expanded_contract_root_paths(compiled_input_contracts))
        ),
        "outputContracts": compiled_output_contracts,
        "requiredOutputs": required_outputs,
    }
    if consumed_source_contracts:
        result["inputSources"] = {}
        for source, contracts in consumed_source_contracts.items():
            contracts = expand_contracts(contracts)
            result["inputSources"][source] = {
                "contracts": contracts,
                "requiredRoots": sorted(
                    set(normalized_source_required_roots[source])
                    & set(expanded_contract_root_paths(contracts))
                ),
            }
    return result, verified_definitions


def compile_module_graph(
    graph,
    instances,
    definitions,
    initial_contracts,
    *,
    allowed_kinds=None,
    label="Graph",
    strict_sources=True,
    required_roots=None,
    source_contracts=None,
    source_required_roots=None,
):
    """Compile raw Graph authorities after strict unique-record verification."""
    plan, _authorities = _compile_module_graph_with_authorities(
        graph,
        instances,
        definitions,
        {},
        initial_contracts,
        allowed_kinds=allowed_kinds,
        label=label,
        strict_sources=strict_sources,
        required_roots=required_roots,
        source_contracts=source_contracts,
        source_required_roots=source_required_roots,
    )
    return plan


def _definition_authority_material(definition_authorities):
    return {
        key: _module_definition_authority.verified_module_definition_material(authority)
        for key, authority in definition_authorities.items()
    }


def compile_module_graph_authority(
    graph,
    instances,
    definitions,
    initial_contracts,
    *,
    allowed_kinds=None,
    label="Graph",
    required_roots=None,
    source_contracts=None,
    source_required_roots=None,
):
    """Compile one Graph and retain an Engine-owned proof for same-stack Runtime use."""
    definitions = copy.deepcopy(definitions)
    plan, definition_authorities = _compile_module_graph_with_authorities(
        graph,
        instances,
        definitions,
        {},
        initial_contracts,
        allowed_kinds=allowed_kinds,
        label=label,
        strict_sources=True,
        required_roots=required_roots,
        source_contracts=source_contracts,
        source_required_roots=source_required_roots,
    )
    return _seal_compiled_graph_authority(plan, definition_authorities)


def compile_verified_module_graph(
    graph,
    instances,
    definition_authorities,
    initial_contracts,
    *,
    allowed_kinds=None,
    label="Graph",
    strict_sources=True,
    required_roots=None,
    source_contracts=None,
    source_required_roots=None,
):
    """Compile a non-executable plan from already verified Definitions."""
    if not isinstance(definition_authorities, Mapping):
        raise TypeError("Verified Module Definition authorities must be an object.")
    authorities = dict(definition_authorities)
    definitions = _definition_authority_material(authorities)
    plan, _used_authorities = _compile_module_graph_with_authorities(
        graph,
        instances,
        definitions,
        authorities,
        initial_contracts,
        allowed_kinds=allowed_kinds,
        label=label,
        strict_sources=strict_sources,
        required_roots=required_roots,
        source_contracts=source_contracts,
        source_required_roots=source_required_roots,
    )
    return plan


def compile_verified_module_graph_authority(
    graph,
    instances,
    definition_authorities,
    initial_contracts,
    *,
    allowed_kinds=None,
    label="Graph",
    required_roots=None,
    source_contracts=None,
    source_required_roots=None,
):
    """Compile using exact Module records verified earlier in this call stack."""
    if not isinstance(definition_authorities, Mapping):
        raise TypeError("Verified Module Definition authorities must be an object.")
    authorities = dict(definition_authorities)
    definitions = _definition_authority_material(authorities)
    plan, used_authorities = _compile_module_graph_with_authorities(
        graph,
        instances,
        definitions,
        authorities,
        initial_contracts,
        allowed_kinds=allowed_kinds,
        label=label,
        strict_sources=True,
        required_roots=required_roots,
        source_contracts=source_contracts,
        source_required_roots=source_required_roots,
    )
    return _seal_compiled_graph_authority(plan, used_authorities)
